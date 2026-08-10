from __future__ import annotations

import datetime
from pathlib import Path
from typing import Iterable, Sequence, Set

from ..constants import FORMAT_TYPES, MAX_FILENAME_COUNTER, SUPPORTED_EXTENSIONS
from ..logging_config import get_logger
from ..models import ConversionTask, PlannedConversion
from ..path_utils import canonicalize_path, check_write_permission, iter_supported_files
from .artifact_policy import (
    artifact_key,
    existing_artifact_conflicts,
)

logger = get_logger(__name__)


class TaskPlanner:
    def preview_allowed_extensions(self, format_type: str) -> Iterable[str]:
        output_ext = FORMAT_TYPES[format_type]["ext"].lower()
        if output_ext in SUPPORTED_EXTENSIONS:
            return [ext for ext in SUPPORTED_EXTENSIONS if ext != output_ext]
        return SUPPORTED_EXTENSIONS

    def build_tasks(
        self,
        *,
        is_folder_mode: bool,
        format_type: str,
        folder_path: str,
        include_sub: bool,
        same_location: bool,
        output_path: str,
        overwrite: bool = False,
        file_paths: Sequence[str],
        backup_enabled: bool = True,
        retry_count: int = 1,
        backup_max_files_per_stem: int = 20,
        pdf_export_mode: str = "saveas_first",
        folder_file_paths: Sequence[str] | None = None,
    ) -> PlannedConversion:
        tasks: list[ConversionTask] = []
        skipped_tasks: list[ConversionTask] = []
        warnings: list[str] = []
        format_info = FORMAT_TYPES[format_type]
        output_ext = format_info["ext"]

        if is_folder_mode:
            collect_start_path = folder_path.strip()
            if not collect_start_path:
                raise ValueError("폴더를 선택하세요.")

            folder = Path(canonicalize_path(collect_start_path))
            if not folder.exists():
                raise ValueError("폴더가 존재하지 않습니다.")
            if not folder.is_dir():
                raise ValueError("폴더 경로가 올바르지 않습니다.")

            if folder_file_paths is not None:
                # 미리보기 스캔 캐시를 재사용해 UI 스레드 전체 재스캔을 피한다.
                input_files = [
                    Path(canonicalize_path(str(p)))
                    for p in folder_file_paths
                    if str(p).strip()
                ]
                logger.debug(f"폴더 작업 수집(캐시): {len(input_files)}개")
            else:
                allowed_exts: Set[str] = set(SUPPORTED_EXTENSIONS)
                input_files = [
                    Path(canonicalize_path(str(p)))
                    for p in iter_supported_files(
                        folder,
                        include_sub=include_sub,
                        allowed_exts=allowed_exts,
                    )
                ]
                logger.debug(f"폴더 작업 수집(재스캔): {len(input_files)}개")

            if not input_files:
                raise ValueError("변환할 파일이 없습니다.")

            input_files = sorted(input_files, key=lambda p: str(p).lower())

            for input_file in input_files:
                if input_file.suffix.lower() == output_ext.lower():
                    skipped_tasks.append(
                        ConversionTask(
                            input_file=input_file,
                            output_file=input_file,
                            status="건너뜀",
                            error=f"이미 {format_type} 형식입니다.",
                        )
                    )
                    continue

                if same_location:
                    output_file = input_file.parent / (input_file.stem + output_ext)
                else:
                    output_folder_text = output_path.strip()
                    if not output_folder_text:
                        raise ValueError("출력 폴더를 선택하세요.")
                    output_folder = Path(output_folder_text)
                    if not output_folder.exists():
                        raise ValueError(f"출력 폴더가 존재하지 않습니다: {output_folder}")

                    try:
                        rel_path = input_file.relative_to(folder)
                        output_file = (
                            output_folder / rel_path.parent / (input_file.stem + output_ext)
                        )
                    except ValueError:
                        # 캐시 경로가 선택 폴더 트리 밖으로 해석되면 flat 저장 + 경고
                        output_file = output_folder / (input_file.stem + output_ext)
                        warnings.append(
                            f"폴더 밖 경로로 보여 출력 하위 구조를 유지하지 않습니다: {input_file.name}"
                        )

                tasks.append(ConversionTask(input_file=input_file, output_file=output_file))

            if skipped_tasks:
                warnings.append(
                    f"동일 형식 {len(skipped_tasks)}개는 자동으로 건너뜁니다."
                )
            self._append_output_warnings(tasks, warnings, same_location=same_location)

            return PlannedConversion(
                format_type=format_type,
                same_location=same_location,
                output_path=output_path.strip(),
                overwrite=overwrite,
                backup_enabled=backup_enabled,
                retry_count=retry_count,
                backup_max_files_per_stem=backup_max_files_per_stem,
                pdf_export_mode=pdf_export_mode,
                tasks=tasks,
                skipped_tasks=skipped_tasks,
                warnings=warnings,
            )

        if not file_paths:
            raise ValueError("파일을 추가하세요.")

        for file_path in file_paths:
            input_file = Path(file_path)
            if input_file.suffix.lower() == output_ext.lower():
                skipped_tasks.append(
                    ConversionTask(
                        input_file=input_file,
                        output_file=input_file,
                        status="건너뜀",
                        error=f"이미 {format_type} 형식입니다.",
                    )
                )
                continue

            if same_location:
                output_file = input_file.parent / (input_file.stem + output_ext)
            else:
                output_folder_text = output_path.strip()
                if not output_folder_text:
                    raise ValueError("출력 폴더를 선택하세요.")
                output_folder = Path(output_folder_text)
                if not output_folder.exists():
                    raise ValueError(f"출력 폴더가 존재하지 않습니다: {output_folder}")
                output_file = output_folder / (input_file.stem + output_ext)

            tasks.append(ConversionTask(input_file=input_file, output_file=output_file))

        if skipped_tasks:
            warnings.append(
                f"동일 형식 {len(skipped_tasks)}개는 자동으로 건너뜁니다."
            )
        self._append_output_warnings(tasks, warnings, same_location=same_location)

        return PlannedConversion(
            format_type=format_type,
            same_location=same_location,
            output_path=output_path.strip(),
            overwrite=overwrite,
            backup_enabled=backup_enabled,
            retry_count=retry_count,
            backup_max_files_per_stem=backup_max_files_per_stem,
            pdf_export_mode=pdf_export_mode,
            tasks=tasks,
            skipped_tasks=skipped_tasks,
            warnings=warnings,
        )

    def resolve_output_conflicts(
        self,
        tasks: list[ConversionTask],
        overwrite: bool,
        format_type: str | None = None,
    ) -> int:
        used_path_keys: set[str] = set()
        renamed_count = 0

        for task in tasks:
            if self.allocate_output_path(
                task,
                used_path_keys=used_path_keys,
                overwrite=overwrite,
                format_type=format_type,
            ):
                renamed_count += 1

        return renamed_count

    def allocate_output_path(
        self,
        task: ConversionTask,
        *,
        used_path_keys: set[str],
        overwrite: bool,
        format_type: str | None,
    ) -> bool:
        """Allocate a collision-free output path and return whether it changed."""
        original_path = task.output_file
        batch_duplicate = artifact_key(original_path) in used_path_keys
        existing_conflict = (not overwrite) and self._has_existing_output_conflict(
            original_path, format_type
        )
        if not (batch_duplicate or existing_conflict):
            used_path_keys.add(artifact_key(task.output_file))
            return False

        counter = 1
        stem = original_path.stem
        ext = original_path.suffix
        parent = original_path.parent
        while counter <= MAX_FILENAME_COUNTER:
            new_name = f"{stem} ({counter}){ext}"
            new_path = parent / new_name
            exists_conflict = (not overwrite) and self._has_existing_output_conflict(
                new_path, format_type
            )
            batch_conflict = artifact_key(new_path) in used_path_keys
            if not exists_conflict and not batch_conflict:
                task.output_file = new_path
                break
            counter += 1
        else:
            fallback_counter = 1
            while True:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                suffix = "" if fallback_counter == 1 else f"_{fallback_counter}"
                new_name = f"{stem}_{timestamp}{suffix}{ext}"
                new_path = parent / new_name
                exists_conflict = (not overwrite) and self._has_existing_output_conflict(
                    new_path, format_type
                )
                batch_conflict = artifact_key(new_path) in used_path_keys
                if not exists_conflict and not batch_conflict:
                    task.output_file = new_path
                    logger.warning(f"파일명 카운터 초과, 타임스탬프 사용: {new_name}")
                    break
                fallback_counter += 1

        task.conflict_original_output_file = original_path
        used_path_keys.add(artifact_key(task.output_file))
        logger.info(f"출력 경로 조정: {original_path} -> {task.output_file}")
        return True

    def _has_existing_output_conflict(self, output_file: Path, format_type: str | None) -> bool:
        if format_type is None:
            return output_file.exists()
        return bool(existing_artifact_conflicts(output_file, format_type))

    def _append_output_warnings(
        self,
        tasks: list[ConversionTask],
        warnings: list[str],
        *,
        same_location: bool,
    ) -> None:
        if not same_location:
            return

        unwritable_dirs: list[Path] = []
        seen: set[str] = set()
        for task in tasks:
            parent = task.output_file.parent
            key = str(parent).lower()
            if key in seen:
                continue
            seen.add(key)
            if not check_write_permission(parent):
                unwritable_dirs.append(parent)

        if unwritable_dirs:
            preview = ", ".join(str(path) for path in unwritable_dirs[:3])
            suffix = "" if len(unwritable_dirs) <= 3 else f" 외 {len(unwritable_dirs) - 3}개"
            warnings.append(f"같은 위치 저장 대상 중 쓰기 권한을 확인하지 못한 폴더가 있습니다: {preview}{suffix}")
