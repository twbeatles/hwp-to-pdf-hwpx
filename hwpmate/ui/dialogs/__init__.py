"""UI 다이얼로그 패키지.

호환: `from hwpmate.ui.dialogs import PreflightDialog, ResultDialog, write_*`
"""

from __future__ import annotations

from .atomic_io import (
    _write_text_file_atomically,
    write_failed_list,
    write_results_csv,
    write_results_json,
)
from .preflight import PreflightDialog
from .result import ResultDialog
from .update_dialog import UpdateDialog

__all__ = [
    "PreflightDialog",
    "ResultDialog",
    "UpdateDialog",
    "_write_text_file_atomically",
    "write_failed_list",
    "write_results_csv",
    "write_results_json",
]

