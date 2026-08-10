"""HWPConverter — COM 연결·단일 파일 변환·소유 프로세스 종료."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Tuple, cast

from ...constants import DOCUMENT_LOAD_DELAY, FORMAT_TYPES, HWP_PROGIDS
from ...logging_config import get_logger
from ...path_utils import com_path_candidates
from ..hwp_print_settings import (
    EXPORT_METHOD_SAVEAS_2,
    EXPORT_METHOD_SAVEAS_3,
    PDF_EXPORT_PRINT_TO_PDF_EX_FIRST,
    PDF_EXPORT_SAVEAS_FIRST,
    apply_default_print_settings,
    is_valid_pdf_file,
    normalize_pdf_export_mode,
    remove_incomplete_output,
    try_export_pdf_via_print_to_pdf_ex,
    uses_print_settings_control,
)
from ..hwp_security_module import (
    SECURITY_MODULE_ALIAS,
    ensure_hwp_security_module,
)
from .artifact_snapshot import (
    _changed_artifacts,
    _snapshot_artifacts,
    remove_new_attempt_artifacts,
)
from .com_types import HwpAutomation, is_com_failure_result, pythoncom, require_pywin32
from .process_snapshot import _snapshot_hwp_pids, get_snapshot_health

logger = get_logger(__name__)

# 한컴 보안 모듈 RegisterModule 두 번째 인자 후보
# (ensure_hwp_security_module 이 등록하는 별칭을 최우선)
SECURITY_MODULE_ALIASES = (
    SECURITY_MODULE_ALIAS,
    "FilePathCheckerModule",
    "SecurityModule",
)

# Windows: 콘솔 창 없이 자식 프로세스 실행
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class HWPConverter:
    """한글 변환 엔진 - 기존 로직 완전 유지."""

    def __init__(self) -> None:
        self.hwp: Optional[HwpAutomation] = None
        self.progid_used: Optional[str] = None
        self.is_initialized = False
        self.owned_pids: set[int] = set()
        self.security_module_registered: bool | None = None
        self.security_module_error: str | None = None
        self.process_tracking_warning: str | None = None
        self.snapshot_unreliable: bool = False
        self.last_created_files: list[Path] = []
        self.last_output_size: int | None = None
        self.last_output_mtime: float | None = None
        self.last_save_format: str | None = None
        self.last_export_method: str | None = None
        # PDF 내보내기 전략 (saveas_first | print_to_pdf_ex_first)
        self.pdf_export_mode: str = PDF_EXPORT_SAVEAS_FIRST
        # True only when this instance successfully called CoInitialize itself.
        self._com_apartment_owned = False

    def initialize(self, *, manage_com_apartment: bool = True) -> bool:
        """COM 초기화 및 한글 객체 생성.

        manage_com_apartment=False 이면 호출 스레드가 이미 CoInitialize 한 경우
        (ConversionWorker 등) 중복 초기화/해제를 하지 않는다.
        """
        if self.is_initialized:
            return True

        pythoncom_module, win32_client_module = require_pywin32()

        if manage_com_apartment:
            try:
                pythoncom_module.CoInitialize()
                self._com_apartment_owned = True
            except Exception as e:
                # 이미 동일 스레드에서 초기화된 경우 등은 무시하고 소유하지 않는다.
                self._com_apartment_owned = False
                logger.debug(f"CoInitialize 오류 (무시 가능): {e}")
        else:
            self._com_apartment_owned = False

        # DLL 설치 + HKCU\...\HwpAutomation\Modules 레지스트리 (RegisterModule 사전 조건)
        prep_ok, prep_msg, prep_alias = ensure_hwp_security_module()
        if prep_ok:
            logger.info(f"보안 모듈 사전 준비 완료: {prep_msg}")
        else:
            logger.warning(f"보안 모듈 사전 준비 실패: {prep_msg}")

        errors = []
        for progid in HWP_PROGIDS:
            before_pids = _snapshot_hwp_pids()
            try:
                self.hwp = cast(HwpAutomation, win32_client_module.Dispatch(progid))
                self.progid_used = progid
                hwp = self.hwp

                module_errors: list[str] = []
                self.security_module_registered = False
                self.security_module_error = None
                aliases: list[str] = []
                if prep_alias:
                    aliases.append(prep_alias)
                for name in SECURITY_MODULE_ALIASES:
                    if name not in aliases:
                        aliases.append(name)

                for alias in aliases:
                    try:
                        result = hwp.RegisterModule("FilePathCheckDLL", alias)
                        if result is False or result == 0:
                            module_errors.append(f"{alias}: RegisterModule returned {result!r}")
                            continue
                        # 레지스트리+DLL 사전 준비가 된 경우에만 "완전 성공" (팝업 억제 기대)
                        if prep_ok:
                            self.security_module_registered = True
                            self.security_module_error = None
                            logger.info(
                                f"한글 보안 모듈 등록 성공: alias={alias}, result={result!r}"
                            )
                        else:
                            self.security_module_registered = False
                            self.security_module_error = (
                                f"RegisterModule({alias}) 호출됨(result={result!r})이나 "
                                f"레지스트리/DLL 미준비: {prep_msg}"
                            )
                            logger.warning(self.security_module_error)
                        break
                    except Exception as module_error:
                        module_errors.append(f"{alias}: {module_error}")

                if not self.security_module_registered and self.security_module_error is None:
                    self.security_module_error = (
                        f"prep={prep_msg}; " + ("; ".join(module_errors) or "알 수 없는 오류")
                    )
                    logger.warning(
                        "한글 보안 모듈 등록 실패 (파일 접근 시 '모두 허용' 창이 뜰 수 있음): "
                        f"{self.security_module_error}"
                    )

                hwp.SetMessageBoxMode(0x00000001)
                time.sleep(0.2)
                after_pids = _snapshot_hwp_pids()
                fail_count, fail_msg = get_snapshot_health()
                self.snapshot_unreliable = fail_count > 0 and not after_pids and not before_pids
                self.owned_pids = after_pids - before_pids
                self.is_initialized = True
                logger.info(f"한글 연결 성공: {progid}")
                if self.snapshot_unreliable:
                    detail = f" ({fail_msg})" if fail_msg else ""
                    self.process_tracking_warning = (
                        "한글 프로세스 스냅샷(Toolhelp) 수집에 실패했습니다"
                        f"{detail}. 강제 종료와 창 전면화 범위가 제한될 수 있습니다."
                    )
                    logger.warning(self.process_tracking_warning)
                elif self.owned_pids:
                    self.process_tracking_warning = None
                    logger.info(f"앱 소유 한글 프로세스 추적: {sorted(self.owned_pids)}")
                else:
                    self.process_tracking_warning = (
                        "새로 생성된 한글 프로세스를 추적하지 못했습니다. "
                        "강제 종료는 비활성화됩니다. 변환 전 다른 한글 창을 닫으면 추적이 안정됩니다."
                    )
                    logger.info(self.process_tracking_warning)
                # 메인 편집 창 숨김 + 보안/허용 팝업만 전면화 (실패해도 연결 유지)
                self._suppress_hwp_ui_flash()
                return True

            except Exception as e:
                errors.append(f"{progid}: {str(e)}")
                continue

        error_detail = "\n".join(errors)
        if self._com_apartment_owned and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_apartment_owned = False
        raise Exception(f"한글 COM 객체 생성 실패\n\n시도한 ProgID:\n{error_detail}")

    def _try_set_xhw_windows_visible(self, visible: bool) -> bool:
        """XHwpWindows.Item(i).Visible 로 메인 창 표시 여부 설정 (버전 미지원 시 False)."""
        hwp = self.hwp
        if hwp is None:
            return False
        try:
            xwindows = getattr(hwp, "XHwpWindows", None)
            if xwindows is None:
                return False
            count_raw = getattr(xwindows, "Count", None)
            try:
                count = int(count_raw) if count_raw is not None else 1
            except (TypeError, ValueError):
                count = 1
            if count < 1:
                count = 1
            any_ok = False
            for index in range(count):
                try:
                    window = xwindows.Item(index)
                    window.Visible = visible
                    any_ok = True
                except Exception:
                    if index == 0:
                        return False
                    break
            return any_ok
        except Exception as e:
            logger.debug(f"XHwpWindows.Visible={visible} 실패(무시): {e}")
            return False

    def _suppress_hwp_ui_flash(self) -> None:
        """메인 편집 창 숨김 + 보안 대화상자 전면화 (best-effort, 변환 실패로 전파하지 않음).

        HWND 조작은 소유 PID가 있을 때만 수행한다.
        owned_pids 가 비어 있을 때 None 을 넘기면 전역 HWP 조작이 되므로 금지한다.
        COM XHwpWindows.Visible 은 현재 Dispatch 세션에만 적용된다.
        """
        try:
            self._try_set_xhw_windows_visible(False)
        except Exception as e:
            logger.debug(f"COM Visible=False 실패(무시): {e}")
        if not self.owned_pids:
            return
        try:
            from ...windows_integration import suppress_hwp_ui_flash

            # 빈 set 이 아니라 확정된 소유 PID 집합만 전달 (None=전역 금지)
            hidden, raised = suppress_hwp_ui_flash(set(self.owned_pids))
            if hidden or raised:
                logger.debug(
                    f"한글 UI 억제: hidden={hidden}, security_raised={raised}, "
                    f"pids={sorted(self.owned_pids)}"
                )
        except Exception as e:
            logger.debug(f"한글 UI 억제 실패(무시): {e}")

    def convert_file(
        self,
        input_path,
        output_path,
        format_type="PDF",
        *,
        cancel_check: Any = None,
    ) -> Tuple[bool, Optional[str]]:
        """단일 파일 변환.

        cancel_check: 호출 시 취소 여부(bool)를 반환하는 콜백(선택).
        PDF 전략은 self.pdf_export_mode (saveas_first | print_to_pdf_ex_first).
        """
        hwp = self.hwp
        if not self.is_initialized or hwp is None:
            return False, "한글 객체가 초기화되지 않았습니다"

        def _cancelled() -> bool:
            if cancel_check is None:
                return False
            try:
                return bool(cancel_check())
            except Exception:
                return False

        try:
            # 산출물 검증용 논리 경로 (확장 접두 없는 Path)
            output_file = Path(str(output_path))
            input_open_candidates = com_path_candidates(input_path)
            output_save_candidates = com_path_candidates(output_file)
            self.last_created_files = []
            self.last_output_size = None
            self.last_output_mtime = None
            self.last_save_format = None
            self.last_export_method = None

            # 일부 환경에서 Open 직전 재등록이 파일 경로 승인 훅을 안정화함
            try:
                alias = SECURITY_MODULE_ALIAS
                hwp.RegisterModule("FilePathCheckDLL", alias)
            except Exception as re_reg_error:
                logger.debug(f"Open 전 RegisterModule 재호출 실패(무시): {re_reg_error}")

            if _cancelled():
                return False, "사용자 취소"

            open_result: object = False
            opened = False
            for input_str in input_open_candidates:
                if _cancelled():
                    return False, "사용자 취소"
                open_result = hwp.Open(input_str, "", "forceopen:true")
                if not is_com_failure_result(open_result):
                    opened = True
                    if input_str != input_open_candidates[0]:
                        logger.debug(f"Open 확장/대체 경로 성공: {input_str}")
                    break
                logger.debug(f"Open 실패 후보: {input_str!r} → {open_result!r}")
            if not opened:
                try:
                    hwp.Clear(option=1)
                except Exception:
                    pass
                return False, _open_failure_message(open_result)
            time.sleep(DOCUMENT_LOAD_DELAY)
            # Open 후 메인 창이 다시 뜰 수 있어 best-effort 재숨김
            self._suppress_hwp_ui_flash()

            if _cancelled():
                try:
                    hwp.Clear(option=1)
                except Exception:
                    pass
                return False, "사용자 취소"

            format_info = FORMAT_TYPES.get(format_type, FORMAT_TYPES["PDF"])
            save_format = format_info["save_format"]
            self.last_save_format = save_format

            # PDF·이미지: 문서에 남은 모아찍기 등 인쇄설정을 1쪽씩으로 best-effort 리셋
            # (원본 디스크 파일은 저장하지 않음. 실패해도 변환은 계속)
            format_key = str(format_type).upper()
            if uses_print_settings_control(format_key):
                try:
                    if apply_default_print_settings(hwp):
                        logger.debug(f"인쇄 설정 리셋 적용: format={format_key}")
                    else:
                        logger.debug(f"인쇄 설정 리셋 미적용(무시): format={format_key}")
                except Exception as print_reset_error:
                    logger.debug(f"인쇄 설정 리셋 예외(무시): {print_reset_error}")

            save_error = None
            before_artifacts = _snapshot_artifacts(output_file, format_type)

            def _cleanup_failed_artifacts() -> None:
                removed, cleanup_warnings = remove_new_attempt_artifacts(
                    before_artifacts, output_file, format_type
                )
                if removed:
                    logger.warning(
                        "실패한 변환 산출물 정리: %s",
                        ", ".join(str(path) for path in removed),
                    )
                for warning in cleanup_warnings:
                    logger.warning(warning)

            exported = False
            pdf_mode = normalize_pdf_export_mode(self.pdf_export_mode)

            def _try_print_to_pdf() -> bool:
                """PrintToPDFEx/RunToPDF (물리 Print Execute 없음)."""
                if _cancelled():
                    return False
                for output_str in output_save_candidates:
                    if _cancelled():
                        return False
                    try:
                        ok, method = try_export_pdf_via_print_to_pdf_ex(
                            hwp,
                            output_str,
                            cancel_check=cancel_check,
                        )
                        if ok:
                            self.last_save_format = "PDF"
                            self.last_export_method = method
                            logger.debug(
                                f"PrintToPDF 경로 성공: method={method}, path={output_str}"
                            )
                            return True
                    except Exception as pdf_ex_error:
                        logger.debug(f"PrintToPDF 경로 예외 ({output_str}): {pdf_ex_error}")
                return False

            def _try_saveas() -> bool:
                nonlocal save_error
                if _cancelled():
                    return False
                errors: list[str] = []
                for output_str in output_save_candidates:
                    if _cancelled():
                        return False
                    try:
                        save_result = hwp.SaveAs(output_str, save_format)
                        if is_com_failure_result(save_result):
                            raise RuntimeError(
                                f"SaveAs 2-param returned failure: {save_result!r}"
                            )
                        logger.debug(f"SaveAs 2-param 성공: {output_str}")
                        self.last_export_method = EXPORT_METHOD_SAVEAS_2
                        return True
                    except Exception as e1:
                        logger.debug(f"SaveAs 2-param 실패 ({output_str}): {e1}")
                        if _cancelled():
                            return False
                        try:
                            save_result = hwp.SaveAs(output_str, save_format, "")
                            if is_com_failure_result(save_result):
                                raise RuntimeError(
                                    f"SaveAs 3-param returned failure: {save_result!r}"
                                )
                            logger.debug(f"SaveAs 3-param 성공: {output_str}")
                            self.last_export_method = EXPORT_METHOD_SAVEAS_3
                            return True
                        except Exception as e2:
                            errors.append(f"{output_str}: 2-param: {e1}, 3-param: {e2}")
                save_error = "; ".join(errors) if errors else "SaveAs 실패"
                logger.error(f"모든 SaveAs 방식 실패: {save_error}")
                return False

            # PDF 전략:
            # - saveas_first(기본): 용지 품질 우선 → SaveAs 후 실패 시 PrintToPDFEx
            # - print_to_pdf_ex_first: 모아찍기 완화 우선 → PrintToPDFEx 1패스 후 SaveAs
            used_print_path = False
            used_saveas_path = False
            if format_key == "PDF":
                if pdf_mode == PDF_EXPORT_PRINT_TO_PDF_EX_FIRST:
                    if _try_print_to_pdf():
                        exported = True
                        used_print_path = True
                    elif _try_saveas():
                        exported = True
                        used_saveas_path = True
                else:
                    if _try_saveas():
                        exported = True
                        used_saveas_path = True
                    elif _try_print_to_pdf():
                        exported = True
                        used_print_path = True
            else:
                if _try_saveas():
                    exported = True
                    used_saveas_path = True

            if not exported:
                try:
                    hwp.Clear(option=1)
                except Exception:
                    pass
                if _cancelled():
                    return False, "사용자 취소"
                _cleanup_failed_artifacts()
                return False, save_error or "내보내기 실패"

            def _artifact_ok() -> Tuple[bool, Optional[str], list[Path], Any]:
                after = _snapshot_artifacts(output_file, format_type)
                primary = after.get(output_file)
                if not after:
                    return False, f"출력 파일이 생성되지 않았습니다: {output_file.name}", [], None
                if primary is not None and primary.size <= 0:
                    return False, f"출력 파일이 비어 있습니다: {output_file.name}", [], None
                changed = _changed_artifacts(before_artifacts, after)
                if not changed:
                    return (
                        False,
                        f"출력 파일이 새로 생성되거나 갱신되지 않았습니다: {output_file.name}",
                        [],
                        None,
                    )
                return True, None, changed, after

            ok, artifact_error, changed_files, after_artifacts = _artifact_ok()
            if not ok:
                # SaveAs 가 성공처럼 보였으나 산출물이 없으면 Print 폴백 1회
                recovered = False
                if (
                    format_key == "PDF"
                    and used_saveas_path
                    and not used_print_path
                    and not _cancelled()
                ):
                    if _try_print_to_pdf():
                        used_print_path = True
                        ok, artifact_error, changed_files, after_artifacts = _artifact_ok()
                        recovered = ok
                if not recovered:
                    try:
                        hwp.Clear(option=1)
                    except Exception:
                        pass
                    if _cancelled():
                        return False, "사용자 취소"
                    _cleanup_failed_artifacts()
                    return False, artifact_error

            # PDF: SaveAs 경로도 %PDF 매직 검증. 실패 시 아직 안 쓴 폴백 경로 1회 시도.
            if format_key == "PDF":
                pdf_check = (
                    output_file if output_file in changed_files else changed_files[0]
                )
                if not is_valid_pdf_file(pdf_check):
                    logger.warning(
                        f"PDF 매직/크기 검증 실패 (method={self.last_export_method}): {pdf_check}"
                    )
                    before_ns = None
                    before_sz = None
                    prev = before_artifacts.get(pdf_check)
                    if prev is not None:
                        before_ns = prev.mtime_ns
                        before_sz = prev.size
                    remove_incomplete_output(
                        pdf_check,
                        before_mtime_ns=before_ns,
                        before_size=before_sz,
                    )
                    recovered = False
                    if used_saveas_path and not used_print_path and not _cancelled():
                        if _try_print_to_pdf():
                            ok2, err2, changed_files, after_artifacts = _artifact_ok()
                            if ok2 and is_valid_pdf_file(
                                output_file
                                if output_file in changed_files
                                else changed_files[0]
                            ):
                                recovered = True
                            elif not ok2:
                                artifact_error = err2
                    if not recovered:
                        try:
                            hwp.Clear(option=1)
                        except Exception:
                            pass
                        if _cancelled():
                            return False, "사용자 취소"
                        _cleanup_failed_artifacts()
                        return False, (
                            artifact_error
                            or f"유효한 PDF가 아닙니다 (매직/크기 검사 실패): {pdf_check.name}"
                        )

            assert after_artifacts is not None
            representative = output_file if output_file in changed_files else changed_files[0]
            representative_snapshot = after_artifacts[representative]
            self.last_created_files = changed_files
            self.last_output_size = representative_snapshot.size
            try:
                self.last_output_mtime = representative.stat().st_mtime
            except OSError:
                self.last_output_mtime = representative_snapshot.mtime_ns / 1_000_000_000

            hwp.Clear(option=1)

            return True, None

        except Exception as e:
            error_msg = _with_document_access_hint(str(e))
            logger.error(f"변환 실패 ({input_path}): {error_msg}")
            if hwp is not None:
                try:
                    hwp.Clear(option=1)
                except Exception:
                    pass

            return False, error_msg

    def has_owned_processes(self) -> bool:
        return bool(self.owned_pids)

    def kill_owned_processes(self) -> bool:
        """앱이 새로 띄운 한글 프로세스만 강제 종료."""
        if not self.owned_pids:
            logger.warning("추적된 한글 프로세스가 없어 강제 종료를 수행하지 않습니다.")
            return False

        # PID 재사용 방지를 위해 종료 직전 한글 이미지명 프로세스인지 재확인한다.
        live_hwp_pids = _snapshot_hwp_pids()
        killed_any = False
        remaining: set[int] = set()
        for pid in sorted(self.owned_pids):
            if pid not in live_hwp_pids:
                logger.warning(
                    f"PID={pid} 가 현재 한글 관련 프로세스가 아니거나 이미 종료되어 강제 종료를 건너뜁니다."
                )
                continue
            try:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=_CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    killed_any = True
                    logger.warning(f"앱 소유 한글 프로세스를 강제 종료했습니다: PID={pid}")
                else:
                    remaining.add(pid)
                    logger.debug(f"PID 종료 실패 또는 이미 종료됨: PID={pid}, code={result.returncode}")
            except Exception as e:
                remaining.add(pid)
                logger.error(f"PID 강제 종료 실패: PID={pid}, 오류={e}")

        self.owned_pids = remaining
        return killed_any

    def cleanup(self) -> None:
        """정리."""
        hwp = self.hwp
        if hwp is not None and self.is_initialized:
            try:
                hwp.Clear(3)
            except Exception:
                pass

            try:
                hwp.Quit()
            except Exception:
                pass

            self.hwp = None
            self.is_initialized = False
            self.owned_pids.clear()
            self.process_tracking_warning = None

        if self._com_apartment_owned and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_apartment_owned = False


def _with_document_access_hint(message: str) -> str:
    """암호·보호·권한 관련 안내를 메시지에 best-effort 로 붙인다."""
    hint = "(암호·보호된 문서이거나 접근이 제한된 파일일 수 있습니다. 암호를 해제한 뒤 다시 시도하세요.)"
    if hint in message:
        return message
    lowered = message.lower()
    tokens_ko = ("암호", "비밀번호", "패스워드", "보호", "권한")
    tokens_en = ("password", "passwd", "encrypted", "protected", "access denied", "permission")
    if any(t in message for t in tokens_ko) or any(t in lowered for t in tokens_en):
        return f"{message} {hint}"
    return message


def _open_failure_message(open_result: object) -> str:
    base = f"문서 열기 실패: HWP Open이 실패를 반환했습니다 ({open_result!r})"
    return _with_document_access_hint(
        f"{base} 암호가 걸린 문서이면 해제 후 다시 시도하세요."
    )
