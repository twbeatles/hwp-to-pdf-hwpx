from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from PyQt6.QtCore import QThread, pyqtSignal

from ...logging_config import get_logger
from ...constants import MAX_RETRY_COUNT, RETRY_DELAY_SECONDS
from ...models import ConversionSummary, ConversionTask, PlannedConversion
from ...services.hwp_converter import HWPConverter, pythoncom
from ...services.hwp_print_settings import normalize_pdf_export_mode
from ...services.task_planner import TaskPlanner

logger = get_logger(__name__)


from .backup import create_backup
from .protocol import ConverterEngine
from .summary import (
    apply_converter_artifacts,
    build_summary as build_summary_fn,
    collect_converter_warnings as collect_converter_warnings_fn,
    engine_status_payload,
)


class ConversionWorker(QThread):
    """변환 작업 워커 스레드"""

    progress_updated = pyqtSignal(int, int, str)  # current, total, filename
    status_updated = pyqtSignal(str)
    task_completed = pyqtSignal(object)  # ConversionSummary
    stage_updated = pyqtSignal(str, float)
    # 보안 모듈 등록·소유 PID 등 UI 폴링 정책용 (initialize 직후)
    engine_status_updated = pyqtSignal(object)

    _com_initialized = False

    def __init__(
        self,
        planned_conversion: PlannedConversion,
        converter_factory: Optional[Callable[[], ConverterEngine]] = None,
    ) -> None:
        super().__init__()
        self.planned_conversion = planned_conversion
        self.tasks = planned_conversion.tasks
        self.format_type = planned_conversion.format_type
        self.backup_enabled = planned_conversion.backup_enabled
        self.retry_count = max(0, min(MAX_RETRY_COUNT, planned_conversion.retry_count))
        self.backup_max_files_per_stem = int(
            getattr(planned_conversion, "backup_max_files_per_stem", 20) or 20
        )
        self.cancel_requested = False
        self.converter: Optional[ConverterEngine] = None
        self._converter_factory: Callable[[], ConverterEngine] = converter_factory or HWPConverter
        self._task_planner = TaskPlanner()
        self.current_stage = "대기"
        self._started_at: float | None = None

    def cancel(self) -> None:
        """취소 요청"""
        self.cancel_requested = True

    def can_force_terminate(self) -> bool:
        converter = self.converter
        return bool(converter and converter.has_owned_processes())

    def run(self) -> None:
        """변환 작업 수행"""
        self._started_at = time.perf_counter()
        self._set_stage("COM 초기화")
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
                self._com_initialized = True
            except Exception as e:
                logger.debug(f"Worker COM 초기화: {e}")

        start_ts = self._started_at
        total = len(self.tasks)
        converter: ConverterEngine | None = None
        runtime_warnings: list[str] = []
        used_output_path_keys: set[str] = set()

        try:
            converter = self._converter_factory()
            self.converter = converter
            self._set_stage("한글 연결")
            self.status_updated.emit(
                "한글 프로그램 연결 중... 허용/보안 창이 뜨면 작업 표시줄을 확인해 주세요."
            )
            try:
                # 워커 스레드가 COM apartment 를 소유한다. 컨버터는 중복 CoInit/Uninit 하지 않는다.
                converter.initialize(manage_com_apartment=False)
                # PDF 내보내기 전략 (SaveAs 우선 / PrintToPDFEx 우선)
                if hasattr(converter, "pdf_export_mode"):
                    converter.pdf_export_mode = normalize_pdf_export_mode(
                        getattr(self.planned_conversion, "pdf_export_mode", None)
                    )
                runtime_warnings = self._collect_converter_warnings(converter)
                self._emit_engine_status(converter)
            except Exception as e:
                logger.exception("한글 초기화 실패")
                for task in self.tasks:
                    if task.status in {"대기", "진행중"}:
                        task.status = "실패"
                        task.error = f"한글 초기화 실패: {e}"
                summary = self._build_summary(
                    warnings=list(self.planned_conversion.warnings)
                    + self._collect_converter_warnings(converter),
                    elapsed_seconds=time.perf_counter() - start_ts,
                    progid_used=converter.progid_used,
                )
                self.task_completed.emit(summary)
                return
            self.status_updated.emit(f"연결 성공: {converter.progid_used}")

            for idx, task in enumerate(self.tasks):
                if self.cancel_requested:
                    self.status_updated.emit("사용자가 취소했습니다.")
                    break

                self.status_updated.emit(f"변환 중: {task.input_file.name}")

                if self.backup_enabled:
                    self._set_stage("백업 생성")
                    try:
                        task.backup_file = self._create_backup(task.input_file)
                    except Exception as e:
                        task.backup_error = str(e)
                        logger.warning(f"백업 실패 (계속 진행): {e}")

                original_output = task.output_file
                self._set_stage("출력 경로 확인")
                if self._task_planner.allocate_output_path(
                    task,
                    used_path_keys=used_output_path_keys,
                    overwrite=self.planned_conversion.overwrite,
                    format_type=self.format_type,
                ):
                    warning = f"변환 직전 출력 충돌 감지로 경로 변경: {original_output} -> {task.output_file}"
                    runtime_warnings.append(warning)
                    logger.warning(warning)

                try:
                    self._set_stage("출력 폴더 준비")
                    task.output_file.parent.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    task.status = "실패"
                    task.error = f"폴더 생성 실패: {e}"
                    self.progress_updated.emit(idx + 1, total, task.input_file.name)
                    continue

                if not task.input_file.exists():
                    task.status = "실패"
                    task.error = f"파일을 찾을 수 없음: {task.input_file.name}"
                    logger.warning(f"파일 없음: {task.input_file}")
                    self.progress_updated.emit(idx + 1, total, task.input_file.name)
                    continue

                task.status = "진행중"
                success = False
                error: str | None = None
                for attempt in range(self.retry_count + 1):
                    if self.cancel_requested:
                        break

                    self._set_stage("COM 내보내기")
                    success, error = converter.convert_file(
                        task.input_file,
                        task.output_file,
                        self.format_type,
                        cancel_check=lambda: self.cancel_requested,
                    )
                    if success:
                        self._apply_converter_artifacts(task, converter)
                        break

                    if attempt < self.retry_count:
                        self._set_stage("재시도 대기")
                        task.retry_count += 1
                        self.status_updated.emit(
                            f"재시도 중: {task.input_file.name} ({task.retry_count}/{self.retry_count})"
                        )
                        time.sleep(RETRY_DELAY_SECONDS)

                if success:
                    task.status = "성공"
                    task.error = None
                elif self.cancel_requested:
                    # 취소 요청 후 실패(또는 미완료)는 실패 대신 취소로 집계한다.
                    detail = error.strip() if error else "사용자 취소"
                    task.status = "취소됨"
                    task.error = detail if detail == "사용자 취소" else f"사용자 취소 ({detail})"
                else:
                    task.status = "실패"
                    task.error = error

                self.progress_updated.emit(idx + 1, total, task.input_file.name)

            if self.cancel_requested:
                for task in self.tasks:
                    if task.status == "대기":
                        task.status = "취소됨"
                        task.error = "사용자 취소"

            self.progress_updated.emit(total, total, "완료" if not self.cancel_requested else "취소됨")
            summary = self._build_summary(
                warnings=list(self.planned_conversion.warnings) + runtime_warnings,
                elapsed_seconds=time.perf_counter() - start_ts,
                progid_used=converter.progid_used,
            )
            self.task_completed.emit(summary)
        except Exception as e:
            logger.exception("변환 중 오류 발생")
            for task in self.tasks:
                if task.status in {"대기", "진행중"}:
                    task.status = "취소됨" if self.cancel_requested else "실패"
                    task.error = "사용자 취소" if self.cancel_requested else f"변환 워커 오류: {e}"
            summary = self._build_summary(
                warnings=list(self.planned_conversion.warnings)
                + runtime_warnings
                + [f"변환 워커 오류: {e}"],
                elapsed_seconds=time.perf_counter() - start_ts,
                progid_used=converter.progid_used if converter is not None else None,
            )
            self.task_completed.emit(summary)
        finally:
            self._set_stage("정리")
            if converter is not None:
                try:
                    converter.cleanup()
                except Exception as e:
                    logger.error(f"정리 중 오류: {e}")

            if self._com_initialized:
                if pythoncom is not None:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

    def _set_stage(self, stage: str) -> None:
        self.current_stage = stage
        started_at = self._started_at
        elapsed = time.perf_counter() - started_at if started_at is not None else 0.0
        self.stage_updated.emit(stage, elapsed)

    def force_terminate(self) -> bool:
        """앱이 소유한 한글 프로세스만 강제 종료."""
        converter = self.converter
        if converter is None:
            return False
        return converter.kill_owned_processes()

    def _create_backup(self, file_path: Path) -> Path:
        """파일 백업 생성"""
        return create_backup(file_path, max_files=self.backup_max_files_per_stem)

    def _apply_converter_artifacts(self, task: ConversionTask, converter: ConverterEngine) -> None:
        apply_converter_artifacts(task, converter)

    def _build_summary(
        self,
        *,
        warnings: list[str],
        elapsed_seconds: float,
        progid_used: str | None,
    ) -> ConversionSummary:
        """UI 스레드에 넘기기 전 작업 스냅샷을 복사한다."""
        return build_summary_fn(
            format_type=self.format_type,
            tasks=self.tasks,
            planned=self.planned_conversion,
            warnings=warnings,
            elapsed_seconds=elapsed_seconds,
            progid_used=progid_used,
        )

    def _emit_engine_status(self, converter: ConverterEngine) -> None:
        self.engine_status_updated.emit(engine_status_payload(converter))

    def _collect_converter_warnings(self, converter: ConverterEngine) -> list[str]:
        return collect_converter_warnings_fn(converter)
