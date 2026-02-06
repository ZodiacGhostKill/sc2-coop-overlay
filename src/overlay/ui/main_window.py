from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from overlay.screen_clock import ScreenClock


def _fmt_mmss(t_s: Optional[int]) -> str:
    if t_s is None:
        return "--:--"
    t = max(0, int(t_s))
    return f"{t // 60:02d}:{t % 60:02d}"


@dataclass(frozen=True)
class EventSchedule:
    times_s: Tuple[int, ...]


class TimerModule(QFrame):
    """
    UI module:
    - Left icon (placeholder emoji for now)
    - Title
    - Time label (next-event countdown)
    - Horizontal fill/progress bar (NEUTRAL color for all modules)
    - Flash state: ENTIRE module box flashes accent color
    """

    # Neutral progress bar styling (same for all modules)
    _BAR_TRACK = "rgba(0, 0, 0, 18)"
    _BAR_CHUNK = "rgba(0, 0, 0, 55)"

    def __init__(self, *, icon_text: str, title: str, flash_color: str) -> None:
        super().__init__()
        self._flash_color = flash_color
        self._flash_on = False

        self.setObjectName("timerModule")

        self._icon = QLabel(icon_text)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedWidth(28)

        self._title = QLabel(title)

        self._time = QLabel("--:--")

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._apply_bar_style()

        right_top = QHBoxLayout()
        right_top.setContentsMargins(0, 0, 0, 0)
        right_top.setSpacing(6)
        right_top.addWidget(self._title, 1)
        right_top.addWidget(self._time, 0)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)
        right.addLayout(right_top)
        right.addWidget(self._bar)

        root = QHBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._icon)
        root.addLayout(right, 1)
        self.setLayout(root)

        # Apply initial (non-flashing) styling
        self._apply_module_style(flash=False)

    def _apply_bar_style(self) -> None:
        # Neutral, polished. No per-module colors here.
        self._bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: {self._BAR_TRACK};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: {self._BAR_CHUNK};
                border-radius: 4px;
            }}
            """
        )

    def _apply_module_style(self, *, flash: bool) -> None:
        """
        Flash affects the entire module container and text color.
        Progress bar remains neutral.
        """
        if flash:
            # Strong but clean: colored background with slightly higher opacity and crisp border.
            self.setStyleSheet(
                f"""
                QFrame#timerModule {{
                    background: {self._flash_color};
                    border: 1px solid rgba(0, 0, 0, 55);
                    border-radius: 8px;
                }}
                """
            )
            # White text/icon during flash for contrast.
            self._icon.setStyleSheet("font-size: 16px; color: white;")
            self._title.setStyleSheet("font-size: 12px; color: white; font-weight: 700;")
            self._time.setStyleSheet("font-size: 12px; color: white; font-weight: 700;")
        else:
            # Normal: light frosted card.
            self.setStyleSheet(
                """
                QFrame#timerModule {
                    background: rgba(255, 255, 255, 170);
                    border: 1px solid rgba(0, 0, 0, 30);
                    border-radius: 8px;
                }
                """
            )
            self._icon.setStyleSheet("font-size: 16px; color: black;")
            self._title.setStyleSheet("font-size: 12px; color: black; font-weight: 600;")
            self._time.setStyleSheet("font-size: 12px; color: black;")

    def set_time_text(self, mmss: str) -> None:
        if self._time.text() != mmss:
            self._time.setText(mmss)

    def set_progress_ratio(self, ratio_0_to_1: float) -> None:
        r = max(0.0, min(1.0, float(ratio_0_to_1)))
        v = int(r * 1000)
        if self._bar.value() != v:
            self._bar.setValue(v)

    def set_flash(self, on: bool) -> None:
        on = bool(on)
        if on == self._flash_on:
            return
        self._flash_on = on
        self._apply_module_style(flash=on)


class ScheduledModule:
    """
    Pure logic wrapper: schedule + fill + flash window computation.
    """

    def __init__(self, ui: TimerModule, schedule: EventSchedule, *, flash_pre_s: int = 10, flash_post_s: int = 5):
        self.ui = ui
        self.schedule = schedule
        self.flash_pre_s = int(flash_pre_s)
        self.flash_post_s = int(flash_post_s)

    def _prev_next(self, t_s: int) -> Tuple[Optional[int], Optional[int]]:
        prev_t = None
        next_t = None
        for ev in self.schedule.times_s:
            if ev <= t_s:
                prev_t = ev
            if ev > t_s:
                next_t = ev
                break
        return prev_t, next_t

    def update(self, t_s: Optional[int]) -> None:
        if t_s is None:
            self.ui.set_time_text("--:--")
            self.ui.set_progress_ratio(0.0)
            self.ui.set_flash(False)
            return

        t = int(t_s)
        prev_t, next_t = self._prev_next(t)

        # Display: countdown to next event (or --:-- if none)
        if next_t is None:
            self.ui.set_time_text("--:--")
        else:
            self.ui.set_time_text(_fmt_mmss(max(0, next_t - t)))

        # Fill: between prev and next. If no prev, treat as start=0.
        start = 0 if prev_t is None else prev_t
        end = next_t

        if end is None:
            self.ui.set_progress_ratio(1.0)
        else:
            span = max(1, end - start)
            elapsed = max(0, min(span, t - start))
            self.ui.set_progress_ratio(elapsed / span)

        # Flash: within [-pre, +post] of nearest event(s)
        flash = False
        pre = self.flash_pre_s
        post = self.flash_post_s
        for ev in (prev_t, next_t):
            if ev is None:
                continue
            if (t >= ev - pre) and (t <= ev + post):
                flash = True
                break

        self.ui.set_flash(flash)


class MainWindow(QWidget):
    def __init__(self, clock: ScreenClock) -> None:
        super().__init__()
        self._clock = clock

        self.setWindowTitle("SC2 Co-op Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

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
        self._label_time.setStyleSheet("font-size: 20px; color: black;")

        # Calibration/instructions: transient only.
        self._label_instr = QLabel("")
        self._label_instr.setWordWrap(True)
        self._label_instr.setStyleSheet("font-size: 11px; color: black;")
        self._label_instr.setVisible(False)

        self._instr_hide_timer = QTimer(self)
        self._instr_hide_timer.setSingleShot(True)
        self._instr_hide_timer.timeout.connect(lambda: self._label_instr.setVisible(False))

        # Flash colors (ENTIRE module box)
        flash_green = "rgba(0, 200, 80, 220)"
        flash_orange = "rgba(255, 140, 0, 220)"
        flash_red = "rgba(255, 40, 40, 220)"

        # --- Modules UI (bars are neutral; only module flashes) ---
        self._mod_obj_main = TimerModule(icon_text="🚩", title="Main Objective", flash_color=flash_green)
        self._mod_obj_bonus = TimerModule(icon_text="🏁", title="Bonus Objective", flash_color=flash_green)

        self._mod_escort = TimerModule(icon_text="🛡️⚔️", title="Escort Wave", flash_color=flash_orange)

        self._mod_attack = TimerModule(icon_text="⚔️", title="Attack Wave", flash_color=flash_red)
        self._mod_warp = TimerModule(icon_text="🌀", title="Warp-in", flash_color=flash_red)
        self._mod_drop = TimerModule(icon_text="☄️", title="Drop Pods", flash_color=flash_red)

        col_obj = QVBoxLayout()
        col_obj.setContentsMargins(0, 0, 0, 0)
        col_obj.setSpacing(6)
        col_obj.addWidget(self._mod_obj_main)
        col_obj.addWidget(self._mod_obj_bonus)
        col_obj.addStretch(1)

        col_escort = QVBoxLayout()
        col_escort.setContentsMargins(0, 0, 0, 0)
        col_escort.setSpacing(6)
        col_escort.addWidget(self._mod_escort)
        col_escort.addStretch(1)

        col_attacks = QVBoxLayout()
        col_attacks.setContentsMargins(0, 0, 0, 0)
        col_attacks.setSpacing(6)
        col_attacks.addWidget(self._mod_attack)
        col_attacks.addWidget(self._mod_warp)
        col_attacks.addWidget(self._mod_drop)
        col_attacks.addStretch(1)

        modules_row = QHBoxLayout()
        modules_row.setContentsMargins(0, 0, 0, 0)
        modules_row.setSpacing(8)
        modules_row.addLayout(col_obj, 1)
        modules_row.addLayout(col_escort, 1)
        modules_row.addLayout(col_attacks, 1)

        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)
        panel_layout.addWidget(self._label_time)
        panel_layout.addWidget(self._label_instr)
        panel_layout.addLayout(modules_row)
        panel.setLayout(panel_layout)

        root = QVBoxLayout()
        root.setContentsMargins(6, 6, 6, 6)
        root.addWidget(panel)
        self.setLayout(root)

        # --- Placeholder schedules (seconds since mission start) ---
        self._sched_obj_main = EventSchedule(times_s=(240, 480, 720))
        self._sched_obj_bonus = EventSchedule(times_s=(300, 600))

        self._sched_escort = EventSchedule(times_s=(150, 330, 510))

        self._sched_attack = EventSchedule(times_s=(120, 240, 360, 480, 600))
        self._sched_warp = EventSchedule(times_s=(180, 300, 420, 540))
        self._sched_drop = EventSchedule(times_s=(210, 390, 570))

        self._logic_modules: List[ScheduledModule] = [
            ScheduledModule(self._mod_obj_main, self._sched_obj_main, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_obj_bonus, self._sched_obj_bonus, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_escort, self._sched_escort, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_attack, self._sched_attack, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_warp, self._sched_warp, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_drop, self._sched_drop, flash_pre_s=10, flash_post_s=5),
        ]

        # Reduce UI churn: paint only on mm:ss changes.
        self._last_mmss: str = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

        self._tick()

    def _tick(self) -> None:
        t_game_s, mmss = self._clock.display_time()

        if mmss == self._last_mmss:
            return
        self._last_mmss = mmss

        self._label_time.setText(f"Game Time: {mmss}")

        for lm in self._logic_modules:
            lm.update(t_game_s)

    def set_debug_text(self, text: str) -> None:
        if not text:
            self._label_instr.setVisible(False)
            return

        self._label_instr.setText(text)
        self._label_instr.setVisible(True)

        if text.startswith("CALIBRATE:"):
            self._instr_hide_timer.stop()
            return

        self._instr_hide_timer.start(2000)
