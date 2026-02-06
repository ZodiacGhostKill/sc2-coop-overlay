from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
ole32 = ctypes.windll.ole32

SRCCOPY = 0x00CC0020
BI_RGB = 0

COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1


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
            hdc_mem, 0, 0, rect.w, rect.h, hdc_screen, rect.x, rect.y, SRCCOPY
        ):
            raise RuntimeError("BitBlt failed")

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = rect.w
        bmi.bmiHeader.biHeight = -rect.h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bmi.bmiHeader.biSizeImage = rect.w * rect.h * 4

        buf = (ctypes.c_ubyte * (rect.w * rect.h * 4))()
        lines = gdi32.GetDIBits(
            hdc_mem, hbmp, 0, rect.h, ctypes.byref(buf), ctypes.byref(bmi), 0
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


def _dilate_mask(mask: bytearray, w: int, h: int, iters: int = 1) -> bytearray:
    if iters <= 0:
        return mask
    cur = mask
    for _ in range(iters):
        nxt = bytearray(w * h)
        for y in range(h):
            y0 = max(0, y - 1)
            y1 = min(h - 1, y + 1)
            for x in range(w):
                x0 = max(0, x - 1)
                x1 = min(w - 1, x + 1)
                v = 0
                for yy in range(y0, y1 + 1):
                    row = yy * w
                    for xx in range(x0, x1 + 1):
                        if cur[row + xx]:
                            v = 1
                            break
                    if v:
                        break
                nxt[y * w + x] = v
        cur = nxt
    return cur


def _auto_crop_to_glyph_band(
    bgra: bytes, w: int, h: int
) -> tuple[bytes, int, int, tuple[int, int, int, int]]:
    x_min = w
    y_min = h
    x_max = -1
    y_max = -1

    idx = 0
    for y in range(h):
        for x in range(w):
            b = bgra[idx]
            g = bgra[idx + 1]
            r = bgra[idx + 2]
            idx += 4

            lum = (54 * r + 183 * g + 19 * b) // 256
            if lum >= 35 and g >= 40 and g >= r and g >= b:
                if x < x_min:
                    x_min = x
                if y < y_min:
                    y_min = y
                if x > x_max:
                    x_max = x
                if y > y_max:
                    y_max = y

    if x_max < 0 or y_max < 0:
        return bgra, w, h, (0, 0, w, h)

    PAD_X = 3
    PAD_Y = 2
    x0 = max(0, x_min - PAD_X)
    y0 = max(0, y_min - PAD_Y)
    x1 = min(w - 1, x_max + PAD_X)
    y1 = min(h - 1, y_max + PAD_Y)

    cw = (x1 - x0) + 1
    ch = (y1 - y0) + 1

    if cw < 20 or ch < 10:
        return bgra, w, h, (0, 0, w, h)

    out = bytearray(cw * ch * 4)
    for yy in range(ch):
        src_row = ((y0 + yy) * w + x0) * 4
        dst_row = yy * cw * 4
        out[dst_row : dst_row + cw * 4] = bgra[src_row : src_row + cw * 4]

    return bytes(out), cw, ch, (x0, y0, cw, ch)


def _preprocess_clock_bgra(
    bgra: bytes,
    w: int,
    h: int,
    *,
    scale: int,
    lum_threshold: int,
    g_min: int,
    dilate_iters: int,
) -> tuple[bytes, int, int]:
    if scale < 1:
        raise ValueError("scale must be >= 1")

    mask = bytearray(w * h)
    mi = 0
    for i in range(0, len(bgra), 4):
        b = int(bgra[i])
        g = int(bgra[i + 1])
        r = int(bgra[i + 2])

        lum = (54 * r + 183 * g + 19 * b) // 256
        is_digit = (lum >= lum_threshold) and (g >= g_min) and (g >= r) and (g >= b)
        mask[mi] = 1 if is_digit else 0
        mi += 1

    if dilate_iters > 0:
        mask = _dilate_mask(mask, w, h, iters=dilate_iters)

    mono = bytearray(w * h * 4)
    for p in range(w * h):
        v = 0 if mask[p] else 255
        j = p * 4
        mono[j] = v
        mono[j + 1] = v
        mono[j + 2] = v
        mono[j + 3] = 255

    if scale == 1:
        return bytes(mono), w, h

    w2 = w * scale
    h2 = h * scale
    out = bytearray(w2 * h2 * 4)

    for y in range(h):
        src_row = y * w * 4
        dy0 = y * scale
        for x in range(w):
            si = src_row + x * 4
            v = mono[si]
            dx0 = x * scale
            for yy in range(scale):
                di_row = ((dy0 + yy) * w2 + dx0) * 4
                for xx in range(scale):
                    di = di_row + xx * 4
                    out[di] = v
                    out[di + 1] = v
                    out[di + 2] = v
                    out[di + 3] = 255

    return bytes(out), w2, h2


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


Variant = Tuple[int, int, int, int]  # (scale, lum_threshold, g_min, dilate_iters)


def _variant_tag(v: Variant) -> str:
    s, lum, gmin, di = v
    return f"s{s}_lum{lum}_g{gmin}_d{di}"


@dataclass
class ScreenClock:
    rect: Rect
    game_speed_multiplier: float = 1.4
    time_offset_s: int = 1
    poll_hz: float = 10.0

    dropout_grace_s: float = 10.0
    holdover_s: float = 600.0  # 10 minutes; prevents --:-- during long OCR droughts

    max_correction_per_tick_s: int = 1

    # If worker fails to parse for this long, rebuild OCR engine.
    engine_reset_after_fail_s: float = 20.0

    _engine: Optional["OcrEngine"] = None

    _lock: threading.Lock = threading.Lock()
    _ocr_lock: threading.Lock = threading.Lock()

    _last_observed_s: Optional[int] = None
    _last_observed_wall_mono: float = 0.0
    _last_ocr_text: str = ""

    _disp_s: Optional[int] = None
    _disp_next_tick_mono: float = 0.0

    _preferred_variant: Optional[Variant] = None
    _last_parse_ok_mono: float = 0.0

    _stop_evt: threading.Event = threading.Event()
    _thread: Optional[threading.Thread] = None

    def __post_init__(self) -> None:
        if OcrEngine is None:
            raise RuntimeError(
                "Missing dependency: WinRT OCR wheels.\n"
                "Install in your venv:\n"
                "  pip install winrt-Windows.Foundation winrt-Windows.Media.Ocr "
                "winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams\n"
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

    def debug_snapshot(self) -> dict:
        raw_full = _capture_bgra(self.rect)
        ts = time.strftime("%Y%m%d_%H%M%S")
        raw_path = os.path.join(os.getcwd(), ".debug", f"clock_raw_{ts}.png")
        _write_png_from_bgra(raw_path, raw_full, self.rect.w, self.rect.h)

        raw, rw, rh, used_local = _auto_crop_to_glyph_band(raw_full, self.rect.w, self.rect.h)
        crop_path = os.path.join(os.getcwd(), ".debug", f"clock_crop_{ts}.png")
        _write_png_from_bgra(crop_path, raw, rw, rh)

        best = None
        tried = []

        with self._ocr_lock:
            for idx, (proc, pw, ph, tag) in enumerate(self._iter_preprocess_variants(raw, rw, rh)):
                proc_path = os.path.join(os.getcwd(), ".debug", f"clock_proc_{ts}_{idx}_{tag}.png")
                _write_png_from_bgra(proc_path, proc, pw, ph)
                text, parsed = self._ocr_once_locked(proc, pw, ph)
                tried.append({"tag": tag, "proc_path": proc_path, "ocr_text": text, "parsed_seconds": parsed})
                if parsed is not None and best is None:
                    best = tried[-1]

        if best is None:
            best = tried[-1] if tried else {"tag": "none", "proc_path": None, "ocr_text": "", "parsed_seconds": None}

        return {
            "rect": {"x": self.rect.x, "y": self.rect.y, "w": self.rect.w, "h": self.rect.h},
            "raw_path": raw_path,
            "crop_path": crop_path,
            "used_local_crop": {"x": used_local[0], "y": used_local[1], "w": used_local[2], "h": used_local[3]},
            "best": best,
            "tried_count": len(tried),
        }

    def display_time(self) -> tuple[Optional[int], str]:
        now_m = time.perf_counter()
        tick_period = 1.0 / max(0.01, float(self.game_speed_multiplier))

        with self._lock:
            obs_s = self._last_observed_s
            obs_m = self._last_observed_wall_mono
            disp_s = self._disp_s
            disp_next = self._disp_next_tick_mono

        if obs_s is None and disp_s is None:
            return None, "--:--"

        obs_age = (now_m - obs_m) if obs_s is not None else 999999.0

        # Holdover tick when OCR is stale.
        if obs_age > self.dropout_grace_s:
            if disp_s is None:
                return None, "--:--"
            if obs_age > (self.dropout_grace_s + self.holdover_s):
                return None, "--:--"

            if now_m >= disp_next:
                steps = int((now_m - disp_next) // tick_period) + 1
                disp_s += steps
                disp_next += steps * tick_period

            with self._lock:
                self._disp_s = disp_s
                self._disp_next_tick_mono = disp_next

            shown = max(0, disp_s + self.time_offset_s)
            return shown, f"{shown // 60:02d}:{shown % 60:02d}"

        # OCR fresh: estimate from observed.
        assert obs_s is not None
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

        shown = max(0, disp_s + self.time_offset_s)
        return shown, f"{shown // 60:02d}:{shown % 60:02d}"

    # ---------------- internal ----------------

    def _iter_preprocess_variants(self, raw_bgra: bytes, w: int, h: int):
        # Reduced search space (36 variants, not 96) for long-run stability.
        base_scales = [8, 6, 4]
        base_lum = [70, 60]
        base_gmin = [80, 60, 100]
        base_di = [0, 1]

        variants: list[Variant] = [(s, lum, gmin, di) for s in base_scales for lum in base_lum for gmin in base_gmin for di in base_di]

        # Try last-known-good variant first.
        pref = self._preferred_variant
        if pref is not None and pref in variants:
            variants.remove(pref)
            variants.insert(0, pref)

        for v in variants:
            scale, lum_th, g_min, di = v
            proc, pw, ph = _preprocess_clock_bgra(
                raw_bgra,
                w,
                h,
                scale=scale,
                lum_threshold=lum_th,
                g_min=g_min,
                dilate_iters=di,
            )
            yield proc, pw, ph, _variant_tag(v)

    def _ocr_once_locked(self, proc_bgra: bytes, pw: int, ph: int) -> tuple[str, Optional[int]]:
        sb = _bgra_to_software_bitmap(proc_bgra, pw, ph)

        import asyncio

        async def _do_ocr() -> str:
            assert self._engine is not None
            result = await self._engine.recognize_async(sb)
            return (result.text or "").strip()

        # If WinRT glitches here, let caller decide how to recover.
        text = asyncio.run(_do_ocr())

        with self._lock:
            self._last_ocr_text = text

        m = _MMSS_RE.search(text.replace("\n", " "))
        if not m:
            return text, None

        mm = int(m.group("mm"))
        ss = int(m.group("ss"))
        if 0 <= ss <= 59 and 0 <= mm <= 99:
            return text, mm * 60 + ss
        return text, None

    def _score_candidate(self, parsed_s: int, text: str, last_s: Optional[int]) -> int:
        score = 10_000
        if last_s is not None:
            diff = abs(parsed_s - last_s)
            score += max(0, 2000 - diff * 50)
        if text:
            score += 200
        if ":" in text:
            score += 50
        return score

    def _reset_engine_locked(self) -> None:
        # Must be called under _ocr_lock.
        if OcrEngine is None:
            return
        self._engine = OcrEngine.try_create_from_user_profile_languages()

    def _worker(self) -> None:
        hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        com_ok = (hr == S_OK) or (hr == S_FALSE)

        try:
            period = 1.0 / max(1.0, float(self.poll_hz))
            next_time = time.perf_counter()

            while not self._stop_evt.is_set():
                now_m = time.perf_counter()
                if now_m < next_time:
                    time.sleep(min(0.01, next_time - now_m))
                    continue
                next_time = now_m + period

                try:
                    t, used_variant = self._read_seconds_once_best()
                except Exception:
                    # If we have been failing for a while, rebuild the engine to recover.
                    if (now_m - self._last_parse_ok_mono) > self.engine_reset_after_fail_s:
                        with self._ocr_lock:
                            self._reset_engine_locked()
                            self._last_parse_ok_mono = now_m
                    continue

                if t is None:
                    if (now_m - self._last_parse_ok_mono) > self.engine_reset_after_fail_s:
                        with self._ocr_lock:
                            self._reset_engine_locked()
                            self._last_parse_ok_mono = now_m
                    continue

                with self._lock:
                    self._last_observed_s = t
                    self._last_observed_wall_mono = time.perf_counter()

                self._last_parse_ok_mono = now_m
                if used_variant is not None:
                    self._preferred_variant = used_variant
        finally:
            if com_ok:
                ole32.CoUninitialize()

    def _read_seconds_once_best(self) -> tuple[Optional[int], Optional[Variant]]:
        raw_full = _capture_bgra(self.rect)
        raw, rw, rh, _used_local = _auto_crop_to_glyph_band(raw_full, self.rect.w, self.rect.h)

        with self._lock:
            last_s = self._last_observed_s

        best_score = -1
        best_parsed: Optional[int] = None
        best_text = ""
        best_variant: Optional[Variant] = None

        # We derive the Variant from the tag format we emit.
        def tag_to_variant(tag: str) -> Optional[Variant]:
            m = re.match(r"s(\d+)_lum(\d+)_g(\d+)_d(\d+)", tag)
            if not m:
                return None
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))

        with self._ocr_lock:
            for proc, pw, ph, tag in self._iter_preprocess_variants(raw, rw, rh):
                text, parsed = self._ocr_once_locked(proc, pw, ph)
                if parsed is None:
                    if text:
                        best_text = text
                    continue

                sc = self._score_candidate(parsed, text, last_s)
                if sc > best_score:
                    best_score = sc
                    best_parsed = parsed
                    best_text = text
                    best_variant = tag_to_variant(tag)

                # Very strong match: stop early.
                if best_score >= 11_800:
                    break

        with self._lock:
            if best_text:
                self._last_ocr_text = best_text

        return best_parsed, best_variant
