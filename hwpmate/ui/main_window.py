from __future__ import annotations

import platform
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent, QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTabWidget,
    QWidget,
)

from ..config_repository import load_config, save_config
from ..constants import SCAN_CANCEL_WAIT_MS, VERSION
from ..logging_config import get_logger
from ..models import ConversionSummary, ConversionTask, PlannedConversion
from ..services.file_selection_store import FileSelectionStore
from ..services.task_planner import TaskPlanner
from ..workers.conversion_worker import ConversionWorker
from ..workers.file_scan_worker import FileScanWorker
from .dialogs import PreflightDialog, ResultDialog
from .main_window_controllers import (
    AppearanceController,
    ConversionController,
    FileSelectionController,
    LifecycleController,
    MainWindowState,
    NativeDropController,
    UpdateController,
)

from .main_window_ui import MainWindowCallbacks, MainWindowWidgets, build_main_window_ui
from .toast import ToastManager
from .widgets import DropArea, FormatCard

logger = get_logger(__name__)


class MainWindow(QMainWindow):
    """Main application window and controller composition root."""

    ui: MainWindowWidgets
    theme_btn: QPushButton
    update_btn: QPushButton
    folder_radio: QRadioButton
    files_radio: QRadioButton
    folder_widget: QWidget
    folder_entry: QLineEdit
    folder_btn: QPushButton
    include_sub_check: QCheckBox
    files_widget: QWidget
    drop_area: DropArea
    add_btn: QPushButton
    remove_btn: QPushButton
    clear_btn: QPushButton
    file_table: QTableWidget
    same_location_check: QCheckBox
    output_entry: QLineEdit
    output_btn: QPushButton
    format_tabs: QTabWidget
    format_cards: dict[str, FormatCard]
    overwrite_check: QCheckBox
    backup_check: QCheckBox
    backup_max_spin: QSpinBox
    auto_accept_security_check: QCheckBox
    pdf_export_mode_combo: QComboBox
    retry_spin: QSpinBox
    start_btn: QPushButton
    cancel_btn: QPushButton
    status_label: QLabel
    progress_bar: QProgressBar
    progress_label: QLabel
    hwp_status_label: QLabel
    file_count_label: QLabel
    tray_icon: QSystemTrayIcon

    def __init__(self) -> None:
        super().__init__()

        self.state = MainWindowState()
        self.config = load_config()
        self.current_theme = self.config.get("theme", "dark")

        self.file_store = FileSelectionStore()
        self.task_planner = TaskPlanner()

        self.appearance_controller = AppearanceController(self, self.state, save_config)
        self.file_selection_controller = FileSelectionController(self, self.state)
        self.conversion_controller = ConversionController(self, self.state)
        self.native_drop_controller = NativeDropController(self, self.state)
        self.lifecycle_controller = LifecycleController(self, self.state, save_config)
        self.update_controller = UpdateController(self)


        self._init_menu_bar()
        self._init_ui()
        self._init_status_bar()
        self._init_shortcuts()
        self._init_tray_icon()
        self._apply_theme()
        self._update_mode_ui()
        self._update_output_ui()

        self.toast = ToastManager(self)

        logger.info(f"HWP 변환기 v{VERSION} 시작")
        logger.info(f"시스템 정보: {platform.system()} {platform.release()} ({platform.version()})")
        logger.info(f"Python 버전: {sys.version}")

    @property
    def tasks(self) -> list[ConversionTask]:
        return self.state.tasks

    @tasks.setter
    def tasks(self, value: list[ConversionTask]) -> None:
        self.state.tasks = value

    @property
    def plan(self) -> PlannedConversion | None:
        return self.state.plan

    @plan.setter
    def plan(self, value: PlannedConversion | None) -> None:
        self.state.plan = value

    @property
    def last_summary(self) -> ConversionSummary | None:
        return self.state.last_summary

    @last_summary.setter
    def last_summary(self, value: ConversionSummary | None) -> None:
        self.state.last_summary = value

    @property
    def worker(self) -> ConversionWorker | None:
        return self.state.worker

    @worker.setter
    def worker(self, value: ConversionWorker | None) -> None:
        self.state.worker = value

    @property
    def is_converting(self) -> bool:
        return self.state.is_converting

    @is_converting.setter
    def is_converting(self, value: bool) -> None:
        self.state.is_converting = value

    @property
    def conversion_start_time(self) -> float | None:
        return self.state.conversion_start_time

    @conversion_start_time.setter
    def conversion_start_time(self, value: float | None) -> None:
        self.state.conversion_start_time = value

    @property
    def file_scan_worker(self) -> FileScanWorker | None:
        return self.state.scan_worker

    @file_scan_worker.setter
    def file_scan_worker(self, value: FileScanWorker | None) -> None:
        self.state.scan_worker = value

    @property
    def _scan_mode(self) -> str | None:
        return self.state.scan_mode

    @_scan_mode.setter
    def _scan_mode(self, value: str | None) -> None:
        self.state.scan_mode = value

    @property
    def _scan_new_file_count(self) -> int:
        return self.state.scan_new_file_count

    @_scan_new_file_count.setter
    def _scan_new_file_count(self, value: int) -> None:
        self.state.scan_new_file_count = value

    @property
    def _scan_preview_count(self) -> int:
        return self.state.scan_preview_count

    @_scan_preview_count.setter
    def _scan_preview_count(self, value: int) -> None:
        self.state.scan_preview_count = value

    @property
    def _scan_started_at(self) -> float | None:
        return self.state.scan_started_at

    @_scan_started_at.setter
    def _scan_started_at(self, value: float | None) -> None:
        self.state.scan_started_at = value

    @property
    def _force_kill_pending(self) -> bool:
        return self.state.force_kill_pending

    @_force_kill_pending.setter
    def _force_kill_pending(self, value: bool) -> None:
        self.state.force_kill_pending = value

    @property
    def _close_after_worker(self) -> bool:
        return self.state.close_after_worker

    @_close_after_worker.setter
    def _close_after_worker(self, value: bool) -> None:
        self.state.close_after_worker = value

    @property
    def _drag_drop_initialized(self) -> bool:
        return self.state.drag_drop_initialized

    @_drag_drop_initialized.setter
    def _drag_drop_initialized(self, value: bool) -> None:
        self.state.drag_drop_initialized = value

    @property
    def _selected_format(self) -> str:
        return self.state.selected_format

    @_selected_format.setter
    def _selected_format(self, value: str) -> None:
        self.state.selected_format = value

    def showEvent(self, a0: Optional[QShowEvent]) -> None:
        if a0 is None:
            return
        super().showEvent(a0)
        # showEvent 재진입/첫 paint 중 native filter 설치 시 QtCore AV 를 피하기 위해
        # 이벤트 루프 한 틱 뒤로 미룬다.
        QTimer.singleShot(0, self.native_drop_controller.initialize_native_drag_drop)
        # 설정에서 복원한 폴더가 있으면 미리보기 스캔 (콜드 UI 재스캔 방지)
        QTimer.singleShot(0, self._maybe_start_restored_folder_scan)
        # 이전 업데이트 결과 확인 및 백그라운드 업데이트 확인 스케줄링
        QTimer.singleShot(500, self.update_controller.check_previous_update_result)
        self.update_controller.schedule_startup_check(3000)

    def _maybe_start_restored_folder_scan(self) -> None:
        if not self.folder_radio.isChecked():
            return
        folder = self.folder_entry.text().strip()
        if not folder:
            return
        if self.state.folder_scan_ready:
            return
        if self.state.scan_worker and self.state.scan_worker.isRunning():
            return
        if not Path(folder).is_dir():
            return
        self.file_selection_controller.start_folder_preview_scan(folder)

    def closeEvent(self, a0: Optional[QCloseEvent]) -> None:
        if a0 is None:
            return
        self.lifecycle_controller.close_event(a0)

    def _init_ui(self) -> None:
        callbacks = MainWindowCallbacks(
            toggle_theme=self._toggle_theme,
            check_updates=lambda: self.update_controller.start_check(manual=True),
            update_mode_ui=self._update_mode_ui,
            select_folder=self._select_folder,
            include_sub_toggled=self._on_include_sub_toggled,
            add_files=self._add_files,
            browse_files=self._browse_files,
            remove_selected=self._remove_selected,
            clear_all=self._clear_all,
            update_output_ui=self._update_output_ui,
            select_output=self._select_output,
            format_card_clicked=self._on_format_card_clicked,
            start_conversion=self._start_conversion,
            cancel_conversion=self._cancel_conversion,
            update_format_cards=self._update_format_cards,
        )
        self.ui = build_main_window_ui(self, self.config, callbacks)


    def _init_menu_bar(self) -> None:
        self.lifecycle_controller.init_menu_bar()

    def _init_status_bar(self) -> None:
        self.lifecycle_controller.init_status_bar()

    def _init_shortcuts(self) -> None:
        self.lifecycle_controller.init_shortcuts()

    def _init_tray_icon(self) -> None:
        self.lifecycle_controller.init_tray_icon()

    def _show_from_tray(self) -> None:
        self.lifecycle_controller.show_from_tray()

    def _quit_app(self) -> None:
        self.lifecycle_controller.quit_app()

    def _on_tray_activated(self, reason: object) -> None:
        self.lifecycle_controller.on_tray_activated(reason)

    def _cancel_conversion_if_running(self) -> None:
        self.lifecycle_controller.cancel_conversion_if_running()

    def _show_usage(self) -> None:
        self.lifecycle_controller.show_usage()

    def _show_about(self) -> None:
        self.lifecycle_controller.show_about()

    def _save_settings(self) -> bool:
        return self.lifecycle_controller.save_settings()

    def _apply_theme(self) -> None:
        self.appearance_controller.apply_theme()

    def _toggle_theme(self) -> None:
        self.appearance_controller.toggle_theme()

    def _on_format_card_clicked(self, format_type: str) -> None:
        self.appearance_controller.on_format_card_clicked(format_type)

    def _update_format_cards(self) -> None:
        self.appearance_controller.update_format_cards()

    def _update_mode_ui(self, *_: object) -> None:
        self.appearance_controller.update_mode_ui()

    def _update_output_ui(self, *_: object) -> None:
        self.appearance_controller.update_output_ui()

    def _on_include_sub_toggled(self, checked: bool) -> None:
        self.appearance_controller.on_include_sub_toggled(checked)

    def _set_converting_state(self, converting: bool) -> None:
        self.appearance_controller.set_converting_state(converting)

    def _cancel_active_scan(self, wait_ms: int = SCAN_CANCEL_WAIT_MS) -> bool:
        return self.file_selection_controller.cancel_active_scan(wait_ms)

    def _start_scan(
        self,
        input_paths: list[str],
        mode: str,
        include_sub: bool = True,
        allowed_exts: Iterable[str] | None = None,
    ) -> None:
        self.file_selection_controller.start_scan(input_paths, mode, include_sub, allowed_exts)

    def _start_folder_preview_scan(self, folder_path: str) -> None:
        self.file_selection_controller.start_folder_preview_scan(folder_path)

    def _append_files_batch(self, files: list[str]) -> int:
        return self.file_selection_controller.append_files_batch(files)

    def _on_scan_batch_found(self, batch: list[str]) -> None:
        self.file_selection_controller.on_scan_batch_found(batch)

    def _on_scan_progress(self, current: int, total: int) -> None:
        self.file_selection_controller.on_scan_progress(current, total)

    def _on_scan_finished(self, total_found: int, canceled: bool) -> None:
        self.file_selection_controller.on_scan_finished(total_found, canceled)

    def _on_scan_error(self, error_msg: str) -> None:
        self.file_selection_controller.on_scan_error(error_msg)

    def _on_scan_worker_finished(self) -> None:
        self.file_selection_controller.on_scan_worker_finished()

    def _select_folder(self) -> None:
        self.file_selection_controller.select_folder()

    def _select_output(self) -> None:
        self.file_selection_controller.select_output()

    def _browse_files(self) -> None:
        self.file_selection_controller.browse_files()

    def _add_files(self, files: list[str]) -> None:
        self.file_selection_controller.add_files(files)

    def _remove_selected(self) -> None:
        self.file_selection_controller.remove_selected()

    def _clear_all(self) -> None:
        self.file_selection_controller.clear_all()

    def _update_file_count(self) -> None:
        self.file_selection_controller.update_file_count()

    def _collect_tasks(self) -> PlannedConversion:
        return self.conversion_controller.collect_tasks()

    def _adjust_output_paths(self, plan: PlannedConversion, *, overwrite: bool) -> int:
        return self.conversion_controller.adjust_output_paths(plan, overwrite=overwrite)

    def _validate_output_settings(self) -> None:
        self.conversion_controller.validate_output_settings()

    def _start_conversion(self) -> None:
        self.conversion_controller.start_conversion()

    def _show_skipped_only_result(self, plan: PlannedConversion) -> None:
        self.conversion_controller.show_skipped_only_result(plan)

    def _request_worker_stop(self, waiting_text: str) -> bool:
        return self.conversion_controller.request_worker_stop(waiting_text)

    def _perform_force_terminate(self) -> bool:
        return self.conversion_controller.perform_force_terminate()

    def _cancel_conversion(self) -> None:
        self.conversion_controller.cancel_conversion()

    def _on_progress_updated(self, current: int, total: int, filename: str) -> None:
        self.conversion_controller.on_progress_updated(current, total, filename)

    def _on_status_updated(self, text: str) -> None:
        self.conversion_controller.on_status_updated(text)

    def _on_task_completed(self, summary_obj: object) -> None:
        self.conversion_controller.on_task_completed(summary_obj)

    def _on_worker_finished(self) -> None:
        self.conversion_controller.on_worker_finished()

    def _on_native_files_dropped(self, files: list[str]) -> None:
        self.native_drop_controller.on_native_files_dropped(files)

    def _create_preflight_dialog(self, plan: PlannedConversion) -> PreflightDialog:
        return PreflightDialog(plan, self)

    def _create_result_dialog(self, summary: ConversionSummary) -> ResultDialog:
        return ResultDialog(
            summary,
            self,
            on_retry_failed=self.conversion_controller.retry_failed_tasks,
        )

    def _create_conversion_worker(self, plan: PlannedConversion) -> ConversionWorker:
        return ConversionWorker(plan)

    @staticmethod
    def dialog_accepted_code() -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted
