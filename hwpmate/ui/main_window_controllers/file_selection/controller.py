"""파일/폴더 선택·스캔 컨트롤러."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QTableWidgetItem

from ....constants import SCAN_BATCH_SIZE, SCAN_CANCEL_WAIT_MS, SUPPORTED_EXTENSIONS
from ....logging_config import get_logger
from ....path_utils import canonicalize_path, make_path_key
from ....workers.file_scan_worker import FileScanWorker
from ..state import MainWindowState

logger = get_logger(__name__)


class FileSelectionController:
    """Folder/file selection and asynchronous scan lifecycle."""

    def __init__(self, window: Any, state: MainWindowState) -> None:
        self.window = window
        self.state = state

    def cancel_active_scan(self, wait_ms: int = SCAN_CANCEL_WAIT_MS) -> bool:
        worker = self.state.scan_worker
        if not worker:
            return True

        if worker.isRunning():
            worker.cancel()
            worker.wait(wait_ms)

        if worker.isRunning():
            return False

        try:
            worker.batch_found.disconnect(self.window._on_scan_batch_found)
            worker.scan_progress.disconnect(self.window._on_scan_progress)
            worker.scan_finished.disconnect(self.window._on_scan_finished)
            worker.scan_error.disconnect(self.window._on_scan_error)
            worker.finished.disconnect(self.window._on_scan_worker_finished)
        except (TypeError, RuntimeError):
            pass

        worker.deleteLater()
        self._clear_scan_state()
        return True

    def wait_for_active_scan(self, wait_ms: int) -> bool:
        """취소 없이 활성 스캔 종료를 대기하고, 완료 시그널을 메인 루프에 드레인한다.

        QThread.wait() 만 호출하면 scan_finished 슬롯이 아직 처리되지 않아
        folder_scan_ready 캐시가 비어 있을 수 있다. 짧은 구간으로 나누어 대기하며
        processEvents 로 캐시 갱신 슬롯을 실행한다.
        종료 요청(close_requested)이 있으면 즉시 False 를 반환한다.
        """
        from PyQt6.QtWidgets import QApplication

        worker = self.state.scan_worker
        if not worker or not worker.isRunning():
            QApplication.processEvents()
            return not self.state.close_requested

        remaining = max(0, int(wait_ms))
        slice_ms = 100
        while remaining > 0 and worker.isRunning():
            if self.state.close_requested:
                return False
            step = min(slice_ms, remaining)
            if worker.wait(step):
                break
            QApplication.processEvents()
            remaining -= step

        # 스레드 종료 직후 큐에 쌓인 scan_finished / finished 슬롯 처리
        for _ in range(5):
            if self.state.close_requested:
                return False
            QApplication.processEvents()
            if self.state.folder_scan_ready or not worker.isRunning():
                # finished 후에도 ready 가 설정되도록 한 번 더
                if not self.state.folder_scan_ready:
                    QApplication.processEvents()
                break

        if self.state.close_requested:
            return False
        return not worker.isRunning()

    def start_scan(
        self,
        input_paths: list[str],
        mode: str,
        include_sub: bool = True,
        allowed_exts: Iterable[str] | None = None,
        *,
        allow_while_planning: bool = False,
    ) -> None:
        if self._input_locked() and not (allow_while_planning and self.state.is_planning):
            return

        cleaned_inputs = [str(p).strip() for p in input_paths if str(p).strip()]
        if not cleaned_inputs:
            return

        if not self.cancel_active_scan():
            logger.warning("이전 파일 스캔이 아직 종료되지 않아 새 스캔을 시작하지 않습니다.")
            return

        self.state.scan_mode = mode
        self.state.scan_new_file_count = 0
        self.state.scan_preview_count = 0
        self.state.scan_started_at = time.perf_counter()
        if mode == "folder_preview":
            self.invalidate_folder_scan_cache()
            self.state.folder_scan_accum = []
            self.state.folder_scan_folder = canonicalize_path(cleaned_inputs[0])
            self.state.folder_scan_include_sub = include_sub

        self.state.scan_worker = FileScanWorker(
            cleaned_inputs,
            include_sub=include_sub,
            allowed_exts=allowed_exts or SUPPORTED_EXTENSIONS,
            batch_size=SCAN_BATCH_SIZE,
        )
        self.state.scan_worker.batch_found.connect(self.window._on_scan_batch_found)
        self.state.scan_worker.scan_progress.connect(self.window._on_scan_progress)
        self.state.scan_worker.scan_finished.connect(self.window._on_scan_finished)
        self.state.scan_worker.scan_error.connect(self.window._on_scan_error)
        self.state.scan_worker.finished.connect(self.window._on_scan_worker_finished)
        self.state.scan_worker.start()

    def start_folder_preview_scan(self, folder_path: str, *, allow_while_planning: bool = False) -> None:
        self.window.status_label.setText("📂 폴더 스캔 중...")
        # 전체 지원 확장자를 캐시하고, 표시 카운트만 현재 형식 기준으로 계산한다.
        self.start_scan(
            [folder_path],
            mode="folder_preview",
            include_sub=self.window.include_sub_check.isChecked(),
            allowed_exts=set(SUPPORTED_EXTENSIONS),
            allow_while_planning=allow_while_planning,
        )

    def refresh_folder_scan_for_conversion(self, folder_path: str) -> bool:
        """Refresh the folder cache without opening the normal input mutation path."""
        self.invalidate_folder_scan_cache()
        self.start_folder_preview_scan(folder_path, allow_while_planning=True)
        return self.wait_for_active_scan(SCAN_CANCEL_WAIT_MS)

    def append_files_batch(self, files: list[str]) -> int:
        if not files:
            return 0

        unique_files = self.window.file_store.add_paths(files)
        if not unique_files:
            return 0

        render_start = time.perf_counter()
        start_row = self.window.file_table.rowCount()
        end_row = start_row + len(unique_files)

        self.window.file_table.setUpdatesEnabled(False)
        blocker = QSignalBlocker(self.window.file_table)
        try:
            self.window.file_table.setRowCount(end_row)
            for row_idx, file_path in enumerate(unique_files, start=start_row):
                file_obj = Path(file_path)
                self.window.file_table.setItem(row_idx, 0, QTableWidgetItem(file_obj.name))
                self.window.file_table.setItem(row_idx, 1, QTableWidgetItem(str(file_obj.parent)))
        finally:
            del blocker
            self.window.file_table.setUpdatesEnabled(True)

        self.update_file_count()

        if logger.isEnabledFor(logging.DEBUG):
            elapsed = time.perf_counter() - render_start
            logger.debug(f"파일 목록 렌더링: batch={len(unique_files)}, 소요={elapsed:.4f}s")
        return len(unique_files)

    def on_scan_batch_found(self, batch: list[str]) -> None:
        if self.window.sender() is not self.state.scan_worker:
            return

        if self.state.scan_mode == "add_files":
            added = self.append_files_batch(batch)
            self.state.scan_new_file_count += added
            return

        if self.state.scan_mode == "folder_preview":
            self.state.folder_scan_accum.extend(batch)
            self.state.scan_preview_count = self._count_preview_convertible(self.state.folder_scan_accum)

    def on_scan_progress(self, current: int, total: int) -> None:
        if self.window.sender() is not self.state.scan_worker:
            return

        if self.state.scan_mode == "add_files":
            self.window.status_label.setText(
                f"📥 파일 스캔 중... {current}/{total} 경로 처리 (신규 {self.state.scan_new_file_count}개)"
            )
            return

        if self.state.scan_mode == "folder_preview":
            self.window.status_label.setText(
                f"📂 폴더 스캔 중... {current}/{total} 경로 처리 "
                f"(전체 {len(self.state.folder_scan_accum)}개 / 변환가능 {self.state.scan_preview_count}개)"
            )

    def on_scan_finished(self, total_found: int, canceled: bool) -> None:
        if self.window.sender() is not self.state.scan_worker:
            return

        elapsed = 0.0
        if self.state.scan_started_at is not None:
            elapsed = time.perf_counter() - self.state.scan_started_at

        if self.state.scan_mode == "add_files":
            if canceled:
                self.window.status_label.setText("파일 스캔이 취소되었습니다")
            elif self.state.scan_new_file_count == 0:
                self.window.status_label.setText("추가할 새 파일이 없습니다")
            else:
                self.window.status_label.setText(
                    f"{self.state.scan_new_file_count}개 파일 추가됨 (총 {self.window.file_store.count}개)"
                )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"파일 추가 스캔 완료: 발견={total_found}, 신규={self.state.scan_new_file_count}, "
                    f"취소={canceled}, 소요={elapsed:.3f}s"
                )
            return

        if self.state.scan_mode == "folder_preview":
            if canceled:
                self.invalidate_folder_scan_cache()
                self.window.status_label.setText("폴더 스캔이 취소되었습니다")
            else:
                self.state.folder_scan_files = list(self.state.folder_scan_accum)
                self.state.folder_scan_ready = True
                self.state.folder_scan_ready_at = time.perf_counter()
                self.state.folder_scan_file_count = len(self.state.folder_scan_files)
                self.state.folder_scan_dir_mtime = self._dir_mtime(self.state.folder_scan_folder)
                self.state.scan_preview_count = self._count_preview_convertible(self.state.folder_scan_files)
                if self.state.scan_preview_count == 0:
                    self.window.status_label.setText("⚠️ 현재 포맷으로 변환 가능한 파일이 없습니다")
                else:
                    self.window.status_label.setText(
                        f"📁 변환 가능 {self.state.scan_preview_count}개 "
                        f"(스캔 전체 {len(self.state.folder_scan_files)}개)"
                    )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"폴더 미리보기 스캔 완료: 전체={len(self.state.folder_scan_files)}, "
                    f"변환가능={self.state.scan_preview_count}, 취소={canceled}, 소요={elapsed:.3f}s"
                )

    def on_scan_error(self, error_msg: str) -> None:
        if self.window.sender() is not self.state.scan_worker:
            return
        logger.error(f"파일 스캔 오류: {error_msg}")
        if self.state.scan_mode == "folder_preview":
            self.invalidate_folder_scan_cache()
        self.window.status_label.setText("파일 스캔 중 오류가 발생했습니다")

    def on_scan_worker_finished(self) -> None:
        worker = self.state.scan_worker
        if self.window.sender() is not worker or worker is None:
            return
        worker.deleteLater()
        self._clear_scan_state()

    def _clear_scan_state(self) -> None:
        self.state.scan_worker = None
        self.state.scan_mode = None
        self.state.scan_started_at = None
        self.state.scan_new_file_count = 0
        self.state.scan_preview_count = 0
        self.state.folder_scan_accum = []

    def invalidate_folder_scan_cache(self) -> None:
        self.state.folder_scan_ready = False
        self.state.folder_scan_ready_at = None
        self.state.folder_scan_files = []
        self.state.folder_scan_accum = []
        self.state.folder_scan_dir_mtime = None
        self.state.folder_scan_file_count = 0

    @staticmethod
    def _dir_mtime(folder_path: str) -> float | None:
        try:
            return Path(folder_path).stat().st_mtime
        except OSError:
            return None

    def get_folder_scan_cache(
        self,
        *,
        folder_path: str,
        include_sub: bool,
        max_age_seconds: float | None = None,
    ) -> list[str] | None:
        from ....constants import FOLDER_SCAN_CACHE_MAX_AGE_SECONDS

        if not self.state.folder_scan_ready:
            return None
        folder_key = make_path_key(canonicalize_path(folder_path.strip()))
        cached_key = make_path_key(self.state.folder_scan_folder) if self.state.folder_scan_folder else ""
        if folder_key != cached_key:
            return None
        if bool(include_sub) != bool(self.state.folder_scan_include_sub):
            return None

        age_limit = (
            FOLDER_SCAN_CACHE_MAX_AGE_SECONDS
            if max_age_seconds is None
            else max_age_seconds
        )
        ready_at = self.state.folder_scan_ready_at
        if ready_at is not None and age_limit >= 0:
            if (time.perf_counter() - ready_at) > age_limit:
                logger.info(
                    f"폴더 스캔 캐시 만료 ({age_limit:.0f}s 초과) — 재스캔 필요"
                )
                return None

        return list(self.state.folder_scan_files)

    def validate_folder_scan_cache_freshness(
        self,
        paths: list[str],
        *,
        sample_size: int | None = None,
        folder_path: str | None = None,
    ) -> tuple[bool, str]:
        """캐시 경로 샘플·폴더 mtime·파일 수로 신선도를 검사. (ok, reason).

        샘플은 앞·뒤·중간을 섞어 후반부 삭제·중간 누락도 잡도록 한다.
        폴더 mtime 변경은 신규 파일 추가 등 변경 감지에 사용한다(NTFS best-effort).
        """
        from ....constants import FOLDER_SCAN_CACHE_SAMPLE_SIZE

        if not paths:
            return False, "캐시가 비어 있습니다."

        # 파일 수 불일치 (스캔 직후 상태와 캐시 목록 길이)
        cached_count = self.state.folder_scan_file_count
        if cached_count > 0 and len(paths) != cached_count:
            return (
                False,
                f"스캔 캐시 파일 수가 달라졌습니다 ({len(paths)} ≠ {cached_count}).",
            )

        # 디렉터리 mtime 만 바뀐 경우는 NTFS 접근·백신 등으로 오탐이 잦아 하드 실패하지 않는다.
        # 샘플 파일 존재 여부와 캐시 파일 수로 신선도를 판단한다.
        check_folder = (folder_path or self.state.folder_scan_folder or "").strip()
        cached_mtime = self.state.folder_scan_dir_mtime
        mtime_changed = False
        if check_folder and cached_mtime is not None:
            current_mtime = self._dir_mtime(check_folder)
            if current_mtime is not None and abs(current_mtime - cached_mtime) > 1e-6:
                mtime_changed = True
                logger.debug(
                    "폴더 스캔 캐시: 디렉터리 mtime 변경 감지(소프트). "
                    f"folder={check_folder!r}"
                )

        limit = FOLDER_SCAN_CACHE_SAMPLE_SIZE if sample_size is None else max(1, sample_size)
        sample = self._sample_cache_paths(paths, limit)
        missing = 0
        for raw in sample:
            try:
                if not Path(raw).is_file():
                    missing += 1
            except OSError:
                missing += 1

        if missing == 0:
            # mtime 만 바뀌고 샘플이 모두 있으면 통과 (신규 하위 파일 누락 가능 — best-effort)
            if mtime_changed:
                logger.info(
                    "폴더 스캔 캐시: mtime 변경이 있으나 샘플 파일은 유효 — 변환 계속"
                )
            return True, ""

        ratio = missing / len(sample)
        # 샘플의 25% 이상 없으면 신선하지 않음
        if ratio >= 0.25 or missing >= 3:
            return (
                False,
                f"스캔 이후 파일이 변경된 것으로 보입니다 (샘플 {missing}/{len(sample)}개 없음).",
            )
        return True, ""

    @staticmethod
    def _sample_cache_paths(paths: list[str], limit: int) -> list[str]:
        """앞·뒤·중간 구간에서 중복 없이 최대 limit 개 경로를 고른다."""
        n = len(paths)
        if n <= limit:
            return list(paths)

        head_n = max(1, limit // 3)
        tail_n = max(1, limit // 3)
        mid_n = max(0, limit - head_n - tail_n)

        chosen: list[str] = []
        seen: set[int] = set()

        def _take(indices: list[int]) -> None:
            for idx in indices:
                if idx in seen or idx < 0 or idx >= n:
                    continue
                seen.add(idx)
                chosen.append(paths[idx])

        _take(list(range(head_n)))
        _take(list(range(n - tail_n, n)))

        if mid_n > 0 and n > head_n + tail_n:
            mid_start = head_n
            mid_end = n - tail_n
            mid_span = mid_end - mid_start
            if mid_span > 0:
                if mid_span <= mid_n:
                    _take(list(range(mid_start, mid_end)))
                else:
                    # 균등 간격 샘플 (결정적 — 테스트 안정)
                    step = mid_span / mid_n
                    _take([mid_start + int(i * step) for i in range(mid_n)])

        # 부족하면 앞에서부터 보충
        if len(chosen) < limit:
            for idx in range(n):
                if len(chosen) >= limit:
                    break
                if idx not in seen:
                    seen.add(idx)
                    chosen.append(paths[idx])

        return chosen

    def _count_preview_convertible(self, paths: list[str]) -> int:
        allowed = {
            ext.lower()
            for ext in self.window.task_planner.preview_allowed_extensions(self.state.selected_format)
        }
        count = 0
        for path in paths:
            if Path(path).suffix.lower() in allowed:
                count += 1
        return count

    def refresh_folder_preview_count(self) -> None:
        """포맷 변경 시 캐시된 전체 스캔에서 변환 가능 수만 다시 계산."""
        if not self.state.folder_scan_ready:
            return
        self.state.scan_preview_count = self._count_preview_convertible(self.state.folder_scan_files)
        if self.state.scan_preview_count == 0:
            self.window.status_label.setText("⚠️ 현재 포맷으로 변환 가능한 파일이 없습니다")
        else:
            self.window.status_label.setText(
                f"📁 변환 가능 {self.state.scan_preview_count}개 "
                f"(스캔 전체 {len(self.state.folder_scan_files)}개)"
            )

    def select_folder(self) -> None:
        if self._input_locked():
            return
        initial = self.window.config.get("last_folder", "")
        folder = QFileDialog.getExistingDirectory(self.window, "폴더 선택", initial)
        if folder:
            self.window.folder_entry.setText(folder)
            self.window.config["last_folder"] = folder
            self.start_folder_preview_scan(folder)

    def select_output(self) -> None:
        if self._input_locked("변환 중에는 출력 폴더를 변경할 수 없습니다"):
            return
        initial = self.window.config.get("last_output", "")
        folder = QFileDialog.getExistingDirectory(self.window, "출력 폴더 선택", initial)
        if folder:
            self.window.output_entry.setText(folder)
            self.window.config["last_output"] = folder

    def browse_files(self) -> None:
        if self._input_locked():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self.window,
            "파일 선택",
            "",
            "한글 파일 (*.hwp *.hwpx);;모든 파일 (*.*)",
        )
        if files:
            self.add_files(files)

    def add_files(self, files: list[str]) -> None:
        if self._input_locked():
            return
        if not files:
            return

        requested = [canonicalize_path(p) for p in files if str(p).strip()]
        if not requested:
            return

        scan_enqueue_start = time.perf_counter()
        self.window.status_label.setText(f"📥 {len(requested)}개 경로 스캔 시작...")
        self.start_scan(
            requested,
            mode="add_files",
            include_sub=True,
            allowed_exts=set(SUPPORTED_EXTENSIONS),
        )
        if logger.isEnabledFor(logging.DEBUG):
            elapsed = time.perf_counter() - scan_enqueue_start
            logger.debug(f"파일 스캔 요청 등록: 입력={len(requested)}, 소요={elapsed:.4f}s")

    def remove_selected(self) -> None:
        if self._input_locked("변환 중에는 파일 목록을 변경할 수 없습니다"):
            return
        selected = self.window.file_table.selectedItems()
        if not selected:
            return

        rows = set(item.row() for item in selected)
        self.window.file_store.remove_rows(rows)
        for row in sorted(rows, reverse=True):
            self.window.file_table.removeRow(row)

        self.window.status_label.setText(f"선택 파일 제거됨 (총 {self.window.file_store.count}개)")
        self.update_file_count()

    def clear_all(self) -> None:
        if self._input_locked("변환 중에는 파일 목록을 변경할 수 없습니다"):
            return
        if self.window.file_store.count == 0:
            return

        reply = QMessageBox.question(
            self.window,
            "확인",
            f"{self.window.file_store.count}개 파일을 모두 제거하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.window.file_store.clear()
            self.window.file_table.setRowCount(0)
            self.window.status_label.setText("모든 파일 제거됨")
            self.update_file_count()

    def update_file_count(self) -> None:
        count = self.window.file_store.count
        self.window.file_count_label.setText(f"📄 파일: {count}개")

    def _input_locked(self, message: str = "변환 중에는 입력을 변경할 수 없습니다") -> bool:
        worker = self.state.worker
        worker_running = bool(worker and getattr(worker, "isRunning", lambda: False)())
        if self.state.is_planning:
            msg = "작업 준비 중에는 입력을 변경할 수 없습니다"
            self.window.status_label.setText(msg)
            if hasattr(self.window, "toast"):
                self.window.toast.show_message(msg, "⚠️")
            return True
        if not (self.state.is_converting or worker_running):
            return False
        self.window.status_label.setText(message)
        if hasattr(self.window, "toast"):
            self.window.toast.show_message(message, "⚠️")
        return True
