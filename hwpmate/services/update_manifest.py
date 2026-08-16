from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..constants import (
    UPDATE_ARTIFACT_MAX_BYTES,
    UPDATE_MANIFEST_MAX_BYTES,
    UPDATE_REQUEST_TIMEOUT_SECONDS,
)

_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class NoUpdateAvailableError(ValueError):
    """서명 검증 완료 후, 최신 릴리즈가 현재 버전보다 높지 않을 때 발생합니다."""


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    version: str
    artifact_url: str
    artifact_sha256: str
    artifact_size: int
    expires_at: datetime
    signature: str


def _version_tuple(value: str) -> tuple[int, ...]:
    normalized = str(value or "").strip()
    if not _VERSION_PATTERN.fullmatch(normalized):
        raise ValueError(f"유효하지 않은 버전 형식: {value}")
    return tuple(int(part) for part in normalized.split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_parts = _version_tuple(candidate)
    current_parts = _version_tuple(current)
    width = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (width - len(candidate_parts)) > current_parts + (
        0,
    ) * (width - len(current_parts))


def canonical_manifest_payload(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_public_key(value: bytes | str) -> bytes:
    if isinstance(value, bytes):
        return value
    try:
        return base64.b64decode(str(value), validate=True)
    except Exception as exc:
        raise ValueError("유효하지 않은 업데이트 공개키") from exc


def verify_release_manifest(
    document: bytes | str | Mapping[str, Any],
    *,
    public_key: bytes | str,
    current_version: str,
    now: datetime | None = None,
    max_bytes: int | None = None,
) -> ReleaseManifest:
    size_limit = int(max_bytes or UPDATE_MANIFEST_MAX_BYTES)
    if isinstance(document, bytes):
        if len(document) > size_limit:
            raise ValueError("매니페스트 크기가 허용 한도를 초과했습니다.")
        try:
            parsed = json.loads(document.decode("utf-8"))
        except Exception as exc:
            raise ValueError("유효하지 않은 매니페스트 JSON") from exc
    elif isinstance(document, str):
        encoded = document.encode("utf-8")
        if len(encoded) > size_limit:
            raise ValueError("매니페스트 크기가 허용 한도를 초과했습니다.")
        try:
            parsed = json.loads(document)
        except Exception as exc:
            raise ValueError("유효하지 않은 매니페스트 JSON") from exc
    else:
        parsed = dict(document)
        if len(canonical_manifest_payload(parsed)) > size_limit:
            raise ValueError("매니페스트 크기가 허용 한도를 초과했습니다.")

    if not isinstance(parsed, dict) or not isinstance(parsed.get("payload"), dict):
        raise ValueError("매니페스트 payload 누락")
    payload = dict(parsed["payload"])
    signature_text = str(parsed.get("signature", "") or "").strip()
    if not signature_text:
        raise ValueError("매니페스트 서명 누락")
    try:
        signature = base64.b64decode(signature_text, validate=True)
        verifier = Ed25519PublicKey.from_public_bytes(_decode_public_key(public_key))
        verifier.verify(signature, canonical_manifest_payload(payload))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("매니페스트 서명 검증 실패") from exc

    version = str(payload.get("version", "") or "").strip()
    if not is_newer_version(version, current_version):
        raise NoUpdateAvailableError("매니페스트 버전이 현재 버전보다 높지 않습니다.")
    artifact_url = str(payload.get("artifact_url", "") or "").strip()
    parsed_url = urlsplit(artifact_url)
    if parsed_url.scheme.lower() != "https" or not parsed_url.hostname:
        raise ValueError("아티팩트 URL은 반드시 HTTPS여야 합니다.")
    sha256 = str(payload.get("sha256", "") or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(sha256):
        raise ValueError("아티팩트 SHA-256 해시가 유효하지 않습니다.")
    try:
        artifact_size = int(payload.get("size", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("아티팩트 크기가 유효하지 않습니다.") from exc
    if artifact_size <= 0 or artifact_size > int(UPDATE_ARTIFACT_MAX_BYTES):
        raise ValueError("아티팩트 크기가 유효 범위를 벗어났습니다.")
    try:
        expires_at = datetime.fromisoformat(
            str(payload.get("expires_at", "") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("매니페스트 만료일이 유효하지 않습니다.") from exc
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if expires_at <= current_time:
        raise ValueError("매니페스트 유효기간이 만료되었습니다.")

    return ReleaseManifest(
        version=version,
        artifact_url=artifact_url,
        artifact_sha256=sha256,
        artifact_size=artifact_size,
        expires_at=expires_at,
        signature=signature_text,
    )


def download_release_manifest(url: str) -> bytes:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("업데이트 매니페스트 URL은 반드시 HTTPS여야 합니다.")
    limit = int(UPDATE_MANIFEST_MAX_BYTES)
    request = Request(str(url), headers={"User-Agent": "HwpMate-Updater"})
    with urlopen(request, timeout=float(UPDATE_REQUEST_TIMEOUT_SECONDS)) as response:
        final_url = urlsplit(response.geturl())
        if final_url.scheme.lower() != "https" or not final_url.hostname:
            raise ValueError("업데이트 매니페스트 리다이렉트는 HTTPS를 유지해야 합니다.")
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("매니페스트 크기가 허용 한도를 초과했습니다.")
    return payload
