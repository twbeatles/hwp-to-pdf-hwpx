from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hwpmate.services.update_installer import (
    UpdateApplyError,
    apply_staged_update,
    cleanup_update_backups,
    consume_update_result,
    prepare_staged_update,
    write_update_result,
)
from hwpmate.services.update_manifest import ReleaseManifest


@pytest.fixture
def sample_manifest() -> ReleaseManifest:
    content = b"fake executable binary content for testing"
    return ReleaseManifest(
        version="9.1.0",
        artifact_url="https://example.com/app.exe",
        artifact_sha256=hashlib.sha256(content).hexdigest(),
        artifact_size=len(content),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        signature="fake-signature",
    )


def test_prepare_staged_update_success(sample_manifest: ReleaseManifest, tmp_path: Path) -> None:
    chunks = [b"fake executable ", b"binary content ", b"for testing"]
    staged = prepare_staged_update(
        sample_manifest,
        chunks=chunks,
        staging_root=tmp_path,
        approve=lambda m, p: True,
    )
    assert staged is not None
    assert staged.is_file()
    assert staged.read_bytes() == b"fake executable binary content for testing"


def test_prepare_staged_update_hash_mismatch(sample_manifest: ReleaseManifest, tmp_path: Path) -> None:
    chunks = [b"corrupted binary payload"]
    with pytest.raises(ValueError, match="일치하지 않습니다"):
        prepare_staged_update(
            sample_manifest,
            chunks=chunks,
            staging_root=tmp_path,
        )
    # 임시 파일이 정리되었는지 확인
    assert len(list(tmp_path.glob("update-*.exe"))) == 0


def test_prepare_staged_update_user_declined(sample_manifest: ReleaseManifest, tmp_path: Path) -> None:
    chunks = [b"fake executable binary content for testing"]
    staged = prepare_staged_update(
        sample_manifest,
        chunks=chunks,
        staging_root=tmp_path,
        approve=lambda m, p: False,
    )
    assert staged is None
    assert len(list(tmp_path.glob("update-*.exe"))) == 0


def test_apply_staged_update_success_and_smoke_pass(tmp_path: Path) -> None:
    target = tmp_path / "app.exe"
    staged = tmp_path / "staged.exe"
    backup = tmp_path / "app.exe.v9.0.bak"

    old_bytes = b"version 9.0"
    new_bytes = b"version 9.1"
    target.write_bytes(old_bytes)
    staged.write_bytes(new_bytes)

    apply_staged_update(
        target=target,
        staged=staged,
        backup=backup,
        expected_sha256=hashlib.sha256(new_bytes).hexdigest(),
        expected_size=len(new_bytes),
        smoke_runner=lambda p: True,
    )

    assert target.read_bytes() == new_bytes
    assert backup.read_bytes() == old_bytes


def test_apply_staged_update_rollback_on_smoke_failure(tmp_path: Path) -> None:
    target = tmp_path / "app.exe"
    staged = tmp_path / "staged.exe"
    backup = tmp_path / "app.exe.v9.0.bak"

    old_bytes = b"version 9.0"
    bad_bytes = b"broken binary"
    target.write_bytes(old_bytes)
    staged.write_bytes(bad_bytes)

    with pytest.raises(UpdateApplyError, match="롤백됨"):
        apply_staged_update(
            target=target,
            staged=staged,
            backup=backup,
            expected_sha256=hashlib.sha256(bad_bytes).hexdigest(),
            expected_size=len(bad_bytes),
            smoke_runner=lambda p: False,  # 스모크 실패 시뮬레이션
        )

    # 타깃 파일이 이전 버전(9.0)으로 복구되었는지 확인
    assert target.read_bytes() == old_bytes


def test_write_and_consume_update_result(tmp_path: Path) -> None:
    res_path = tmp_path / "last-update-result.json"
    write_update_result(res_path, {"status": "applied", "version": "9.1.0"})
    assert res_path.is_file()

    result = consume_update_result(res_path)
    assert result is not None
    assert result["status"] == "applied"
    assert result["version"] == "9.1.0"

    # 소비 후 삭제되었는지 확인
    assert not res_path.exists()
    assert consume_update_result(res_path) is None


def test_cleanup_update_backups(tmp_path: Path) -> None:
    target = tmp_path / "app.exe"
    target.write_text("main")

    b1 = tmp_path / "app.exe.v1.bak"
    b2 = tmp_path / "app.exe.v2.bak"
    b3 = tmp_path / "app.exe.v3.bak"
    b4 = tmp_path / "app.exe.v4.bak"

    b1.write_text("1")
    b2.write_text("2")
    b3.write_text("3")
    b4.write_text("4")

    cleanup_update_backups(target, keep_count=2)
    remaining = sorted(p.name for p in tmp_path.glob("app.exe.v*.bak"))
    assert len(remaining) == 2
