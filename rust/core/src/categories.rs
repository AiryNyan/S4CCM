//! 분류 체계(폴더 ↔ 분류 키 ↔ 표시 이름).

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Category {
    pub key: &'static str,
    pub folder: &'static str,
    pub label: &'static str,
    pub group: &'static str,
}

pub const SCRIPT_MOD: &str = "script";
pub const UNSORTED: &str = "unsorted";
pub const SCRIPT_FOLDER: &str = "Scripts";

const fn c(key: &'static str, folder: &'static str, label: &'static str, group: &'static str) -> Category {
    Category { key, folder, label, group }
}

pub static CATEGORIES: &[Category] = &[
    c("overrides", "00_Overrides", "오버레이 (Overrides)", "overrides"),
    c("cas.hair", "10_CAS/Hair", "헤어", "cas"),
    c("cas.facialhair", "10_CAS/FacialHair", "수염", "cas"),
    c("cas.hats", "10_CAS/Hats", "모자", "cas"),
    c("cas.tops", "10_CAS/Tops", "상의", "cas"),
    c("cas.bottoms", "10_CAS/Bottoms", "하의", "cas"),
    c("cas.fullbody", "10_CAS/FullBody", "전신", "cas"),
    c("cas.shoes", "10_CAS/Shoes", "신발", "cas"),
    c("cas.hosiery", "10_CAS/Hosiery", "양말/스타킹", "cas"),
    c("cas.acc.earrings", "10_CAS/Accessories/Earrings", "귀걸이", "cas"),
    c("cas.acc.necklaces", "10_CAS/Accessories/Necklaces", "목걸이", "cas"),
    c("cas.acc.rings", "10_CAS/Accessories/Rings", "반지", "cas"),
    c("cas.acc.bracelets", "10_CAS/Accessories/Bracelets", "팔찌", "cas"),
    c("cas.acc.glasses", "10_CAS/Accessories/Glasses", "안경", "cas"),
    c("cas.acc.gloves", "10_CAS/Accessories/Gloves", "장갑", "cas"),
    c("cas.acc.piercings", "10_CAS/Accessories/Piercings", "피어싱", "cas"),
    c("cas.acc.other", "10_CAS/Accessories/Other", "기타 액세서리", "cas"),
    c("cas.makeup.lipstick", "10_CAS/Makeup/Lipstick", "립스틱", "cas"),
    c("cas.makeup.eyeshadow", "10_CAS/Makeup/Eyeshadow", "아이섀도", "cas"),
    c("cas.makeup.eyeliner", "10_CAS/Makeup/Eyeliner", "아이라이너", "cas"),
    c("cas.makeup.blush", "10_CAS/Makeup/Blush", "블러셔", "cas"),
    c("cas.makeup.eyebrows", "10_CAS/Makeup/Eyebrows", "눈썹", "cas"),
    c("cas.makeup.lashes", "10_CAS/Makeup/Lashes", "속눈썹", "cas"),
    c("cas.makeup.nails", "10_CAS/Makeup/Nails", "네일", "cas"),
    c("cas.makeup.other", "10_CAS/Makeup/Other", "기타 메이크업", "cas"),
    c("cas.skin", "10_CAS/Skin", "스킨/디테일", "cas"),
    c("cas.eyes", "10_CAS/Eyes", "눈동자", "cas"),
    c("cas.tattoos", "10_CAS/Tattoos", "타투", "cas"),
    c("cas.sliders", "10_CAS/Sliders", "슬라이더", "cas"),
    c("cas.presets", "10_CAS/Presets", "프리셋", "cas"),
    c("cas.pets", "10_CAS/Pets", "펫 CAS", "cas"),
    c("cas.other", "10_CAS/Other", "기타 CAS", "cas"),
    c("bb.buy.seating", "20_BuildBuy/Buy/Seating", "의자/소파", "bb"),
    c("bb.buy.surfaces", "20_BuildBuy/Buy/Surfaces", "테이블/표면", "bb"),
    c("bb.buy.beds", "20_BuildBuy/Buy/Beds", "침대", "bb"),
    c("bb.buy.storage", "20_BuildBuy/Buy/Storage", "수납", "bb"),
    c("bb.buy.appliances", "20_BuildBuy/Buy/Appliances", "가전", "bb"),
    c("bb.buy.electronics", "20_BuildBuy/Buy/Electronics", "전자기기", "bb"),
    c("bb.buy.plumbing", "20_BuildBuy/Buy/Plumbing", "욕실", "bb"),
    c("bb.buy.lighting", "20_BuildBuy/Buy/Lighting", "조명", "bb"),
    c("bb.buy.decor", "20_BuildBuy/Buy/Decor", "장식", "bb"),
    c("bb.buy.plants", "20_BuildBuy/Buy/Plants", "식물", "bb"),
    c("bb.buy.kids", "20_BuildBuy/Buy/Kids", "아이용", "bb"),
    c("bb.buy.activities", "20_BuildBuy/Buy/Activities", "활동/취미", "bb"),
    c("bb.buy.misc", "20_BuildBuy/Buy/Misc", "기타 구매", "bb"),
    c("bb.build.walls", "20_BuildBuy/Build/Walls", "벽지", "bb"),
    c("bb.build.floors", "20_BuildBuy/Build/Floors", "바닥", "bb"),
    c("bb.build.doors", "20_BuildBuy/Build/Doors", "문", "bb"),
    c("bb.build.windows", "20_BuildBuy/Build/Windows", "창문", "bb"),
    c("bb.build.roofs", "20_BuildBuy/Build/Roofs", "지붕", "bb"),
    c("bb.build.stairs", "20_BuildBuy/Build/Stairs", "계단/난간", "bb"),
    c("bb.build.columns", "20_BuildBuy/Build/Columns", "기둥", "bb"),
    c("bb.build.fences", "20_BuildBuy/Build/Fences", "울타리", "bb"),
    c("bb.build.misc", "20_BuildBuy/Build/Misc", "기타 건축", "bb"),
    c("tuning", "30_Tuning", "게임 모드", "tuning"),
    c("animations", "40_Animations", "포즈/애니메이션", "animations"),
    c(UNSORTED, "90_Unsorted", "미분류", UNSORTED),
];

pub static GROUPS: &[(&str, &str)] = &[
    ("overrides", "오버레이"),
    ("cas", "심 생성 (CAS)"),
    ("bb", "건축 / 구매"),
    ("tuning", "게임 모드"),
    ("animations", "포즈 / 애니메이션"),
    (SCRIPT_MOD, "스크립트 모드"),
    (UNSORTED, "미분류"),
];

pub fn by_key(key: &str) -> Option<&'static Category> {
    CATEGORIES.iter().find(|c| c.key == key)
}

pub fn label(key: &str) -> String {
    if key == SCRIPT_MOD {
        return "스크립트 모드".to_string();
    }
    by_key(key).map(|c| c.label.to_string()).unwrap_or_else(|| key.to_string())
}

pub fn group_of(key: &str) -> &'static str {
    if key == SCRIPT_MOD {
        return SCRIPT_MOD;
    }
    by_key(key).map(|c| c.group).unwrap_or(UNSORTED)
}

pub fn folder_of(key: &str) -> &'static str {
    by_key(key).or_else(|| by_key(UNSORTED)).map(|c| c.folder).unwrap_or("90_Unsorted")
}

pub fn category_for_folder(rel_dir: &str) -> Option<&'static str> {
    let rel = rel_dir.replace('\\', "/");
    let rel = rel.trim_matches('/').to_lowercase();
    let mut best: Option<&Category> = None;
    for c in CATEGORIES {
        let f = c.folder.to_lowercase();
        if rel == f || rel.starts_with(&(f.clone() + "/")) {
            // 원본은 소문자 길이와 원래 폴더 길이를 비교한다(ASCII 라 동일).
            if best.map_or(true, |b| f.chars().count() > b.folder.chars().count()) {
                best = Some(c);
            }
        }
    }
    best.map(|c| c.key)
}

pub fn category_for_body_type(bt: i32) -> &'static str {
    match bt {
        1 => "cas.hats",
        2 => "cas.hair",
        3 | 4 => "cas.skin",
        5 => "cas.fullbody",
        6 => "cas.tops",
        7 => "cas.bottoms",
        8 => "cas.shoes",
        9 => "cas.acc.other",
        10 => "cas.acc.earrings",
        11 => "cas.acc.glasses",
        12 => "cas.acc.necklaces",
        13 => "cas.acc.gloves",
        14 | 15 => "cas.acc.bracelets",
        16..=21 => "cas.acc.piercings",
        22..=27 => "cas.acc.rings",
        28 => "cas.facialhair",
        29 => "cas.makeup.lipstick",
        30 => "cas.makeup.eyeshadow",
        31 => "cas.makeup.eyeliner",
        32 => "cas.makeup.blush",
        33 => "cas.makeup.other",
        34 => "cas.makeup.eyebrows",
        35 => "cas.eyes",
        36 => "cas.hosiery",
        37 => "cas.makeup.lashes",
        38..=41 => "cas.skin",
        42 => "cas.hosiery",
        43 | 44 => "cas.skin",
        45..=54 => "cas.tattoos",
        55..=57 => "cas.skin",
        58 => "cas.skin",
        59..=62 => "cas.pets",
        63 => "cas.eyes",
        64..=72 => "cas.skin",
        73 | 74 => "cas.makeup.nails",
        75..=127 => "cas.skin",
        _ => "cas.other",
    }
}
