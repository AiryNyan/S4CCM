//! Tray 폴더(가족·집·방) 읽기, 복사본 저장, 내보내기.

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::sync::OnceLock;

use chrono::{Datelike, Local, NaiveDate, NaiveDateTime, Timelike};
use image::DynamicImage;
use indexmap::IndexMap;
use regex::Regex;
use serde_json::Value;

use crate::db::Item;
use crate::error::{Error, Result};
use crate::library::Library;
use crate::proto::{self, enc_varint, encode, fields, first, first_bytes, text, Field, PResult, ProtoError, Val};
use crate::pyos;
use crate::thumbs::{self, Filter, THUMB_EXT};

pub const TYPE_HOUSEHOLD: u64 = 1;
pub const TYPE_LOT: u64 = 2;
pub const TYPE_ROOM: u64 = 3;
pub const IMAGE_EXT: [&str; 4] = [".hhi", ".sgi", ".bpi", ".rmi"];
pub const DATA_EXT: [&str; 3] = [".householdbinary", ".blueprint", ".room"];
pub const MAIN_IMAGE_EXT: [&str; 3] = [".hhi", ".bpi", ".rmi"];
const IMAGE_KEY: [u8; 8] = [0x41, 0x25, 0xe6, 0xcd, 0x47, 0xba, 0xb2, 0x1a];
const IMAGE_MAGIC: [u8; 8] = [0x8e, 0xfc, 0x24, 0x48, 0x9b, 0x78, 0x0e, 0x3c];
pub const GROUP_MASK: u64 = 0x00FF_FFFF_FFFF_FFF0;

pub fn type_label(kind: u64) -> &'static str {
    match kind {
        TYPE_HOUSEHOLD => "가족",
        TYPE_LOT => "집",
        TYPE_ROOM => "방",
        _ => "기타",
    }
}

pub fn age_label(age: u64) -> &'static str {
    match age {
        1 => "아기",
        2 => "유아",
        4 => "어린이",
        8 => "청소년",
        16 => "청년",
        32 => "성인",
        64 => "노인",
        128 => "영아",
        _ => "",
    }
}

pub fn gender_label(gender: u64) -> &'static str {
    match gender {
        4096 => "남성",
        8192 => "여성",
        _ => "",
    }
}

fn skill_table() -> &'static Value {
    static T: OnceLock<Value> = OnceLock::new();
    T.get_or_init(|| {
        serde_json::from_str(include_str!("../data/skills.json"))
            .unwrap_or_else(|_| serde_json::json!({"levels": {}, "skills": {}}))
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct TraySkill {
    pub skill_id: u64,
    pub points: f64,
    pub name: String,
    pub level: i64,
    pub max_level: i64,
}

pub fn make_skill(skill_id: u64, points: f64) -> TraySkill {
    let table = skill_table();
    let plain = TraySkill { skill_id, points, name: String::new(), level: 0, max_level: 0 };
    let Some(info) = table["skills"].get(skill_id.to_string()) else { return plain };
    let levels = &table["levels"];
    let truthy = |v: Option<&Value>| v.and_then(|v| v.as_array()).filter(|a| !a.is_empty()).cloned();
    let steps = info["type"]
        .as_str()
        .and_then(|t| truthy(levels.get(t)))
        .or_else(|| truthy(levels.get("MAJOR")))
        .unwrap_or_default();
    let level = if steps.is_empty() {
        0
    } else {
        (steps.iter().filter(|t| t.as_f64().map_or(false, |t| points >= t)).count() as i64).max(1)
    };
    let s = |k: &str| info.get(k).and_then(|v| v.as_str()).filter(|s| !s.is_empty());
    let name = s("ko").or_else(|| s("en")).or_else(|| s("tuning")).unwrap_or("").to_string();
    TraySkill { skill_id, points, name, level, max_level: steps.len() as i64 }
}

fn name_rx() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^0x([0-9a-fA-F]+)!0x([0-9a-fA-F]+)\.(\w+)$").unwrap())
}

/// 트레이 파일 이름 `0x<type>!0x<instance>.<ext>` 를 나눈다.
pub fn parse_name(name: &str) -> Option<(String, u64, String)> {
    let m = name_rx().captures(name)?;
    // 16진수가 64비트를 넘는 이름은 원본에서도 쓸모가 없으므로 버린다.
    let inst = u64::from_str_radix(&m[2], 16).ok()?;
    Some((m[1].to_string(), inst, m[3].to_string()))
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TraySim {
    pub first_name: String,
    pub last_name: String,
    pub age: u64,
    pub gender: u64,
    pub traits: Vec<String>,
    pub aspiration: String,
    pub likes: Vec<String>,
    pub dislikes: Vec<String>,
    pub sim_id: u64,
    pub skills: Vec<TraySkill>,
}

fn is_korean(s: &str) -> bool {
    s.chars().any(|ch| ('가'..='힣').contains(&ch))
}

impl TraySim {
    pub fn full_name(&self) -> String {
        if is_korean(&format!("{}{}", self.last_name, self.first_name)) {
            format!("{}{}", self.last_name, self.first_name)
        } else {
            format!("{} {}", self.first_name, self.last_name).trim().to_string()
        }
    }

    pub fn summary(&self) -> String {
        [age_label(self.age), gender_label(self.gender)]
            .into_iter()
            .filter(|x| !x.is_empty())
            .collect::<Vec<_>>()
            .join(" · ")
    }
}

/// 0001-01-01 부터의 초 → 날짜. 9999년을 넘으면 None.
pub fn stamp_to_datetime(stamp: u64) -> Option<NaiveDateTime> {
    const MAX: u64 = 315_537_897_600; // 10000-01-01
    if stamp == 0 || stamp >= MAX {
        return None;
    }
    let epoch = NaiveDate::from_ymd_opt(1, 1, 1)?.and_hms_opt(0, 0, 0)?;
    epoch.checked_add_signed(chrono::Duration::seconds(stamp as i64))
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct TrayEntry {
    pub key: String,
    pub kind: u64,
    pub name: String,
    pub description: String,
    pub creator: String,
    pub saved_at: Option<NaiveDateTime>,
    pub sims: Vec<TraySim>,
    pub files: Vec<String>,
}

impl TrayEntry {
    pub fn new(key: String) -> Self {
        TrayEntry { key, ..Default::default() }
    }

    pub fn kind_label(&self) -> &'static str {
        type_label(self.kind)
    }

    /// 큰 파일부터.
    pub fn images(&self) -> Result<Vec<String>> {
        let mut v: Vec<(u64, String)> = Vec::new();
        for f in &self.files {
            if pyos::ends_with_any(&f.to_lowercase(), &IMAGE_EXT) {
                v.push((pyos::getsize(f)?, f.clone()));
            }
        }
        v.sort_by(|a, b| b.0.cmp(&a.0));
        Ok(v.into_iter().map(|x| x.1).collect())
    }

    pub fn main_images(&self) -> Vec<String> {
        self.files.iter().filter(|f| pyos::ends_with_any(&f.to_lowercase(), &MAIN_IMAGE_EXT)).cloned().collect()
    }

    pub fn export_name(&self) -> String {
        if self.kind == TYPE_HOUSEHOLD && self.sims.len() == 1 && !self.sims[0].full_name().is_empty() {
            return self.sims[0].full_name();
        }
        if self.name.is_empty() {
            self.key.clone()
        } else {
            self.name.clone()
        }
    }

    pub fn data_files(&self) -> Vec<String> {
        self.files.iter().filter(|f| pyos::ends_with_any(&f.to_lowercase(), &DATA_EXT)).cloned().collect()
    }
}

fn named(blob: &[u8]) -> String {
    match fields(blob) {
        Ok(f) => text(first(&f, 2)),
        Err(_) => String::new(),
    }
}

fn preference(blob: &[u8]) -> String {
    static RX: OnceLock<Regex> = OnceLock::new();
    let rx = RX.get_or_init(|| Regex::new(r"\s*(을|를)?\s*(좋아함|싫어함)$").unwrap());
    let Ok(f) = fields(blob) else { return String::new() };
    let subject = text(first(&f, 4)).trim().to_string();
    if !subject.is_empty() {
        return subject;
    }
    rx.replace_all(&text(first(&f, 2)), "").trim().to_string()
}

/// `_first(f, n, 0) or 0` — 정수가 아니면 0 으로 본다.
fn int_or_zero(f: &[Field], num: u64) -> u64 {
    first(f, num).and_then(|v| v.as_int()).unwrap_or(0)
}

fn parse_sim(blob: &[u8]) -> PResult<TraySim> {
    let f = fields(blob)?;
    let mut sim = TraySim {
        first_name: text(first(&f, 3)),
        last_name: text(first(&f, 4)),
        gender: int_or_zero(&f, 6),
        age: int_or_zero(&f, 9),
        sim_id: int_or_zero(&f, 5),
        ..Default::default()
    };
    for fld in &f {
        let Val::Bytes(v) = &fld.val else { continue };
        match fld.num {
            10 => sim.traits.push(named(v)),
            11 => sim.aspiration = named(v),
            19 => sim.likes.push(preference(v)),
            21 => sim.dislikes.push(preference(v)),
            _ => {}
        }
    }
    sim.traits.retain(|t| !t.is_empty());
    sim.likes.retain(|t| !t.is_empty());
    sim.dislikes.retain(|t| !t.is_empty());
    Ok(sim)
}

/// `.trayitem` 을 읽어 `entry` 를 채운다. 중간에 실패하면 그때까지 채운 값은 남는다.
pub fn parse_trayitem(path: &str, entry: &mut TrayEntry) -> Result<()> {
    let data = std::fs::read(path)?;
    let perr = |_: ProtoError| Error::msg("트레이 항목을 해석할 수 없습니다.");
    let f = fields(data.get(8..).unwrap_or(&[])).map_err(perr)?;
    entry.kind = int_or_zero(&f, 2);
    entry.name = text(first(&f, 4));
    entry.description = text(first(&f, 5));
    entry.creator = text(first(&f, 7));
    if let Some(stamp) = first(&f, 11).and_then(|v| v.as_int()) {
        if stamp > 0 {
            if let Some(t) = stamp_to_datetime(stamp) {
                entry.saved_at = Some(t);
            }
        }
    }
    if entry.kind == TYPE_HOUSEHOLD {
        if let Some(specific) = first_bytes(&f, 10) {
            let sf = fields(specific).map_err(perr)?;
            if let Some(household) = first_bytes(&sf, 2) {
                let mut sims = Vec::new();
                for fld in fields(household).map_err(perr)? {
                    if fld.num == 2 {
                        if let Val::Bytes(v) = &fld.val {
                            sims.push(parse_sim(v).map_err(perr)?);
                        }
                    }
                }
                entry.sims = sims;
            }
        }
    }
    Ok(())
}

fn household_sims(path: &str) -> Result<Vec<Vec<Field>>> {
    let data = std::fs::read(path)?;
    if data.len() < 18 || data[16] != 0x0A {
        return Ok(Vec::new());
    }
    let perr = |_: ProtoError| Error::msg("가족 데이터를 해석할 수 없습니다.");
    let (n, p) = proto::varint(&data, 17).map_err(perr)?;
    let start = p.min(data.len());
    let end = p.saturating_add(usize::try_from(n).unwrap_or(usize::MAX)).min(data.len());
    let household = &data[start..end];
    let mut out = Vec::new();
    for fld in fields(household).map_err(perr)? {
        if fld.num == 6 {
            if let Val::Bytes(v) = &fld.val {
                out.push(fields(v).map_err(perr)?);
            }
        }
    }
    Ok(out)
}

fn sim_skills(sim_fields: &[Field]) -> PResult<Vec<TraySkill>> {
    let Some(attrs) = first_bytes(sim_fields, 30) else { return Ok(Vec::new()) };
    let af = fields(attrs)?;
    let Some(tracker) = first_bytes(&af, 13) else { return Ok(Vec::new()) };
    let mut out = Vec::new();
    for fld in fields(tracker)? {
        if fld.num != 1 {
            continue;
        }
        let Val::Bytes(v) = &fld.val else { continue };
        let stat = fields(v)?;
        let sid = first(&stat, 1).and_then(|v| v.as_int()).unwrap_or(0);
        let points = match first(&stat, 2) {
            Some(Val::Bytes(_)) => 0.0,
            Some(v) => {
                let raw = v.as_int().unwrap_or(0);
                // 32비트를 넘으면 원본에서 struct.error 가 난다.
                f32::from_bits(u32::try_from(raw).map_err(|_| ProtoError)?) as f64
            }
            None => f32::from_bits(0) as f64,
        };
        out.push(make_skill(sid, points));
    }
    out.sort_by(|a, b| {
        (a.name.is_empty(), -a.level, a.name.as_str()).cmp(&(b.name.is_empty(), -b.level, b.name.as_str()))
    });
    Ok(out)
}

pub fn load_skills(entry: &mut TrayEntry) {
    let binary = entry.files.iter().find(|f| f.to_lowercase().ends_with(".householdbinary")).cloned();
    let Some(binary) = binary else { return };
    if entry.sims.is_empty() {
        return;
    }
    let Ok(sims) = household_sims(&binary) else { return };
    let mut by_id: HashMap<u64, usize> = HashMap::new();
    for (i, s) in sims.iter().enumerate() {
        by_id.insert(first(s, 1).and_then(|v| v.as_int()).unwrap_or(0), i);
    }
    for (i, sim) in entry.sims.iter_mut().enumerate() {
        // 원본의 `by_id.get(id) or sims[i]`: 찾았더라도 빈 목록이면 순서로 대신한다.
        let src = by_id
            .get(&sim.sim_id)
            .map(|&k| &sims[k])
            .filter(|s| !s.is_empty())
            .or_else(|| sims.get(i));
        if let Some(src) = src {
            sim.skills = sim_skills(src).unwrap_or_default();
        }
    }
}

pub fn group_key(instance: u64) -> u64 {
    instance & GROUP_MASK
}

pub fn load_tray(tray_dir: &str) -> Vec<TrayEntry> {
    let mut groups: IndexMap<u64, TrayEntry> = IndexMap::new();
    if !pyos::isdir(tray_dir) {
        return Vec::new();
    }
    for name in pyos::listdir(tray_dir).unwrap_or_default() {
        let Some((_, inst, _)) = parse_name(&name) else { continue };
        let key = group_key(inst);
        let entry = groups.entry(key).or_insert_with(|| TrayEntry::new(format!("{key:016x}")));
        entry.files.push(pyos::join(tray_dir, &name));
    }
    let mut out = Vec::new();
    for (_, mut entry) in groups {
        let Some(meta) = entry.files.iter().find(|f| f.to_lowercase().ends_with(".trayitem")).cloned() else {
            continue;
        };
        if parse_trayitem(&meta, &mut entry).is_err() && entry.name.is_empty() {
            entry.name = pyos::basename(&meta);
        }
        load_skills(&mut entry);
        out.push(entry);
    }
    out.sort_by(|a, b| b.saved_at.cmp(&a.saved_at));
    out
}

fn xor_key(raw: &[u8]) -> Vec<u8> {
    raw.iter().enumerate().map(|(i, b)| b ^ IMAGE_KEY[i % 8]).collect()
}

pub fn decode_image(path: &str) -> Result<Option<DynamicImage>> {
    let data = std::fs::read(path)?;
    let payload: Vec<u8>;
    if data.starts_with(b"\xff\xd8\xff") {
        payload = data;
    } else if data.len() > 24 {
        let n = u32::from_le_bytes([data[0], data[1], data[2], data[3]]) as usize;
        let raw = &data[24..(24usize.saturating_add(n)).min(data.len())];
        payload = if raw.starts_with(b"\xff\xd8\xff") { raw.to_vec() } else { xor_key(raw) };
    } else {
        return Ok(None);
    }
    Ok(thumbs::jpeg_with_alfa(&payload).ok())
}

/// 게임이 읽는 트레이 이미지 형식으로 인코딩한다 (가운데를 맞춰 잘라 `size` 로).
pub fn encode_image(img: &DynamicImage, size: (u32, u32)) -> Result<Vec<u8>> {
    let rgba = img.to_rgba8();
    let (tw, th) = size;
    let (w, h) = (rgba.width() as f64, rgba.height() as f64);
    let scale = (tw as f64 / w).max(th as f64 / h);
    let nw = tw.max(thumbs::py_round(w * scale) as u32);
    let nh = th.max(thumbs::py_round(h * scale) as u32);
    let resized = thumbs::resize_rgba(&rgba, nw, nh, Filter::Lanczos);
    let (left, top) = ((nw - tw) / 2, (nh - th) / 2);
    let cropped = image::imageops::crop_imm(&resized, left, top, tw, th).to_image();
    let flat = thumbs::flatten(&cropped, [255, 255, 255]);
    let jpeg = thumbs::encode_jpeg(&flat, 92, false)?;
    let payload = xor_key(&jpeg);
    let mut out = Vec::with_capacity(payload.len() + 24);
    out.extend((payload.len() as u32).to_le_bytes());
    out.extend(0u32.to_le_bytes());
    out.extend(IMAGE_MAGIC);
    out.extend(2u32.to_le_bytes());
    out.extend(0u32.to_le_bytes());
    out.extend(payload);
    Ok(out)
}

pub fn tray_ext() -> Vec<&'static str> {
    let mut v: Vec<&'static str> = IMAGE_EXT.to_vec();
    v.extend(DATA_EXT);
    v.push(".trayitem");
    v
}

pub fn is_tray_file(name: &str) -> bool {
    name_rx().is_match(&pyos::basename(name)) && pyos::ends_with_any(&name.to_lowercase(), &tray_ext())
}

const SIM_DROP: [u64; 6] = [15, 20, 44, 46, 68, 72];
const GAMEPLAY_DROP: [u64; 6] = [2, 7, 11, 12, 22, 27];
const ATTR_DROP: [u64; 13] = [1, 6, 12, 13, 14, 15, 16, 18, 23, 26, 30, 31, 36];
const HOUSEHOLD_DROP: [u64; 1] = [12];

pub const CLEARED_LABELS: [&str; 15] = [
    "스킬",
    "직업",
    "대학 학위",
    "열망·업적 진행도",
    "인생 이정표",
    "만족 포인트와 바람",
    "특전(포인트로 산 능력)",
    "수집품",
    "해금한 레시피·곡 등",
    "로열티 수입",
    "조직·동아리 활동",
    "가족 레시피",
    "노트",
    "개인·가족 인벤토리",
    "가족·연인 관계",
];
pub const KEPT_LABELS: [&str; 8] =
    ["이름", "나이", "성별", "외형과 의상", "특성", "열망 종류", "좋아함·싫어함", "초자연 종류"];

fn drop_fields(blob: &[u8], drop: &[u64]) -> PResult<(Vec<u8>, usize)> {
    let parsed = fields(blob)?;
    let total = parsed.len();
    let kept: Vec<Field> = parsed.into_iter().filter(|f| !drop.contains(&f.num)).collect();
    let removed = total - kept.len();
    Ok((encode(&kept), removed))
}

fn clean_sim(sim_blob: &[u8]) -> PResult<(Vec<u8>, usize)> {
    let mut removed = 0;
    let mut out = Vec::new();
    for mut f in fields(sim_blob)? {
        if SIM_DROP.contains(&f.num) {
            removed += 1;
            continue;
        }
        if let Val::Bytes(v) = &f.val {
            if f.num == 30 {
                let (nv, n) = drop_fields(v, &ATTR_DROP)?;
                removed += n;
                f.val = Val::Bytes(nv);
            } else if f.num == 47 {
                let (nv, n) = drop_fields(v, &GAMEPLAY_DROP)?;
                removed += n;
                f.val = Val::Bytes(nv);
            }
        }
        out.push(f);
    }
    Ok((encode(&out), removed))
}

fn rewrite_household(
    data: &[u8],
    clean: bool,
    old_name: &str,
    new_name: &str,
    description: Option<&str>,
) -> PResult<(Vec<u8>, usize)> {
    if data.len() < 16 {
        return Err(ProtoError);
    }
    let le = |at: usize| u32::from_le_bytes([data[at], data[at + 1], data[at + 2], data[at + 3]]);
    let (h0, h2, msglen) = (le(0), le(8), le(12) as usize);
    let msg_end = (16 + msglen).min(data.len());
    let msg = &data[16..msg_end];
    let tail = &data[msg_end..];
    let mut removed = 0;
    let mut top = Vec::new();
    for mut f in fields(msg)? {
        if f.num == 1 {
            if let Val::Bytes(v) = &f.val {
                let mut household: Vec<Field> = Vec::new();
                for mut hf in fields(v)? {
                    let is_bytes = matches!(hf.val, Val::Bytes(_));
                    if hf.num == 6 && is_bytes && clean {
                        let (nv, n) = clean_sim(hf.val.as_bytes().unwrap())?;
                        removed += n;
                        hf.val = Val::Bytes(nv);
                    } else if HOUSEHOLD_DROP.contains(&hf.num) && clean {
                        removed += 1;
                        continue;
                    } else if hf.num == 3 && is_bytes && !new_name.is_empty() && text(Some(&hf.val)) == old_name {
                        hf.val = Val::Bytes(new_name.as_bytes().to_vec());
                    } else if hf.num == 11 && is_bytes && description.is_some() {
                        hf.val = Val::Bytes(description.unwrap().as_bytes().to_vec());
                    }
                    household.push(hf);
                }
                if let Some(desc) = description {
                    if !household.iter().any(|f| f.num == 11) {
                        household.push(Field { num: 11, val: Val::Bytes(desc.as_bytes().to_vec()) });
                    }
                }
                f.val = Val::Bytes(encode(&household));
            }
        }
        top.push(f);
    }
    let new_msg = encode(&top);
    let mut out = Vec::with_capacity(16 + new_msg.len() + tail.len());
    out.extend(h0.to_le_bytes());
    out.extend(((16 + new_msg.len() + tail.len() - 4) as u32).to_le_bytes());
    out.extend(h2.to_le_bytes());
    out.extend((new_msg.len() as u32).to_le_bytes());
    out.extend(&new_msg);
    out.extend(tail);
    Ok((out, removed))
}

/// 지금 시각을 게임 형식(0001-01-01 부터의 초, 현지 시각)으로.
fn now_stamp() -> u64 {
    let now = Local::now().naive_local();
    let days = now.date().num_days_from_ce() as i64 - 1;
    (days * 86400 + now.time().num_seconds_from_midnight() as i64).max(0) as u64
}

fn rewrite_trayitem(data: &[u8], new_name: &str, description: Option<&str>) -> PResult<Vec<u8>> {
    if data.len() < 4 {
        return Err(ProtoError);
    }
    let h0 = u32::from_le_bytes([data[0], data[1], data[2], data[3]]);
    let mut out: Vec<Field> = Vec::new();
    for mut f in fields(data.get(8..).unwrap_or(&[]))? {
        let wt = f.wt();
        if f.num == 4 && wt == 2 && !new_name.is_empty() {
            f.val = Val::Bytes(new_name.as_bytes().to_vec());
        } else if f.num == 5 && wt == 2 && description.is_some() {
            f.val = Val::Bytes(description.unwrap().as_bytes().to_vec());
        } else if f.num == 11 && wt == 0 {
            f.val = Val::Varint(now_stamp());
        }
        out.push(f);
    }
    if let Some(desc) = description {
        if !out.iter().any(|f| f.num == 5) {
            let at = out.iter().position(|f| f.num == 4).map(|i| i + 1).unwrap_or(out.len());
            out.insert(at, Field { num: 5, val: Val::Bytes(desc.as_bytes().to_vec()) });
        }
    }
    let payload = encode(&out);
    let mut res = Vec::with_capacity(8 + payload.len());
    res.extend(h0.to_le_bytes());
    res.extend((payload.len() as u32).to_le_bytes());
    res.extend(payload);
    Ok(res)
}

pub fn instance_of(path: &str) -> u64 {
    parse_name(&pyos::basename(path)).map(|x| x.1).unwrap_or(0)
}

fn related_ids(entry: &TrayEntry, group: u64) -> Result<Vec<u64>> {
    let mut ids: HashSet<u64> = entry.files.iter().map(|f| instance_of(f)).collect();
    for path in &entry.files {
        if pyos::ends_with_any(&path.to_lowercase(), &IMAGE_EXT) {
            continue;
        }
        let data = std::fs::read(path)?;
        for w in data.windows(8) {
            let v = u64::from_le_bytes(w.try_into().unwrap());
            if v & GROUP_MASK == group {
                ids.insert(v);
            }
        }
    }
    let mut out: Vec<u64> = ids.into_iter().filter(|i| i & GROUP_MASK == group).collect();
    out.sort_unstable();
    Ok(out)
}

fn new_group(group: u64, tray_dir: &str) -> Result<u64> {
    let taken: HashSet<u64> = pyos::listdir(tray_dir)?
        .iter()
        .filter_map(|n| parse_name(n))
        .map(|x| group_key(x.1))
        .collect();
    loop {
        let g = (group & !0xFFFF_FFF0u64) | ((fastrand::u32(..) as u64 & 0x0FFF_FFFF) << 4);
        if g != group && !taken.contains(&g) {
            return Ok(g);
        }
    }
}

fn replace_all(data: &[u8], from: &[u8], to: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut last = 0;
    for pos in memchr::memmem::find_iter(data, from) {
        if pos < last {
            continue;
        }
        out.extend_from_slice(&data[last..pos]);
        out.extend_from_slice(to);
        last = pos + from.len();
    }
    out.extend_from_slice(&data[last..]);
    out
}

fn swap_ids(mut data: Vec<u8>, mapping: &[(u64, u64)]) -> Result<Vec<u8>> {
    for &(old, new) in mapping {
        data = replace_all(&data, &old.to_le_bytes(), &new.to_le_bytes());
        let (a, b) = (enc_varint(old), enc_varint(new));
        if a.len() != b.len() {
            return Err(Error::msg("id length changed"));
        }
        data = replace_all(&data, &a, &b);
    }
    Ok(data)
}

#[derive(Debug, Clone, Default)]
pub struct SaveResult {
    pub key: String,
    pub files: Vec<String>,
    pub cleared: usize,
    pub thumbs: usize,
}

/// 트레이 항목을 새 ID 로 복사해 저장한다 (이름·설명·대표 이미지 교체, 외형만 남기기).
pub fn save_copy(
    entry: &TrayEntry,
    tray_dir: &str,
    new_name: &str,
    thumb_image: Option<&str>,
    look_only: bool,
    description: Option<&str>,
) -> Result<SaveResult> {
    let group = u64::from_str_radix(&entry.key, 16).map_err(|e| Error::msg(e.to_string()))?;
    let new_group = new_group(group, tray_dir)?;
    let mapping: Vec<(u64, u64)> =
        related_ids(entry, group)?.into_iter().map(|i| (i, (i & !GROUP_MASK) | new_group)).collect();
    let src_img = match thumb_image {
        Some(p) if !p.is_empty() => Some(thumbs::open_path(p)?),
        _ => None,
    };
    let mut result = SaveResult { key: format!("{new_group:016x}"), ..Default::default() };
    let mut written: Vec<String> = Vec::new();

    let run = |result: &mut SaveResult, written: &mut Vec<String>| -> Result<()> {
        let perr = |_: ProtoError| Error::msg("트레이 파일을 해석할 수 없습니다.");
        let mut planned: Vec<(String, Vec<u8>)> = Vec::new();
        for path in &entry.files {
            let (type_hex, inst, ext) = parse_name(&pyos::basename(path))
                .ok_or_else(|| Error::msg(format!("트레이 파일 이름이 아닙니다: {path}")))?;
            let low = path.to_lowercase();
            let mut data = std::fs::read(path)?;
            if pyos::ends_with_any(&low, &MAIN_IMAGE_EXT) && src_img.is_some() {
                let old = decode_image(path)?;
                let size = old.map(|o| (o.width(), o.height())).unwrap_or((591, 394));
                data = encode_image(src_img.as_ref().unwrap(), size)?;
                result.thumbs += 1;
            } else if !pyos::ends_with_any(&low, &IMAGE_EXT) {
                data = swap_ids(data, &mapping)?;
                if low.ends_with(".trayitem") {
                    data = rewrite_trayitem(&data, new_name, description).map_err(perr)?;
                } else if low.ends_with(".householdbinary") {
                    let (d, n) =
                        rewrite_household(&data, look_only, &entry.name, new_name, description).map_err(perr)?;
                    data = d;
                    result.cleared += n;
                }
            }
            let inst = mapping.iter().find(|(o, _)| *o == inst).map(|(_, n)| *n).unwrap_or(inst);
            let name = format!("0x{type_hex}!0x{inst:016x}.{ext}");
            planned.push((pyos::join(tray_dir, &name), data));
        }
        for (dst, data) in &planned {
            if pyos::exists(dst) {
                return Err(Error::msg(dst.clone()));
            }
            let tmp = format!("{dst}.s4ccm_tmp");
            std::fs::File::create(&tmp)?.write_all(data)?;
            written.push(tmp);
        }
        for (dst, _) in &planned {
            let tmp = format!("{dst}.s4ccm_tmp");
            std::fs::rename(&tmp, dst)?;
            written.retain(|w| *w != tmp);
            written.push(dst.clone());
            result.files.push(dst.clone());
        }
        Ok(())
    };

    if let Err(e) = run(&mut result, &mut written) {
        for f in &written {
            let _ = std::fs::remove_file(f);
        }
        return Err(e);
    }
    Ok(result)
}

pub fn delete_entry(entry: &TrayEntry) -> Result<usize> {
    let mut done = 0;
    for path in &entry.files {
        if pyos::exists(path) {
            trash::delete(path)?;
            done += 1;
        }
    }
    Ok(done)
}

pub fn safe_name(name: &str, fallback: &str) -> String {
    let replaced: String =
        name.chars().map(|c| if r#"<>:"/\|?*"#.contains(c) { '_' } else { c }).collect();
    let trimmed = replaced.trim_matches(|c| c == ' ' || c == '.');
    if trimmed.is_empty() {
        fallback.to_string()
    } else {
        trimmed.to_string()
    }
}

pub const EXPORT_README: &str = "이 압축 파일은 S4CCM으로 내보낸 심즈 4 트레이 항목입니다.

[S4CCM이 있다면]
압축 파일을 그대로 S4CCM 창에 끌어다 놓으세요. 트레이 파일과 CC를 알아서 나눠 넣습니다.

[직접 넣는다면]
· Tray 폴더 안의 파일 → 문서\\Electronic Arts\\The Sims 4\\Tray
· Mods 폴더 안의 폴더 → 문서\\Electronic Arts\\The Sims 4\\Mods
그 다음 게임을 켜고 라이브러리에서 찾으면 됩니다.
";

fn zip_time(path: &str) -> zip::DateTime {
    let fallback = zip::DateTime::default();
    let Ok(meta) = std::fs::metadata(path) else { return fallback };
    let Ok(t) = meta.modified() else { return fallback };
    let dt: chrono::DateTime<Local> = t.into();
    zip::DateTime::from_date_and_time(
        dt.year().clamp(1980, 2107) as u16,
        dt.month() as u8,
        dt.day() as u8,
        dt.hour() as u8,
        dt.minute() as u8,
        dt.second() as u8,
    )
    .unwrap_or(fallback)
}

fn zip_now() -> zip::DateTime {
    let dt = Local::now();
    zip::DateTime::from_date_and_time(
        dt.year().clamp(1980, 2107) as u16,
        dt.month() as u8,
        dt.day() as u8,
        dt.hour() as u8,
        dt.minute() as u8,
        dt.second() as u8,
    )
    .unwrap_or_default()
}

/// 트레이 항목(과 사용된 CC)을 zip 하나로 내보낸다.
pub fn export_zip(entry: &TrayEntry, out_dir: &str, cc: Option<&[Item]>, lib: &Library) -> Result<String> {
    use zip::write::SimpleFileOptions;
    let title = safe_name(&entry.export_name(), "tray");
    let base = pyos::join(out_dir, &title);
    let mut path = format!("{base}.zip");
    let mut n = 2;
    while pyos::exists(&path) {
        path = format!("{base} ({n}).zip");
        n += 1;
    }
    let mut z = zip::ZipWriter::new(std::fs::File::create(&path)?);
    let opts = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let mut readme = vec![0xEF, 0xBB, 0xBF];
    readme.extend(EXPORT_README.replace('\n', "\r\n").as_bytes());
    z.start_file("읽어주세요.txt", opts.last_modified_time(zip_now()))?;
    z.write_all(&readme)?;
    let add = |z: &mut zip::ZipWriter<std::fs::File>, src: &str, arc: String| -> Result<()> {
        let big = pyos::getsize(src).unwrap_or(0) >= 0xFFFF_FFFF;
        z.start_file(arc, opts.last_modified_time(zip_time(src)).large_file(big))?;
        std::io::copy(&mut std::fs::File::open(src)?, z)?;
        Ok(())
    };
    for f in &entry.files {
        add(&mut z, f, format!("Tray/{}", pyos::basename(f)))?;
    }
    for it in cc.unwrap_or(&[]) {
        let src = lib.abs(&it.rel_path);
        if pyos::isfile(&src) {
            let rel = it.rel_path.split_once('/').map(|x| x.1).unwrap_or(&it.rel_path);
            add(&mut z, &src, format!("Mods/{title} CC/{rel}"))?;
        }
    }
    z.finish()?;
    Ok(path)
}

pub fn entry_thumb(entry: &TrayEntry, cache_dir: &str) -> Result<Option<String>> {
    let images = entry.images()?;
    let Some(src) = images.first() else { return Ok(None) };
    let out = pyos::join(cache_dir, &format!("tray_{}_{}{THUMB_EXT}", entry.key, pyos::getmtime(src)? as i64));
    if pyos::exists(&out) {
        return Ok(Some(out));
    }
    match decode_image(src)? {
        Some(img) => Ok(Some(thumbs::save(&img, &out)?)),
        None => Ok(None),
    }
}

pub fn sim_thumbs(entry: &TrayEntry, cache_dir: &str) -> Result<Vec<String>> {
    let mut out = Vec::new();
    let mut srcs: Vec<&String> = entry.files.iter().filter(|f| f.to_lowercase().ends_with(".sgi")).collect();
    srcs.sort();
    for src in srcs {
        let dst = pyos::join(
            cache_dir,
            &format!("tray_{}_{}{THUMB_EXT}", pyos::basename(src).replace('!', "_"), pyos::getmtime(src)? as i64),
        );
        if !pyos::exists(&dst) {
            match decode_image(src)? {
                Some(img) => {
                    thumbs::save(&img, &dst)?;
                }
                None => continue,
            }
        }
        out.push(dst);
    }
    Ok(out)
}

/// 파일 안에서 리소스 ID 일 수 있는 값을 모두 훑어 `id_map` 에 있는 것만 돌려준다.
fn matching_ids<'a>(data: &[u8], id_map: &'a HashMap<u64, HashSet<String>>) -> Vec<&'a HashSet<String>> {
    let mut hits: HashMap<u64, &HashSet<String>> = HashMap::new();
    for w in data.windows(8) {
        let v = u64::from_le_bytes(w.try_into().unwrap());
        if let Some(fps) = id_map.get(&v) {
            hits.insert(v, fps);
        }
    }
    // varint 로 적힌 ID: 상위 비트가 선 바이트가 4개 이상 이어지고 그 뒤에 끝 바이트 하나.
    let n = data.len();
    let mut i = 0;
    while i < n {
        if data[i] < 0x80 {
            i += 1;
            continue;
        }
        let run_start = i;
        while i < n && data[i] >= 0x80 {
            i += 1;
        }
        if i - run_start >= 4 && i < n {
            let end = i + 1;
            let from = run_start.max(end.saturating_sub(10));
            for start in from..end.saturating_sub(4) {
                let mut r: u128 = 0;
                for (k, b) in data[start..end].iter().enumerate() {
                    r |= ((b & 0x7F) as u128) << (7 * k);
                }
                if r > 0xFFFF_FFFF {
                    if let Ok(v) = u64::try_from(r) {
                        if let Some(fps) = id_map.get(&v) {
                            hits.insert(v, fps);
                        }
                    }
                }
            }
            i = end;
        }
    }
    hits.into_values().collect()
}

/// 이 트레이 항목이 쓰는 CC (설치된 패키지 중에서).
pub fn used_cc(
    lib: &Library,
    entry: &TrayEntry,
    id_map: Option<&HashMap<u64, HashSet<String>>>,
) -> Result<Vec<Item>> {
    let owned;
    let id_map = match id_map {
        Some(m) => m,
        None => {
            owned = lib.db.res_id_map()?;
            &owned
        }
    };
    let mut fps: HashSet<&str> = HashSet::new();
    for path in entry.data_files() {
        let Ok(data) = std::fs::read(&path) else { continue };
        for set in matching_ids(&data, id_map) {
            fps.extend(set.iter().map(|s| s.as_str()));
        }
    }
    let mut items: Vec<Item> =
        lib.items()?.into_iter().filter(|it| fps.contains(it.fingerprint.as_str()) && it.kind == "package").collect();
    items.sort_by(|a, b| {
        (a.status != "active", a.name.to_lowercase()).cmp(&(b.status != "active", b.name.to_lowercase()))
    });
    let mut seen: HashSet<String> = HashSet::new();
    Ok(items.into_iter().filter(|it| seen.insert(it.fingerprint.clone())).collect())
}
