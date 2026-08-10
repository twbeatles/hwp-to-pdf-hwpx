"""변환 산출물 파일 스냅샷·변경 감지."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..artifact_policy import iter_candidate_artifact_paths


@dataclass(frozen=True)
class _FileSnapshot:
    size: int
    mtime_ns: int
    ctime_ns: int


def _snapshot_file(path: Path) -> _FileSnapshot | None:
    try:
        stat = path.stat()
        if not path.is_file():
            return None
        return _FileSnapshot(
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
        )
    except OSError:
        return None


def _iter_candidate_artifact_files(output_file: Path, format_type: str) -> list[Path]:
    return iter_candidate_artifact_paths(output_file, format_type)


def _snapshot_artifacts(output_file: Path, format_type: str) -> dict[Path, _FileSnapshot]:
    snapshots: dict[Path, _FileSnapshot] = {}
    for path in _iter_candidate_artifact_files(output_file, format_type):
        snapshot = _snapshot_file(path)
        if snapshot is not None:
            snapshots[path] = snapshot
    return snapshots


def _changed_artifacts(
    before: dict[Path, _FileSnapshot],
    after: dict[Path, _FileSnapshot],
) -> list[Path]:
    changed: list[Path] = []
    for path, snapshot in after.items():
        if snapshot.size <= 0:
            continue
        if before.get(path) != snapshot:
            changed.append(path)
    return sorted(changed, key=lambda p: str(p).lower())


def remove_new_attempt_artifacts(
    before: dict[Path, _FileSnapshot],
    output_file: Path,
    format_type: str,
) -> tuple[list[Path], list[str]]:
    """Best-effort removal of artifacts created by a failed export attempt.

    Paths present before the attempt are never removed, even if an export
    changed them. This keeps cleanup safe when a converter ignored the
    application's overwrite policy.
    """
    after = _snapshot_artifacts(output_file, format_type)
    removed: list[Path] = []
    warnings: list[str] = []
    for path in sorted(after, key=lambda item: str(item).lower(), reverse=True):
        if path in before:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:
            warnings.append(f"실패 산출물 정리 실패: {path} ({exc})")

    candidates = _iter_candidate_artifact_files(output_file, format_type)
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue
    return removed, warnings
