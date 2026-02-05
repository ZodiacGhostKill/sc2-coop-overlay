from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass

user32 = ctypes.windll.user32

VK_F8 = 0x77
VK_F9 = 0x78
VK_ESCAPE = 0x1B


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_pos() -> tuple[int, int]:
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        raise RuntimeError("GetCursorPos failed")
    return int(pt.x), int(pt.y)


def _key_down(vk: int) -> bool:
    # High-order bit set means the key is currently down
    return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def _make_rect(p1: tuple[int, int], p2: tuple[int, int]) -> Rect:
    x1, y1 = p1
    x2, y2 = p2
    x = min(x1, x2)
    y = min(y1, y2)
    w = abs(x2 - x1)
    h = abs(y2 - y1)
    return Rect(x=x, y=y, w=w, h=h)


def main() -> int:
    os.system("cls" if os.name == "nt" else "clear")
    print("SC2 Co-op Overlay - Clock Region Calibrator")
    print()
    print("Goal: capture the screen rectangle covering ONLY the in-game mission clock (MM:SS).")
    print("Instructions:")
    print("  1) Start SC2 and load into a mission so the MM:SS clock is visible.")
    print("  2) Move your mouse to the TOP-LEFT corner of the clock area.")
    print("  3) Press F8 to capture Point A.")
    print("  4) Move your mouse to the BOTTOM-RIGHT corner of the clock area.")
    print("  5) Press F9 to capture Point B.")
    print("  6) Press Esc to quit at any time.")
    print()
    print("Tip: include a tiny margin around the digits (a few pixels), but avoid surrounding UI.")
    print()

    p1: tuple[int, int] | None = None
    p2: tuple[int, int] | None = None

    last_print = 0.0
    f8_was_down = False
    f9_was_down = False
    esc_was_down = False

    while True:
        now = time.time()
        x, y = _cursor_pos()

        if now - last_print >= 0.10:
            sys.stdout.write(
                f"\rCursor: x={x:5d} y={y:5d}   "
                f"PointA={'set' if p1 else '---'}   "
                f"PointB={'set' if p2 else '---'}   "
                f"(F8=set A, F9=set B, Esc=quit)     "
            )
            sys.stdout.flush()
            last_print = now

        f8_down = _key_down(VK_F8)
        f9_down = _key_down(VK_F9)
        esc_down = _key_down(VK_ESCAPE)

        # Rising-edge detection (avoid repeat while holding)
        if f8_down and not f8_was_down:
            p1 = (x, y)
            print(f"\nCaptured Point A: {p1}")

        if f9_down and not f9_was_down:
            p2 = (x, y)
            print(f"\nCaptured Point B: {p2}")

        if esc_down and not esc_was_down:
            print("\nExiting.")
            return 0

        f8_was_down = f8_down
        f9_was_down = f9_down
        esc_was_down = esc_down

        if p1 and p2:
            rect = _make_rect(p1, p2)
            print()
            print("Captured rectangle (screen pixels):")
            print(json.dumps(rect.to_dict(), indent=2))
            print()
            print("Paste that JSON back to me exactly. Then we implement the real reader.")
            return 0

        time.sleep(0.01)


if __name__ == "__main__":
    raise SystemExit(main())
