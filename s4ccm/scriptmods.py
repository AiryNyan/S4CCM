from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .dbpf import Package

MOD_EXT = (".package", ".ts4script")


@dataclass(frozen=True)
class KnownMod:
    id: str
    name: str
    folder: str
    script_pattern: str
    keep_note: str = ""
    is_library: bool = False
    tuning_module: str | None = None
    url: str = ""
    extra: tuple[str, ...] = field(default_factory=tuple)


KNOWN_MODS: list[KnownMod] = [
    KnownMod("mccc", "MC Command Center", "MCCC", r"^mc_cmd_center",
             keep_note="설정 파일(mc_settings.cfg 등)은 그대로 유지합니다."),
    KnownMod("wickedwhims", "WickedWhims", "WickedWhimsMod", r"^turbodriver_wickedwhims"),
    KnownMod("wonderfulwhims", "WonderfulWhims", "WonderfulWhimsMod", r"^turbodriver_wonderfulwhims"),
    KnownMod("uicheats", "UI Cheats Extension", "UICheatsExtension", r"^ui_cheats_extension"),
    KnownMod("basemental_drugs", "Basemental Drugs", "BasementalDrugs", r"^basementaldrugs|^basemental.?drugs"),
    KnownMod("basemental_gangs", "Basemental Gangs", "BasementalGangs", r"^basementalgangs|^basemental.?gangs"),
    KnownMod("sliceoflife", "Slice of Life", "SliceOfLife", r"slice.?of.?life"),
    KnownMod("meaningfulstories", "Meaningful Stories", "MeaningfulStories", r"meaningfulstories"),
    KnownMod("betterbuildbuy", "Better BuildBuy", "Tmex-BetterBuildBuy", r"^tmex-betterbuildbuy"),
    KnownMod("tool", "T.O.O.L.", "Tmex-TOOL", r"^tmex-tool"),
    KnownMod("betterexceptions", "Better Exceptions", "", r"^tmex-betterexceptions"),
    KnownMod("xmlinjector", "XML Injector", "", r"^xmlinjector", is_library=True,
             tuning_module="xml_injector"),
    KnownMod("lot51core", "Lot 51 Core Library", "", r"^lot51_core", is_library=True,
             tuning_module="lot51_core", url="https://lot51.cc/core"),
    KnownMod("s4cl", "Sims 4 Community Library", "", r"^sims4communitylib", is_library=True,
             tuning_module="sims4communitylib"),
]
BY_ID = {m.id: m for m in KNOWN_MODS}
LIB_BY_MODULE = {m.tuning_module: m for m in KNOWN_MODS if m.tuning_module}
_TUNING_RX = re.compile(rb'm="(' + b"|".join(re.escape(k.encode()) for k in LIB_BY_MODULE) + rb')[."]')


def identify(file_names: list[str]) -> KnownMod | None:
    scripts = [os.path.basename(f).lower() for f in file_names if f.lower().endswith(".ts4script")]
    for m in KNOWN_MODS:
        rx = re.compile(m.script_pattern)
        if any(rx.search(s) for s in scripts):
            return m
    return None


_VERSION_RX = re.compile(r"[_\-\s.]*v?\d+([._]\d+)*[a-z]?$", re.I)


def script_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    return _VERSION_RX.sub("", stem)


def required_libraries(package_paths: list[str]) -> set[str]:
    found: set[str] = set()
    for p in package_paths:
        try:
            with Package(p) as pk:
                for e in pk.entries[:600]:
                    if e.mem_size > 1_500_000:
                        continue
                    try:
                        data = pk.read(e)
                    except Exception:
                        continue
                    for mod in _TUNING_RX.findall(data):
                        found.add(LIB_BY_MODULE[mod.decode()].id)
        except Exception:
            continue
    return found


def installed_mod_ids(items, active_only: bool = False) -> dict[str, object]:
    out: dict[str, object] = {}
    for it in items:
        if it.kind != "script" or it.status == "quarantine" or (active_only and it.status != "active"):
            continue
        m = identify(it.members)
        if m and (m.id not in out or it.status == "active"):
            out[m.id] = it
    return out


def find_installed(items, known: KnownMod | None, script_names: list[str]):
    if known:
        hit = installed_mod_ids(items).get(known.id)
        if hit is not None:
            return hit
    stems = {script_stem(n) for n in script_names if n.lower().endswith(".ts4script")}
    for it in items:
        if it.kind == "script" and it.status != "quarantine":
            if stems & {script_stem(m) for m in it.members if m.lower().endswith(".ts4script")}:
                return it
    return None
