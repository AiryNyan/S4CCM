from __future__ import annotations

import collections
import struct
import os
import re
from dataclasses import dataclass, field

from . import categories as cats
from .casp import parse_casp
from .dbpf import DBPFError, Package

T_CASP = 0x034AEECB
T_OBJD = 0xC0DB5AE7
T_COBJ = 0x319E4F1D
T_TONE = 0x0354796A
T_SMOD = 0xC5F6763E
T_CPRE = 0xEAA32ADD
T_CLIP = 0x6B20C4F3
T_SIMDATA = 0x545AC67A

T_BUILD = {
    0xD5F0F921: "bb.build.walls",
    0xB4F762C9: "bb.build.floors",
    0xB0311D0F: "bb.build.roofs",
    0xF1EDBD86: "bb.build.roofs",
    0x9A20CD1C: "bb.build.stairs",
    0x1C1CF1F7: "bb.build.stairs",
    0x1D6DF1CF: "bb.build.columns",
    0x0418FE2A: "bb.build.fences",
    0x2FAE983E: "bb.build.misc",
    0xEBCBB16C: "bb.build.misc",
}

_OBJ_KEYWORDS: list[tuple[str, str]] = [
    (r"door|gate", "bb.build.doors"),
    (r"window|arch", "bb.build.windows"),
    (r"stair|rail", "bb.build.stairs"),
    (r"column|pillar", "bb.build.columns"),
    (r"fence", "bb.build.fences"),
    (r"chair|sofa|couch|stool|bench|seat|loveseat|armchair", "bb.buy.seating"),
    (r"bed|crib|mattress", "bb.buy.beds"),
    (r"table|desk|counter|island|nightstand|dresser", "bb.buy.surfaces"),
    (r"shelf|shelves|cabinet|bookcase|wardrobe|closet|storage|drawer", "bb.buy.storage"),
    (r"fridge|stove|oven|microwave|dishwasher|coffee ?maker|toaster|kitchen", "bb.buy.appliances"),
    (r"\btv\b|television|computer|pc|laptop|stereo|speaker|console|phone", "bb.buy.electronics"),
    (r"toilet|shower|bath|tub|sink|vanity", "bb.buy.plumbing"),
    (r"lamp|light|chandelier|sconce|candle|neon", "bb.buy.lighting"),
    (r"plant|flower|tree|bush|cactus|succulent|vase", "bb.buy.plants"),
    (r"toy|kid|toddler|baby|infant|nursery", "bb.buy.kids"),
    (r"easel|guitar|piano|instrument|gym|treadmill|chess", "bb.buy.activities"),
    (r"rug|curtain|frame|painting|poster|mirror|clutter|deco|pillow|clock|sculpture|wall ?art", "bb.buy.decor"),
]


T_TEXTURES = (0x3453CF95, 0xBA856C78, 0x2BC04EDF, 0x00B2D882)
T_REFERENCED = {T_CASP, T_OBJD, T_COBJ, T_TONE, T_SMOD, T_CPRE, *T_BUILD}


def resource_ids(path: str) -> list[int]:
    try:
        pk = Package(path)
    except (DBPFError, OSError):
        return []
    with pk:
        return [e.instance for e in pk.entries if e.type in T_REFERENCED and e.instance > 0xFFFFFFFF]


def texture_size(path: str) -> tuple[int, int] | None:
    try:
        pk = Package(path)
    except (DBPFError, OSError):
        return None
    best = None
    with pk:
        textures = sorted((e for e in pk.entries if e.type in T_TEXTURES), key=lambda e: -e.mem_size)[:3]
        for e in textures:
            try:
                head = pk.read_head(e, 24)
            except Exception:
                continue
            if head[:4] == b"DDS " and len(head) >= 20:
                h, w = struct.unpack_from("<II", head, 12)
            elif head[4:8] in (b"RLE2", b"RLES") and len(head) >= 12:
                w, h = struct.unpack_from("<HH", head, 8)
            elif head[:4] == b"LRLE" and len(head) >= 12:
                w, h = struct.unpack_from("<HH", head, 8)
            else:
                continue
            if best is None or w * h > best[0] * best[1]:
                best = (w, h)
    return best


def is_hq(size) -> bool:
    return bool(size) and (max(size) >= 4096 or min(size) >= 2048)


@dataclass
class Classification:
    category: str
    confidence: float
    reason: str
    casp_names: list[str] = field(default_factory=list)
    casp_instances: list[int] = field(default_factory=list)
    object_instances: list[int] = field(default_factory=list)
    resource_count: int = 0
    error: str | None = None


def _guess_object(name: str) -> str:
    low = name.lower()
    for pat, key in _OBJ_KEYWORDS:
        if re.search(pat, low):
            return key
    return "bb.buy.misc"


def _looks_like_tuning(pk: Package, limit: int = 12) -> bool:
    checked = 0
    for e in pk.entries:
        if e.mem_size > 2_000_000:
            continue
        try:
            head = pk.read(e)[:64].lstrip(b"\xef\xbb\xbf \r\n\t")
        except Exception:
            continue
        if head.startswith(b"<?xml") or head.startswith(b"<I ") or head.startswith(b"<M "):
            return True
        checked += 1
        if checked >= limit:
            break
    return False


def classify_package(path: str) -> Classification:
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        pk = Package(path)
    except (DBPFError, OSError) as ex:
        return Classification(cats.UNSORTED, 0.0, f"읽기 실패: {ex}", error=str(ex))

    with pk:
        types = collections.Counter(e.type for e in pk.entries)
        result = Classification(cats.UNSORTED, 0.0, "알 수 없는 구성", resource_count=len(pk.entries))

        if types[T_CASP]:
            per_cat: collections.Counter[str] = collections.Counter()
            bt_seen: collections.Counter[int] = collections.Counter()
            parsed = 0
            for e in pk.by_type(T_CASP):
                try:
                    info = parse_casp(pk.read(e))
                except (DBPFError, OSError):
                    info = None
                if info is None:
                    continue
                parsed += 1
                per_cat[cats.category_for_body_type(info.body_type)] += 1
                bt_seen[info.body_type] += 1
                result.casp_instances.append(e.instance)
                if info.name and len(result.casp_names) < 5:
                    result.casp_names.append(info.name)
            if per_cat:
                cat, n = per_cat.most_common(1)[0]
                result.category = cat
                result.confidence = 0.95 * n / sum(per_cat.values()) if len(per_cat) > 1 else 0.98
                bts = ", ".join(f"{bt}×{c}" for bt, c in bt_seen.most_common(3))
                result.reason = f"CAS 파츠 {parsed}개, BodyType {bts}"
                if len(per_cat) > 1:
                    others = ", ".join(cats.label(k) for k, _ in per_cat.most_common()[1:3])
                    result.reason += f" (섞임: {others})"
                return result
            result.reason = f"CAS 파츠 {types[T_CASP]}개 (형식 해석 실패)"
            result.category = "cas.other"
            result.confidence = 0.5
            return result

        if types[T_OBJD] or types[T_COBJ]:
            result.object_instances = [e.instance for e in pk.by_type(T_OBJD)]
            cat = _guess_object(name)
            result.category = cat
            result.confidence = 0.6 if cat != "bb.buy.misc" else 0.5
            result.reason = f"오브젝트 {max(types[T_OBJD], types[T_COBJ])}개 (세부 분류는 파일 이름 기준)"
            return result

        for t, key in T_BUILD.items():
            if types[t]:
                result.category = key
                result.confidence = 0.9
                result.reason = f"건축 리소스 0x{t:08X}"
                return result

        if types[T_TONE]:
            return Classification("cas.skin", 0.95, "스킨톤(TONE)", resource_count=len(pk.entries))
        if types[T_SMOD]:
            return Classification("cas.sliders", 0.9, f"슬라이더(SMOD) {types[T_SMOD]}개",
                                  resource_count=len(pk.entries))
        if types[T_CPRE]:
            return Classification("cas.presets", 0.85, "CAS 프리셋", resource_count=len(pk.entries))
        if types[T_CLIP]:
            return Classification("animations", 0.85, f"애니메이션 클립 {types[T_CLIP]}개",
                                  resource_count=len(pk.entries))
        if types[T_SIMDATA] or _looks_like_tuning(pk):
            return Classification("tuning", 0.8, "튜닝 XML / SimData", resource_count=len(pk.entries))
        return result
