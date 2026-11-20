from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .classify import T_CASP, T_COBJ, T_OBJD
from .db import Item
from .dbpf import Package
from .library import Library

SAME_FILE = "same_file"
SAME_ITEM = "same_item"
ITEM_TYPES = (T_CASP, T_OBJD, T_COBJ)


@dataclass
class DupGroup:
    kind: str
    items: list[Item]
    shared: int = 0
    keep: Item | None = field(default=None)
    overlap: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def title(self) -> str:
        if self.kind == SAME_FILE:
            return f"완전히 같은 파일 {len(self.items)}개"
        return f"같은 아이템이 들어 있는 파일 {len(self.items)}개 (아이템 {self.shared}개 겹침)"


def _full_hash(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _suggest_keep(items: list[Item]) -> Item:
    return max(items, key=lambda i: (i.status == "active", i.mtime))


def _unit_content(lib: Library, it: Item) -> tuple | None:
    files = [m for m in it.members if m.lower().endswith((".ts4script", ".package"))] or [it.rel_path]
    try:
        return tuple(sorted(_full_hash(lib.abs(m)) for m in files))
    except OSError:
        return None


def find_duplicates(lib: Library, progress: Callable[[int, int, str], None] | None = None) -> list[DupGroup]:
    items = [i for i in lib.items() if i.status in ("active", "disabled")]
    groups: list[DupGroup] = []

    by_fp: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        by_fp[it.fingerprint].append(it)
    exact_ids: set[frozenset[int]] = set()
    for same in (v for v in by_fp.values() if len(v) > 1):
        if same[0].kind == "package":
            by_hash: dict[str, list[Item]] = defaultdict(list)
            for it in same:
                try:
                    by_hash[_full_hash(lib.abs(it.rel_path))].append(it)
                except OSError:
                    pass
            clusters = [v for v in by_hash.values() if len(v) > 1]
        else:
            by_content: dict[tuple, list[Item]] = defaultdict(list)
            for it in same:
                key = _unit_content(lib, it)
                if key is not None:
                    by_content[key].append(it)
            clusters = [v for v in by_content.values() if len(v) > 1]
        for c in clusters:
            groups.append(DupGroup(SAME_FILE, c, keep=_suggest_keep(c)))
            exact_ids.add(frozenset(i.id for i in c))

    packages = [i for i in items if i.kind == "package"]
    owners: dict[tuple[int, int], list[int]] = defaultdict(list)
    by_id = {i.id: i for i in packages}
    item_keys: dict[int, set] = {}
    for n, it in enumerate(packages):
        if progress and n % 10 == 0:
            progress(n, len(packages), it.name)
        try:
            with Package(lib.abs(it.rel_path)) as pk:
                keys = {(e.type, e.instance) for e in pk.by_type(*ITEM_TYPES)}
        except Exception:
            continue
        item_keys[it.id] = keys
        for k in keys:
            owners[k].append(it.id)

    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    shared_keys: dict[int, set] = defaultdict(set)
    for k, ids in owners.items():
        if len(ids) < 2:
            continue
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])
    for k, ids in owners.items():
        if len(ids) >= 2:
            shared_keys[find(ids[0])].add(k)

    clusters: dict[int, list[Item]] = defaultdict(list)
    for iid in by_id:
        if iid in parent:
            clusters[find(iid)].append(by_id[iid])
    for root, members in clusters.items():
        if len(members) < 2 or frozenset(i.id for i in members) in exact_ids:
            continue
        g = DupGroup(SAME_ITEM, members, shared=len(shared_keys[root]), keep=_suggest_keep(members))
        for m in members:
            others = set().union(*(item_keys[o.id] for o in members if o is not m))
            g.overlap[m.id] = (len(item_keys[m.id] & others), len(item_keys[m.id]))
        groups.append(g)

    if progress:
        progress(len(packages), len(packages), "완료")
    groups.sort(key=lambda g: (g.kind != SAME_FILE, -len(g.items)))
    return groups
