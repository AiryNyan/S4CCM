from __future__ import annotations

import datetime as dt
import io
import json
import os
import random
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field

from PIL import Image

from .db import Item
from .library import Library
from .thumbs import THUMB_EXT, _jpeg_with_alfa, _save

TYPE_HOUSEHOLD, TYPE_LOT, TYPE_ROOM = 1, 2, 3
TYPE_LABEL = {TYPE_HOUSEHOLD: "가족", TYPE_LOT: "집", TYPE_ROOM: "방"}
IMAGE_EXT = (".hhi", ".sgi", ".bpi", ".rmi")
DATA_EXT = (".householdbinary", ".blueprint", ".room")
IMAGE_KEY = bytes.fromhex("4125e6cd47bab21a")
IMAGE_MAGIC = bytes.fromhex("8efc24489b780e3c")
MAIN_IMAGE_EXT = (".hhi", ".bpi", ".rmi")
AGE_LABEL = {1: "아기", 2: "유아", 4: "어린이", 8: "청소년", 16: "청년", 32: "성인", 64: "노인", 128: "영아"}
GENDER_LABEL = {4096: "남성", 8192: "여성"}
SKILLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "skills.json")
_skill_table: dict | None = None


def skill_table() -> dict:
    global _skill_table
    if _skill_table is None:
        try:
            with open(SKILLS_FILE, encoding="utf-8") as f:
                _skill_table = json.load(f)
        except (OSError, ValueError):
            _skill_table = {"levels": {}, "skills": {}}
    return _skill_table


@dataclass
class TraySkill:
    skill_id: int
    points: float
    name: str = ""
    level: int = 0
    max_level: int = 0


def make_skill(skill_id: int, points: float) -> TraySkill:
    table = skill_table()
    info = table["skills"].get(str(skill_id))
    if info is None:
        return TraySkill(skill_id, points)
    steps = table["levels"].get(info["type"]) or table["levels"].get("MAJOR") or []
    level = max(1, sum(1 for t in steps if points >= t)) if steps else 0
    return TraySkill(skill_id, points, info.get("ko") or info.get("en") or info.get("tuning", ""), level, len(steps))


_NAME_RX = re.compile(r"^0x([0-9a-fA-F]+)!0x([0-9a-fA-F]+)\.(\w+)$")


@dataclass
class TraySim:
    first_name: str = ""
    last_name: str = ""
    age: int = 0
    gender: int = 0
    traits: list[str] = field(default_factory=list)
    aspiration: str = ""
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    sim_id: int = 0
    skills: list[TraySkill] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.last_name}{self.first_name}" if _is_korean(self.last_name + self.first_name) \
            else f"{self.first_name} {self.last_name}".strip()

    @property
    def summary(self) -> str:
        return " · ".join(x for x in (AGE_LABEL.get(self.age, ""), GENDER_LABEL.get(self.gender, "")) if x)


@dataclass
class TrayEntry:
    key: str
    kind: int = 0
    name: str = ""
    description: str = ""
    creator: str = ""
    saved_at: dt.datetime | None = None
    sims: list[TraySim] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    @property
    def kind_label(self) -> str:
        return TYPE_LABEL.get(self.kind, "기타")

    def images(self) -> list[str]:
        return sorted((f for f in self.files if f.lower().endswith(IMAGE_EXT)), key=os.path.getsize, reverse=True)

    def main_images(self) -> list[str]:
        return [f for f in self.files if f.lower().endswith(MAIN_IMAGE_EXT)]

    @property
    def export_name(self) -> str:
        if self.kind == TYPE_HOUSEHOLD and len(self.sims) == 1 and self.sims[0].full_name:
            return self.sims[0].full_name
        return self.name or self.key

    def data_files(self) -> list[str]:
        return [f for f in self.files if f.lower().endswith(DATA_EXT)]


def _is_korean(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s)


def _varint(d: bytes, p: int) -> tuple[int, int]:
    r = s = 0
    while True:
        b = d[p]
        p += 1
        r |= (b & 0x7F) << s
        if b < 0x80:
            return r, p
        s += 7
        if s > 63:
            raise ValueError("varint too long")


def _fields(d: bytes) -> list[tuple[int, int, object]]:
    p, out = 0, []
    while p < len(d):
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
            n, p = _varint(d, p)
            v = d[p:p + n]
            p += n
        else:
            raise ValueError("unsupported wire type")
        if p > len(d):
            raise ValueError("truncated")
        out.append((num, wt, v))
    return out


def _text(v) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else ""


def _first(fields, num, default=None):
    return next((v for n, _wt, v in fields if n == num), default)


def _named(blob: bytes) -> str:
    try:
        return _text(_first(_fields(blob), 2, b""))
    except (ValueError, IndexError, struct.error):
        return ""


_PREFERENCE_RX = re.compile(r"\s*(을|를)?\s*(좋아함|싫어함)$")


def _preference(blob: bytes) -> str:
    try:
        f = _fields(blob)
    except (ValueError, IndexError, struct.error):
        return ""
    subject = _text(_first(f, 4, b"")).strip()
    return subject or _PREFERENCE_RX.sub("", _text(_first(f, 2, b""))).strip()


def _parse_sim(blob: bytes) -> TraySim:
    f = _fields(blob)
    sim = TraySim(first_name=_text(_first(f, 3, b"")), last_name=_text(_first(f, 4, b"")),
                  gender=_first(f, 6, 0) or 0, age=_first(f, 9, 0) or 0, sim_id=_first(f, 5, 0) or 0)
    for num, wt, v in f:
        if wt != 2:
            continue
        if num == 10:
            sim.traits.append(_named(v))
        elif num == 11:
            sim.aspiration = _named(v)
        elif num == 19:
            sim.likes.append(_preference(v))
        elif num == 21:
            sim.dislikes.append(_preference(v))
    sim.traits = [t for t in sim.traits if t]
    sim.likes = [t for t in sim.likes if t]
    sim.dislikes = [t for t in sim.dislikes if t]
    return sim


def parse_trayitem(path: str, entry: TrayEntry) -> None:
    data = open(path, "rb").read()
    f = _fields(data[8:])
    entry.kind = _first(f, 2, 0) or 0
    entry.name = _text(_first(f, 4, b""))
    entry.description = _text(_first(f, 5, b""))
    entry.creator = _text(_first(f, 7, b""))
    stamp = _first(f, 11)
    if isinstance(stamp, int) and stamp > 0:
        try:
            entry.saved_at = dt.datetime(1, 1, 1) + dt.timedelta(seconds=stamp)
        except OverflowError:
            pass
    specific = _first(f, 10)
    if entry.kind == TYPE_HOUSEHOLD and isinstance(specific, bytes):
        household = _first(_fields(specific), 2)
        if isinstance(household, bytes):
            entry.sims = [_parse_sim(v) for n, wt, v in _fields(household) if n == 2 and wt == 2]


def _household_sims(path: str) -> list[list]:
    data = open(path, "rb").read()
    if len(data) < 18 or data[16] != 0x0A:
        return []
    n, p = _varint(data, 17)
    household = data[p:p + n]
    return [_fields(v) for n, wt, v in _fields(household) if n == 6 and wt == 2]


def _sim_skills(sim_fields) -> list[TraySkill]:
    attrs = _first(sim_fields, 30)
    if not isinstance(attrs, bytes):
        return []
    tracker = _first(_fields(attrs), 13)
    if not isinstance(tracker, bytes):
        return []
    out = []
    for n, wt, v in _fields(tracker):
        if n != 1 or wt != 2:
            continue
        stat = _fields(v)
        sid = _first(stat, 1, 0)
        raw = _first(stat, 2, 0)
        points = struct.unpack("<f", struct.pack("<I", raw))[0] if isinstance(raw, int) else 0.0
        out.append(make_skill(sid, points))
    out.sort(key=lambda s: (not s.name, -s.level, s.name))
    return out


def load_skills(entry: TrayEntry) -> None:
    binary = next((f for f in entry.files if f.lower().endswith(".householdbinary")), None)
    if binary is None or not entry.sims:
        return
    try:
        sims = _household_sims(binary)
    except (OSError, ValueError, IndexError, struct.error):
        return
    by_id = {_first(s, 1, 0): s for s in sims}
    for i, sim in enumerate(entry.sims):
        src = by_id.get(sim.sim_id) or (sims[i] if i < len(sims) else None)
        if src is not None:
            try:
                sim.skills = _sim_skills(src)
            except (ValueError, IndexError, struct.error):
                sim.skills = []


def group_key(instance: int) -> int:
    return instance & 0x00FFFFFFFFFFFFF0


def load_tray(tray_dir: str) -> list[TrayEntry]:
    groups: dict[int, TrayEntry] = {}
    if not os.path.isdir(tray_dir):
        return []
    for name in os.listdir(tray_dir):
        m = _NAME_RX.match(name)
        if not m:
            continue
        key = group_key(int(m.group(2), 16))
        entry = groups.setdefault(key, TrayEntry(key=f"{key:016x}"))
        entry.files.append(os.path.join(tray_dir, name))
    out = []
    for entry in groups.values():
        meta = next((f for f in entry.files if f.lower().endswith(".trayitem")), None)
        if meta is None:
            continue
        try:
            parse_trayitem(meta, entry)
        except (OSError, ValueError, IndexError, struct.error):
            entry.name = entry.name or os.path.basename(meta)
        load_skills(entry)
        out.append(entry)
    out.sort(key=lambda e: e.saved_at or dt.datetime.min, reverse=True)
    return out


def decode_image(path: str) -> Image.Image | None:
    data = open(path, "rb").read()
    if data[:3] == b"\xff\xd8\xff":
        payload = data
    elif len(data) > 24:
        n = struct.unpack_from("<I", data, 0)[0]
        raw = data[24:24 + n]
        payload = raw if raw[:3] == b"\xff\xd8\xff" else bytes(b ^ IMAGE_KEY[i % 8] for i, b in enumerate(raw))
    else:
        return None
    try:
        return _jpeg_with_alfa(payload)
    except Exception:
        try:
            img = Image.open(io.BytesIO(payload))
            img.load()
            return img
        except Exception:
            return None


def encode_image(img: Image.Image, size: tuple[int, int]) -> bytes:
    img = img.convert("RGBA")
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    img = img.resize((max(tw, round(img.width * scale)), max(th, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - tw) // 2, (img.height - th) // 2
    img = img.crop((left, top, left + tw, top + th))
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.getchannel("A"))
    buf = io.BytesIO()
    flat.save(buf, "JPEG", quality=92)
    payload = bytes(b ^ IMAGE_KEY[i % 8] for i, b in enumerate(buf.getvalue()))
    return struct.pack("<II", len(payload), 0) + IMAGE_MAGIC + struct.pack("<II", 2, 0) + payload


GROUP_MASK = 0x00FFFFFFFFFFFFF0
TRAY_EXT = IMAGE_EXT + DATA_EXT + (".trayitem",)
EPOCH = dt.datetime(1, 1, 1)


def is_tray_file(name: str) -> bool:
    return bool(_NAME_RX.match(os.path.basename(name))) and name.lower().endswith(TRAY_EXT)


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _encode(fields) -> bytes:
    out = bytearray()
    for num, wt, v in fields:
        out += _enc_varint((num << 3) | wt)
        if wt == 0:
            out += _enc_varint(v)
        elif wt == 1:
            out += struct.pack("<Q", v)
        elif wt == 5:
            out += struct.pack("<I", v)
        else:
            out += _enc_varint(len(v)) + v
    return bytes(out)


SIM_DROP = {15, 20, 44, 46, 68, 72}
GAMEPLAY_DROP = {2, 7, 11, 12, 22, 27}
ATTR_DROP = {1, 6, 12, 13, 14, 15, 16, 18, 23, 26, 30, 31, 36}
HOUSEHOLD_DROP = {12}
CLEARED_LABELS = ("스킬", "직업", "대학 학위", "열망·업적 진행도", "인생 이정표", "만족 포인트와 바람",
                  "특전(포인트로 산 능력)", "수집품", "해금한 레시피·곡 등", "로열티 수입", "조직·동아리 활동",
                  "가족 레시피", "노트", "개인·가족 인벤토리", "가족·연인 관계")
KEPT_LABELS = ("이름", "나이", "성별", "외형과 의상", "특성", "열망 종류", "좋아함·싫어함", "초자연 종류")


def _drop(blob: bytes, fields: set[int]) -> tuple[bytes, int]:
    parsed = _fields(blob)
    kept = [f for f in parsed if f[0] not in fields]
    return _encode(kept), len(parsed) - len(kept)


def _clean_sim(sim_blob: bytes) -> tuple[bytes, int]:
    removed = 0
    out = []
    for num, wt, v in _fields(sim_blob):
        if num in SIM_DROP:
            removed += 1
            continue
        if num == 30 and wt == 2:
            v, n = _drop(v, ATTR_DROP)
            removed += n
        elif num == 47 and wt == 2:
            v, n = _drop(v, GAMEPLAY_DROP)
            removed += n
        out.append((num, wt, v))
    return _encode(out), removed


def _rewrite_household(data: bytes, clean: bool, old_name: str, new_name: str,
                       description: str | None = None) -> tuple[bytes, int]:
    h0, _total, h2, msglen = struct.unpack_from("<IIII", data, 0)
    msg = data[16:16 + msglen]
    tail = data[16 + msglen:]
    removed = 0
    top = []
    for num, wt, v in _fields(msg):
        if num == 1 and wt == 2:
            household = []
            for hn, hwt, hv in _fields(v):
                if hn == 6 and hwt == 2 and clean:
                    hv, n = _clean_sim(hv)
                    removed += n
                elif hn in HOUSEHOLD_DROP and clean:
                    removed += 1
                    continue
                elif hn == 3 and hwt == 2 and new_name and _text(hv) == old_name:
                    hv = new_name.encode("utf-8")
                elif hn == 11 and hwt == 2 and description is not None:
                    hv = description.encode("utf-8")
                household.append((hn, hwt, hv))
            if description is not None and not any(f[0] == 11 for f in household):
                household.append((11, 2, description.encode("utf-8")))
            v = _encode(household)
        top.append((num, wt, v))
    new_msg = _encode(top)
    head = struct.pack("<IIII", h0, 16 + len(new_msg) + len(tail) - 4, h2, len(new_msg))
    return head + new_msg + tail, removed


def _rewrite_trayitem(data: bytes, new_name: str, description: str | None = None) -> bytes:
    h0 = struct.unpack_from("<I", data, 0)[0]
    out = []
    for num, wt, v in _fields(data[8:]):
        if num == 4 and wt == 2 and new_name:
            v = new_name.encode("utf-8")
        elif num == 5 and wt == 2 and description is not None:
            v = description.encode("utf-8")
        elif num == 11 and wt == 0:
            v = int((dt.datetime.now() - EPOCH).total_seconds())
        out.append((num, wt, v))
    if description is not None and not any(f[0] == 5 for f in out):
        at = next((i + 1 for i, f in enumerate(out) if f[0] == 4), len(out))
        out.insert(at, (5, 2, description.encode("utf-8")))
    payload = _encode(out)
    return struct.pack("<II", h0, len(payload)) + payload


def _instance(path: str) -> int:
    m = _NAME_RX.match(os.path.basename(path))
    return int(m.group(2), 16) if m else 0


def _related_ids(entry: TrayEntry, group: int) -> set[int]:
    ids = {_instance(f) for f in entry.files}
    for path in entry.files:
        if path.lower().endswith(IMAGE_EXT):
            continue
        data = open(path, "rb").read()
        view = memoryview(data)
        for start in range(8):
            usable = (len(data) - start) // 8 * 8
            if usable > 0:
                ids.update(v for (v,) in struct.iter_unpack("<Q", view[start:start + usable])
                           if v & GROUP_MASK == group)
    return {i for i in ids if i & GROUP_MASK == group}


def _new_group(group: int, tray_dir: str) -> int:
    taken = {group_key(_instance(n)) for n in os.listdir(tray_dir) if _NAME_RX.match(n)}
    while True:
        g = (group & ~0xFFFFFFF0) | (random.getrandbits(28) << 4)
        if g != group and g not in taken:
            return g


def _swap_ids(data: bytes, mapping: dict[int, int]) -> bytes:
    for old, new in mapping.items():
        data = data.replace(struct.pack("<Q", old), struct.pack("<Q", new))
        a, b = _enc_varint(old), _enc_varint(new)
        if len(a) != len(b):
            raise ValueError("id length changed")
        data = data.replace(a, b)
    return data


@dataclass
class SaveResult:
    key: str
    files: list[str]
    cleared: int = 0
    thumbs: int = 0


def save_copy(entry: TrayEntry, tray_dir: str, new_name: str = "", thumb_image: str | None = None,
              look_only: bool = False, description: str | None = None) -> SaveResult:
    group = int(entry.key, 16)
    new_group = _new_group(group, tray_dir)
    mapping = {i: (i & ~GROUP_MASK) | new_group for i in _related_ids(entry, group)}
    src_img = None
    if thumb_image:
        src_img = Image.open(thumb_image)
        src_img.load()
    result = SaveResult(f"{new_group:016x}", [])
    written: list[str] = []
    try:
        planned = []
        for path in entry.files:
            m = _NAME_RX.match(os.path.basename(path))
            low = path.lower()
            data = open(path, "rb").read()
            if low.endswith(MAIN_IMAGE_EXT) and src_img is not None:
                old = decode_image(path)
                data = encode_image(src_img, old.size if old is not None else (591, 394))
                result.thumbs += 1
            elif not low.endswith(IMAGE_EXT):
                data = _swap_ids(data, mapping)
                if low.endswith(".trayitem"):
                    data = _rewrite_trayitem(data, new_name, description)
                elif low.endswith(".householdbinary"):
                    data, n = _rewrite_household(data, look_only, entry.name, new_name, description)
                    result.cleared += n
            inst = mapping.get(int(m.group(2), 16), int(m.group(2), 16))
            name = f"0x{m.group(1)}!0x{inst:016x}.{m.group(3)}"
            planned.append((os.path.join(tray_dir, name), data))
        for dst, data in planned:
            if os.path.exists(dst):
                raise FileExistsError(dst)
            tmp = dst + ".s4ccm_tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            written.append(tmp)
        for dst, _data in planned:
            os.replace(dst + ".s4ccm_tmp", dst)
            written.remove(dst + ".s4ccm_tmp")
            written.append(dst)
            result.files.append(dst)
    except Exception:
        for f in written:
            try:
                os.remove(f)
            except OSError:
                pass
        raise
    return result


def delete_entry(entry: TrayEntry) -> int:
    from send2trash import send2trash
    done = 0
    for path in entry.files:
        if os.path.exists(path):
            send2trash(path)
            done += 1
    return done


def _safe(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    return name or "tray"


EXPORT_README = """이 압축 파일은 S4CCM으로 내보낸 심즈 4 트레이 항목입니다.

[S4CCM이 있다면]
압축 파일을 그대로 S4CCM 창에 끌어다 놓으세요. 트레이 파일과 CC를 알아서 나눠 넣습니다.

[직접 넣는다면]
· Tray 폴더 안의 파일 → 문서\\Electronic Arts\\The Sims 4\\Tray
· Mods 폴더 안의 폴더 → 문서\\Electronic Arts\\The Sims 4\\Mods
그 다음 게임을 켜고 라이브러리에서 찾으면 됩니다.
"""


def export_zip(entry: TrayEntry, out_dir: str, cc: list[Item] | None, lib: Library) -> str:
    title = _safe(entry.export_name)
    base = os.path.join(out_dir, title)
    path, n = base + ".zip", 2
    while os.path.exists(path):
        path, n = f"{base} ({n}).zip", n + 1
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("읽어주세요.txt", EXPORT_README.replace("\n", "\r\n").encode("utf-8-sig"))
        for f in entry.files:
            z.write(f, "Tray/" + os.path.basename(f))
        for it in cc or []:
            src = lib.abs(it.rel_path)
            if os.path.isfile(src):
                rel = it.rel_path.split("/", 1)[1] if "/" in it.rel_path else it.rel_path
                z.write(src, f"Mods/{title} CC/{rel}")
    return path


def entry_thumb(entry: TrayEntry, cache_dir: str) -> str | None:
    images = entry.images()
    if not images:
        return None
    src = images[0]
    out = os.path.join(cache_dir, f"tray_{entry.key}_{int(os.path.getmtime(src))}{THUMB_EXT}")
    if os.path.exists(out):
        return out
    img = decode_image(src)
    return _save(img, out) if img is not None else None


def sim_thumbs(entry: TrayEntry, cache_dir: str) -> list[str]:
    out = []
    for src in sorted(f for f in entry.files if f.lower().endswith(".sgi")):
        dst = os.path.join(cache_dir, f"tray_{os.path.basename(src).replace('!', '_')}_{int(os.path.getmtime(src))}{THUMB_EXT}")
        if not os.path.exists(dst):
            img = decode_image(src)
            if img is None or not _save(img, dst):
                continue
        out.append(dst)
    return out


_VARINT_RUN = re.compile(rb"[\x80-\xff]{4,}[\x00-\x7f]")


def _candidate_ids(data: bytes) -> set[int]:
    ids: set[int] = set()
    view = memoryview(data)
    for start in range(8):
        usable = (len(data) - start) // 8 * 8
        if usable > 0:
            chunk = view[start:start + usable]
            if sys.byteorder == "little":
                ids.update(chunk.cast("Q"))
            else:
                ids.update(v for (v,) in struct.iter_unpack("<Q", chunk))
    for m in _VARINT_RUN.finditer(data):
        end = m.end()
        for start in range(max(m.start(), end - 10), end - 4):
            r = 0
            for k, b in enumerate(data[start:end]):
                r |= (b & 0x7F) << (7 * k)
            if r > 0xFFFFFFFF:
                ids.add(r)
    return ids


def used_cc(lib: Library, entry: TrayEntry, id_map: dict[int, set[str]] | None = None) -> list[Item]:
    id_map = id_map if id_map is not None else lib.db.res_id_map()
    fps: set[str] = set()
    for path in entry.data_files():
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        for inst in _candidate_ids(data) & id_map.keys():
            fps |= id_map[inst]
    items = [it for it in lib.items() if it.fingerprint in fps and it.kind == "package"]
    seen, out = set(), []
    for it in sorted(items, key=lambda i: (i.status != "active", i.name.lower())):
        if it.fingerprint not in seen:
            seen.add(it.fingerprint)
            out.append(it)
    return out
