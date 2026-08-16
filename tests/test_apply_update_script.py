from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

import scripts.apply_update as apply_update_module
from scripts.apply_update import main


def test_apply_update_script_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "app.exe"
    staged = tmp_path / "staged.exe"
    backup = tmp_path / "app.exe.v9.0.bak"
    result_file = tmp_path / "result.json"

    target.write_bytes(b"v9.0")
    new_bytes = b"v9.1"
    staged.write_bytes(new_bytes)

    monkeypatch.setattr(apply_update_module, "_wait_for_parent", lambda *args: None)
    monkeypatch.setattr(apply_update_module, "apply_staged_update", lambda **kwargs: None)

    ret = main([
        "--target", str(target),
        "--staged", str(staged),
        "--backup", str(backup),
        "--parent-pid", "1",
        "--expected-sha256", hashlib.sha256(new_bytes).hexdigest(),
        "--expected-size", str(len(new_bytes)),
        "--result-file", str(result_file),
    ])

    assert ret == 0
    assert result_file.is_file()
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert data["status"] == "applied"
