"""변환 컨트롤러."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from ....constants import (
    BACKUP_MAX_FILES_PER_STEM,
    FOLDER_SCAN_CACHE_CONVERT_MAX_AGE_SECONDS,
    FOLDER_SCAN_WAIT_MS,
    HWP_FOREGROUND_POLL_MS,
    HWP_PERMISSION_HINT,
    WORKER_FORCE_TERMINATE_SLICE_MS,
    WORKER_FORCE_TERMINATE_WAIT_MS,
    WORKER_WAIT_TIMEOUT,
)
from ....logging_config import get_logger
from ....models import ConversionSummary, ConversionTask, PlannedConversion
from ....path_utils import check_write_permission, is_valid_path_name
from ....services.hwp_print_settings import (
    PDF_EXPORT_SAVEAS_FIRST,
    normalize_pdf_export_mode,
)
from ....windows_integration import (
    hide_hwp_main_windows,
    bring_hwp_windows_to_foreground,
    try_accept_hwp_security_dialog,
)
from ..state import MainWindowState

logger = get_logger(__name__)


class ConversionController:
    """Task planning, worker orchestration, and conversion result handling."""

    def __init__(self, window: Any, state: MainWindowState) -> None:
        self.window = window
        self.state = state
        self._security_dialog_auto_accepted = False
        self._waited_for_folder_scan = False

    def _auto_accept_enabled_from_ui(self) -> bool:
        check = getattr(self.window, "auto_accept_security_check", None)
        if check is not None:
            return bool(check.isChecked())
        return bool(self.window.config.get("auto_accept_security_dialog", True))

    def _pdf_export_mode_from_ui(self) -> str:
        combo = getattr(self.window, "pdf_export_mode_combo", None)
        if combo is not None:
            data = combo.currentData()
            if data is not None:
                return normalize_pdf_export_mode(str(data))
            return normalize_pdf_export_mode(combo.currentText())
        try:
            return normalize_pdf_export_mode(
                self.window.config.get("pdf_export_mode", PDF_EXPORT_SAVEAS_FIRST)
            )
        except Exception:
            return PDF_EXPORT_SAVEAS_FIRST

    def _backup_max_files_from_ui(self) -> int:
        spin = getattr(self.window, "backup_max_spin", None)
        if spin is not None:
            try:
                return int(spin.value())
            except (TypeError, ValueError):
                pass
        try:
            return int(
                self.window.config.get("backup_max_files_per_stem", BACKUP_MAX_FILES_PER_STEM)
                or BACKUP_MAX_FILES_PER_STEM
            )
        except (TypeError, ValueError):
            return BACKUP_MAX_FILES_PER_STEM

    def _start_hwp_foreground_polling(self) -> None:
        """Dispatch/Open 블로킹 중에도 UI 스레드에서 한글 창을 전면화한다.

        engine_status 수신 전(owned_pids 미확정)에는 폴링 본문을 no-op 하여
        다른 한글 세션 전역 전면화를 피한다. 수신 직후 on_engine_status 에서 1회 돌린다.
        """
        self._stop_hwp_foreground_polling()
        self._security_dialog_auto_accepted = False
        session = self.state.security_session
        session.reset_runtime()
        session.auto_accept_enabled = self._auto_accept_enabled_from_ui()
        timer = QTimer(self.window)
        timer.setInterval(session.poll_interval_ms())
        timer.timeout.connect(self._poll_hwp_foreground)
        self.state.hwp_foreground_timer = timer
        timer.start()

    def _stop_hwp_foreground_polling(self) -> None:
        timer = self.state.hwp_foreground_timer
        if timer is None:
            return
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            pass
        self.state.hwp_foreground_timer = None

    def _sync_poll_interval(self) -> None:
        timer = self.state.hwp_foreground_timer
        if timer is None:
            return
        try:
            timer.setInterval(self.state.security_session.poll_interval_ms())
        except RuntimeError:
            pass

    def on_engine_status_updated(self, status_obj: object) -> None:
        """워커 initialize 직후 보안 모듈·소유 PID 를 세션에 반영."""
        if not isinstance(status_obj, dict):
            return
        self.state.security_session.apply_engine_status(status_obj)
        self._sync_poll_interval()
        session = self.state.security_session
        if session.snapshot_unreliable:
            self.window.hwp_status_label.setText("🟡 프로세스 스냅샷 불안정")
        elif not session.owned_pids:
            self.window.hwp_status_label.setText("🟡 한글 연결됨 (프로세스 추적 제한)")
        elif session.security_module_registered is False:
            self.window.hwp_status_label.setText("🟡 한글 연결됨 (보안 모듈 실패)")
        # 소유 PID·모듈 상태 확정 직후 1회 전면화/보조 클릭
        self._poll_hwp_foreground()

    def _poll_hwp_foreground(self) -> None:
        from hwpmate.ui.main_window_controllers import conversion as api
        if not self.is_conversion_active():
            self._stop_hwp_foreground_polling()
            return
        session = self.state.security_session
        # initialize 결과 전: 전역 HWP 전면화로 다른 세션을 건드리지 않는다.
        if not session.engine_status_received:
            return
        # 소유 PID 미추적: 다른 한글 편집 세션 숨김/전면화/자동 클릭 금지
        if not session.allows_window_control():
            return
        target = session.target_pids()
        if not target:
            return
        try:
            # Open 등으로 메인 창이 다시 보이면 숨기고, 보안 대화상자만 전면화.
            # 소유 PID 범위만 조작한다.
            api.hide_hwp_main_windows(target)
            api.bring_hwp_windows_to_foreground(target)
            # 모듈 성공·설정 꺼짐·쿨다운이면 자동 클릭 생략
            if session.should_auto_accept() and api.try_accept_hwp_security_dialog(target):
                session.note_auto_accept()
                self._security_dialog_auto_accepted = True
        except Exception as e:
            logger.debug(f"한글 창 전면화 폴링 오류: {e}")

    def collect_tasks(self, *, require_folder_cache: bool = False) -> PlannedConversion:
        is_folder_mode = self.window.folder_radio.isChecked()
        folder_file_paths = None
        if is_folder_mode:
            # 변환 시점에는 미리보기보다 짧은 캐시 연령을 요구해 신규 파일 누락을 줄인다.
            folder_file_paths = self.window.file_selection_controller.get_folder_scan_cache(
                folder_path=self.window.folder_entry.text(),
                include_sub=self.window.include_sub_check.isChecked(),
                max_age_seconds=FOLDER_SCAN_CACHE_CONVERT_MAX_AGE_SECONDS,
            )
            if folder_file_paths is None and require_folder_cache:
                # wait 직후 시그널 드레인 재시도
                QApplication.processEvents()
                folder_file_paths = self.window.file_selection_controller.get_folder_scan_cache(
                    folder_path=self.window.folder_entry.text(),
                    include_sub=self.window.include_sub_check.isChecked(),
                    max_age_seconds=FOLDER_SCAN_CACHE_CONVERT_MAX_AGE_SECONDS,
                )
            # 폴더 모드: UI 스레드 동기 재스캔 금지 (캐시 필수)
            if folder_file_paths is None:
                raise ValueError(
                    "폴더 스캔 결과가 아직 준비되지 않았거나 오래되었습니다.\n"
                    f"변환 직전 캐시는 {int(FOLDER_SCAN_CACHE_CONVERT_MAX_AGE_SECONDS)}초 이내여야 합니다.\n"
                    "폴더를 다시 선택하거나 미리보기 스캔 후 변환하세요."
                )
            # 캐시 신선도: 디스크에서 사라진 파일이 많으면 재스캔 유도
            ok, reason = self.window.file_selection_controller.validate_folder_scan_cache_freshness(
                folder_file_paths,
                folder_path=self.window.folder_entry.text(),
            )
            if not ok:
                self.window.file_selection_controller.invalidate_folder_scan_cache()
                raise ValueError(
                    f"{reason}\n폴더를 다시 선택하거나 미리보기 스캔 후 변환하세요."
                )
        return self.window.task_planner.build_tasks(
            is_folder_mode=is_folder_mode,
            format_type=self.state.selected_format,
            folder_path=self.window.folder_entry.text(),
            include_sub=self.window.include_sub_check.isChecked(),
            same_location=self.window.same_location_check.isChecked(),
            output_path=self.window.output_entry.text(),
            overwrite=self.window.overwrite_check.isChecked(),
            file_paths=self.window.file_store.paths,
            backup_enabled=self.window.backup_check.isChecked(),
            retry_count=self.window.retry_spin.value(),
            backup_max_files_per_stem=self._backup_max_files_from_ui(),
            pdf_export_mode=self._pdf_export_mode_from_ui(),
            folder_file_paths=folder_file_paths if is_folder_mode else None,
        )

    def _set_planning(self, planning: bool) -> None:
        if hasattr(self.window, "appearance_controller"):
            self.window.appearance_controller.set_planning_state(planning)
        else:
            self.state.is_planning = planning

    def _ensure_folder_scan_ready(self) -> None:
        """폴더 모드에서 변환용 유효 캐시가 없으면 비동기 스캔을 시작하고 완료를 기다린다.

        변환 직전에는 미리보기보다 짧은 캐시 연령을 요구한다.
        """
        folder = self.window.folder_entry.text().strip()
        if not folder:
            raise ValueError("폴더를 선택하세요.")

        # 변환 직전에는 항상 비동기 최신 스캔을 수행한다.
        scan_worker = self.state.scan_worker
        scan_running = bool(
            scan_worker
            and getattr(scan_worker, "isRunning", lambda: False)()
            and self.state.scan_mode == "folder_preview"
        )
        if not scan_running:
            self.window.status_label.setText("폴더 스캔 시작 중 (변환 직전 최신화)...")
            if not self.window.file_selection_controller.refresh_folder_scan_for_conversion(folder):
                self._abort_if_close_requested()
                raise ValueError("폴더 스캔이 아직 종료되지 않았습니다. 잠시 후 다시 시도하세요.")
            QApplication.processEvents()

        self.window.status_label.setText("폴더 스캔 완료 대기 중...")
        if not self.window.file_selection_controller.wait_for_active_scan(FOLDER_SCAN_WAIT_MS):
            self._abort_if_close_requested()
            raise ValueError(
                "폴더 스캔이 아직 종료되지 않았습니다. 잠시 후 다시 시도하세요."
            )
        self._waited_for_folder_scan = True

    def adjust_output_paths(self, plan: PlannedConversion, *, overwrite: bool) -> int:
        return self.window.task_planner.resolve_output_conflicts(
            plan.tasks,
            overwrite=overwrite,
            format_type=plan.format_type,
        )

    def validate_output_settings(self) -> None:
        if self.window.same_location_check.isChecked():
            return

        output_path = self.window.output_entry.text().strip()
        if not output_path:
            raise ValueError("출력 폴더를 선택하세요.")
        if not is_valid_path_name(output_path):
            raise ValueError(f"출력 경로에 사용할 수 없는 문자가 있습니다:\n{output_path}")

        output_folder = Path(output_path)
        if not output_folder.exists():
            raise ValueError(f"출력 폴더가 존재하지 않습니다:\n{output_folder}")
        if not check_write_permission(output_folder):
            raise ValueError(f"출력 폴더에 쓰기 권한이 없습니다:\n{output_folder}")

    def _abort_if_close_requested(self) -> None:
        """종료 요청이 있으면 계획 경로를 중단한다."""
        if self.state.close_requested:
            raise ValueError("종료 요청으로 변환 시작이 취소되었습니다.")

    def _begin_worker_ui(self, worker: Any, *, toast_message: str, toast_icon: str) -> None:
        """워커 시그널 연결, 상태 표시, 전면화 폴링, 토스트."""
        self.state.worker = worker
        worker.progress_updated.connect(self.window._on_progress_updated)
        worker.status_updated.connect(self.window._on_status_updated)
        worker.task_completed.connect(self.window._on_task_completed)
        worker.finished.connect(self.window._on_worker_finished)
        engine_status = getattr(worker, "engine_status_updated", None)
        if engine_status is not None:
            engine_status.connect(self.on_engine_status_updated)
        stage_updated = getattr(worker, "stage_updated", None)
        if stage_updated is not None:
            stage_updated.connect(self.on_stage_updated)
        worker.start()
        self.window.hwp_status_label.setText("🟡 한글 연결 중... (허용 창 확인)")
        self._start_hwp_foreground_polling()
        if hasattr(self.window, "toast"):
            # 시작 메시지에 허용 창 힌트를 합쳐 토스트 스택을 아끼고 가독성을 유지
            combined = f"{toast_message}\n{HWP_PERMISSION_HINT}"
            self.window.toast.show_message(combined, toast_icon)

    def on_stage_updated(self, stage: str, elapsed_seconds: float) -> None:
        self.window.status_label.setText(f"변환 단계: {stage} ({elapsed_seconds:.1f}초)")

    def _confirm_preflight_and_start_worker(
        self,
        plan: PlannedConversion,
        *,
        toast_message: str,
        toast_icon: str,
    ) -> bool:
        """사전 점검 확인 후 변환 워커를 시작한다.

        호출 시점에 이미 is_planning 이거나, 여기서 planning 을 잠깐 잡는다.
        성공 시 converting 이 planning 을 대체한다. 시작했으면 True.
        """
        planning_held_here = False
        try:
            if self.is_conversion_active():
                self.window.status_label.setText("변환이 이미 진행 중입니다")
                if hasattr(self.window, "toast"):
                    self.window.toast.show_message("변환이 이미 진행 중입니다", "⚠️")
                return False

            if not self.state.is_planning:
                self._set_planning(True)
                planning_held_here = True

            self._abort_if_close_requested()
            preflight = self.window._create_preflight_dialog(plan)
            if preflight.exec() != self.window.dialog_accepted_code():
                self.window.status_label.setText("변환 시작이 취소되었습니다")
                return False

            self._abort_if_close_requested()
            self.state.plan = plan
            self.state.tasks = plan.tasks
            self.window._save_settings()

            self.window._set_converting_state(True)
            planning_held_here = False
            self.window.progress_bar.setMaximum(plan.runnable_count)
            self.window.progress_bar.setValue(0)
            self.state.conversion_start_time = time.time()
            worker = self.window._create_conversion_worker(plan)
            self._begin_worker_ui(worker, toast_message=toast_message, toast_icon=toast_icon)
            return True
        except ValueError as e:
            if self.state.close_requested or self.state.close_after_plan:
                self.window.status_label.setText("종료 요청으로 변환 시작이 취소되었습니다")
            else:
                QMessageBox.warning(self.window, "경고", str(e))
            return False
        finally:
            if planning_held_here:
                self._set_planning(False)

    def start_conversion(self) -> None:
        planning_held = False
        try:
            if self.is_conversion_active() or self.state.is_planning:
                msg = (
                    "작업 준비가 이미 진행 중입니다"
                    if self.state.is_planning
                    else "변환이 이미 진행 중입니다"
                )
                self.window.status_label.setText(msg)
                if hasattr(self.window, "toast"):
                    self.window.toast.show_message(msg, "⚠️")
                return

            # processEvents 재진입 방지: 스캔 대기·계획 수립 전 구간 잠금
            self.state.close_requested = False
            self._set_planning(True)
            planning_held = True
            self._waited_for_folder_scan = False

            if self.state.scan_worker and self.state.scan_worker.isRunning():
                if self.state.scan_mode == "add_files":
                    raise ValueError("파일 스캔이 진행 중입니다. 스캔 완료 후 다시 시도하세요.")
                # 폴더 미리보기는 취소하지 않고 완료를 기다려 캐시를 확보한다.
                self.window.status_label.setText("폴더 스캔 완료 대기 중...")
                if not self.window.file_selection_controller.wait_for_active_scan(FOLDER_SCAN_WAIT_MS):
                    self._abort_if_close_requested()
                    raise ValueError(
                        "폴더 스캔이 아직 종료되지 않았습니다. 잠시 후 다시 시도하세요."
                    )
                self._waited_for_folder_scan = True

            self._abort_if_close_requested()

            # 폴더 모드: 캐시 없으면 비동기 스캔 후 대기 (UI 스레드 동기 재스캔 금지)
            if self.window.folder_radio.isChecked():
                self._ensure_folder_scan_ready()

            self._abort_if_close_requested()
            self.validate_output_settings()

            task_collect_start = time.perf_counter()
            # 폴더 모드는 항상 캐시 필수 (콜드 경로도 위에서 스캔 완료)
            require_cache = self.window.folder_radio.isChecked()
            plan = self.collect_tasks(require_folder_cache=require_cache)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"작업 목록 생성 완료: 실행={plan.runnable_count}개, 건너뜀={plan.skipped_count}개, "
                    f"소요={time.perf_counter() - task_collect_start:.3f}s"
                )

            self._abort_if_close_requested()
            overwrite = self.window.overwrite_check.isChecked()
            plan.conflict_renamed_count = self.adjust_output_paths(plan, overwrite=overwrite)
            if plan.conflict_renamed_count:
                if overwrite:
                    plan.warnings.append(
                        f"실행 배치 내부 출력 경로 충돌 {plan.conflict_renamed_count}개는 자동으로 새 이름으로 저장됩니다."
                    )
                else:
                    plan.warnings.append(
                        f"출력 경로 충돌 {plan.conflict_renamed_count}개는 자동으로 새 이름으로 저장됩니다."
                    )

            if not plan.tasks and plan.skipped_count:
                self.state.plan = plan
                self.window._save_settings()
                self._set_planning(False)
                planning_held = False
                self.show_skipped_only_result(plan)
                return

            if not plan.tasks:
                message = "실행할 변환 대상이 없습니다."
                if plan.skipped_count:
                    message += f"\n동일 형식 {plan.skipped_count}개는 자동으로 건너뜁니다."
                raise ValueError(message)

            start_message = f"{plan.runnable_count}개 파일 변환 시작"
            if plan.skipped_count:
                start_message += f" (건너뜀 {plan.skipped_count}개)"
            # planning 은 헬퍼가 converting 으로 넘기거나 실패 시 finally 에서 해제
            started = self._confirm_preflight_and_start_worker(
                plan,
                toast_message=start_message,
                toast_icon="🚀",
            )
            if started:
                planning_held = False
        except ValueError as e:
            # 종료 요청으로 인한 취소는 경고 박스 없이 조용히 처리
            if self.state.close_requested or self.state.close_after_plan:
                self.window.status_label.setText("종료 요청으로 변환 시작이 취소되었습니다")
            else:
                QMessageBox.warning(self.window, "경고", str(e))
        except Exception as e:
            logger.exception("변환 시작 오류")
            QMessageBox.critical(self.window, "오류", f"오류 발생: {e}")
        finally:
            if planning_held:
                self._set_planning(False)
            if self.state.close_after_plan and not self.state.is_converting:
                self.state.close_after_plan = False
                self.state.close_requested = False
                QTimer.singleShot(0, self.window.close)

    def is_conversion_active(self) -> bool:
        worker = self.state.worker
        worker_running = bool(worker and getattr(worker, "isRunning", lambda: False)())
        return self.state.is_converting or worker_running

    def show_skipped_only_result(self, plan: PlannedConversion) -> None:
        summary = ConversionSummary(
            format_type=plan.format_type,
            tasks=list(plan.skipped_tasks),
            warnings=list(plan.warnings),
            elapsed_seconds=0.0,
        )
        self.state.last_summary = summary
        self.window.status_label.setText("동일 형식 파일만 있어 변환 없이 건너뜀 처리했습니다")
        self.window.toast.show_message(f"건너뜀 {summary.skipped_count}개", "⏭️")
        dialog = self.window._create_result_dialog(summary)
        dialog.exec()
        self.state.plan = None

    def request_worker_stop(self, waiting_text: str) -> bool:
        worker = self.state.worker
        if worker is None:
            return True

        self.window.status_label.setText(
            f"{waiting_text} (현재 파일의 한글 응답을 기다리는 중…)"
        )
        worker.cancel()
        # COM Open/SaveAs 블로킹 중에는 즉시 반환되지 않을 수 있음
        remaining = max(0, int(WORKER_WAIT_TIMEOUT))
        slice_ms = 200
        while remaining > 0 and worker.isRunning():
            step = min(slice_ms, remaining)
            if worker.wait(step):
                return True
            QApplication.processEvents()
            remaining -= step

        if not worker.isRunning():
            return True

        if worker.can_force_terminate():
            self.state.force_kill_pending = True
            self.window.cancel_btn.setText("🛑 강제 종료")
            self.window.status_label.setText(
                "취소 요청됨 — 한글 COM 응답 대기 중. 응답이 없으면 「강제 종료」를 누르세요."
            )
        else:
            self.state.force_kill_pending = False
            self.window.cancel_btn.setText("⏹️ 취소")
            self.window.status_label.setText(
                "취소 요청됨. 안전하게 강제 종료할 대상 프로세스를 확인하지 못해 종료를 기다리는 중입니다."
            )
        return False

    def perform_force_terminate(self) -> bool:
        worker = self.state.worker
        if worker is None:
            return False

        self.window.status_label.setText("강제 종료 중… (앱이 띄운 한글만)")
        QApplication.processEvents()
        killed = worker.force_terminate()
        if not killed:
            self.state.force_kill_pending = False
            self.window.cancel_btn.setText("⏹️ 취소")
            QMessageBox.warning(
                self.window,
                "강제 종료 불가",
                "안전하게 종료할 대상 프로세스를 확인하지 못해 강제 종료를 수행하지 않았습니다.",
            )
            self.window.status_label.setText(
                "안전한 강제 종료 대상이 없어 워커 종료를 기다리는 중입니다."
            )
            return False

        remaining = max(0, int(WORKER_FORCE_TERMINATE_WAIT_MS))
        slice_ms = max(100, int(WORKER_FORCE_TERMINATE_SLICE_MS))
        while remaining > 0 and worker.isRunning():
            step = min(slice_ms, remaining)
            if worker.wait(step):
                break
            QApplication.processEvents()
            remaining -= step

        self.state.force_kill_pending = False
        self.window.cancel_btn.setText("⏹️ 취소")
        if worker.isRunning():
            self.window.status_label.setText(
                "강제 종료 후 워커가 아직 종료되지 않았습니다. 잠시 더 기다려 주세요."
            )
            return False
        return True

    def cancel_conversion(self) -> None:
        if not self.state.worker:
            return

        if self.state.force_kill_pending:
            reply = QMessageBox.question(
                self.window,
                "강제 종료 경고",
                "앱이 소유한 한글 프로세스만 강제 종료합니다.\n"
                "열려 있는 문서가 저장되지 않을 수 있습니다.\n\n계속할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self.perform_force_terminate():
                    self.window.status_label.setText("강제 종료 완료")
            return

        reply = QMessageBox.question(
            self.window,
            "확인",
            "변환을 취소하시겠습니까?\n"
            "현재 파일은 한글 응답이 끝날 때까지 기다린 뒤 중단됩니다.\n"
            "응답이 없으면 앱이 소유한 한글 프로세스만 강제 종료할 수 있습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.request_worker_stop("취소 요청 중..."):
            self.window.status_label.setText("취소됨")

    def on_progress_updated(self, current: int, total: int, filename: str) -> None:
        self.window.progress_bar.setValue(current)

        if current > 0 and self.state.conversion_start_time:
            elapsed = time.time() - self.state.conversion_start_time
            avg_time = elapsed / current
            remaining = avg_time * (total - current)
            remaining_str = f" (남은 시간: {int(remaining)}초)" if remaining > 0 else ""
        else:
            remaining_str = ""

        self.window.progress_label.setText(f"{current} / {total}{remaining_str}")
        self.window.status_label.setText(f"변환 중: {filename}")

    def on_status_updated(self, text: str) -> None:
        self.window.status_label.setText(text)
        # 연결 성공 후에도 폴링을 유지한다.
        # 보안 승인 창은 Open/SaveAs(파일 루프) 시점에 뜨므로 연결 직후 끄면 자동 클릭이 무력화된다.
        if "연결 성공" in text:
            self.window.hwp_status_label.setText("🟢 한글 연결됨")
        elif "연결 중" in text or "허용" in text or "보안" in text:
            self.window.hwp_status_label.setText("🟡 한글 연결 중... (허용 창 확인)")
        elif text.startswith("변환 중") or text.startswith("재시도"):
            self.window.hwp_status_label.setText("🟢 한글 변환 중")

    def on_task_completed(self, summary_obj: object) -> None:
        if not isinstance(summary_obj, ConversionSummary):
            return

        self._stop_hwp_foreground_polling()
        summary = summary_obj
        self.state.last_summary = summary
        elapsed_str = f"{summary.elapsed_seconds:.1f}초" if summary.elapsed_seconds is not None else "알 수 없음"

        if summary.failed_count == 0 and summary.canceled_count == 0:
            self.window.toast.show_message(
                f"✅ 성공 {summary.success_count}개, 건너뜀 {summary.skipped_count}개 ({elapsed_str})",
                "🎉",
            )
        else:
            self.window.toast.show_message(
                f"⚠️ 성공 {summary.success_count} / 실패 {summary.failed_count} / 취소 {summary.canceled_count} ({elapsed_str})",
                "⚠️",
            )

        if any("강제 종료는 비활성화" in warning for warning in summary.warnings):
            self.window.hwp_status_label.setText("🟡 프로세스 추적 불가")
        elif summary.progid_used:
            self.window.hwp_status_label.setText("🟢 한글 연결됨")
        elif summary.failed_count:
            self.window.hwp_status_label.setText("🔴 한글 연결 오류")
        else:
            self.window.hwp_status_label.setText("🟢 한글 대기중")
        if self.state.close_after_worker:
            return
        dialog = self.window._create_result_dialog(summary)
        dialog.exec()

    def retry_failed_tasks(self, failed_tasks: list[ConversionTask]) -> None:
        """결과 다이얼로그에서 실패 항목만 다시 변환한다."""
        if self.is_conversion_active() or self.state.is_planning:
            msg = (
                "작업 준비가 이미 진행 중입니다"
                if self.state.is_planning
                else "변환이 이미 진행 중입니다"
            )
            self.window.status_label.setText(msg)
            if hasattr(self.window, "toast"):
                self.window.toast.show_message(msg, "⚠️")
            return
        if not failed_tasks:
            return

        format_type = self.state.selected_format
        if self.state.last_summary is not None:
            format_type = self.state.last_summary.format_type

        retry_tasks = [
            ConversionTask(input_file=task.input_file, output_file=task.output_file)
            for task in failed_tasks
            if task.input_file.exists()
        ]
        missing = len(failed_tasks) - len(retry_tasks)
        warnings: list[str] = []
        if missing:
            warnings.append(f"다시 변환할 수 없는 실패 항목 {missing}개는 제외했습니다 (파일 없음).")
        if not retry_tasks:
            QMessageBox.warning(self.window, "경고", "다시 변환할 실패 파일이 없습니다.")
            return

        plan = PlannedConversion(
            format_type=format_type,
            same_location=self.window.same_location_check.isChecked(),
            output_path=self.window.output_entry.text().strip(),
            overwrite=self.window.overwrite_check.isChecked(),
            backup_enabled=self.window.backup_check.isChecked(),
            retry_count=self.window.retry_spin.value(),
            backup_max_files_per_stem=self._backup_max_files_from_ui(),
            pdf_export_mode=self._pdf_export_mode_from_ui(),
            tasks=retry_tasks,
            warnings=warnings + ["실패 항목만 다시 변환합니다."],
        )
        plan.conflict_renamed_count = self.adjust_output_paths(
            plan,
            overwrite=self.window.overwrite_check.isChecked(),
        )
        if plan.conflict_renamed_count:
            plan.warnings.append(
                f"출력 경로 충돌 {plan.conflict_renamed_count}개는 자동으로 새 이름으로 저장됩니다."
            )

        self._confirm_preflight_and_start_worker(
            plan,
            toast_message=f"실패 {plan.runnable_count}개 재변환 시작",
            toast_icon="🔁",
        )

    def on_worker_finished(self) -> None:
        self._stop_hwp_foreground_polling()
        self.window._set_converting_state(False)
        self.window.progress_bar.setValue(0)
        self.window.progress_label.setText("0 / 0")
        self.window.status_label.setText("대기 중")
        summary = self.state.last_summary
        if summary and any(
            "강제 종료는 비활성화" in warning or "스냅샷" in warning
            for warning in summary.warnings
        ):
            self.window.hwp_status_label.setText("🟡 프로세스 추적 불가 (강제 종료 제한)")
        elif summary and summary.failed_count:
            self.window.hwp_status_label.setText("🔴 마지막 작업 실패")
        elif summary and summary.canceled_count:
            self.window.hwp_status_label.setText("🟡 변환 취소됨")
        else:
            self.window.hwp_status_label.setText("🟢 한글 대기중")

        if self.state.worker:
            try:
                self.state.worker.progress_updated.disconnect()
                self.state.worker.status_updated.disconnect()
                self.state.worker.task_completed.disconnect()
                self.state.worker.finished.disconnect()
                engine_status = getattr(self.state.worker, "engine_status_updated", None)
                if engine_status is not None:
                    engine_status.disconnect()
                stage_updated = getattr(self.state.worker, "stage_updated", None)
                if stage_updated is not None:
                    stage_updated.disconnect()
            except (TypeError, RuntimeError):
                pass

        self.state.worker = None
        self.state.plan = None
        if self.state.close_after_worker:
            QTimer.singleShot(0, self.window.close)
