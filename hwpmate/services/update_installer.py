from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..constants import (
    UPDATE_ARTIFACT_MAX_BYTES,
    UPDATE_BACKUP_KEEP_COUNT,
    UPDATE_REQUEST_TIMEOUT_SECONDS,
)
from .update_manifest import ReleaseManifest


class UpdateApplyError(RuntimeError):
    pass


def atomic_write_json(path: Path, payload: Any, *, ensure_ascii: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(payload, f, ensure_ascii=ensure_ascii, indent=2)
        temp_path.replace(path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def update_result_path(staging_root: str | Path) -> Path:
    return (Path(staging_root).resolve() / "last-update-result.json").resolve()


def write_update_result(path: str | Path, payload: dict[str, object]) -> None:
    data = dict(payload)
    data["status"] = str(data.get("status", "failed") or "failed")
    atomic_write_json(Path(path).resolve(), data, ensure_ascii=False)


def consume_update_result(path: str | Path) -> dict[str, object] | None:
    result_path = Path(path).resolve()
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if not isinstance(data, dict) or str(data.get("status", "")) not in {
        "applied",
        "rolled_back",
        "failed",
    }:
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        result_path.unlink(missing_ok=True)
    except OSError:
        pass
    return data


def resolve_update_staging_root(
    *,
    custom_root: str | Path | None = None,
) -> Path:
    if custom_root is not None:
        return Path(custom_root).resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        return (Path(local_app_data) / "HwpMate" / "updates").resolve()
    return (Path.home() / ".hwpmate" / "updates").resolve()


def prepare_staged_update(
    manifest: ReleaseManifest,
    *,
    chunks: Iterable[bytes],
    staging_root: str | Path,
    approve: Callable[[ReleaseManifest, Path], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path | None:
    root = Path(staging_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    staged = root / f"update-{manifest.version}-{uuid4().hex}.exe"
    digest = hashlib.sha256()
    total = 0
    cancelled = False
    try:
        with open(staged, "xb") as handle:
            for chunk in chunks:
                if cancel_check is not None and cancel_check():
                    cancelled = True
                    break
                if not isinstance(chunk, bytes):
                    raise TypeError("업데이트 청크는 바이트 형식이어야 합니다.")
                total += len(chunk)
                if total > manifest.artifact_size or total > int(
                    UPDATE_ARTIFACT_MAX_BYTES
                ):
                    raise ValueError("업데이트 파일 크기가 매니페스트와 일치하지 않습니다.")
                digest.update(chunk)
                handle.write(chunk)
                if progress_callback is not None:
                    progress_callback(total, manifest.artifact_size)
            handle.flush()
            os.fsync(handle.fileno())

        if cancelled or (cancel_check is not None and cancel_check()):
            staged.unlink(missing_ok=True)
            return None

        if total != manifest.artifact_size:
            staged.unlink(missing_ok=True)
            raise ValueError("업데이트 파일 크기가 매니페스트와 일치하지 않습니다.")
        if digest.hexdigest().lower() != manifest.artifact_sha256.lower():
            staged.unlink(missing_ok=True)
            raise ValueError("업데이트 파일 SHA-256 해시가 일치하지 않습니다.")
        if approve is not None and not approve(manifest, staged):
            staged.unlink(missing_ok=True)
            return None
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise



def stream_update_artifact(
    manifest: ReleaseManifest,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> Iterable[bytes]:
    from urllib.parse import urlsplit
    from urllib.request import Request, urlopen

    request = Request(
        manifest.artifact_url,
        headers={"User-Agent": "HwpMate-Updater"},
    )
    with urlopen(
        request,
        timeout=float(UPDATE_REQUEST_TIMEOUT_SECONDS),
    ) as response:
        final_url = urlsplit(response.geturl())
        if final_url.scheme.lower() != "https" or not final_url.hostname:
            raise ValueError("업데이트 아티팩트 리다이렉트는 HTTPS를 유지해야 합니다.")
        while True:
            if cancel_check is not None and cancel_check():
                return
            chunk = response.read(1024 * 1024)
            if not chunk:
                return
            yield chunk



def _validate_apply_paths(target: Path, staged: Path, backup: Path) -> None:
    paths = [target.resolve(), staged.resolve(), backup.resolve()]
    if len(set(paths)) != 3:
        raise ValueError("대상, 스테이징, 백업 경로는 서로 달라야 합니다.")
    if target.suffix.lower() != ".exe" or staged.suffix.lower() != ".exe":
        raise ValueError("대상과 스테이징 파일은 반드시 .exe 확장자여야 합니다.")
    if backup.parent != target.parent:
        raise ValueError("백업 파일은 대상 설치 디렉터리에 위치해야 합니다.")
    if not target.is_file() or not staged.is_file():
        raise FileNotFoundError("대상 파일 또는 스테이징 파일이 존재하지 않습니다.")
    if backup.exists():
        raise FileExistsError(f"백업 파일이 이미 존재합니다: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)


def cleanup_update_backups(target: str | Path, *, keep_count: int | None = None) -> None:
    target_path = Path(target).resolve()
    keep = max(0, int(UPDATE_BACKUP_KEEP_COUNT if keep_count is None else keep_count))
    candidates: list[tuple[float, Path]] = []
    for backup in target_path.parent.glob(f"{target_path.name}.v*.bak"):
        try:
            candidates.append((backup.stat().st_mtime, backup))
        except OSError:
            continue
    backups = [item for _mtime, item in sorted(candidates, reverse=True)]
    for backup in backups[keep:]:
        try:
            backup.unlink()
        except OSError:
            continue


def apply_staged_update(
    *,
    target: str | Path,
    staged: str | Path,
    backup: str | Path,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    smoke_runner: Callable[[Path], bool] | None = None,
) -> None:
    target_path = Path(target).resolve()
    staged_path = Path(staged).resolve()
    backup_path = Path(backup).resolve()
    _validate_apply_paths(target_path, staged_path, backup_path)
    if expected_size is not None and staged_path.stat().st_size != int(expected_size):
        raise ValueError("교체 전 업데이트 파일 크기가 일치하지 않습니다.")
    if expected_sha256 is not None:
        digest = hashlib.sha256()
        with open(staged_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != str(expected_sha256).strip().lower():
            raise ValueError("교체 전 업데이트 파일 해시가 일치하지 않습니다.")
    shutil.copy2(target_path, backup_path)
    try:
        os.replace(staged_path, target_path)
        if smoke_runner is None:
            completed = subprocess.run(
                [str(target_path), "--smoke"],
                timeout=60,
                check=False,
                capture_output=True,
            )
            smoke_ok = completed.returncode == 0
        else:
            smoke_ok = bool(smoke_runner(target_path))
        if not smoke_ok:
            raise RuntimeError("업데이트된 실행 파일의 스모크 검증에 실패했습니다.")
        try:
            cleanup_update_backups(target_path)
        except Exception:
            pass
    except Exception as exc:
        try:
            if backup_path.is_file():
                os.replace(backup_path, target_path)
        except Exception as rollback_exc:
            raise UpdateApplyError(
                f"업데이트 적용 및 롤백 복구 모두 실패: {rollback_exc}"
            ) from exc
        raise UpdateApplyError(f"업데이트 적용 실패로 이전 버전으로 롤백됨: {exc}") from exc


def launch_update_helper(
    *,
    target: str | Path,
    staged: str | Path,
    backup: str | Path,
    parent_pid: int,
    expected_sha256: str,
    expected_size: int,
    result_file: str | Path,
) -> subprocess.Popen[bytes]:
    staged_path = Path(staged).resolve()
    helper_path = staged_path.parent / f"update-helper-{uuid4().hex}.exe"
    shutil.copy2(Path(sys.executable).resolve(), helper_path)
    return subprocess.Popen(
        [
            str(helper_path),
            "--apply-update",
            "--update-target",
            str(Path(target).resolve()),
            "--update-staged",
            str(staged_path),
            "--update-backup",
            str(Path(backup).resolve()),
            "--update-parent-pid",
            str(int(parent_pid)),
            "--update-expected-sha256",
            str(expected_sha256),
            "--update-expected-size",
            str(int(expected_size)),
            "--update-result-file",
            str(Path(result_file).resolve()),
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
