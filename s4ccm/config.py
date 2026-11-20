from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from . import APP_NAME


def app_dir() -> str:
    base = os.environ.get("S4CCM_HOME") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def thumbs_dir() -> str:
    d = os.path.join(app_dir(), "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class Config:
    sims_dir: str = ""
    game_exe: str = ""
    clear_thumbcache_on_launch: bool = False
    theme: str = "system"
    view_mode: str = "grid"
    thumb_size: int = 128
    folder_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def mods_dir(self) -> str:
        return os.path.join(self.sims_dir, "Mods") if self.sims_dir else ""

    @staticmethod
    def path() -> str:
        return os.path.join(app_dir(), "config.json")

    @classmethod
    def load(cls) -> "Config":
        try:
            with open(cls.path(), encoding="utf-8") as f:
                data = json.load(f)
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        tmp = self.path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path())
