from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        # Basic overlay: frameless + always on top
        self.setWindowTitle("SC2 Co-op Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()


        self._label_time = QLabel("Game Time: 00:00")
        self._label_next = QLabel("Next: (not wired yet)")

        self._label_time.setStyleSheet("font-size: 20px; color: white;")
        self._label_next.setStyleSheet("font-size: 14px; color: white;")

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._label_time)
        layout.addWidget(self._label_next)
        self.setLayout(layout)

        # TEMP: simulated timer so you can see updates
        self._t = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self) -> None:
        self._t += 1
        mm = self._t // 60
        ss = self._t % 60
        self._label_time.setText(f"Game Time: {mm:02d}:{ss:02d}")

        def keyPressEvent(self, event) -> None:
            if event.key() == Qt.Key.Key_Escape:
                self.close()
                return
            super().keyPressEvent(event)
