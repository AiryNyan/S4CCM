from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field

from send2trash import send2trash

from . import categories as cats
from . import scriptmods
from . import tray
from .classify import classify_package
from .db import Item
from .library import IMAGE_EXT, Library, fingerprint
from .scriptmods import KnownMod

ARCHIVE_EXT = (".zip", ".7z", ".rar")
MOD_EXT = (".package", ".ts4script")
SEVEN_ZIP = [r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"]

MODE_SCRIPT = "script"
MODE_CC = "cc"


@dataclass
class ImportEntry:
    src: str
    rel: str
    kind: str
    category: str = cats.UNSORTED
    reason: str = ""
    confidence: float = 0.0
    include: bool = True
    duplicate: str | None = None
    user_changed: bool = False


@dataclass
class TrayImport:
    key: str
    name: str
    kind_label: str
    entries: list[ImportEntry] = field(default_factory=list)
    include: bool = True
    exists: bool = False


@dataclass
class ImportGroup:
    name: str
    source: str
    mode: str
    entries: list[ImportEntry] = field(default_factory=list)
    mode_reason: str = ""
    known: KnownMod | None = None
    replaces: Item | None = None
    replace: bool = True
    missing_libs: list[KnownMod] = field(default_factory=list)
    folder: str = ""
    tray_items: list[TrayImport] = field(default_factory=list)

    @property
    def trays(self) -> list[ImportEntry]:
        return [e for e in self.entries if e.kind == "tray"]

    @property
    def packages(self) -> list[ImportEntry]:
        return [e for e in self.entries if e.kind == "package"]

    @property
    def scripts(self) -> list[ImportEntry]:
        return [e for e in self.entries if e.kind == "script"]

    @property
    def images(self) -> list[ImportEntry]:
        return [e for e in self.entries if e.kind == "image"]


@dataclass
class ImportPlan:
    groups: list[ImportGroup] = field(default_factory=list)
    temp_dirs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    trays_added: int = 0
    mods_added: int = 0

    def cleanup(self) -> None:
        for d in self.temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self.temp_dirs.clear()


def _kind(name: str) -> str:
    low = name.lower()
    if tray.is_tray_file(name):
        return "tray"
    if low.endswith(".package"):
        return "package"
    if low.endswith(".ts4script"):
        return "script"
    if low.endswith(IMAGE_EXT):
        return "image"
    return "other"


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return name or "mod"


def _extract_zip(path: str, out: str) -> None:
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("cp949")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            target = os.path.normpath(os.path.join(out, name))
            if not target.startswith(os.path.normpath(out) + os.sep):
                continue
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _extract(path: str, out: str) -> None:
    low = path.lower()
    if low.endswith(".zip"):
        _extract_zip(path, out)
        return
    if low.endswith(".7z"):
        try:
            import py7zr
            with py7zr.SevenZipFile(path) as z:
                z.extractall(out)
            return
        except ImportError:
            pass
    exe = next((p for p in SEVEN_ZIP if os.path.isfile(p)), None)
    if not exe:
        raise RuntimeError("RAR/7z 압축을 풀려면 7-Zip(https://7-zip.org) 설치가 필요합니다.")
    r = subprocess.run([exe, "x", "-y", f"-o{out}", path], capture_output=True, text=True,
                       creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError(f"압축 해제 실패: {r.stderr.strip() or r.stdout.strip()[-200:]}")


def _expand_nested(base: str, rounds: int = 2) -> None:
    for _ in range(rounds):
        nested = [os.path.join(dp, n) for dp, _dn, fn in os.walk(base) for n in fn
                  if n.lower().endswith(ARCHIVE_EXT)]
        if not nested:
            return
        for p in nested:
            try:
                _extract(p, os.path.splitext(p)[0])
                os.remove(p)
            except Exception:
                pass


def _collect(base: str) -> list[ImportEntry]:
    out = []
    for dp, _dn, fn in os.walk(base):
        for n in fn:
            p = os.path.join(dp, n)
            if "__MACOSX" in p:
                continue
            out.append(ImportEntry(p, os.path.relpath(p, base).replace("\\", "/"), _kind(n)))
    return out


def _group_trays(lib: Library, g: ImportGroup) -> None:
    by_key: dict[int, list[ImportEntry]] = {}
    for e in g.trays:
        by_key.setdefault(tray.group_key(tray._instance(e.src)), []).append(e)
    tray_dir = os.path.join(lib.sims_dir, "Tray")
    g.tray_items = []
    for key, entries in by_key.items():
        info = tray.TrayEntry(key=f"{key:016x}", files=[e.src for e in entries])
        meta = next((e.src for e in entries if e.src.lower().endswith(".trayitem")), None)
        if meta is None:
            continue
        try:
            tray.parse_trayitem(meta, info)
        except Exception:
            pass
        exists = any(os.path.exists(os.path.join(tray_dir, os.path.basename(e.src))) for e in entries)
        g.tray_items.append(TrayImport(info.key, info.name or os.path.basename(meta), info.kind_label,
                                       entries, include=not exists, exists=exists))


def _classify_group(lib: Library, g: ImportGroup) -> None:
    _group_trays(lib, g)
    for e in g.packages:
        c = classify_package(e.src)
        e.category, e.reason, e.confidence = c.category, c.reason, c.confidence
    for e in g.packages + g.scripts:
        dup = lib.db.by_fingerprint(fingerprint(e.src))
        if dup:
            e.duplicate = dup[0].name
            e.include = False
    if g.scripts:
        g.mode = MODE_SCRIPT
        names = [e.rel for e in g.scripts + g.packages]
        g.known = scriptmods.identify(names)
        own = _own_folder_name(g)
        g.folder = g.known.folder if g.known else own or cats.SCRIPT_FOLDER
        if g.known:
            g.mode_reason = f"{g.known.name}(으)로 인식했습니다."
        else:
            g.mode_reason = f".ts4script {len(g.scripts)}개 포함 → 스크립트 모드로 보입니다."
        g.replaces = scriptmods.find_installed(lib.items(), g.known, names)
        if g.replaces is not None:
            g.mode_reason += f" 이미 설치된 '{g.replaces.name}'의 새 버전으로 보입니다."
            for e in g.packages + g.scripts:
                if e.duplicate:
                    e.include = True
    elif g.packages:
        g.mode = MODE_CC
        kinds = {cats.label(e.category) for e in g.packages}
        g.mode_reason = f"CC {len(g.packages)}개 ({', '.join(sorted(kinds)[:4])})"
    else:
        g.mode = MODE_CC
    if g.tray_items:
        note = f"트레이 항목 {len(g.tray_items)}개 → Tray 폴더"
        g.mode_reason = f"{note} · {g.mode_reason}" if g.mode_reason else note


def _own_folder_name(g: ImportGroup) -> str | None:
    if os.path.isdir(g.source):
        return _safe_name(os.path.basename(g.source.rstrip("\\/")))
    tops = {e.rel.split("/")[0] for e in g.entries if "/" in e.rel}
    if len(tops) == 1 and all("/" in e.rel for e in g.entries):
        return _safe_name(tops.pop())
    return None


def analyze(lib: Library, paths: list[str]) -> ImportPlan:
    plan = ImportPlan()
    loose: list[str] = []
    loose_tray: list[str] = []
    for p in paths:
        try:
            if os.path.isdir(p):
                g = ImportGroup(os.path.basename(p.rstrip("\\/")), p, MODE_CC, _collect(p))
                plan.groups.append(g)
            elif p.lower().endswith(ARCHIVE_EXT):
                tmp = tempfile.mkdtemp(prefix="s4ccm_")
                plan.temp_dirs.append(tmp)
                _extract(p, tmp)
                _expand_nested(tmp)
                g = ImportGroup(os.path.splitext(os.path.basename(p))[0], p, MODE_CC, _collect(tmp))
                plan.groups.append(g)
            elif p.lower().endswith(MOD_EXT):
                loose.append(p)
            elif tray.is_tray_file(p):
                loose_tray.append(p)
            else:
                plan.errors.append(f"지원하지 않는 파일: {os.path.basename(p)}")
        except Exception as ex:
            plan.errors.append(f"{os.path.basename(p)}: {ex}")

    used: set[str] = set()
    tok = lambda f: re.split(r"[_\-\s.]", os.path.basename(f), 1)[0].lower()
    by_tok: dict[str, list[str]] = {}
    for s in [f for f in loose if f.lower().endswith(".ts4script")]:
        by_tok.setdefault(tok(s), []).append(s)
    for t, scripts in by_tok.items():
        members = scripts + [f for f in loose if f.lower().endswith(".package") and tok(f) == t]
        used.update(members)
        plan.groups.append(ImportGroup(os.path.splitext(os.path.basename(scripts[0]))[0], scripts[0], MODE_SCRIPT,
                                       [ImportEntry(m, os.path.basename(m), _kind(m)) for m in members]))
    for f in loose:
        if f not in used:
            plan.groups.append(ImportGroup(os.path.splitext(os.path.basename(f))[0], f, MODE_CC,
                                           [ImportEntry(f, os.path.basename(f), "package")]))

    if loose_tray:
        plan.groups.append(ImportGroup("트레이 파일", os.path.dirname(loose_tray[0]), MODE_CC,
                                       [ImportEntry(f, os.path.basename(f), "tray") for f in loose_tray]))

    failed: set[int] = set()
    for g in plan.groups:
        if not g.packages and not g.scripts and not g.trays:
            plan.errors.append(f"{g.name}: 모드 파일(.package/.ts4script)이나 트레이 파일이 없습니다.")
            continue
        try:
            _classify_group(lib, g)
        except Exception as ex:
            plan.errors.append(f"{g.name}: {ex}")
            failed.add(id(g))
    plan.groups = [g for g in plan.groups
                   if id(g) not in failed and (g.packages or g.scripts or g.tray_items)]
    _check_libraries(lib, plan)
    return plan


def _check_libraries(lib: Library, plan: ImportPlan) -> None:
    have = set(scriptmods.installed_mod_ids(lib.items(), active_only=True))
    have |= {g.known.id for g in plan.groups if g.known}
    for g in plan.groups:
        needed = scriptmods.required_libraries([e.src for e in g.packages])
        if g.known:
            needed.discard(g.known.id)
        g.missing_libs = [scriptmods.BY_ID[i] for i in sorted(needed - have)]


def _strip_common_root(rels: list[str]) -> list[str]:
    parts = [r.split("/") for r in rels]
    while parts and all(len(p) > 1 for p in parts) and len({p[0] for p in parts}) == 1:
        parts = [p[1:] for p in parts]
    return ["/".join(p) for p in parts]


def _remove_old_version(lib: Library, old: Item, new_entries: list[ImportEntry]) -> str | None:
    if old.status == "disabled":
        lib.set_status([old], "active", f"업데이트 준비: {old.name} 다시 활성화")
        old = lib.db.get(old.id) or old
    new_stems = {scriptmods.script_stem(e.rel) for e in new_entries if e.kind in ("package", "script")}
    for m in old.members:
        if m.lower().endswith(scriptmods.MOD_EXT) and scriptmods.script_stem(m) in new_stems:
            p = lib.abs(m)
            if os.path.exists(p):
                send2trash(p)
    old_abs = lib.abs(old.rel_path)
    if os.path.isdir(old_abs) and old.status == "active":
        return old.rel_path
    return None


def _copy_trays(lib: Library, g: ImportGroup) -> list[str]:
    tray_dir = os.path.join(lib.sims_dir, "Tray")
    out = []
    for t in g.tray_items:
        if not t.include:
            continue
        os.makedirs(tray_dir, exist_ok=True)
        for e in t.entries:
            dst = os.path.join(tray_dir, os.path.basename(e.src))
            shutil.copy2(e.src, dst)
            out.append("Tray/" + os.path.basename(e.src))
    return out


def execute(lib: Library, plan: ImportPlan) -> tuple[int, list[str]]:
    with lib.op_lock:
        return _execute(lib, plan)


def _execute(lib: Library, plan: ImportPlan) -> tuple[int, list[str]]:
    added: list[str] = []
    user_cats: dict[str, str] = {}
    thumbs: dict[str, str] = {}
    for g in plan.groups:
        trays_added = _copy_trays(lib, g)
        added += trays_added
        plan.trays_added += len({os.path.basename(a) for a in trays_added if a.lower().endswith(".trayitem")})
        if g.mode == MODE_SCRIPT:
            entries = [e for e in g.entries if e.include and e.kind != "tray"]
            folder = "Mods/" + _safe_name(g.folder) if g.folder else "Mods"
            if g.replaces is not None and g.replace:
                folder = _remove_old_version(lib, g.replaces, entries) or folder
            rels = _strip_common_root([e.rel for e in entries])
            for e, rel in zip(entries, rels):
                if e.kind == "script":
                    rel = os.path.basename(rel)
                dst = lib.abs(f"{folder}/{rel}")
                if os.path.exists(dst):
                    if e.kind not in ("package", "script"):
                        continue
                    dst = lib._unique(dst)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(e.src, dst)
                added.append(os.path.relpath(dst, lib.sims_dir).replace("\\", "/"))
        else:
            img = g.images[0].src if g.images and len(g.packages) <= 3 else None
            for e in g.packages:
                if not e.include:
                    continue
                folder = "Mods/" + lib.folder_for(e.category)
                dst = lib._unique(lib.abs(f"{folder}/{os.path.basename(e.src)}"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(e.src, dst)
                rel = os.path.relpath(dst, lib.sims_dir).replace("\\", "/")
                added.append(rel)
                if e.user_changed:
                    user_cats[rel] = e.category
                if img:
                    thumbs[rel] = img
            for e in g.scripts:
                if not e.include:
                    continue
                dst = lib._unique(lib.abs(f"Mods/{cats.SCRIPT_FOLDER}/{os.path.basename(e.src)}"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(e.src, dst)
                added.append(os.path.relpath(dst, lib.sims_dir).replace("\\", "/"))
    plan.mods_added = sum(1 for a in added if not a.startswith("Tray/"))
    if plan.mods_added:
        lib.scan()
    by_rel = {it.rel_path: it for it in lib.items()}
    for rel, cat in user_cats.items():
        if rel in by_rel:
            lib.set_category(by_rel[rel], cat, move=False)
    for rel, img in thumbs.items():
        it = by_rel.get(rel)
        if it and not it.thumb:
            lib.set_user_thumb(it, img)
    plan.cleanup()
    return len(added), added
