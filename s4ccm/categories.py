from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    folder: str
    label: str
    group: str


SCRIPT_MOD = "script"
UNSORTED = "unsorted"

_C = Category
CATEGORIES: list[Category] = [
    _C("overrides", "00_Overrides", "오버레이 (Overrides)", "overrides"),
    _C("cas.hair", "10_CAS/Hair", "헤어", "cas"),
    _C("cas.facialhair", "10_CAS/FacialHair", "수염", "cas"),
    _C("cas.hats", "10_CAS/Hats", "모자", "cas"),
    _C("cas.tops", "10_CAS/Tops", "상의", "cas"),
    _C("cas.bottoms", "10_CAS/Bottoms", "하의", "cas"),
    _C("cas.fullbody", "10_CAS/FullBody", "전신", "cas"),
    _C("cas.shoes", "10_CAS/Shoes", "신발", "cas"),
    _C("cas.hosiery", "10_CAS/Hosiery", "양말/스타킹", "cas"),
    _C("cas.acc.earrings", "10_CAS/Accessories/Earrings", "귀걸이", "cas"),
    _C("cas.acc.necklaces", "10_CAS/Accessories/Necklaces", "목걸이", "cas"),
    _C("cas.acc.rings", "10_CAS/Accessories/Rings", "반지", "cas"),
    _C("cas.acc.bracelets", "10_CAS/Accessories/Bracelets", "팔찌", "cas"),
    _C("cas.acc.glasses", "10_CAS/Accessories/Glasses", "안경", "cas"),
    _C("cas.acc.gloves", "10_CAS/Accessories/Gloves", "장갑", "cas"),
    _C("cas.acc.piercings", "10_CAS/Accessories/Piercings", "피어싱", "cas"),
    _C("cas.acc.other", "10_CAS/Accessories/Other", "기타 액세서리", "cas"),
    _C("cas.makeup.lipstick", "10_CAS/Makeup/Lipstick", "립스틱", "cas"),
    _C("cas.makeup.eyeshadow", "10_CAS/Makeup/Eyeshadow", "아이섀도", "cas"),
    _C("cas.makeup.eyeliner", "10_CAS/Makeup/Eyeliner", "아이라이너", "cas"),
    _C("cas.makeup.blush", "10_CAS/Makeup/Blush", "블러셔", "cas"),
    _C("cas.makeup.eyebrows", "10_CAS/Makeup/Eyebrows", "눈썹", "cas"),
    _C("cas.makeup.lashes", "10_CAS/Makeup/Lashes", "속눈썹", "cas"),
    _C("cas.makeup.nails", "10_CAS/Makeup/Nails", "네일", "cas"),
    _C("cas.makeup.other", "10_CAS/Makeup/Other", "기타 메이크업", "cas"),
    _C("cas.skin", "10_CAS/Skin", "스킨/디테일", "cas"),
    _C("cas.eyes", "10_CAS/Eyes", "눈동자", "cas"),
    _C("cas.tattoos", "10_CAS/Tattoos", "타투", "cas"),
    _C("cas.sliders", "10_CAS/Sliders", "슬라이더", "cas"),
    _C("cas.presets", "10_CAS/Presets", "프리셋", "cas"),
    _C("cas.pets", "10_CAS/Pets", "펫 CAS", "cas"),
    _C("cas.other", "10_CAS/Other", "기타 CAS", "cas"),
    _C("bb.buy.seating", "20_BuildBuy/Buy/Seating", "의자/소파", "bb"),
    _C("bb.buy.surfaces", "20_BuildBuy/Buy/Surfaces", "테이블/표면", "bb"),
    _C("bb.buy.beds", "20_BuildBuy/Buy/Beds", "침대", "bb"),
    _C("bb.buy.storage", "20_BuildBuy/Buy/Storage", "수납", "bb"),
    _C("bb.buy.appliances", "20_BuildBuy/Buy/Appliances", "가전", "bb"),
    _C("bb.buy.electronics", "20_BuildBuy/Buy/Electronics", "전자기기", "bb"),
    _C("bb.buy.plumbing", "20_BuildBuy/Buy/Plumbing", "욕실", "bb"),
    _C("bb.buy.lighting", "20_BuildBuy/Buy/Lighting", "조명", "bb"),
    _C("bb.buy.decor", "20_BuildBuy/Buy/Decor", "장식", "bb"),
    _C("bb.buy.plants", "20_BuildBuy/Buy/Plants", "식물", "bb"),
    _C("bb.buy.kids", "20_BuildBuy/Buy/Kids", "아이용", "bb"),
    _C("bb.buy.activities", "20_BuildBuy/Buy/Activities", "활동/취미", "bb"),
    _C("bb.buy.misc", "20_BuildBuy/Buy/Misc", "기타 구매", "bb"),
    _C("bb.build.walls", "20_BuildBuy/Build/Walls", "벽지", "bb"),
    _C("bb.build.floors", "20_BuildBuy/Build/Floors", "바닥", "bb"),
    _C("bb.build.doors", "20_BuildBuy/Build/Doors", "문", "bb"),
    _C("bb.build.windows", "20_BuildBuy/Build/Windows", "창문", "bb"),
    _C("bb.build.roofs", "20_BuildBuy/Build/Roofs", "지붕", "bb"),
    _C("bb.build.stairs", "20_BuildBuy/Build/Stairs", "계단/난간", "bb"),
    _C("bb.build.columns", "20_BuildBuy/Build/Columns", "기둥", "bb"),
    _C("bb.build.fences", "20_BuildBuy/Build/Fences", "울타리", "bb"),
    _C("bb.build.misc", "20_BuildBuy/Build/Misc", "기타 건축", "bb"),
    _C("tuning", "30_Tuning", "게임 모드", "tuning"),
    _C("animations", "40_Animations", "포즈/애니메이션", "animations"),
    _C(UNSORTED, "90_Unsorted", "미분류", UNSORTED),
]

GROUPS: list[tuple[str, str]] = [
    ("overrides", "오버레이"),
    ("cas", "심 생성 (CAS)"),
    ("bb", "건축 / 구매"),
    ("tuning", "게임 모드"),
    ("animations", "포즈 / 애니메이션"),
    (SCRIPT_MOD, "스크립트 모드"),
    (UNSORTED, "미분류"),
]

BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
SCRIPT_FOLDER = "Scripts"


def label(key: str) -> str:
    if key == SCRIPT_MOD:
        return "스크립트 모드"
    c = BY_KEY.get(key)
    return c.label if c else key


def group_of(key: str) -> str:
    if key == SCRIPT_MOD:
        return SCRIPT_MOD
    c = BY_KEY.get(key)
    return c.group if c else UNSORTED


def folder_of(key: str) -> str:
    return BY_KEY[key].folder if key in BY_KEY else BY_KEY[UNSORTED].folder


def category_for_folder(rel_dir: str) -> str | None:
    rel = rel_dir.replace("\\", "/").strip("/").lower()
    best = None
    for c in CATEGORIES:
        f = c.folder.lower()
        if rel == f or rel.startswith(f + "/"):
            if best is None or len(f) > len(best.folder):
                best = c
    return best.key if best else None


_BODY_TYPE: dict[int, str] = {
    1: "cas.hats", 2: "cas.hair", 3: "cas.skin", 4: "cas.skin",
    5: "cas.fullbody", 6: "cas.tops", 7: "cas.bottoms", 8: "cas.shoes",
    9: "cas.acc.other", 10: "cas.acc.earrings", 11: "cas.acc.glasses",
    12: "cas.acc.necklaces", 13: "cas.acc.gloves",
    14: "cas.acc.bracelets", 15: "cas.acc.bracelets",
    28: "cas.facialhair",
    29: "cas.makeup.lipstick", 30: "cas.makeup.eyeshadow", 31: "cas.makeup.eyeliner",
    32: "cas.makeup.blush", 33: "cas.makeup.other", 34: "cas.makeup.eyebrows",
    35: "cas.eyes", 36: "cas.hosiery", 37: "cas.makeup.lashes", 42: "cas.hosiery",
    58: "cas.skin", 59: "cas.pets", 60: "cas.pets", 61: "cas.pets", 62: "cas.pets",
    63: "cas.eyes", 73: "cas.makeup.nails", 74: "cas.makeup.nails",
}
for _bt in range(16, 22):
    _BODY_TYPE[_bt] = "cas.acc.piercings"
for _bt in range(22, 28):
    _BODY_TYPE[_bt] = "cas.acc.rings"
for _bt in list(range(38, 42)) + list(range(43, 45)) + list(range(55, 58)) + list(range(64, 73)):
    _BODY_TYPE[_bt] = "cas.skin"
for _bt in range(45, 55):
    _BODY_TYPE[_bt] = "cas.tattoos"


def category_for_body_type(bt: int) -> str:
    if bt in _BODY_TYPE:
        return _BODY_TYPE[bt]
    if 75 <= bt < 128:
        return "cas.skin"
    return "cas.other"
