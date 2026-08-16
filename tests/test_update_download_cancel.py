from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from hwpmate.services.update_installer import prepare_staged_update
from hwpmate.services.update_manifest import ReleaseManifest
from hwpmate.ui.main_window_controllers.update import UpdateDownloadWorker


@pytest.fixture
def sample_manifest() -> ReleaseManifest:
    payload = b"test payload binary for download cancellation"
    return ReleaseManifest(
        version="9.1.0",
        artifact_url="https://example.com/app.exe",
        artifact_sha256=hashlib.sha256(payload).hexdigest(),
        artifact_size=len(payload),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        signature="dummy-signature",
    )


def test_prepare_staged_update_cancellation(sample_manifest: ReleaseManifest, tmp_path: Path) -> None:
    is_cancelled = False

    def check_cancel() -> bool:
        return is_cancelled

    def chunk_generator():
        nonlocal is_cancelled
        yield b"chunk1"
        is_cancelled = True
        yield b"chunk2"

    staged = prepare_staged_update(
        sample_manifest,
        chunks=chunk_generator(),
        staging_root=tmp_path,
        cancel_check=check_cancel,
    )

    assert staged is None
    assert len(list(tmp_path.glob("update-*.exe"))) == 0


def test_update_download_worker_cancel(sample_manifest: ReleaseManifest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = UpdateDownloadWorker(sample_manifest, tmp_path)

    import hwpmate.ui.main_window_controllers.update as update_module
    monkeypatch.setattr(
        update_module,
        "stream_update_artifact",
        lambda m, cancel_check=None: [b"dummy chunk"],
    )

    failed_messages = []
    worker.download_failed.connect(lambda msg: failed_messages.append(msg))

    worker.cancel()
    worker.run()

    assert len(failed_messages) == 1
    assert "취소" in failed_messages[0]
