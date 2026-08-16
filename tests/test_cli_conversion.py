from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import pytest

from hwpmate.app import _parse_args, _run_cli_conversion


class MockCliConverter:
    def __init__(self) -> None:
        self.progid_used = "HWPControl.HwpCtrl.1"
        self.pdf_export_mode = "saveas_first"

    def initialize(self, *, manage_com_apartment: bool = True) -> bool:
        return True

    def convert_file(self, in_p: Any, out_p: Any, fmt: str = "PDF") -> tuple[bool, str | None]:
        return True, None

    def cleanup(self) -> None:
        pass


def test_parse_args_cli() -> None:
    args = _parse_args(["--input", "test.hwp", "--format", "DOCX", "--output", "out_dir", "--recursive", "--overwrite"])
    assert args.input == "test.hwp"
    assert args.format == "DOCX"
    assert args.output == "out_dir"
    assert args.recursive is True
    assert args.overwrite is True


def test_cli_conversion_file_not_found() -> None:
    args = _parse_args(["--input", "non_existent_file_path_xyz.hwp"])
    ret = _run_cli_conversion(args)
    assert ret == 1


def test_cli_conversion_invalid_format(tmp_path: Path) -> None:
    doc = tmp_path / "test.hwp"
    doc.write_bytes(b"dummy")
    args = _parse_args(["--input", str(doc), "--format", "INVALID_EXT"])
    ret = _run_cli_conversion(args)
    assert ret == 1


def test_cli_conversion_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "test.hwp"
    doc.write_bytes(b"dummy")

    import hwpmate.app as app_module
    monkeypatch.setattr(app_module, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(app_module, "HWPConverter", MockCliConverter)

    args = _parse_args(["--input", str(doc), "--format", "PDF", "--output", str(tmp_path / "out")])
    ret = _run_cli_conversion(args)
    assert ret == 0
