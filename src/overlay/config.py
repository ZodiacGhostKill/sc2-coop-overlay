from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from overlay.screen_clock import Rect


@dataclass(frozen=True)
class OverlayConfig:
    clock_rect: Rect

    @staticmethod
    def default_path() -> Path:
        # Repo root / config.json (one simple source of truth)
        return Path.cwd() / "config.json"

    @staticmethod
    def load(path: Path) -> "OverlayConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        r = data["clock_rect"]
        rect = Rect(x=int(r["x"]), y=int(r["y"]), w=int(r["w"]), h=int(r["h"]))
        return OverlayConfig(clock_rect=rect)

    def save(self, path: Path) -> None:
        data = {"clock_rect": {"x": self.clock_rect.x, "y": self.clock_rect.y, "w": self.clock_rect.w, "h": self.clock_rect.h}}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
