from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FormatSpec:
    ext: str
    save_format: str
    icon: str
    desc: str

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class AppConfig:
    config_version: int = 3
    theme: str = "dark"
    mode: str = "folder"
    format: str = "PDF"
    include_sub: bool = True
    same_location: bool = True
    overwrite: bool = False
    backup_enabled: bool = True
    retry_count: int = 1
    # 동일 stem 백업 최대 보관 개수 (1~100)
    backup_max_files_per_stem: int = 20
    # 보안 모듈 실패 시 「모두 허용」 대화상자 자동 클릭 (best-effort)
    auto_accept_security_dialog: bool = True
    # PDF: saveas_first(용지 품질 우선) | print_to_pdf_ex_first(모아찍기 완화 우선)
    pdf_export_mode: str = "saveas_first"
    folder_path: str = ""
    output_path: str = ""
    last_folder: str = ""
    last_output: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AppConfig":
        known_keys = set(cls.__dataclass_fields__.keys())
        filtered = {key: value for key, value in data.items() if key in known_keys}
        return cls(**filtered)


@dataclass
class ConversionTask:
    input_file: Path
    output_file: Path
    status: str = "대기"
    error: str | None = None
    retry_count: int = 0
    backup_file: Path | None = None
    backup_error: str | None = None
    conflict_original_output_file: Path | None = None
    created_files: list[Path] = field(default_factory=list)
    output_size: int | None = None
    output_mtime: float | None = None
    save_format: str | None = None
    # saveas_2 | saveas_3 | print_to_pdf_ex | run_to_pdf
    export_method: str | None = None
    progid_used: str | None = None

    def __post_init__(self) -> None:
        self.input_file = Path(self.input_file)
        self.output_file = Path(self.output_file)
        if self.backup_file is not None:
            self.backup_file = Path(self.backup_file)
        if self.conflict_original_output_file is not None:
            self.conflict_original_output_file = Path(self.conflict_original_output_file)
        self.created_files = [Path(path) for path in self.created_files]

    @property
    def detail(self) -> str:
        return self.error or ""

    def to_record(self) -> dict[str, Any]:
        return {
            "input_file": str(self.input_file),
            "output_file": str(self.output_file),
            "status": self.status,
            "detail": self.detail,
            "retry_count": self.retry_count,
            "backup_file": str(self.backup_file) if self.backup_file is not None else "",
            "backup_error": self.backup_error or "",
            "created_files": "; ".join(str(path) for path in self.created_files),
            "output_size": self.output_size if self.output_size is not None else "",
            "output_mtime": self.output_mtime if self.output_mtime is not None else "",
            "save_format": self.save_format or "",
            "export_method": self.export_method or "",
            "progid_used": self.progid_used or "",
        }

    def to_json_record(self) -> dict[str, Any]:
        record = self.to_record()
        record["created_files"] = [str(path) for path in self.created_files]
        return record

    def snapshot(self) -> "ConversionTask":
        """워커→UI 전달용 독립 복사본 (공유 뮤테이션 방지)."""
        return ConversionTask(
            input_file=self.input_file,
            output_file=self.output_file,
            status=self.status,
            error=self.error,
            retry_count=self.retry_count,
            backup_file=self.backup_file,
            backup_error=self.backup_error,
            conflict_original_output_file=self.conflict_original_output_file,
            created_files=list(self.created_files),
            output_size=self.output_size,
            output_mtime=self.output_mtime,
            save_format=self.save_format,
            export_method=self.export_method,
            progid_used=self.progid_used,
        )


@dataclass
class PlannedConversion:
    format_type: str
    same_location: bool
    output_path: str
    overwrite: bool = False
    backup_enabled: bool = True
    retry_count: int = 1
    backup_max_files_per_stem: int = 20
    pdf_export_mode: str = "saveas_first"
    tasks: list[ConversionTask] = field(default_factory=list)
    skipped_tasks: list[ConversionTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflict_renamed_count: int = 0

    @property
    def runnable_count(self) -> int:
        return len(self.tasks)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_tasks)

    @property
    def total_requested(self) -> int:
        return self.runnable_count + self.skipped_count

    @property
    def all_tasks(self) -> list[ConversionTask]:
        return sorted(self.tasks + self.skipped_tasks, key=lambda task: str(task.input_file).lower())

    @property
    def output_policy_label(self) -> str:
        if self.same_location:
            return "입력 파일과 같은 위치"
        return self.output_path or "사용자 지정 출력 폴더"


@dataclass
class ConversionSummary:
    format_type: str
    tasks: list[ConversionTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float | None = None
    progid_used: str | None = None

    @property
    def total_requested(self) -> int:
        return len(self.tasks)

    @property
    def success_count(self) -> int:
        return len([task for task in self.tasks if task.status == "성공"])

    @property
    def failed_count(self) -> int:
        return len([task for task in self.tasks if task.status == "실패"])

    @property
    def skipped_count(self) -> int:
        return len([task for task in self.tasks if task.status == "건너뜀"])

    @property
    def canceled_count(self) -> int:
        return len([task for task in self.tasks if task.status == "취소됨"])

    @property
    def output_paths(self) -> list[str]:
        paths: list[str] = []
        for task in self.tasks:
            if task.status != "성공":
                continue
            if task.created_files:
                paths.extend(str(path) for path in task.created_files)
            else:
                paths.append(str(task.output_file))
        return paths

    @property
    def failed_tasks(self) -> list[ConversionTask]:
        return [task for task in self.tasks if task.status == "실패"]

    @property
    def skipped_tasks(self) -> list[ConversionTask]:
        return [task for task in self.tasks if task.status == "건너뜀"]

    @property
    def canceled_tasks(self) -> list[ConversionTask]:
        return [task for task in self.tasks if task.status == "취소됨"]

    def sorted_tasks(self) -> list[ConversionTask]:
        return sorted(self.tasks, key=lambda task: str(task.input_file).lower())

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "format_type": self.format_type,
                "total_requested": self.total_requested,
                "success_count": self.success_count,
                "failed_count": self.failed_count,
                "skipped_count": self.skipped_count,
                "canceled_count": self.canceled_count,
                "elapsed_seconds": self.elapsed_seconds,
                "progid_used": self.progid_used,
                "warnings": list(self.warnings),
            },
            "tasks": [task.to_json_record() for task in self.sorted_tasks()],
        }
