from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

from .library import Library

STATE_KEY = "bisect_state"


@dataclass
class BisectState:
    suspects: list[int] = field(default_factory=list)
    cleared: list[int] = field(default_factory=list)
    out: list[int] = field(default_factory=list)
    step: int = 0
    found: int | None = None
    total: int = 0

    @property
    def active(self) -> bool:
        return bool(self.suspects) or self.found is not None

    @property
    def testing(self) -> bool:
        return self.active and self.found is None

    @property
    def suspects_in(self) -> list[int]:
        return [i for i in self.suspects if i not in self.out]

    @property
    def suspects_out(self) -> list[int]:
        return [i for i in self.suspects if i in self.out]

    @property
    def remaining_steps(self) -> int:
        n = len(self.suspects)
        return 0 if n <= 1 else math.ceil(math.log2(n))


class Bisect:
    def __init__(self, lib: Library):
        self.lib = lib
        raw = lib.db.kv_get(STATE_KEY)
        known = BisectState.__dataclass_fields__
        self.state = BisectState(**{k: v for k, v in raw.items() if k in known}) if raw else BisectState()
        self.progress = None

    def _save(self) -> None:
        self.lib.db.kv_set(STATE_KEY, asdict(self.state))

    def _items(self, ids: list[int]):
        return [it for it in (self.lib.db.get(i) for i in ids) if it is not None]

    def _sync_out(self) -> None:
        tracked = set(self.state.suspects) | set(self.state.cleared)
        self.state.out = [it.id for it in self.lib.items() if it.status == "quarantine" and it.id in tracked]

    def _move_out(self, ids: list[int]) -> None:
        try:
            self.lib.set_status(self._items(ids), "quarantine", f"충돌 테스트 {self.state.step + 1}단계: 절반 빼기",
                                self.progress)
        finally:
            self._sync_out()
            self._save()

    def _move_in(self, ids: list[int]) -> None:
        try:
            self.lib.set_status(self._items(ids), "active", f"충돌 테스트 {self.state.step + 1}단계: 절반 다시 넣기",
                                self.progress)
        finally:
            self._sync_out()
            self._save()

    def start(self, item_ids: list[int]) -> int:
        self.end()
        ids = list(item_ids)
        random.shuffle(ids)
        self.state = BisectState(suspects=ids, total=len(ids))
        half = ids[: len(ids) // 2]
        self._move_out(half)
        return len(half)

    def report(self, worked: bool) -> int:
        s = self.state
        if not s.testing:
            return 0
        inside, outside = s.suspects_in, s.suspects_out
        if worked:
            s.suspects, newly_cleared = outside, inside
        else:
            s.suspects, newly_cleared = inside, outside
        s.cleared += newly_cleared
        s.step += 1
        if len(s.suspects) <= 1:
            s.found = s.suspects[0] if s.suspects else None
            if s.found is not None and s.found in s.out:
                self._move_in([s.found])
            self._save()
            return 0
        ids = list(s.suspects)
        random.shuffle(ids)
        half = ids[: len(ids) // 2]
        if worked:
            self._move_in(half)
        else:
            self._move_out(half)
        self._save()
        return len(half)

    def end(self) -> None:
        if self.state.out:
            self.lib.set_status(self._items(self.state.out), "active", "충돌 테스트 종료: 원위치", self.progress)
        stray = [it for it in self.lib.items() if it.status == "quarantine"]
        if stray:
            self.lib.set_status(stray, "active", "충돌 테스트 종료: 원위치", self.progress)
        self.state = BisectState()
        self._save()
