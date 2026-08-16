from __future__ import annotations

import webbrowser
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...constants import VERSION
from ...services.update_manifest import ReleaseManifest


class UpdateDialog(QDialog):
    """업데이트 안내 및 진행 대화상자."""

    update_accepted = pyqtSignal()
    download_cancelled = pyqtSignal()
    apply_restart_requested = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget],
        manifest: ReleaseManifest,
        *,
        is_dark: bool = True,
    ) -> None:
        super().__init__(parent)
        self.manifest = manifest
        self.is_dark = is_dark
        self._is_downloading = False
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("새 버전 업데이트 안내")
        self.setFixedSize(480, 260)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 제목 라벨
        self.title_label = QLabel("🎉 새로운 HwpMate 버전이 출시되었습니다!")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(self.title_label)

        # 버전 정보 라벨
        size_mb = self.manifest.artifact_size / (1024 * 1024)
        info_text = (
            f"현재 버전: <b>v{VERSION}</b>  ➔  최신 버전: <b style='color: #4CAF50;'>v{self.manifest.version}</b>\n"
            f"다운로드 크기: <b>{size_mb:.1f} MB</b>"
        )
        self.info_label = QLabel(info_text)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        self.info_label.setStyleSheet("font-size: 13px; line-height: 1.4;")
        layout.addWidget(self.info_label)

        # 진행 상태 라벨
        self.status_label = QLabel("새 버전으로 업데이트하시겠습니까?")
        self.status_label.setStyleSheet("font-size: 12px; color: #888888;")
        layout.addWidget(self.status_label)

        # 프로그레스바 (초기 숨김)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        layout.addStretch()

        # 버튼 영역
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        self.release_notes_btn = QPushButton("릴리즈 노트")
        self.release_notes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.release_notes_btn.clicked.connect(self._open_release_notes)
        button_layout.addWidget(self.release_notes_btn)

        button_layout.addStretch()

        self.cancel_btn = QPushButton("나중에")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.action_btn = QPushButton("지금 업데이트")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setStyleSheet(
            "background-color: #0078D4; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.action_btn.clicked.connect(self._on_action_clicked)
        button_layout.addWidget(self.action_btn)

        layout.addLayout(button_layout)

    def reject(self) -> None:
        if self._is_downloading:
            self.download_cancelled.emit()
        super().reject()

    def _open_release_notes(self) -> None:
        release_url = f"https://github.com/twbeatles/HwpMate/releases/tag/v{self.manifest.version}"
        webbrowser.open(release_url)

    def _on_action_clicked(self) -> None:
        if self.action_btn.text() == "지금 업데이트":
            self.update_accepted.emit()
        elif self.action_btn.text() == "지금 재시작하여 적용":
            self.apply_restart_requested.emit()
            self.accept()

    def set_downloading(self) -> None:
        self._is_downloading = True
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setText("업데이트 파일을 다운로드하는 중입니다...")
        self.action_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("취소")

    def update_progress(self, current: int, total: int) -> None:
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            cur_mb = current / (1024 * 1024)
            tot_mb = total / (1024 * 1024)
            self.status_label.setText(f"다운로드 중... ({cur_mb:.1f} MB / {tot_mb:.1f} MB, {pct}%)")

    def set_download_complete(self) -> None:
        self._is_downloading = False
        self.progress_bar.setValue(100)
        self.status_label.setText("다운로드 및 무결성 검증 완료! 프로그램을 재시작하여 적용하세요.")
        self.action_btn.setEnabled(True)
        self.action_btn.setText("지금 재시작하여 적용")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("나중에 적용")

    def set_download_failed(self, error: str) -> None:
        self._is_downloading = False
        self.progress_bar.hide()
        self.status_label.setText(f"<font color='red'>다운로드 실패: {error}</font>")
        self.action_btn.setEnabled(True)
        self.action_btn.setText("다시 시도")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("닫기")

