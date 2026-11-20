from __future__ import annotations

import hashlib
import json
import os
import re
import functools
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Callable

from send2trash import send2trash

from . import categories as cats
from . import scriptmods
from .classify import classify_package, is_hq, resource_ids, texture_size
from .config import Config, app_dir, thumbs_dir
from .db import DB, Item
from .paths import DISABLED_DIR, QUARANTINE_DIR
from .thumbs import THUMB_EXT, ThumbCache, convert_to_jpg, extract_from_package, save_user_image

ROOTS = (("Mods", "active"), (DISABLED_DIR, "disabled"), (QUARANTINE_DIR, "quarantine"))
STATUS_ROOT = {status: root for root, status in ROOTS}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
LEGACY_OVERRIDE_DIRS = ("overrides", "00_overrides")
ORGANIZE_LABEL = "자동 정리"

Progress = Callable[[int, int, str], None]


def _exclusive(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.op_lock:
            return method(self, *args, **kwargs)
    return wrapper


def fingerprint(path: str) -> str:
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return h.hexdigest()[:24]


def _token(name: str) -> str:
    return re.split(r"[_\-\s.]", name, 1)[0].lower()


def _script_groups(files: list[str]) -> tuple[list[list[str]], set[str]]:
    by_tok: dict[str, list[str]] = {}
    for f in files:
        if f.lower().endswith(".ts4script"):
            by_tok.setdefault(_token(os.path.basename(f)), []).append(f)
    groups, claimed = [], set()
    for tok, scripts in by_tok.items():
        members = sorted(scripts) + [f for f in files if not f.lower().endswith(".ts4script")
                                     and _token(os.path.basename(f)) == tok]
        groups.append(members)
        claimed.update(members)
    return groups, claimed


def _is_own_folder(files: list[str]) -> bool:
    scripts = [f for f in files if f.lower().endswith(".ts4script")]
    dirs = {os.path.dirname(f) for f in scripts}
    toks = {_token(os.path.basename(f)) for f in scripts}
    return all(_token(os.path.basename(f)) in toks for f in files
               if f.lower().endswith(".package") and os.path.dirname(f) in dirs)


def to_rel(sims_dir: str, abs_path: str) -> str:
    return os.path.relpath(abs_path, sims_dir).replace("\\", "/")


@dataclass
class MovePlan:
    item: Item
    target_rel: str


class Library:
    def __init__(self, config: Config):
        self.config = config
        self.op_lock = threading.RLock()
        self.db = DB(os.path.join(app_dir(), "s4ccm.db"))
        self.thumbcache = ThumbCache(os.path.join(config.sims_dir, "localthumbcache.package")
                                     if config.sims_dir else None)
        self._migrate_thumbs()

    def _migrate_thumbs(self) -> None:
        if self.db.kv_get("thumbs_jpg"):
            return
        converted: dict[str, str] = {}
        for name in os.listdir(thumbs_dir()):
            if name.lower().endswith(".png"):
                src = os.path.join(thumbs_dir(), name)
                dst = convert_to_jpg(src)
                if dst:
                    converted[src] = dst
        with self.db._lock:
            for src, dst in converted.items():
                self.db.conn.execute("UPDATE items SET thumb=? WHERE thumb=?", (dst, src))
                self.db.conn.execute("UPDATE notes SET user_thumb=? WHERE user_thumb=?", (dst, src))
            self.db.conn.commit()
        self.db.kv_set("thumbs_jpg", True)

    @property
    def sims_dir(self) -> str:
        return self.config.sims_dir

    def abs(self, rel: str) -> str:
        return os.path.join(self.sims_dir, rel.replace("/", os.sep))

    def folder_for(self, category: str) -> str:
        return self.config.folder_overrides.get(category) or cats.folder_of(category)

    def _discover(self) -> tuple[list[tuple[str, str]], list[tuple[str, str, list[str], str | None]]]:
        packages: list[tuple[str, str]] = []
        units: list[tuple[str, str, list[str], str | None]] = []
        for root_name, status in ROOTS:
            root = os.path.join(self.sims_dir, root_name)
            if not os.path.isdir(root):
                continue
            root_files = []
            for entry in os.scandir(root):
                if entry.is_dir():
                    files = []
                    script_depth = None
                    for dp, _dn, fn in os.walk(entry.path):
                        for n in fn:
                            p = os.path.join(dp, n)
                            files.append(p)
                            if n.lower().endswith(".ts4script"):
                                depth = os.path.relpath(dp, root).count(os.sep) + 1
                                script_depth = depth if script_depth is None else min(script_depth, depth)
                    if script_depth is not None and _is_own_folder(files):
                        warn = None if script_depth <= 1 else ".ts4script가 Mods 기준 2단계 이상 깊어 게임이 읽지 못합니다."
                        units.append((to_rel(self.sims_dir, entry.path), status,
                                      [to_rel(self.sims_dir, f) for f in files], warn))
                    elif script_depth is not None:
                        claimed: set[str] = set()
                        for dp in sorted({os.path.dirname(f) for f in files}):
                            here = [f for f in files if os.path.dirname(f) == dp]
                            groups, got = _script_groups(here)
                            claimed |= got
                            depth = os.path.relpath(dp, root).count(os.sep) + 1
                            warn = None if depth <= 1 else ".ts4script가 Mods 기준 2단계 이상 깊어 게임이 읽지 못합니다."
                            for members in groups:
                                units.append((to_rel(self.sims_dir, members[0]), status,
                                              [to_rel(self.sims_dir, m) for m in members], warn))
                        packages += [(to_rel(self.sims_dir, f), status) for f in files
                                     if f.lower().endswith(".package") and f not in claimed]
                    else:
                        packages += [(to_rel(self.sims_dir, f), status) for f in files
                                     if f.lower().endswith(".package")]
                elif entry.is_file():
                    root_files.append(entry.path)
            groups, claimed = _script_groups(root_files)
            for members in groups:
                units.append((to_rel(self.sims_dir, members[0]), status,
                              [to_rel(self.sims_dir, m) for m in members], None))
            packages += [(to_rel(self.sims_dir, f), status) for f in root_files
                         if f.lower().endswith(".package") and f not in claimed]
        return packages, units

    def _thumb_for(self, abs_path: str, fp: str, instances: list[int]) -> str | None:
        out = os.path.join(thumbs_dir(), fp + THUMB_EXT)
        if os.path.exists(out):
            return out
        return extract_from_package(abs_path, out, instances) or self.thumbcache.extract(instances, out)

    def _folder_category(self, rel: str) -> str | None:
        parts = rel.split("/")
        inner = "/".join(parts[1:-1])
        if inner.split("/")[0].lower() in LEGACY_OVERRIDE_DIRS:
            return "overrides"
        return cats.category_for_folder(inner)

    @_exclusive
    def scan(self, progress: Progress | None = None) -> dict:
        if not os.path.isdir(self.sims_dir):
            return {"new": 0, "updated": 0, "removed": 0, "total": 0}
        packages, units = self._discover()
        known = self.db.by_path()
        seen: set[str] = set()
        total = len(packages) + len(units)
        stats = {"new": 0, "updated": 0, "removed": 0, "total": total}

        for i, (rel, status, members, warn) in enumerate(units):
            seen.add(rel)
            if progress:
                progress(i, total, rel)
            abs_p = self.abs(rel)
            scripts = sorted(m for m in members if m.lower().endswith(".ts4script"))
            h = hashlib.sha1()
            for m in scripts:
                try:
                    h.update(fingerprint(self.abs(m)).encode())
                except OSError:
                    h.update(("unreadable:" + m).encode())
            fp = h.hexdigest()[:24]
            size = sum(os.path.getsize(self.abs(m)) for m in members if os.path.exists(self.abs(m)))
            thumb = None
            for m in members:
                if m.lower().endswith(IMAGE_EXT):
                    thumb = save_user_image(self.abs(m), os.path.join(thumbs_dir(), fp + THUMB_EXT))
                    if thumb:
                        break
            row = known.get(rel)
            fields = dict(fingerprint=fp, rel_path=rel, kind="script",
                          name=os.path.splitext(os.path.basename(rel))[0], category=cats.SCRIPT_MOD,
                          auto_category=cats.SCRIPT_MOD, confidence=1.0,
                          reason=f"스크립트 모드 ({len(scripts)}개 .ts4script)", size=size,
                          mtime=os.path.getmtime(abs_p), status=status, members=members,
                          meta={"warning": warn} if warn else {})
            if thumb:
                fields["thumb"] = thumb
            if row is None:
                stats["new"] += 1
            self.db.upsert(**fields)

        for j, (rel, status) in enumerate(packages):
            seen.add(rel)
            if progress and j % 5 == 0:
                progress(len(units) + j, total, rel)
            abs_p = self.abs(rel)
            try:
                st = os.stat(abs_p)
            except OSError:
                continue
            row = known.get(rel)
            if row is not None and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1 \
                    and row["status"] == status:
                meta = json.loads(row["meta"]) if row["meta"] else {}
                if "tex" not in meta or not meta.get("ids"):
                    if "tex" not in meta:
                        size = texture_size(abs_p)
                        meta.update(tex=list(size) if size else None, hq=is_hq(size))
                    self.db.set_res_ids(row["fingerprint"], resource_ids(abs_p))
                    meta["ids"] = True
                    self.db.update_item(row["id"], meta=json.dumps(meta, ensure_ascii=False))
                continue
            fp = fingerprint(abs_p)
            same = self.db.by_fingerprint(fp)
            if row is not None and row["fingerprint"] == fp:
                self.db.upsert(rel_path=rel, fingerprint=fp, kind="package", name=row["name"],
                               category=row["category"], size=st.st_size, mtime=st.st_mtime, status=status)
                continue
            c = classify_package(abs_p)
            category = c.category
            user_set = 0
            folder_cat = self._folder_category(rel)
            if folder_cat == "overrides":
                category, user_set = "overrides", 1
            prev = same[0] if same else None
            mem = None if prev else self.db.recall(fp)
            if prev and prev.user_set:
                category, user_set = prev.category, 1
            elif mem and mem["user_set"]:
                category, user_set = mem["category"], 1
            if row is not None and row["user_set"]:
                category, user_set = row["category"], 1
            thumb = self._thumb_for(abs_p, fp, c.casp_instances + c.object_instances)
            tex = texture_size(abs_p)
            self.db.set_res_ids(fp, resource_ids(abs_p))
            self.db.upsert(
                fingerprint=fp, rel_path=rel, kind="package",
                name=os.path.splitext(os.path.basename(rel))[0], category=category,
                auto_category=c.category, user_set=user_set, confidence=c.confidence, reason=c.reason,
                size=st.st_size, mtime=st.st_mtime, status=status, thumb=thumb,
                added_at=prev.added_at if prev else mem["added_at"] if mem else time.time(),
                meta={"casp_names": c.casp_names, "instances": [str(x) for x in c.casp_instances[:50]],
                      "resources": c.resource_count, "tex": list(tex) if tex else None, "hq": is_hq(tex),
                      "ids": True},
            )
            stats["new" if row is None else "updated"] += 1

        gone = [r for r in known if r not in seen]
        self.db.remember_paths(gone)
        self.db.delete_paths(gone)
        stats["removed"] = len(gone)
        self.db.commit()
        if progress:
            progress(total, total, "완료")
        return stats

    def items(self) -> list[Item]:
        return self.db.all_items()

    def _unique(self, dst: str) -> str:
        if not os.path.exists(dst):
            return dst
        base, ext = os.path.splitext(dst)
        n = 2
        while os.path.exists(f"{base} ({n}){ext}"):
            n += 1
        return f"{base} ({n}){ext}"

    def _cleanup_empty(self, start_dir: str) -> None:
        stops = {os.path.normcase(os.path.join(self.sims_dir, r)) for r, _ in ROOTS}
        d = start_dir
        while os.path.normcase(d) not in stops and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)

    @staticmethod
    def _same_path(a: str, b: str) -> bool:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    def _move_abs(self, src: str, dst: str) -> str:
        if self._same_path(src, dst):
            return src
        dst = self._unique(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        self._cleanup_empty(os.path.dirname(src))
        return dst

    def _move_item(self, item: Item, target_rel: str, moves: list[tuple[str, str]], status: str | None = None) -> None:
        src = self.abs(item.rel_path)
        dst = self._move_abs(src, self.abs(target_rel))
        moves.append((src, dst))
        new_rel = to_rel(self.sims_dir, dst)
        members = item.members
        if item.kind == "script" and members:
            if os.path.isdir(dst):
                members = [new_rel + m[len(item.rel_path):] for m in members]
            else:
                new_members = [new_rel]
                for m in members[1:]:
                    msrc = self.abs(m)
                    if os.path.exists(msrc):
                        mdst = self._move_abs(msrc, os.path.join(os.path.dirname(dst), os.path.basename(msrc)))
                        moves.append((msrc, mdst))
                        new_members.append(to_rel(self.sims_dir, mdst))
                members = new_members
        extra = {"members": members} if item.kind == "script" else {}
        if status:
            extra["status"] = status
        try:
            st = os.stat(dst)
            extra["mtime"] = st.st_mtime
        except OSError:
            pass
        self.db.rename_path(item.rel_path, new_rel, **extra)
        item.rel_path = new_rel

    def _status_rel(self, item: Item, status: str) -> str:
        parts = item.rel_path.split("/", 1)
        return STATUS_ROOT[status] + "/" + parts[1]

    @_exclusive
    def set_status(self, items: list[Item], status: str, label: str, progress: Progress | None = None) -> int:
        moves: list[tuple[str, str]] = []
        for i, it in enumerate(items):
            if progress:
                progress(i, len(items), it.rel_path)
            if it.status == status:
                continue
            self._move_item(it, self._status_rel(it, status), moves, status=status)
            it.status = status
        if moves:
            self.db.log_op(label, moves)
        return len(moves)

    def organize_plan(self, items: list[Item] | None = None) -> list[MovePlan]:
        plans = []
        for it in items if items is not None else self.items():
            if it.status != "active":
                continue
            if it.kind == "script":
                plan = self._script_plan(it)
                if plan:
                    plans.append(plan)
                continue
            target_dir = "Mods/" + self.folder_for(it.category)
            cur_dir = it.rel_path.rsplit("/", 1)[0]
            if cur_dir.lower() == target_dir.lower() or cur_dir.lower().startswith(target_dir.lower() + "/"):
                continue
            if it.category == "overrides" and self._folder_category(it.rel_path) == "overrides":
                continue
            plans.append(MovePlan(it, target_dir + "/" + it.rel_path.rsplit("/", 1)[1]))
        return plans

    @_exclusive
    def _script_plan(self, it: Item) -> MovePlan | None:
        if os.path.isdir(self.abs(it.rel_path)) or it.meta.get("warning"):
            return None
        known = scriptmods.identify(it.members)
        if known and not known.folder:
            target_dir = "Mods"
        else:
            target_dir = "Mods/" + (known.folder if known else cats.SCRIPT_FOLDER)
        cur_dir = it.rel_path.rsplit("/", 1)[0]
        if cur_dir.lower() == target_dir.lower():
            return None
        return MovePlan(it, target_dir + "/" + it.rel_path.rsplit("/", 1)[1])

    @_exclusive
    def apply_moves(self, plans: list[MovePlan], label: str, progress: Progress | None = None) -> int:
        moves: list[tuple[str, str]] = []
        for i, p in enumerate(plans):
            if progress:
                progress(i, len(plans), p.item.rel_path)
            src = self.abs(p.item.rel_path)
            if os.path.exists(src) and not self._same_path(src, self.abs(p.target_rel)):
                self._move_item(p.item, p.target_rel, moves)
        if moves:
            self.db.log_op(label, moves)
        if progress:
            progress(len(plans), len(plans), "")
        return len(moves)

    def set_category(self, item: Item, category: str, move: bool = True) -> None:
        self.db.update_item(item.id, category=category, user_set=1)
        item.category, item.user_set = category, True
        if move and item.kind == "package" and item.status == "active":
            plans = self.organize_plan([item])
            if plans:
                self.apply_moves(plans, f"분류 변경: {item.name}")

    def organize_history(self) -> tuple[int, int]:
        ops = self.db.open_ops(ORGANIZE_LABEL)
        return len(ops), sum(len(moves) for _id, _label, moves in ops)

    @_exclusive
    def undo_organize(self, progress: Progress | None = None) -> int:
        n = 0
        ops = self.db.open_ops(ORGANIZE_LABEL)
        total, done = sum(len(m) for _i, _l, m in ops), 0
        for op_id, _label, moves in ops:
            for src, dst in reversed(moves):
                if progress:
                    progress(done, total, src)
                done += 1
                if os.path.exists(dst) and not os.path.exists(src):
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    shutil.move(dst, src)
                    self._cleanup_empty(os.path.dirname(dst))
                    n += 1
            self.db.mark_undone(op_id)
        return n

    @_exclusive
    def trash(self, items: list[Item]) -> tuple[int, list[str]]:
        n, failed = 0, []
        for it in items:
            paths = [self.abs(it.rel_path)]
            if it.kind == "script" and not os.path.isdir(paths[0]):
                paths = [self.abs(m) for m in it.members]
            try:
                for p in paths:
                    if os.path.exists(p):
                        send2trash(p)
            except Exception as ex:
                failed.append(f"{it.name}: {ex}")
                continue
            self._cleanup_empty(os.path.dirname(paths[0]))
            self.db.delete_paths([it.rel_path])
            n += 1
        return n, failed

    def save_note(self, item: Item, memo: str) -> None:
        self.db.set_note(item.fingerprint, memo=memo)

    def set_user_thumb(self, item: Item, image: str | bytes) -> str | None:
        out = os.path.join(thumbs_dir(), item.fingerprint + "_user" + THUMB_EXT)
        res = save_user_image(image, out)
        if res:
            self.db.set_note(item.fingerprint, user_thumb=res)
        return res
