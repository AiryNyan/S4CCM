//! 패키지 내용으로 분류를 추정한다.

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;

use crate::casp::parse_casp;
use crate::categories as cats;
use crate::dbpf::{DbpfError, Package};
use crate::pyos;

pub const T_CASP: u32 = 0x034AEECB;
pub const T_OBJD: u32 = 0xC0DB5AE7;
pub const T_COBJ: u32 = 0x319E4F1D;
pub const T_TONE: u32 = 0x0354796A;
pub const T_SMOD: u32 = 0xC5F6763E;
pub const T_CPRE: u32 = 0xEAA32ADD;
pub const T_CLIP: u32 = 0x6B20C4F3;
pub const T_SIMDATA: u32 = 0x545AC67A;

pub static T_BUILD: &[(u32, &str)] = &[
    (0xD5F0F921, "bb.build.walls"),
    (0xB4F762C9, "bb.build.floors"),
    (0xB0311D0F, "bb.build.roofs"),
    (0xF1EDBD86, "bb.build.roofs"),
    (0x9A20CD1C, "bb.build.stairs"),
    (0x1C1CF1F7, "bb.build.stairs"),
    (0x1D6DF1CF, "bb.build.columns"),
    (0x0418FE2A, "bb.build.fences"),
    (0x2FAE983E, "bb.build.misc"),
    (0xEBCBB16C, "bb.build.misc"),
];

static OBJ_KEYWORDS: &[(&str, &str)] = &[
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
];

pub const T_TEXTURES: [u32; 4] = [0x3453CF95, 0xBA856C78, 0x2BC04EDF, 0x00B2D882];

fn is_referenced(t: u32) -> bool {
    matches!(t, T_CASP | T_OBJD | T_COBJ | T_TONE | T_SMOD | T_CPRE) || T_BUILD.iter().any(|(b, _)| *b == t)
}

/// 다른 파일(트레이 등)이 참조할 수 있는 리소스 인스턴스 ID 목록.
pub fn resource_ids(path: &str) -> Vec<u64> {
    match Package::open(path) {
        Ok(pk) => resource_ids_of(&pk),
        Err(_) => Vec::new(),
    }
}

pub fn resource_ids_of(pk: &Package) -> Vec<u64> {
    pk.entries.iter().filter(|e| is_referenced(e.type_) && e.instance > 0xFFFF_FFFF).map(|e| e.instance).collect()
}

/// 패키지 안에서 가장 큰 텍스처의 (가로, 세로).
pub fn texture_size(path: &str) -> Option<(u32, u32)> {
    let mut pk = Package::open(path).ok()?;
    texture_size_of(&mut pk)
}

pub fn texture_size_of(pk: &mut Package) -> Option<(u32, u32)> {
    let mut textures: Vec<_> = pk.entries.iter().filter(|e| T_TEXTURES.contains(&e.type_)).copied().collect();
    textures.sort_by(|a, b| b.mem_size.cmp(&a.mem_size));
    textures.truncate(3);
    let mut best: Option<(u32, u32)> = None;
    for e in textures {
        let head = match pk.read_head(&e, 24) {
            Ok(h) => h,
            Err(_) => continue,
        };
        let le32 = |at: usize| u32::from_le_bytes([head[at], head[at + 1], head[at + 2], head[at + 3]]);
        let le16 = |at: usize| u16::from_le_bytes([head[at], head[at + 1]]) as u32;
        let (w, h);
        if head.len() >= 20 && &head[..4] == b"DDS " {
            h = le32(12);
            w = le32(16);
        } else if head.len() >= 12 && (&head[4..8] == b"RLE2" || &head[4..8] == b"RLES") {
            w = le16(8);
            h = le16(10);
        } else if head.len() >= 12 && &head[..4] == b"LRLE" {
            w = le16(8);
            h = le16(10);
        } else {
            continue;
        }
        if best.map_or(true, |b| (w as u64) * (h as u64) > (b.0 as u64) * (b.1 as u64)) {
            best = Some((w, h));
        }
    }
    best
}

pub fn is_hq(size: Option<(u32, u32)>) -> bool {
    match size {
        Some((w, h)) => w.max(h) >= 4096 || w.min(h) >= 2048,
        None => false,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Classification {
    pub category: String,
    pub confidence: f64,
    pub reason: String,
    pub casp_names: Vec<String>,
    pub casp_instances: Vec<u64>,
    pub object_instances: Vec<u64>,
    pub resource_count: usize,
    pub error: Option<String>,
}

impl Classification {
    fn new(category: &str, confidence: f64, reason: impl Into<String>, resource_count: usize) -> Self {
        Classification {
            category: category.to_string(),
            confidence,
            reason: reason.into(),
            casp_names: Vec::new(),
            casp_instances: Vec::new(),
            object_instances: Vec::new(),
            resource_count,
            error: None,
        }
    }
}

fn obj_patterns() -> &'static Vec<(Regex, &'static str)> {
    static P: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    P.get_or_init(|| OBJ_KEYWORDS.iter().map(|(p, k)| (Regex::new(p).unwrap(), *k)).collect())
}

fn guess_object(name: &str) -> &'static str {
    let low = name.to_lowercase();
    for (rx, key) in obj_patterns() {
        if rx.is_match(&low) {
            return key;
        }
    }
    "bb.buy.misc"
}

fn looks_like_tuning(pk: &mut Package, limit: usize) -> bool {
    let mut checked = 0;
    let entries = pk.entries.clone();
    for e in entries {
        if e.mem_size > 2_000_000 {
            continue;
        }
        let data = match pk.read(&e) {
            Ok(d) => d,
            Err(_) => continue,
        };
        let head = &data[..data.len().min(64)];
        let skip = head.iter().position(|b| !matches!(b, 0xEF | 0xBB | 0xBF | b' ' | b'\r' | b'\n' | b'\t')).unwrap_or(head.len());
        let head = &head[skip..];
        if head.starts_with(b"<?xml") || head.starts_with(b"<I ") || head.starts_with(b"<M ") {
            return true;
        }
        checked += 1;
        if checked >= limit {
            break;
        }
    }
    false
}

/// 삽입 순서를 기억하는 카운터 (`collections.Counter.most_common` 의 동률 순서와 같게).
struct Counter<K> {
    items: Vec<(K, usize)>,
}

impl<K: PartialEq + Clone> Counter<K> {
    fn new() -> Self {
        Counter { items: Vec::new() }
    }

    fn add(&mut self, k: K) {
        if let Some(e) = self.items.iter_mut().find(|(x, _)| *x == k) {
            e.1 += 1;
        } else {
            self.items.push((k, 1));
        }
    }

    fn most_common(&self) -> Vec<(K, usize)> {
        let mut v = self.items.clone();
        v.sort_by(|a, b| b.1.cmp(&a.1));
        v
    }

    fn total(&self) -> usize {
        self.items.iter().map(|x| x.1).sum()
    }
}

pub fn classify_package(path: &str) -> Classification {
    match Package::open(path) {
        Ok(mut pk) => classify_open(&mut pk),
        Err(ex) => classify_failed(&ex),
    }
}

pub fn classify_failed(ex: &DbpfError) -> Classification {
    let mut c = Classification::new(cats::UNSORTED, 0.0, format!("읽기 실패: {ex}"), 0);
    c.error = Some(ex.to_string());
    c
}

pub fn classify_open(pk: &mut Package) -> Classification {
    let name = pyos::splitext(&pyos::basename(&pk.path)).0;
    let mut types: HashMap<u32, usize> = HashMap::new();
    for e in &pk.entries {
        *types.entry(e.type_).or_insert(0) += 1;
    }
    let count = |t: u32| types.get(&t).copied().unwrap_or(0);
    let n_entries = pk.entries.len();
    let mut result = Classification::new(cats::UNSORTED, 0.0, "알 수 없는 구성", n_entries);

    if count(T_CASP) > 0 {
        let mut per_cat: Counter<&'static str> = Counter::new();
        let mut bt_seen: Counter<i32> = Counter::new();
        let mut parsed = 0;
        let casps: Vec<_> = pk.entries.iter().filter(|e| e.type_ == T_CASP).copied().collect();
        for e in casps {
            let info = match pk.read(&e) {
                Ok(data) => parse_casp(&data),
                Err(_) => None,
            };
            let Some(info) = info else { continue };
            parsed += 1;
            per_cat.add(cats::category_for_body_type(info.body_type));
            bt_seen.add(info.body_type);
            result.casp_instances.push(e.instance);
            if !info.name.is_empty() && result.casp_names.len() < 5 {
                result.casp_names.push(info.name);
            }
        }
        if !per_cat.items.is_empty() {
            let common = per_cat.most_common();
            let (cat, n) = common[0];
            result.category = cat.to_string();
            result.confidence =
                if common.len() > 1 { 0.95 * n as f64 / per_cat.total() as f64 } else { 0.98 };
            let bts: Vec<String> =
                bt_seen.most_common().iter().take(3).map(|(bt, c)| format!("{bt}×{c}")).collect();
            result.reason = format!("CAS 파츠 {parsed}개, BodyType {}", bts.join(", "));
            if common.len() > 1 {
                let others: Vec<String> = common.iter().skip(1).take(2).map(|(k, _)| cats::label(k)).collect();
                result.reason += &format!(" (섞임: {})", others.join(", "));
            }
            return result;
        }
        result.reason = format!("CAS 파츠 {}개 (형식 해석 실패)", count(T_CASP));
        result.category = "cas.other".to_string();
        result.confidence = 0.5;
        return result;
    }

    if count(T_OBJD) > 0 || count(T_COBJ) > 0 {
        result.object_instances = pk.entries.iter().filter(|e| e.type_ == T_OBJD).map(|e| e.instance).collect();
        let cat = guess_object(&name);
        result.category = cat.to_string();
        result.confidence = if cat != "bb.buy.misc" { 0.6 } else { 0.5 };
        result.reason =
            format!("오브젝트 {}개 (세부 분류는 파일 이름 기준)", count(T_OBJD).max(count(T_COBJ)));
        return result;
    }

    for (t, key) in T_BUILD {
        if count(*t) > 0 {
            result.category = key.to_string();
            result.confidence = 0.9;
            result.reason = format!("건축 리소스 0x{t:08X}");
            return result;
        }
    }

    if count(T_TONE) > 0 {
        return Classification::new("cas.skin", 0.95, "스킨톤(TONE)", n_entries);
    }
    if count(T_SMOD) > 0 {
        return Classification::new("cas.sliders", 0.9, format!("슬라이더(SMOD) {}개", count(T_SMOD)), n_entries);
    }
    if count(T_CPRE) > 0 {
        return Classification::new("cas.presets", 0.85, "CAS 프리셋", n_entries);
    }
    if count(T_CLIP) > 0 {
        return Classification::new("animations", 0.85, format!("애니메이션 클립 {}개", count(T_CLIP)), n_entries);
    }
    if count(T_SIMDATA) > 0 || looks_like_tuning(pk, 12) {
        return Classification::new("tuning", 0.8, "튜닝 XML / SimData", n_entries);
    }
    result
}
