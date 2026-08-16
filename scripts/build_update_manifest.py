from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hwpmate.services.update_manifest import canonical_manifest_payload

VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")


def _load_private_key_from_env(name: str) -> Ed25519PrivateKey:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"필수 환경 변수가 설정되지 않았습니다: {name}")
    try:
        raw_key = base64.b64decode(value, validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw_key)
    except Exception as exc:
        raise ValueError(f"{name}는 유효한 Base64 인코딩된 Ed25519 비공개키여야 합니다.") from exc


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def build_manifest(
    *,
    version: str,
    artifact: Path,
    artifact_url: str,
    private_key: Ed25519PrivateKey,
    expires_at: datetime,
) -> dict[str, object]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"유효하지 않은 버전: {version}")
    if artifact.suffix.lower() != ".exe" or not artifact.is_file():
        raise ValueError(f"아티팩트는 존재하는 .exe 파일이어야 합니다: {artifact}")
    if not artifact_url.startswith("https://"):
        raise ValueError("artifact-url은 HTTPS를 사용해야 합니다.")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("expires-at은 미래 시점이어야 합니다.")
    sha256, size = _sha256_and_size(artifact)
    payload = {
        "version": version,
        "artifact_url": artifact_url,
        "sha256": sha256,
        "size": size,
        "expires_at": expires_at.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    signature = private_key.sign(canonical_manifest_payload(payload))
    return {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="서명된 업데이트 매니페스트 생성")
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--private-key-env", default="HWPMATE_UPDATE_PRIVATE_KEY_B64")
    parser.add_argument("--expires-at")
    parser.add_argument("--expires-in-days", type=int, default=365)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.expires_at:
        expires_at = datetime.fromisoformat(args.expires_at.replace("Z", "+00:00"))
    else:
        if args.expires_in_days <= 0:
            raise ValueError("expires-in-days는 양수여야 합니다.")
        expires_at = datetime.now(timezone.utc) + timedelta(days=args.expires_in_days)
    document = build_manifest(
        version=args.version,
        artifact=args.artifact.resolve(),
        artifact_url=args.artifact_url,
        private_key=_load_private_key_from_env(args.private_key_env),
        expires_at=expires_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
