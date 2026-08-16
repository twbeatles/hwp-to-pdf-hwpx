from __future__ import annotations

from pathlib import Path
from typing import Any
import pytest

from hwpmate.models import ConversionTask, PlannedConversion
from hwpmate.workers.conversion_worker import ConversionWorker


class MockRecycleConverter:
    def __init__(self) -> None:
        self.initialize_call_count = 0
        self.cleanup_call_count = 0
        self.convert_call_count = 0
        self.pdf_export_mode: str = "saveas_first"

    @property
    def progid_used(self) -> str | None:
        return "HWPControl.HwpCtrl.1"

    def initialize(self, *, manage_com_apartment: bool = True) -> bool:
        self.initialize_call_count += 1
        return True

    def convert_file(
        self,
        input_path: Any,
        output_path: Any,
        format_type: str = "PDF",
        *,
        cancel_check: Any = None,
    ) -> tuple[bool, str | None]:
        self.convert_call_count += 1
        return True, None


    def cleanup(self) -> None:
        self.cleanup_call_count += 1

    def has_owned_processes(self) -> bool:
        return True

    def kill_owned_processes(self) -> bool:
        return True


def test_conversion_worker_recycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 2건마다 재순환하도록 설정
    import hwpmate.workers.conversion_worker.worker as worker_module
    monkeypatch.setattr(worker_module, "CONVERTER_RECYCLE_BATCH_COUNT", 2)

    tasks = [
        ConversionTask(
            input_file=tmp_path / f"doc{i}.hwp",
            output_file=tmp_path / f"doc{i}.pdf",
            status="대기",
        )
        for i in range(5)
    ]
    for t in tasks:
        t.input_file.write_bytes(b"dummy hwp")

    plan = PlannedConversion(
        format_type="PDF",
        same_location=True,
        output_path=str(tmp_path),
        backup_enabled=False,
        retry_count=0,
        tasks=tasks,
    )


    mock_converter = MockRecycleConverter()
    worker = ConversionWorker(plan, converter_factory=lambda: mock_converter)

    worker.run()

    # 5개 파일 변환 시:
    # idx=0 (doc0), idx=1 (doc1: 2번째 -> recycle 1회), idx=2 (doc2), idx=3 (doc3: 4번째 -> recycle 1회), idx=4 (doc4: 마지막 건)
    # 총 2회 recycle + 시작 시 1회 init + finally 시 1회 cleanup
    # initialize: 최초 1 + 재순환 2 = 3회
    # cleanup: 재순환 2 + finally 1 = 3회
    assert mock_converter.convert_call_count == 5
    assert mock_converter.initialize_call_count == 3
    assert mock_converter.cleanup_call_count == 3
