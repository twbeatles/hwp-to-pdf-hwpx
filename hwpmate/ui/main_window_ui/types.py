from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QWidget,
)

from ..widgets import DropArea, FormatCard



@dataclass
class MainWindowWidgets:
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


@dataclass(frozen=True)
class MainWindowCallbacks:
    toggle_theme: Callable[..., None]
    check_updates: Callable[..., None]
    update_mode_ui: Callable[..., None]
    select_folder: Callable[..., None]
    include_sub_toggled: Callable[..., None]
    add_files: Callable[..., None]
    browse_files: Callable[..., None]
    remove_selected: Callable[..., None]
    clear_all: Callable[..., None]
    update_output_ui: Callable[..., None]
    select_output: Callable[..., None]
    format_card_clicked: Callable[..., None]
    start_conversion: Callable[..., None]
    cancel_conversion: Callable[..., None]
    update_format_cards: Callable[..., None]

