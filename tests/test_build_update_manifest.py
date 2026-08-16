from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hwpmate.services.update_manifest import verify_release_manifest
from scripts.build_update_manifest import build_manifest, main


def test_build_manifest_and_verify(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_key_b64 = base64.b64encode(public_bytes).decode("ascii")

    artifact = tmp_path / "HwpMate-v9.1.0.exe"
    artifact.write_bytes(b"dummy executable payload")

    document = build_manifest(
        version="9.1.0",
        artifact=artifact,
        artifact_url="https://github.com/twbeatles/HwpMate/releases/download/v9.1.0/HwpMate-v9.1.0.exe",
        private_key=private_key,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    manifest = verify_release_manifest(
        document,
        public_key=public_key_b64,
        current_version="9.0.0",
    )
    assert manifest.version == "9.1.0"
    assert manifest.artifact_size == len(b"dummy executable payload")


def test_build_update_manifest_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = Ed25519PrivateKey.generate()
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv("HWPMATE_UPDATE_PRIVATE_KEY_B64", base64.b64encode(priv_bytes).decode("ascii"))

    artifact = tmp_path / "HwpMate-v9.2.0.exe"
    artifact.write_bytes(b"another executable")
    output_json = tmp_path / "latest.json"

    ret = main([
        "--version", "9.2.0",
        "--artifact", str(artifact),
        "--artifact-url", "https://github.com/twbeatles/HwpMate/releases/download/v9.2.0/HwpMate-v9.2.0.exe",
        "--output", str(output_json),
    ])
    assert ret == 0
    assert output_json.is_file()

    doc = json.loads(output_json.read_text(encoding="utf-8"))
    assert doc["payload"]["version"] == "9.2.0"
    assert "signature" in doc
