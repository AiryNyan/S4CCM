from __future__ import annotations

import glob
import os
import struct
from dataclasses import dataclass

from .dbpf import Package

T_SAVE_DATA = 0x0000000D
TRACK_FRIENDSHIP = 16650
TRACK_ROMANCE = 16651


@dataclass
class Relation:
    name: str
    friendship: float | None
    romance: float | None


@dataclass
class SaveIndex:
    path: str
    names: dict[int, str]
    by_name: dict[str, list[int]]
    relations: dict[int, list[tuple[int, float | None, float | None]]]


_cache: dict[str, tuple[float, SaveIndex]] = {}


def _varint(d: bytes, p: int) -> tuple[int, int]:
    r = s = 0
    while True:
        b = d[p]
        p += 1
        r |= (b & 0x7F) << s
        if b < 0x80:
            return r, p
        s += 7


def _fields(d: bytes):
    p, n = 0, len(d)
    while p < n:
        key, p = _varint(d, p)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, p = _varint(d, p)
        elif wt == 1:
            v = struct.unpack_from("<Q", d, p)[0]
            p += 8
        elif wt == 5:
            v = struct.unpack_from("<I", d, p)[0]
            p += 4
        elif wt == 2:
            ln, p = _varint(d, p)
            v = d[p:p + ln]
            p += ln
        else:
            raise ValueError("unsupported wire type")
        yield num, wt, v


def _first(d: bytes, num: int):
    for n, _wt, v in _fields(d):
        if n == num:
            return v
    return None


def _float(raw) -> float | None:
    return round(struct.unpack("<f", struct.pack("<I", raw))[0], 1) if isinstance(raw, int) else None


def name_key(first: str, last: str) -> str:
    return (last + first).replace(" ", "")


def load_save(path: str) -> SaveIndex | None:
    mtime = os.path.getmtime(path)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with Package(path) as pk:
        entry = next((e for e in pk.entries if e.type == T_SAVE_DATA), None)
        if entry is None:
            return None
        data = pk.read(entry)
    names, by_name, relations = {}, {}, {}
    world = None
    for n, wt, v in _fields(data):
        if n == 6 and wt == 2:
            sid = first = last = None
            for f, _w, x in _fields(v):
                if f == 1:
                    sid = x
                elif f == 5:
                    first = x.decode("utf-8", "replace")
                elif f == 6:
                    last = x.decode("utf-8", "replace")
                if sid is not None and first is not None and last is not None:
                    break
            if sid is not None:
                names[sid] = f"{last or ''} {first or ''}".strip()
                by_name.setdefault(name_key(first or "", last or ""), []).append(sid)
        elif n == 2 and wt == 2:
            world = v
    service = _first(world, 8) if world else None
    rel_list = _first(service, 13) if isinstance(service, bytes) else None
    if isinstance(rel_list, bytes):
        for n, wt, v in _fields(rel_list):
            if n != 1 or wt != 2:
                continue
            a = b = None
            friend = romance = None
            for f, _w, x in _fields(v):
                if f == 1:
                    a = x
                elif f == 2:
                    b = x
                elif f == 3 and isinstance(x, bytes):
                    for g, _w2, y in _fields(x):
                        if g == 3 and isinstance(y, bytes):
                            track = dict((h, z) for h, _w3, z in _fields(y))
                            if track.get(1) == TRACK_FRIENDSHIP:
                                friend = _float(track.get(2))
                            elif track.get(1) == TRACK_ROMANCE:
                                romance = _float(track.get(2))
            if a is not None and b is not None:
                relations.setdefault(a, []).append((b, friend, romance))
                relations.setdefault(b, []).append((a, friend, romance))
    index = SaveIndex(path, names, by_name, relations)
    _cache[path] = (mtime, index)
    return index


def save_files(saves_dir: str) -> list[str]:
    files = [f for f in glob.glob(os.path.join(saves_dir, "Slot_*.save")) if os.path.isfile(f)]
    return sorted(files, key=os.path.getmtime, reverse=True)


def find_relations(saves_dir: str, first: str, last: str) -> tuple[str, list[Relation]] | None:
    key = name_key(first, last)
    for path in save_files(saves_dir):
        try:
            index = load_save(path)
        except Exception:
            continue
        if index is None or key not in index.by_name:
            continue
        sid = index.by_name[key][0]
        rels = [Relation(index.names.get(other, "(이름 없음)") or "(이름 없음)", fr, ro)
                for other, fr, ro in index.relations.get(sid, [])]
        rels.sort(key=lambda r: (r.friendship is None, -(r.friendship or 0), -(r.romance or 0)))
        return path, rels
    return None
