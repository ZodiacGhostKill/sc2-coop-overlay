from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Stable event type strings (JSON-friendly)
EVENT_MAIN_OBJECTIVE = "main_objective"
EVENT_BONUS_OBJECTIVE = "bonus_objective"
EVENT_ESCORT = "escort"
EVENT_ATTACK = "attack"
EVENT_WARP_IN = "warp_in"
EVENT_DROP_PODS = "drop_pods"

ALL_EVENT_TYPES = {
    EVENT_MAIN_OBJECTIVE,
    EVENT_BONUS_OBJECTIVE,
    EVENT_ESCORT,
    EVENT_ATTACK,
    EVENT_WARP_IN,
    EVENT_DROP_PODS,
}


@dataclass(frozen=True)
class MissionEvent:
    type: str
    time_s: int
    arrow: Optional[str] = None  # e.g. "←", "→", "↑", "↓", "↖", etc.

    @staticmethod
    def from_dict(d: dict) -> "MissionEvent":
        t = str(d.get("type", "")).strip()
        if t not in ALL_EVENT_TYPES:
            raise ValueError(f"Unknown event type: {t!r}")

        time_s = int(d.get("time_s"))
        arrow = d.get("arrow", None)
        if arrow is not None:
            arrow = str(arrow)

        return MissionEvent(type=t, time_s=time_s, arrow=arrow)


@dataclass(frozen=True)
class Mission:
    mission_id: str
    name: str
    duration_s: int  # cycle duration; schedules wrap using this
    events: Tuple[MissionEvent, ...]

    def events_of_type(self, event_type: str) -> Tuple[MissionEvent, ...]:
        return tuple(e for e in self.events if e.type == event_type)


@dataclass(frozen=True)
class MissionDB:
    missions: Dict[str, Mission]

    @staticmethod
    def default_path() -> Path:
        # src/overlay/data/missions.json
        return Path(__file__).resolve().parent / "data" / "missions.json"

    @staticmethod
    def load(path: Path) -> "MissionDB":
        # Tolerate UTF-8 BOM and leading whitespace
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            raise ValueError(f"missions.json is empty: {path}")

        data = json.loads(raw)

        missions_raw = data.get("missions", {})
        if not isinstance(missions_raw, dict):
            raise ValueError("missions.json: expected top-level key 'missions' to be an object")

        missions: Dict[str, Mission] = {}

        for mission_id, m in missions_raw.items():
            if not isinstance(mission_id, str) or not isinstance(m, dict):
                continue

            name = str(m.get("name", mission_id))

            events_raw = m.get("events", [])
            if not isinstance(events_raw, list):
                raise ValueError(f"missions.json: mission {mission_id!r} events must be a list")

            events: List[MissionEvent] = []
            for ev in events_raw:
                if isinstance(ev, dict):
                    events.append(MissionEvent.from_dict(ev))

            events.sort(key=lambda e: e.time_s)

            # duration_s:
            # - Prefer explicit JSON duration_s
            # - Otherwise derive from the last event time + 1 (deterministic, no guessing)
            duration_s_raw = m.get("duration_s", None)
            if duration_s_raw is None:
                duration_s = (events[-1].time_s + 1) if events else 1
            else:
                duration_s = int(duration_s_raw)

            if duration_s <= 0:
                raise ValueError(f"missions.json: mission {mission_id!r} duration_s must be > 0")

            missions[mission_id] = Mission(
                mission_id=mission_id,
                name=name,
                duration_s=duration_s,
                events=tuple(events),
            )

        if not missions:
            raise ValueError("missions.json: no missions loaded")

        return MissionDB(missions=missions)

    def get(self, mission_id: str) -> Mission:
        if mission_id in self.missions:
            return self.missions[mission_id]
        if "default" in self.missions:
            return self.missions["default"]
        return next(iter(self.missions.values()))
