from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hwpmate.services.update_manifest import (
    NoUpdateAvailableError,
    canonical_manifest_payload,
    is_newer_version,
    verify_release_manifest,
)


@pytest.fixture
def signing_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_key, base64.b64encode(public_bytes).decode("ascii")


def test_is_newer_version() -> None:
    assert is_newer_version("9.1", "9.0")
    assert is_newer_version("9.0.1", "9.0")
    assert is_newer_version("10.0.0", "9.9.9")
    assert not is_newer_version("9.0", "9.0")
    assert not is_newer_version("8.9", "9.0")
    assert not is_newer_version("9.0.0", "9.0.0")


def test_verify_release_manifest_success(signing_keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_key_b64 = signing_keypair
    payload = {
        "version": "9.1.0",
        "artifact_url": "https://github.com/twbeatles/HwpMate/releases/download/v9.1.0/HwpMate-v9.1.0.exe",
        "sha256": "a" * 64,
        "size": 1024 * 1024,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30))
        .replace(microsecond=0)
        .isoformat(),
    }
    signature = private_key.sign(canonical_manifest_payload(payload))
    manifest_doc = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }

    manifest = verify_release_manifest(
        manifest_doc,
        public_key=public_key_b64,
        current_version="9.0.0",
    )
    assert manifest.version == "9.1.0"
    assert manifest.artifact_sha256 == "a" * 64
    assert manifest.artifact_size == 1024 * 1024


def test_verify_release_manifest_rejects_tampered_payload(
    signing_keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    private_key, public_key_b64 = signing_keypair
    payload = {
        "version": "9.1.0",
        "artifact_url": "https://github.com/twbeatles/HwpMate/releases/download/v9.1.0/HwpMate-v9.1.0.exe",
        "sha256": "a" * 64,
        "size": 1024,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30))
        .replace(microsecond=0)
        .isoformat(),
    }
    signature = private_key.sign(canonical_manifest_payload(payload))
    payload["sha256"] = "b" * 64  # 변조

    manifest_doc = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }

    with pytest.raises(ValueError, match="서명 검증 실패"):
        verify_release_manifest(
            manifest_doc,
            public_key=public_key_b64,
            current_version="9.0.0",
        )


def test_verify_release_manifest_rejects_expired(
    signing_keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    private_key, public_key_b64 = signing_keypair
    payload = {
        "version": "9.1.0",
        "artifact_url": "https://github.com/twbeatles/HwpMate/releases/download/v9.1.0/HwpMate-v9.1.0.exe",
        "sha256": "a" * 64,
        "size": 1024,
        "expires_at": (datetime.now(timezone.utc) - timedelta(days=1))
        .replace(microsecond=0)
        .isoformat(),
    }
    signature = private_key.sign(canonical_manifest_payload(payload))
    manifest_doc = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }

    with pytest.raises(ValueError, match="만료"):
        verify_release_manifest(
            manifest_doc,
            public_key=public_key_b64,
            current_version="9.0.0",
        )


def test_verify_release_manifest_rejects_older_version(
    signing_keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    private_key, public_key_b64 = signing_keypair
    payload = {
        "version": "8.9.0",
        "artifact_url": "https://github.com/twbeatles/HwpMate/releases/download/v8.9.0/HwpMate-v8.9.0.exe",
        "sha256": "a" * 64,
        "size": 1024,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30))
        .replace(microsecond=0)
        .isoformat(),
    }
    signature = private_key.sign(canonical_manifest_payload(payload))
    manifest_doc = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }

    with pytest.raises(NoUpdateAvailableError):
        verify_release_manifest(
            manifest_doc,
            public_key=public_key_b64,
            current_version="9.0.0",
        )


def test_verify_release_manifest_rejects_non_https_url(
    signing_keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    private_key, public_key_b64 = signing_keypair
    payload = {
        "version": "9.1.0",
        "artifact_url": "http://insecure.example.com/HwpMate.exe",
        "sha256": "a" * 64,
        "size": 1024,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30))
        .replace(microsecond=0)
        .isoformat(),
    }
    signature = private_key.sign(canonical_manifest_payload(payload))
    manifest_doc = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }

    with pytest.raises(ValueError, match="HTTPS"):
        verify_release_manifest(
            manifest_doc,
            public_key=public_key_b64,
            current_version="9.0.0",
        )
