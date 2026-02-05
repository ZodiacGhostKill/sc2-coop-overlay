from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from overlay.screen_clock import ScreenClock


class MainWindow(QWidget):
    def __init__(self, clock: ScreenClock) -> None:
        super().__init__()
        self._clock = clock

        self.setWindowTitle("SC2 Co-op Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

        panel = QFrame(self)
        panel.setObjectName("overlayPanel")
        panel.setStyleSheet(
            """
            QFrame#overlayPanel {
                background: rgba(255, 255, 255, 140);
                border-radius: 10px;
            }
            """
        )

        self._label_time = QLabel("Game Time: --:--")
        self._label_next = QLabel("Next: (not wired yet)")
        self._label_debug = QLabel("")

        self._label_time.setStyleSheet("font-size: 20px; color: black;")
        self._label_next.setStyleSheet("font-size: 14px; color: black;")
        self._label_debug.setStyleSheet("font-size: 11px; color: black;")

        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(4)
        panel_layout.addWidget(self._label_time)
        panel_layout.addWidget(self._label_next)
        panel_layout.addWidget(self._label_debug)
        panel.setLayout(panel_layout)

        root = QVBoxLayout()
        root.setContentsMargins(6, 6, 6, 6)
        root.addWidget(panel)
        self.setLayout(root)

        # UI refresh: fast and cheap now (no OCR here)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)  # 20 FPS label refresh; smooth seconds

        self._tick()

    def _tick(self) -> None:
        _t_game_s, mmss = self._clock.display_time()
        self._label_time.setText(f"Game Time: {mmss}")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return

        if event.key() == Qt.Key.Key_F10:
            path = self._clock.dump_capture_png()
            raw = self._clock.last_raw_text().replace("\n", " ")
            self._label_debug.setText(f"F10 dump: {path} | OCR: {raw!r}")
            return

        super().keyPressEvent(event)
