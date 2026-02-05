from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import ctypes
import ctypes.wintypes as wt

# --- Win32 capture (fast + no extra deps) ---

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


def _capture_bgra(rect: Rect) -> bytes:
    """
    Returns raw BGRA bytes, top-down, length = w*h*4.
    """
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise RuntimeError("GetDC(None) failed")

    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    if not hdc_mem:
        user32.ReleaseDC(None, hdc_screen)
        raise RuntimeError("CreateCompatibleDC failed")

    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, rect.w, rect.h)
    if not hbmp:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
        raise RuntimeError("CreateCompatibleBitmap failed")

    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        if not gdi32.BitBlt(
            hdc_mem,
            0,
            0,
            rect.w,
            rect.h,
            hdc_screen,
            rect.x,
            rect.y,
            SRCCOPY,
        ):
            raise RuntimeError("BitBlt failed")

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = rect.w
        bmi.bmiHeader.biHeight = -rect.h  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bmi.bmiHeader.biSizeImage = rect.w * rect.h * 4

        buf = (ctypes.c_ubyte * (rect.w * rect.h * 4))()
        lines = gdi32.GetDIBits(
            hdc_mem,
            hbmp,
            0,
            rect.h,
            ctypes.byref(buf),
            ctypes.byref(bmi),
            0,
        )
        if lines != rect.h:
            raise RuntimeError(f"GetDIBits failed (lines={lines})")

        return bytes(buf)
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def _write_png_from_bgra(path: str, bgra: bytes, w: int, h: int) -> None:
    from PySide6.QtGui import QImage

    img = QImage(bgra, w, h, QImage.Format.Format_ARGB32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not img.save(path):
        raise RuntimeError(f"Failed to save image: {path}")


def _preprocess_clock_bgra(
    bgra: bytes, w: int, h: int, *, scale: int = 4, g_threshold: int = 60
) -> tuple[bytes, int, int]:
    """
    Green digits on dark background -> black digits on white background + upscale.
    """
    if scale < 1:
        raise ValueError("scale must be >= 1")

    mono = bytearray(w * h * 4)
    for i in range(0, len(bgra), 4):
        b = bgra[i]
        g = bgra[i + 1]
        r = bgra[i + 2]
        greenish = g - max(r, b)
        is_digit = (g >= g_threshold) and (greenish >= 20)

        v = 0 if is_digit else 255
        mono[i] = v
        mono[i + 1] = v
        mono[i + 2] = v
        mono[i + 3] = 255

    if scale == 1:
        return bytes(mono), w, h

    w2 = w * scale
    h2 = h * scale
    out = bytearray(w2 * h2 * 4)

    for y in range(h):
        src_row = y * w * 4
        for x in range(w):
            si = src_row + x * 4
            v = mono[si]
            dx0 = x * scale
            dy0 = y * scale
            for yy in range(scale):
                di_row = ((dy0 + yy) * w2 + dx0) * 4
                for xx in range(scale):
                    di = di_row + xx * 4
                    out[di] = v
                    out[di + 1] = v
                    out[di + 2] = v
                    out[di + 3] = 255

    return bytes(out), w2, h2


# --- OCR via WinRT wheels ---

try:
    from winrt.windows.media.ocr import OcrEngine  # type: ignore
    from winrt.windows.graphics.imaging import (  # type: ignore
        BitmapAlphaMode,
        BitmapPixelFormat,
        SoftwareBitmap,
    )
    from winrt.windows.storage.streams import DataWriter  # type: ignore
except Exception as e:  # pragma: no cover
    OcrEngine = None  # type: ignore
    _WINRT_IMPORT_ERROR = e  # type: ignore
else:
    _WINRT_IMPORT_ERROR = None  # type: ignore


_MMSS_RE = re.compile(r"(?P<mm>\d{1,2})\s*[:]\s*(?P<ss>\d{2})")


def _bgra_to_software_bitmap(bgra: bytes, w: int, h: int) -> "SoftwareBitmap":
    sb = SoftwareBitmap(BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.IGNORE)
    writer = DataWriter()
    writer.write_bytes(bgra)
    ibuf = writer.detach_buffer()
    sb.copy_from_buffer(ibuf)
    return sb


@dataclass
class ScreenClock:
    rect: Rect

    # Brutal: game-time advances faster than real time
    game_speed_multiplier: float = 1.4

    # Hard offset applied to *all* displayed game seconds
    time_offset_s: int = 1

    # OCR poll rate (real seconds)
    poll_hz: float = 10.0

    preprocess_scale: int = 4
    preprocess_green_threshold: int = 60

    # Allow long-ish OCR droughts without blanking
    dropout_grace_s: float = 10.0

    # Display behavior: smooth “game-second” ticker + gentle correction
    max_correction_per_tick_s: int = 1

    _engine: Optional["OcrEngine"] = None

    _lock: threading.Lock = threading.Lock()

    _last_observed_s: Optional[int] = None
    _last_observed_wall_mono: float = 0.0
    _last_ocr_text: str = ""

    _disp_s: Optional[int] = None
    _disp_next_tick_mono: float = 0.0

    _stop_evt: threading.Event = threading.Event()
    _thread: Optional[threading.Thread] = None

    def __post_init__(self) -> None:
        if OcrEngine is None:
            raise RuntimeError(
                "Missing dependency: WinRT OCR wheels.\n"
                "Install in your venv:\n"
                "  pip install winrt-Windows.Foundation winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams\n"
                f"Import error: {_WINRT_IMPORT_ERROR!r}"
            )
        self._engine = OcrEngine.try_create_from_user_profile_languages()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._worker, name="ScreenClockOCR", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)

    def last_raw_text(self) -> str:
        with self._lock:
            return self._last_ocr_text

    def dump_capture_png(self) -> str:
        raw = _capture_bgra(self.rect)
        proc, pw, ph = _preprocess_clock_bgra(
            raw,
            self.rect.w,
            self.rect.h,
            scale=self.preprocess_scale,
            g_threshold=self.preprocess_green_threshold,
        )
        ts = time.strftime("%Y%m%d_%H%M%S")
        raw_path = os.path.join(os.getcwd(), ".debug", f"clock_raw_{ts}.png")
        proc_path = os.path.join(os.getcwd(), ".debug", f"clock_proc_{ts}.png")
        _write_png_from_bgra(raw_path, raw, self.rect.w, self.rect.h)
        _write_png_from_bgra(proc_path, proc, pw, ph)
        return proc_path

    def display_time(self) -> tuple[Optional[int], str]:
        now_m = time.perf_counter()
        tick_period = 1.0 / max(0.01, float(self.game_speed_multiplier))

        with self._lock:
            obs_s = self._last_observed_s
            obs_m = self._last_observed_wall_mono
            disp_s = self._disp_s
            disp_next = self._disp_next_tick_mono

        if obs_s is None:
            return None, "--:--"

        if (now_m - obs_m) > self.dropout_grace_s:
            return None, "--:--"

        est = obs_s + int((now_m - obs_m) * self.game_speed_multiplier)

        if disp_s is None:
            disp_s = est
            disp_next = now_m + tick_period
        else:
            if now_m >= disp_next:
                steps = int((now_m - disp_next) // tick_period) + 1
                disp_s += steps
                disp_next += steps * tick_period

            delta = est - disp_s
            if delta != 0:
                corr = max(-self.max_correction_per_tick_s, min(self.max_correction_per_tick_s, delta))
                disp_s += corr

        with self._lock:
            self._disp_s = disp_s
            self._disp_next_tick_mono = disp_next

        # Apply hard offset at the very end
        shown = max(0, disp_s + self.time_offset_s)
        return shown, f"{shown // 60:02d}:{shown % 60:02d}"

    # ---------------- internal ----------------

    def _worker(self) -> None:
        assert self._engine is not None

        period = 1.0 / max(1.0, float(self.poll_hz))
        next_time = time.perf_counter()

        while not self._stop_evt.is_set():
            now_m = time.perf_counter()
            if now_m < next_time:
                time.sleep(min(0.01, next_time - now_m))
                continue
            next_time = now_m + period

            try:
                t = self._read_seconds_once()
            except Exception:
                continue

            if t is None:
                continue

            with self._lock:
                self._last_observed_s = t
                self._last_observed_wall_mono = time.perf_counter()

    def _read_seconds_once(self) -> Optional[int]:
        assert self._engine is not None

        raw = _capture_bgra(self.rect)
        proc, pw, ph = _preprocess_clock_bgra(
            raw,
            self.rect.w,
            self.rect.h,
            scale=self.preprocess_scale,
            g_threshold=self.preprocess_green_threshold,
        )
        sb = _bgra_to_software_bitmap(proc, pw, ph)

        import asyncio

        async def _do_ocr() -> str:
            result = await self._engine.recognize_async(sb)
            return (result.text or "").strip()

        text = asyncio.run(_do_ocr())

        with self._lock:
            self._last_ocr_text = text

        m = _MMSS_RE.search(text.replace("\n", " "))
        if not m:
            return None

        mm = int(m.group("mm"))
        ss = int(m.group("ss"))
        if ss < 0 or ss > 59 or mm < 0 or mm > 99:
            return None
        return mm * 60 + ss
