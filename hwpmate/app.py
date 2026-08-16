from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Optional

from PyQt6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from .app_instance import SingleInstanceLock
from .constants import FORMAT_TYPES, VERSION
from .logging_config import get_logger
from .services.hwp_converter import HWPConverter, PYWIN32_AVAILABLE, pythoncom
from .services.hwp_print_settings import normalize_pdf_export_mode
from .services.task_planner import TaskPlanner
from .services.update_installer import apply_staged_update, write_update_result
from .ui.main_window import MainWindow
from .windows_integration import (
    enable_drag_drop_for_admin,
    get_native_admin_drag_drop_policy,
    is_admin,
)

logger = get_logger(__name__)

_CLI_CONSOLE_ATTACHED = False


def _ensure_cli_console_output() -> None:
    global _CLI_CONSOLE_ATTACHED
    if _CLI_CONSOLE_ATTACHED or os.name != "nt" or not bool(getattr(sys, "frozen", False)):
        return
    _CLI_CONSOLE_ATTACHED = True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        attached = bool(kernel32.AttachConsole(-1))
        already_attached = int(kernel32.GetLastError()) == 5
        if attached or already_attached:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        pass


def _write_json_line(stream: object, line: str) -> bool:
    payload = f"{line}\n"
    write = getattr(stream, "write", None)
    flush = getattr(stream, "flush", None)
    if callable(write):
        try:
            write(payload)
            if callable(flush):
                flush()
            return True
        except (OSError, UnicodeError, ValueError):
            pass
    return False


def _print_json_line(payload: dict[str, object], *, output_path: str = "") -> None:
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    wrote = False
    original_stdout = getattr(sys, "stdout", None)
    original_stderr = getattr(sys, "stderr", None)
    if _write_json_line(original_stdout, line):
        wrote = True
    elif _write_json_line(original_stderr, line):
        wrote = True

    if not wrote:
        _ensure_cli_console_output()
        if _write_json_line(getattr(sys, "stdout", None), line):
            wrote = True
        elif _write_json_line(getattr(sys, "stderr", None), line):
            wrote = True

    if not wrote and os.name == "nt":
        try:
            with open("CONOUT$", "w", encoding="utf-8", buffering=1) as console:
                console.write(f"{line}\n")
                console.flush()
        except OSError:
            pass

    target = str(output_path or "").strip()
    if not target:
        return
    try:
        out_path = Path(target).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(line + "\n", encoding="utf-8")
    except Exception:
        pass


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"HwpMate v{VERSION} - 한글(HWP/HWPX) 일괄 변환기")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="GUI 없이 import 및 핵심 모듈 무결성을 검증합니다.",
    )
    parser.add_argument(
        "--smoke-output",
        default="",
        help="smoke 결과 JSON을 stdout 외에 추가로 기록할 파일 경로입니다.",
    )
    # 헤드리스 CLI 변환 인자
    parser.add_argument(
        "--input", "-i",
        default="",
        help="변환할 HWP/HWPX 파일 또는 폴더 경로입니다.",
    )
    parser.add_argument(
        "--format", "-f",
        default="PDF",
        help=f"출력 형식 ({', '.join(FORMAT_TYPES.keys())}, 기본값: PDF).",
    )
    parser.add_argument(
        "--output", "-o",
        default="",
        help="변환 결과물이 저장될 출력 폴더 경로입니다. (생략 시 원본과 동일 위치)",
    )
    parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="폴더 입력 시 하위 폴더의 모든 파일을 포함하여 변환합니다.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="대상 경로에 동일한 이름의 파일이 있으면 덮어씁니다.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="변환 전 원본 백업 생성을 비활성화합니다.",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=1,
        help="변환 실패 시 재시도 횟수 (0~3, 기본값: 1).",
    )
    parser.add_argument(
        "--pdf-export-mode",
        default="saveas_first",
        choices=["saveas_first", "print_to_pdf_ex_first"],
        help="PDF 내보내기 우선 모드 (saveas_first | print_to_pdf_ex_first).",
    )

    # 자동 업데이트 내부 헬퍼 인자
    parser.add_argument("--apply-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-target", default="", help=argparse.SUPPRESS)
    parser.add_argument("--update-staged", default="", help=argparse.SUPPRESS)
    parser.add_argument("--update-backup", default="", help=argparse.SUPPRESS)
    parser.add_argument("--update-parent-pid", default=0, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--update-expected-sha256", default="", help=argparse.SUPPRESS)
    parser.add_argument("--update-expected-size", default=0, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--update-result-file", default="", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run_smoke(args: argparse.Namespace) -> int:
    result: dict[str, object] = {
        "status": "ok",
        "version": VERSION,
        "pywin32_available": PYWIN32_AVAILABLE,
    }
    try:
        import cryptography  # noqa: F401
        from .services.update_manifest import verify_release_manifest  # noqa: F401
        from .services.update_installer import apply_staged_update  # noqa: F401
        result["cryptography_available"] = True
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"cryptography / update 모듈 로드 실패: {exc}"
        _print_json_line(result, output_path=args.smoke_output)
        return 1

    _print_json_line(result, output_path=args.smoke_output)
    return 0


def _wait_for_parent(parent_pid: int, timeout: float = 30.0) -> None:
    if parent_pid <= 0:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    raise TimeoutError("업데이트 적용 전 부모 프로세스가 종료되지 않았습니다.")


def _run_apply_update(args: argparse.Namespace) -> int:
    base_result = {
        "target": str(Path(args.update_target).resolve()),
        "backup": str(Path(args.update_backup).resolve()),
        "completed_at": time.time(),
    }
    try:
        _wait_for_parent(args.update_parent_pid)
        apply_staged_update(
            target=Path(args.update_target),
            staged=Path(args.update_staged),
            backup=Path(args.update_backup),
            expected_sha256=args.update_expected_sha256,
            expected_size=args.update_expected_size,
        )
    except Exception as exc:
        status = "rolled_back" if "rolled back" in str(exc).lower() or "롤백" in str(exc) else "failed"
        write_update_result(
            args.update_result_file,
            {**base_result, "status": status, "error": str(exc)},
        )
        return 1

    write_update_result(args.update_result_file, {**base_result, "status": "applied"})
    try:
        import subprocess
        subprocess.Popen([str(Path(args.update_target).resolve())])
    except Exception:
        pass
    return 0


def _run_cli_conversion(args: argparse.Namespace) -> int:
    """헤드리스 CLI 일괄 변환 실행."""
    _ensure_cli_console_output()
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"[오류] 입력 경로가 존재하지 않습니다: {input_path}", file=sys.stderr)
        return 1

    format_type = str(args.format).upper().strip()
    if format_type not in FORMAT_TYPES:
        print(
            f"[오류] 지원하지 않는 출력 형식: {args.format} (가능한 형식: {', '.join(FORMAT_TYPES.keys())})",
            file=sys.stderr,
        )
        return 1

    is_folder_mode = input_path.is_dir()
    same_location = not bool(args.output)
    output_path = ""
    if args.output:
        out_dir = Path(args.output).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir)

    planner = TaskPlanner()

    plan = planner.build_tasks(
        is_folder_mode=is_folder_mode,
        format_type=format_type,
        folder_path=str(input_path) if is_folder_mode else "",
        include_sub=args.recursive,
        same_location=same_location,
        output_path=output_path,
        file_paths=[str(input_path)] if not is_folder_mode else [],
        backup_enabled=not args.no_backup,
        retry_count=max(0, min(3, args.retry)),
        pdf_export_mode=args.pdf_export_mode,
    )

    planner.resolve_output_conflicts(plan.tasks, overwrite=args.overwrite, format_type=format_type)

    tasks = plan.tasks
    if not tasks:
        print("[안내] 변환 대상 파일이 없습니다.")
        return 0

    print(f"=== HwpMate v{VERSION} CLI 일괄 변환 시작 ===")
    print(f"변환 대상: 총 {len(tasks)}개 파일 | 목표 형식: {format_type}")
    if plan.warnings:
        for w in plan.warnings:
            print(f"⚠️ [경고] {w}")

    if not PYWIN32_AVAILABLE:
        print("[오류] pywin32 라이브러리가 필요합니다. pip install pywin32 후 다시 실행하세요.", file=sys.stderr)
        return 1

    converter = HWPConverter()
    try:
        converter.initialize(manage_com_apartment=True)
        if hasattr(converter, "pdf_export_mode"):
            converter.pdf_export_mode = normalize_pdf_export_mode(args.pdf_export_mode)
    except Exception as exc:
        print(f"[오류] 한글 COM 초기화 실패: {exc}", file=sys.stderr)
        return 1

    success_count = 0
    fail_count = 0
    skip_count = 0

    try:
        for idx, task in enumerate(tasks):
            prefix = f"[{idx + 1}/{len(tasks)}]"
            if task.status == "건너뜀":
                print(f"{prefix} ⏭️ 건너뜀: {task.input_file.name} ({task.detail})")
                skip_count += 1
                continue

            print(f"{prefix} 🔄 변환 중: {task.input_file.name} ➔ {task.output_file.name}...", end=" ", flush=True)
            ok, err = converter.convert_file(task.input_file, task.output_file, format_type)
            if ok:
                print("✅ 성공")
                success_count += 1
            else:
                print(f"❌ 실패: {err}")
                fail_count += 1
    finally:
        try:
            converter.cleanup()
        except Exception:
            pass

    print("=" * 45)
    print(f"변환 완료 요약: 성공 {success_count}건, 실패 {fail_count}건, 건너뜀 {skip_count}건")
    return 0 if fail_count == 0 else 1


def handle_exception(exc_type, exc_value, exc_traceback) -> None:
    """글로벌 예외 핸들러."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("치명적 오류 발생", exc_info=(exc_type, exc_value, exc_traceback))

    try:
        if QApplication.instance():
            QMessageBox.critical(
                None,
                "치명적 오류",
                f"프로그램에서 예기치 않은 오류가 발생했습니다.\n\n"
                f"오류: {exc_type.__name__}: {exc_value}\n\n"
                f"프로그램을 다시 시작해 주세요.",
            )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    """메인 함수."""
    sys.excepthook = handle_exception
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.apply_update:
        return _run_apply_update(args)

    if args.smoke:
        return _run_smoke(args)

    if args.input:
        return _run_cli_conversion(args)

    if not PYWIN32_AVAILABLE:
        app = QApplication(sys.argv)
        QMessageBox.critical(
            None, "오류",
            "pywin32 라이브러리가 필요합니다.\n\npip install pywin32"
        )
        del app
        return 1

    if not is_admin():
        app = QApplication(sys.argv)
        QMessageBox.warning(
            None,
            "관리자 권한 필요",
            "이 프로그램은 관리자 권한으로 실행해야 합니다.\n\n"
            "파일을 마우스 오른쪽 버튼으로 클릭하여\n"
            "'관리자 권한으로 실행'을 선택하세요."
        )
        del app
        return 1

    try:
        native_dnd_enabled, native_dnd_reason = get_native_admin_drag_drop_policy()
        if native_dnd_enabled:
            enable_drag_drop_for_admin()
        else:
            logger.warning(f"관리자용 네이티브 드래그 앤 드롭 비활성화: {native_dnd_reason}")

        app = QApplication(sys.argv)
        app.setStyle(QStyleFactory.create("Fusion"))

        instance_lock = SingleInstanceLock()
        if not instance_lock.try_lock():
            QMessageBox.information(
                None,
                "이미 실행 중",
                "HwpMate가 이미 실행 중입니다.\n기존 창을 사용해 주세요.",
            )
            del app
            return 0

        try:
            window = MainWindow()
            window.show()

            exit_code = app.exec()
            logger.info(f"애플리케이션 이벤트 루프 종료: code={exit_code}")
            return int(exit_code)
        finally:
            instance_lock.release()
    except Exception as e:
        logger.critical(f"애플리케이션 실행 오류: {e}")
        raise
