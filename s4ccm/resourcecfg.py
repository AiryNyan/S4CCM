from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field

MAX_DEPTH = 5


@dataclass
class Rule:
    priority: int
    folder: str
    depth: int


@dataclass
class ResourceCfg:
    rules: list[Rule] = field(default_factory=list)
    extra: list[tuple[int, str]] = field(default_factory=list)

    @classmethod
    def default(cls) -> "ResourceCfg":
        return cls([Rule(1000, "00_Overrides", 4), Rule(500, "", 4)])

    @classmethod
    def parse(cls, text: str) -> "ResourceCfg":
        cfg = cls()
        priority = 0
        groups: dict[tuple[int, str], list[int]] = {}
        order: list[tuple[int, str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = re.match(r"(?i)priority\s+(-?\d+)$", line)
            if m:
                priority = int(m.group(1))
                continue
            m = re.match(r"(?i)packedfile\s+(.+)$", line)
            if m:
                glob = m.group(1).strip().replace("/", "\\")
                gm = re.match(r"(?i)^(.*?)((?:\*\\)*)\*\.package$", glob)
                if gm:
                    folder = gm.group(1).rstrip("\\")
                    if "*" not in folder:
                        depth = gm.group(2).count("*\\")
                        key = (priority, folder)
                        if key not in groups:
                            groups[key] = []
                            order.append(key)
                        groups[key].append(depth)
                        continue
            cfg.extra.append((priority, line))
        for key in order:
            pr, folder = key
            cfg.rules.append(Rule(pr, folder, max(groups[key])))
        return cfg

    def render(self) -> str:
        out: list[str] = []
        prios = {r.priority for r in self.rules} | {p for p, _ in self.extra}
        for pr in sorted(prios, reverse=True):
            out.append(f"Priority {pr}")
            for r in (r for r in self.rules if r.priority == pr):
                prefix = r.folder.replace("/", "\\").strip("\\")
                prefix = prefix + "\\" if prefix else ""
                for d in range(min(r.depth, MAX_DEPTH) + 1):
                    out.append(f"PackedFile {prefix}" + "*\\" * d + "*.package")
            out += [line for p, line in self.extra if p == pr]
            out.append("")
        return "\n".join(out).rstrip() + "\n"


def cfg_path(mods_dir: str) -> str:
    return os.path.join(mods_dir, "Resource.cfg")


def load(mods_dir: str) -> tuple[ResourceCfg, str]:
    p = cfg_path(mods_dir)
    if not os.path.isfile(p):
        return ResourceCfg.default(), ""
    with open(p, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    return ResourceCfg.parse(text), text


def save(mods_dir: str, cfg: ResourceCfg) -> str | None:
    p = cfg_path(mods_dir)
    backup = None
    if os.path.isfile(p):
        bdir = os.path.join(os.path.dirname(mods_dir), "S4CCM_Backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, time.strftime("Resource_%Y%m%d_%H%M%S.cfg"))
        shutil.copy2(p, backup)
    with open(p, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(cfg.render())
    return backup


def read_options(sims_dir: str) -> dict[str, str]:
    p = os.path.join(sims_dir, "Options.ini")
    out: dict[str, str] = {}
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().lower()] = v.strip()
    return out


def set_option(sims_dir: str, key: str, value: str) -> bool:
    p = os.path.join(sims_dir, "Options.ini")
    if not os.path.isfile(p):
        return False
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    shutil.copy2(p, p + ".s4ccm.bak")
    found = False
    for i, line in enumerate(lines):
        if "=" in line and line.split("=", 1)[0].strip().lower() == key:
            lines[i] = f"{key} = {value}\n"
            found = True
    if not found:
        idx = next((i + 1 for i, l in enumerate(lines) if l.strip().lower() == "[options]"), len(lines))
        lines.insert(idx, f"{key} = {value}\n")
    with open(p, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True
