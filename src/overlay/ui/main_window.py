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

from overlay.missions import (
    EVENT_ATTACK,
    EVENT_BONUS_OBJECTIVE,
    EVENT_DROP_PODS,
    EVENT_ESCORT,
    EVENT_MAIN_OBJECTIVE,
    EVENT_WARP_IN,
    Mission,
    MissionDB,
    MissionEvent,
)
from overlay.screen_clock import ScreenClock


def _fmt_mmss(t_s: Optional[int]) -> str:
    if t_s is None:
        return "--:--"
    t = max(0, int(t_s))
    return f"{t // 60:02d}:{t % 60:02d}"


@dataclass(frozen=True)
class EventSchedule:
    events: Tuple[MissionEvent, ...]


class TimerModule(QFrame):
    """
    UI module:
    - Left icon (placeholder emoji for now)
    - Title
    - Time label (countdown to next event)
    - Optional direction arrow label (for next event, when known)
    - Horizontal fill/progress bar (NEUTRAL color for all modules)
    - Flash state: ENTIRE module box flashes accent color
    """

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

        self._arrow = QLabel("")
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setFixedWidth(18)

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
        right_top.addWidget(self._arrow, 0)
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

        self._apply_module_style(flash=False)

    def _apply_bar_style(self) -> None:
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
        if flash:
            self.setStyleSheet(
                f"""
                QFrame#timerModule {{
                    background: {self._flash_color};
                    border: 1px solid rgba(0, 0, 0, 55);
                    border-radius: 8px;
                }}
                """
            )
            self._icon.setStyleSheet("font-size: 16px; color: white;")
            self._title.setStyleSheet("font-size: 12px; color: white; font-weight: 700;")
            self._arrow.setStyleSheet("font-size: 14px; color: white; font-weight: 800;")
            self._time.setStyleSheet("font-size: 12px; color: white; font-weight: 700;")
        else:
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
            self._arrow.setStyleSheet("font-size: 14px; color: black; font-weight: 800;")
            self._time.setStyleSheet("font-size: 12px; color: black;")

    def set_time_text(self, mmss: str) -> None:
        if self._time.text() != mmss:
            self._time.setText(mmss)

    def set_arrow_text(self, arrow: str) -> None:
        if self._arrow.text() != arrow:
            self._arrow.setText(arrow)

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
    Non-cyclic schedule logic.
    After the final event, the module resets to empty and shows 00:00.
    """

    def __init__(self, ui: TimerModule, schedule: EventSchedule, *, flash_pre_s: int = 10, flash_post_s: int = 5):
        self.ui = ui
        self.schedule = schedule
        self.flash_pre_s = int(flash_pre_s)
        self.flash_post_s = int(flash_post_s)

    def _prev_next(self, t_s: int) -> Tuple[Optional[MissionEvent], Optional[MissionEvent]]:
        prev_ev: Optional[MissionEvent] = None
        next_ev: Optional[MissionEvent] = None
        for ev in self.schedule.events:
            if ev.time_s <= t_s:
                prev_ev = ev
            if ev.time_s > t_s:
                next_ev = ev
                break
        return prev_ev, next_ev

    def update(self, t_s: Optional[int]) -> None:
        if t_s is None:
            self.ui.set_time_text("--:--")
            self.ui.set_arrow_text("")
            self.ui.set_progress_ratio(0.0)
            self.ui.set_flash(False)
            return

        t = int(t_s)
        prev_ev, next_ev = self._prev_next(t)

        # No events configured at all for this module
        if not self.schedule.events:
            self.ui.set_time_text("--:--")
            self.ui.set_arrow_text("")
            self.ui.set_progress_ratio(0.0)
            self.ui.set_flash(False)
            return

        if next_ev is None:
            # Past the last scheduled event: stay reset and show 00:00 (not --:--)
            self.ui.set_time_text("00:00")
            self.ui.set_arrow_text("")
            self.ui.set_progress_ratio(0.0)
            self.ui.set_flash(False)
            return

        # Countdown to next event
        self.ui.set_time_text(_fmt_mmss(max(0, next_ev.time_s - t)))
        self.ui.set_arrow_text(next_ev.arrow or "")

        # Fill between prev and next (prev defaults to 0)
        start = 0 if prev_ev is None else prev_ev.time_s
        end = next_ev.time_s

        span = max(1, end - start)
        elapsed = max(0, min(span, t - start))
        self.ui.set_progress_ratio(elapsed / span)

        # Flash: within [-pre, +post] of prev or next
        flash = False
        pre = self.flash_pre_s
        post = self.flash_post_s

        for ev in (prev_ev, next_ev):
            if ev is None:
                continue
            if (t >= ev.time_s - pre) and (t <= ev.time_s + post):
                flash = True
                break

        self.ui.set_flash(flash)


def _load_mission_db_or_default() -> MissionDB:
    path = MissionDB.default_path()
    return MissionDB.load(path)


def _pick_mission(db: MissionDB) -> Mission:
    # TEMP: lock to Cradle of Death while we ingest data.
    # We'll add a proper mission selector once multiple missions are populated.
    return db.get("cradle_of_death")


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

        self._label_instr = QLabel("")
        self._label_instr.setWordWrap(True)
        self._label_instr.setStyleSheet("font-size: 11px; color: black;")
        self._label_instr.setVisible(False)

        self._instr_hide_timer = QTimer(self)
        self._instr_hide_timer.setSingleShot(True)
        self._instr_hide_timer.timeout.connect(lambda: self._label_instr.setVisible(False))

        flash_green = "rgba(0, 200, 80, 220)"
        flash_orange = "rgba(255, 140, 0, 220)"
        flash_red = "rgba(255, 40, 40, 220)"

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

        db = _load_mission_db_or_default()
        mission = _pick_mission(db)

        self._sched_obj_main = EventSchedule(events=mission.events_of_type(EVENT_MAIN_OBJECTIVE))
        self._sched_obj_bonus = EventSchedule(events=mission.events_of_type(EVENT_BONUS_OBJECTIVE))
        self._sched_escort = EventSchedule(events=mission.events_of_type(EVENT_ESCORT))
        self._sched_attack = EventSchedule(events=mission.events_of_type(EVENT_ATTACK))
        self._sched_warp = EventSchedule(events=mission.events_of_type(EVENT_WARP_IN))
        self._sched_drop = EventSchedule(events=mission.events_of_type(EVENT_DROP_PODS))

        self._logic_modules: List[ScheduledModule] = [
            ScheduledModule(self._mod_obj_main, self._sched_obj_main, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_obj_bonus, self._sched_obj_bonus, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_escort, self._sched_escort, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_attack, self._sched_attack, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_warp, self._sched_warp, flash_pre_s=10, flash_post_s=5),
            ScheduledModule(self._mod_drop, self._sched_drop, flash_pre_s=10, flash_post_s=5),
        ]

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
