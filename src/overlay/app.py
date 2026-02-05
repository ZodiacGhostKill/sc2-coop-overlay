from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication

from overlay.screen_clock import Rect, ScreenClock
from overlay.ui.main_window import MainWindow

user32 = ctypes.windll.user32

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class HotkeyFilterWin(QAbstractNativeEventFilter):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        if event_type != "windows_generic_MSG":
            return False, 0

        msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
        if msg.message == WM_HOTKEY:
            self._app.quit()
            return True, 0

        return False, 0


def main() -> int:
    app = QApplication(sys.argv)

    HOTKEY_ID = 1
    VK_Q = 0x51
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, VK_Q):
        print("WARNING: RegisterHotKey failed (Ctrl+Shift+Q).", flush=True)

    hk_filter = HotkeyFilterWin(app)
    app.installNativeEventFilter(hk_filter)

    rect = Rect(x=267, y=775, w=64, h=25)
    clock = ScreenClock(rect=rect, poll_hz=10.0)
    clock.start()

    win = MainWindow(clock=clock)
    win.resize(260, 100)
    win.move(50, 50)
    win.show()

    try:
        return app.exec()
    finally:
        clock.stop()
        user32.UnregisterHotKey(None, HOTKEY_ID)


if __name__ == "__main__":
    raise SystemExit(main())
