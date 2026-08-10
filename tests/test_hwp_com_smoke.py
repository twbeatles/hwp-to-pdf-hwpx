from __future__ import annotations

import importlib.util
from pathlib import Path


def load_smoke_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "hwp_com_smoke.py"
    spec = importlib.util.spec_from_file_location("hwp_com_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_format_set_normalizes_deduplicates_and_rejects_unknown() -> None:
    smoke = load_smoke_module()

    assert smoke.parse_format_set("pdf, DOCX,pdf") == ["PDF", "DOCX"]
    try:
        smoke.parse_format_set("PDF,NOPE")
    except ValueError as exc:
        assert "NOPE" in str(exc)
    else:
        raise AssertionError("unknown format must be rejected")
