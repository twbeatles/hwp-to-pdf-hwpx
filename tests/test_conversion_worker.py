from __future__ import annotations

from datetime import datetime as real_datetime
from pathlib import Path

from hwpmate.models import ConversionTask, PlannedConversion
from hwpmate.workers.conversion_worker import ConversionWorker


class StubConverter:
    def __init__(self, *, results: dict[str, tuple[bool, str | None]], owned: bool = True, on_convert=None) -> None:
        self.results = results
        self.owned = owned
        self.on_convert = on_convert
        self.progid_used = "Stub.Hwp"
        self.pdf_export_mode = "saveas_first"
        self.cleaned = False
        self.kill_called = False
        self.manage_com_apartment_args: list[bool] = []

    def initialize(self, *, manage_com_apartment: bool = True) -> bool:
        self.manage_com_apartment_args.append(manage_com_apartment)
        return True

    def convert_file(self, input_path, output_path, format_type="PDF", *, cancel_check=None):
        del cancel_check
        if self.on_convert is not None:
            self.on_convert(Path(input_path))
        return self.results[Path(input_path).name]

    def cleanup(self) -> None:
        self.cleaned = True

    def has_owned_processes(self) -> bool:
        return self.owned

    def kill_owned_processes(self) -> bool:
        self.kill_called = True
        return self.owned


class FailingInitConverter(StubConverter):
    def initialize(self, *, manage_com_apartment: bool = True) -> bool:
        self.manage_com_apartment_args.append(manage_com_apartment)
        raise RuntimeError("init failed")


class SequenceConverter(StubConverter):
    def __init__(self, *, sequence: list[tuple[bool, str | None]]) -> None:
        super().__init__(results={})
        self.sequence = sequence

    def convert_file(self, input_path, output_path, format_type="PDF", *, cancel_check=None):
        del input_path, output_path, format_type, cancel_check
        return self.sequence.pop(0)


class RaisingConverter(StubConverter):
    def convert_file(self, input_path, output_path, format_type="PDF", *, cancel_check=None):
        del input_path, output_path, format_type, cancel_check
        raise RuntimeError("worker boom")


class ArtifactConverter(StubConverter):
    def convert_file(self, input_path, output_path, format_type="PDF", *, cancel_check=None):
        result = super().convert_file(
            input_path, output_path, format_type, cancel_check=cancel_check
        )
        self.last_created_files = [Path(output_path)]
        self.last_output_size = 123
        self.last_output_mtime = 1777777777.0
        self.last_save_format = format_type
        self.last_export_method = "saveas_2"
        return result


def test_conversion_worker_builds_summary_with_success_failure_and_skip(tmp_path: Path, monkeypatch) -> None:
    import hwpmate.workers.conversion_worker as worker_module

    monkeypatch.setattr(worker_module.time, "sleep", lambda _: None)
    first = tmp_path / "a.hwp"
    second = tmp_path / "b.hwp"
    skipped = tmp_path / "c.hwpx"
    for path in (first, second, skipped):
        path.write_text("x", encoding="utf-8")

    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[
            ConversionTask(first, first.with_suffix(".pdf")),
            ConversionTask(second, second.with_suffix(".pdf")),
        ],
        skipped_tasks=[
            ConversionTask(skipped, skipped, status="건너뜀", error="이미 HWPX 형식입니다."),
        ],
        warnings=["동일 형식 1개는 자동으로 건너뜁니다."],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: StubConverter(
            results={
                "a.hwp": (True, None),
                "b.hwp": (False, "save failed"),
            }
        ),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))
    worker.run()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.success_count == 1
    assert summary.failed_count == 1
    assert summary.skipped_count == 1
    assert summary.canceled_count == 0
    assert summary.output_paths == [str(first.with_suffix(".pdf"))]
    failed = next(task for task in summary.tasks if task.status == "실패")
    assert failed.retry_count == 1


def test_conversion_worker_marks_remaining_tasks_as_canceled(tmp_path: Path) -> None:
    first = tmp_path / "a.hwp"
    second = tmp_path / "b.hwp"
    skipped = tmp_path / "c.hwp"
    for path in (first, second, skipped):
        path.write_text("x", encoding="utf-8")

    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[
            ConversionTask(first, first.with_suffix(".pdf")),
            ConversionTask(second, second.with_suffix(".pdf")),
        ],
        skipped_tasks=[
            ConversionTask(skipped, skipped, status="건너뜀", error="이미 PDF 형식입니다."),
        ],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: StubConverter(
            results={
                "a.hwp": (True, None),
                "b.hwp": (True, None),
            },
            on_convert=lambda _: worker.cancel(),
        ),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))
    worker.run()

    summary = summaries[0]
    assert summary.success_count == 1
    assert summary.canceled_count == 1
    assert summary.skipped_count == 1
    assert any(task.status == "취소됨" for task in summary.tasks)


def test_conversion_worker_marks_in_progress_failure_as_canceled(tmp_path: Path) -> None:
    """취소 요청 후 COM 오류가 나도 실패가 아니라 취소로 집계한다."""
    first = tmp_path / "a.hwp"
    first.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        retry_count=0,
        tasks=[ConversionTask(first, first.with_suffix(".pdf"))],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: StubConverter(
            results={"a.hwp": (False, "COM error")},
            on_convert=lambda _: worker.cancel(),
        ),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))
    worker.run()

    task = summaries[0].tasks[0]
    assert task.status == "취소됨"
    assert "COM error" in (task.error or "")
    assert summaries[0].canceled_count == 1
    assert summaries[0].failed_count == 0


def test_conversion_worker_progress_emits_completed_count(tmp_path: Path) -> None:
    first = tmp_path / "a.hwp"
    second = tmp_path / "b.hwp"
    for path in (first, second):
        path.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[
            ConversionTask(first, first.with_suffix(".pdf")),
            ConversionTask(second, second.with_suffix(".pdf")),
        ],
    )
    progress_events: list[tuple[int, int, str]] = []
    created: list[StubConverter] = []

    def factory() -> StubConverter:
        converter = StubConverter(results={"a.hwp": (True, None), "b.hwp": (True, None)})
        created.append(converter)
        return converter

    worker = ConversionWorker(plan, converter_factory=factory)
    worker.progress_updated.connect(lambda cur, total, name: progress_events.append((cur, total, name)))
    worker.run()

    assert (1, 2, "a.hwp") in progress_events
    assert (2, 2, "b.hwp") in progress_events
    assert progress_events[-1][:2] == (2, 2)
    assert created and created[0].manage_com_apartment_args == [False]


def test_conversion_worker_emits_failed_summary_when_initialize_fails(tmp_path: Path) -> None:
    input_file = tmp_path / "a.hwp"
    input_file.write_text("x", encoding="utf-8")
    skipped = tmp_path / "b.hwpx"
    skipped.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[ConversionTask(input_file, input_file.with_suffix(".pdf"))],
        skipped_tasks=[ConversionTask(skipped, skipped, status="건너뜀", error="이미 HWPX 형식입니다.")],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: FailingInitConverter(results={"a.hwp": (True, None)}),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))

    worker.run()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.failed_count == 1
    assert summary.skipped_count == 1
    assert "한글 초기화 실패" in summary.failed_tasks[0].detail


def test_conversion_worker_retries_failed_conversion(tmp_path: Path, monkeypatch) -> None:
    import hwpmate.workers.conversion_worker as worker_module

    monkeypatch.setattr(worker_module.time, "sleep", lambda _: None)
    input_file = tmp_path / "a.hwp"
    input_file.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        retry_count=1,
        tasks=[ConversionTask(input_file, input_file.with_suffix(".pdf"))],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: SequenceConverter(sequence=[(False, "temporary"), (True, None)]),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))

    worker.run()

    task = summaries[0].tasks[0]
    assert task.status == "성공"
    assert task.retry_count == 1


def test_conversion_worker_attaches_converter_artifacts(tmp_path: Path) -> None:
    input_file = tmp_path / "a.hwp"
    input_file.write_text("x", encoding="utf-8")
    output = input_file.with_suffix(".pdf")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[ConversionTask(input_file, output)],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: ArtifactConverter(results={"a.hwp": (True, None)}),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))

    worker.run()

    task = summaries[0].tasks[0]
    assert task.created_files == [output]
    assert task.output_size == 123
    assert task.save_format == "PDF"
    assert task.export_method == "saveas_2"
    assert task.progid_used == "Stub.Hwp"


def test_conversion_worker_emits_summary_for_unexpected_worker_errors(tmp_path: Path) -> None:
    input_file = tmp_path / "a.hwp"
    input_file.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[ConversionTask(input_file, input_file.with_suffix(".pdf"))],
    )
    summaries = []
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: RaisingConverter(results={"a.hwp": (True, None)}),
    )
    worker.task_completed.connect(lambda summary: summaries.append(summary))

    worker.run()

    assert summaries[0].failed_count == 1
    assert "변환 워커 오류" in summaries[0].failed_tasks[0].detail


def test_force_terminate_uses_owned_processes_only(tmp_path: Path) -> None:
    input_file = tmp_path / "a.hwp"
    input_file.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        tasks=[ConversionTask(input_file, input_file.with_suffix(".pdf"))],
    )
    owned_converter = StubConverter(results={"a.hwp": (True, None)}, owned=True)
    worker = ConversionWorker(plan, converter_factory=lambda: owned_converter)
    worker.converter = owned_converter

    assert worker.can_force_terminate() is True
    assert worker.force_terminate() is True
    assert owned_converter.kill_called is True

    unowned_converter = StubConverter(results={"a.hwp": (True, None)}, owned=False)
    worker.converter = unowned_converter
    assert worker.can_force_terminate() is False
    assert worker.force_terminate() is False


def test_create_backup_avoids_name_collisions(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "doc.hwp"
    source.write_text("x", encoding="utf-8")
    plan = PlannedConversion(format_type="PDF", same_location=True, output_path="")
    worker = ConversionWorker(plan)

    class FrozenDateTime:
        @classmethod
        def now(cls):
            return real_datetime(2026, 3, 18, 12, 0, 0, 123456)

    import hwpmate.workers.conversion_worker.backup as backup_module

    monkeypatch.setattr(backup_module, "datetime", FrozenDateTime)
    worker._create_backup(source)
    worker._create_backup(source)

    backups = sorted((tmp_path / "backup").iterdir())
    assert len(backups) == 2
    assert backups[0].name != backups[1].name


def test_create_backup_prunes_old_stem_files(tmp_path: Path, monkeypatch) -> None:
    import hwpmate.workers.conversion_worker.backup as backup_module

    source = tmp_path / "doc.hwp"
    source.write_text("x", encoding="utf-8")
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    for i in range(5):
        old = backup_dir / f"doc_20200101_00000{i}_000000.hwp"
        old.write_text("old", encoding="utf-8")

    path = backup_module.create_backup(source, max_files=3)
    assert path.exists()
    remaining = list(backup_dir.glob("doc_*.hwp"))
    assert len(remaining) <= 3


def test_conversion_worker_renames_output_when_late_conflict_appears(tmp_path: Path) -> None:
    source = tmp_path / "source.hwp"
    source.write_text("x", encoding="utf-8")
    original = tmp_path / "out.pdf"
    original.write_bytes(b"external output")
    task = ConversionTask(source, original)
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        overwrite=False,
        retry_count=0,
        tasks=[task],
    )
    converted_outputs: list[Path] = []

    class OutputRecordingConverter(StubConverter):
        def convert_file(self, input_path, output_path, format_type="PDF", *, cancel_check=None):
            del input_path, format_type, cancel_check
            converted_outputs.append(Path(output_path))
            Path(output_path).write_bytes(b"%PDF-1.4\n")
            return True, None

    worker = ConversionWorker(
        plan,
        converter_factory=lambda: OutputRecordingConverter(results={}),
    )
    worker.run()

    assert converted_outputs == [tmp_path / "out (1).pdf"]
    assert original.read_bytes() == b"external output"
    assert task.conflict_original_output_file == original


def test_conversion_worker_emits_com_export_stage(tmp_path: Path) -> None:
    source = tmp_path / "a.hwp"
    source.write_text("x", encoding="utf-8")
    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path="",
        retry_count=0,
        tasks=[ConversionTask(source, tmp_path / "a.pdf")],
    )
    worker = ConversionWorker(
        plan,
        converter_factory=lambda: StubConverter(results={"a.hwp": (True, None)}),
    )
    stages: list[str] = []
    worker.stage_updated.connect(lambda stage, _: stages.append(stage))

    worker.run()

    assert "COM 내보내기" in stages
