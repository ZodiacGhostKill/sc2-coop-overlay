from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication

from overlay.config import OverlayConfig
from overlay.screen_clock import Rect, ScreenClock
from overlay.ui.main_window import MainWindow

user32 = ctypes.windll.user32

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312

# Calibration / debug hotkeys (global, while overlay runs)
MOD_NONE = 0x0000
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79

HOTKEY_QUIT_ID = 1
HOTKEY_CALIBRATE_ID = 2
HOTKEY_POINT_A_ID = 3
HOTKEY_POINT_B_ID = 4
HOTKEY_DEBUG_SNAPSHOT_ID = 5


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


def _cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        raise RuntimeError("GetCursorPos failed")
    return int(pt.x), int(pt.y)


class HotkeyFilterWin(QAbstractNativeEventFilter):
    def __init__(self, app: QApplication, on_hotkey) -> None:
        super().__init__()
        self._app = app
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        if event_type != "windows_generic_MSG":
            return False, 0

        msg = ctypes.cast(int(message), ctypes.POINTER(MSG)).contents
        if msg.message == WM_HOTKEY:
            hotkey_id = int(msg.wParam)
            self._on_hotkey(hotkey_id)
            return True, 0

        return False, 0


def _load_config_or_die() -> OverlayConfig:
    path = OverlayConfig.default_path()
    if not path.exists():
        raise SystemExit(
            f"Missing config file: {path}\n"
            "Create it by copying config.example.json to config.json and filling in clock_rect,\n"
            "or use the built-in calibrator (press F7 in the running overlay).\n"
        )
    return OverlayConfig.load(path)


def _save_config_rect(rect: Rect) -> None:
    path = OverlayConfig.default_path()
    data = {"clock_rect": {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h}}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    app = QApplication(sys.argv)

    # Register global quit hotkey: Ctrl + Shift + Q
    if not user32.RegisterHotKey(None, HOTKEY_QUIT_ID, MOD_CONTROL | MOD_SHIFT, 0x51):
        print("WARNING: RegisterHotKey failed (Ctrl+Shift+Q).", flush=True)

    # Register calibration/debug hotkeys globally (works even when SC2 focused)
    user32.RegisterHotKey(None, HOTKEY_CALIBRATE_ID, MOD_NONE, VK_F7)
    user32.RegisterHotKey(None, HOTKEY_POINT_A_ID, MOD_NONE, VK_F8)
    user32.RegisterHotKey(None, HOTKEY_POINT_B_ID, MOD_NONE, VK_F9)
    user32.RegisterHotKey(None, HOTKEY_DEBUG_SNAPSHOT_ID, MOD_NONE, VK_F10)

    calibrating = {"active": False, "ax": None, "ay": None, "bx": None, "by": None}

    clock: ScreenClock | None = None
    win: MainWindow | None = None

    def stop_clock() -> None:
        nonlocal clock
        if clock is not None:
            clock.stop()
            clock = None

    def start_clock_from_config() -> None:
        nonlocal clock
        cfg = _load_config_or_die()
        clock = ScreenClock(
            rect=cfg.clock_rect,
            poll_hz=10.0,
            game_speed_multiplier=1.4,
            time_offset_s=1,
        )
        clock.start()

    def on_hotkey(hotkey_id: int) -> None:
        nonlocal win, clock

        if hotkey_id == HOTKEY_QUIT_ID:
            app.quit()
            return

        if hotkey_id == HOTKEY_DEBUG_SNAPSHOT_ID:
            if clock is None:
                print("F10: clock is None", flush=True)
                return

            info = clock.debug_snapshot()
            best = info.get("best", {})

            # New fields (auto-crop build)
            crop_path = info.get("crop_path")
            used_local_crop = info.get("used_local_crop")

            lines = [
                "F10 snapshot:",
                f"rect={info.get('rect')}",
                f"raw={info.get('raw_path')}",
            ]
            if crop_path is not None:
                lines.append(f"crop={crop_path}")
            if used_local_crop is not None:
                lines.append(f"used_local_crop={used_local_crop}")

            lines.extend(
                [
                    f"best_proc={best.get('proc_path')}",
                    f"best_tag={best.get('tag')}",
                    f"best_OCR={best.get('ocr_text')!r}",
                    f"best_parsed_seconds={best.get('parsed_seconds')}",
                    f"tried_count={info.get('tried_count')}",
                ]
            )

            print("\n  ".join(lines), flush=True)

            if win is not None:
                win.set_debug_text("F10: snapshot written to .debug (see console)")
            return

        if hotkey_id == HOTKEY_CALIBRATE_ID:
            calibrating["active"] = True
            calibrating["ax"] = calibrating["ay"] = calibrating["bx"] = calibrating["by"] = None
            if win is not None:
                win.set_debug_text("CALIBRATE: F8=set A (top-left), F9=set B (bottom-right)")
            return

        if not calibrating["active"]:
            return

        if hotkey_id == HOTKEY_POINT_A_ID:
            x, y = _cursor_pos()
            calibrating["ax"], calibrating["ay"] = x, y
            if win is not None:
                win.set_debug_text(f"CALIBRATE: A=({x},{y}) set. Now press F9 at bottom-right.")
            return

        if hotkey_id == HOTKEY_POINT_B_ID:
            x, y = _cursor_pos()
            calibrating["bx"], calibrating["by"] = x, y

            ax, ay, bx, by = calibrating["ax"], calibrating["ay"], calibrating["bx"], calibrating["by"]
            if ax is None or ay is None:
                if win is not None:
                    win.set_debug_text("CALIBRATE: Point A not set. Press F8 first.")
                return

            x0 = min(ax, bx)
            y0 = min(ay, by)
            w = abs(bx - ax)
            h = abs(by - ay)

            if w < 5 or h < 5:
                if win is not None:
                    win.set_debug_text(f"CALIBRATE: Rect too small (w={w}, h={h}). Press F7 and try again.")
                return

            # Add padding + enforce minimum size so OCR is stable.
            PAD = 6
            MIN_W = 64
            MIN_H = 25

            x0 = x0 - PAD
            y0 = y0 - PAD
            w = w + PAD * 2
            h = h + PAD * 2

            if w < MIN_W:
                extra = MIN_W - w
                x0 -= extra // 2
                w = MIN_W

            if h < MIN_H:
                extra = MIN_H - h
                y0 -= extra // 2
                h = MIN_H

            rect = Rect(x=int(x0), y=int(y0), w=int(w), h=int(h))
            _save_config_rect(rect)

            stop_clock()
            start_clock_from_config()

            calibrating["active"] = False
            msg = f"Saved config.json with clock_rect={rect}. Restarted clock."
            print(msg, flush=True)
            if win is not None:
                win.set_debug_text(msg)
            return

    hk_filter = HotkeyFilterWin(app, on_hotkey)
    app.installNativeEventFilter(hk_filter)

    start_clock_from_config()

    win = MainWindow(clock=clock)  # type: ignore[arg-type]
    win.resize(300, 110)
    win.move(50, 50)
    win.show()

    try:
        return app.exec()
    finally:
        stop_clock()
        user32.UnregisterHotKey(None, HOTKEY_QUIT_ID)
        user32.UnregisterHotKey(None, HOTKEY_CALIBRATE_ID)
        user32.UnregisterHotKey(None, HOTKEY_POINT_A_ID)
        user32.UnregisterHotKey(None, HOTKEY_POINT_B_ID)
        user32.UnregisterHotKey(None, HOTKEY_DEBUG_SNAPSHOT_ID)


if __name__ == "__main__":
    raise SystemExit(main())
