//! 세이브 파일에서 심 이름과 관계(우정·애정) 읽기.

use std::collections::HashMap;
use std::sync::Arc;

use indexmap::IndexMap;
use parking_lot::Mutex;

use crate::dbpf::Package;
use crate::error::{Error, Result};
use crate::proto::{lazy_fields, LVal, PResult};
use crate::pyos;

const T_SAVE_DATA: u32 = 0x0000000D;
const TRACK_FRIENDSHIP: u64 = 16650;
const TRACK_ROMANCE: u64 = 16651;

#[derive(Debug, Clone, PartialEq)]
pub struct Relation {
    pub name: String,
    pub friendship: Option<f64>,
    pub romance: Option<f64>,
}

#[derive(Debug, Clone, Default)]
pub struct SaveIndex {
    pub path: String,
    pub names: HashMap<u64, String>,
    pub by_name: HashMap<String, Vec<u64>>,
    pub relations: IndexMap<u64, Vec<(u64, Option<f64>, Option<f64>)>>,
}

fn cache() -> &'static Mutex<HashMap<String, (f64, Arc<SaveIndex>)>> {
    static C: std::sync::OnceLock<Mutex<HashMap<String, (f64, Arc<SaveIndex>)>>> = std::sync::OnceLock::new();
    C.get_or_init(|| Mutex::new(HashMap::new()))
}

fn first<'a>(d: &'a [u8], num: u64) -> PResult<Option<LVal<'a>>> {
    for f in lazy_fields(d) {
        let (n, _wt, v) = f?;
        if n == num {
            return Ok(Some(v));
        }
    }
    Ok(None)
}

/// 32비트 float 비트열 → 소수 첫째 자리 반올림 (`round(x, 1)`).
fn float1(raw: u64) -> PResult<f64> {
    let bits = u32::try_from(raw).map_err(|_| crate::proto::ProtoError)?;
    let x = f32::from_bits(bits) as f64;
    Ok(format!("{x:.1}").parse::<f64>().unwrap_or(x))
}

pub fn name_key(first: &str, last: &str) -> String {
    format!("{last}{first}").replace(' ', "")
}

fn lossy(b: &[u8]) -> String {
    String::from_utf8_lossy(b).into_owned()
}

fn parse_save(path: &str, data: &[u8]) -> PResult<SaveIndex> {
    let mut index = SaveIndex { path: path.to_string(), ..Default::default() };
    let mut world: Option<&[u8]> = None;
    for f in lazy_fields(data) {
        let (n, wt, v) = f?;
        if n == 6 && wt == 2 {
            let LVal::Bytes(v) = v else { continue };
            let (mut sid, mut first_name, mut last_name): (Option<u64>, Option<String>, Option<String>) =
                (None, None, None);
            for g in lazy_fields(v) {
                let (fnum, _w, x) = g?;
                match (fnum, &x) {
                    (1, LVal::Int(i)) => sid = Some(*i),
                    (5, LVal::Bytes(b)) => first_name = Some(lossy(b)),
                    (6, LVal::Bytes(b)) => last_name = Some(lossy(b)),
                    // 이름 칸이 문자열이 아니면 원본은 여기서 예외로 세이브 전체를 건너뛴다.
                    (5, LVal::Int(_)) | (6, LVal::Int(_)) => return Err(crate::proto::ProtoError),
                    _ => {}
                }
                if sid.is_some() && first_name.is_some() && last_name.is_some() {
                    break;
                }
            }
            if let Some(sid) = sid {
                let (fst, lst) = (first_name.unwrap_or_default(), last_name.unwrap_or_default());
                index.names.insert(sid, format!("{lst} {fst}").trim().to_string());
                index.by_name.entry(name_key(&fst, &lst)).or_default().push(sid);
            }
        } else if n == 2 && wt == 2 {
            if let LVal::Bytes(v) = v {
                world = Some(v);
            }
        }
    }
    let service = match world {
        Some(w) if !w.is_empty() => first(w, 8)?,
        _ => None,
    };
    let rel_list = match service {
        Some(LVal::Bytes(s)) => first(s, 13)?,
        _ => None,
    };
    if let Some(LVal::Bytes(rel_list)) = rel_list {
        for f in lazy_fields(rel_list) {
            let (n, wt, v) = f?;
            if n != 1 || wt != 2 {
                continue;
            }
            let LVal::Bytes(v) = v else { continue };
            let (mut a, mut b): (Option<u64>, Option<u64>) = (None, None);
            let (mut friend, mut romance): (Option<f64>, Option<f64>) = (None, None);
            for g in lazy_fields(v) {
                let (fnum, _w, x) = g?;
                match (fnum, x) {
                    (1, LVal::Int(i)) => a = Some(i),
                    (2, LVal::Int(i)) => b = Some(i),
                    (3, LVal::Bytes(x)) => {
                        for h in lazy_fields(x) {
                            let (gnum, _w2, y) = h?;
                            let LVal::Bytes(y) = y else { continue };
                            if gnum != 3 {
                                continue;
                            }
                            let mut track: HashMap<u64, LVal> = HashMap::new();
                            for t in lazy_fields(y) {
                                let (hnum, _w3, z) = t?;
                                track.insert(hnum, z);
                            }
                            let value = |track: &HashMap<u64, LVal>| -> PResult<Option<f64>> {
                                match track.get(&2) {
                                    Some(LVal::Int(raw)) => Ok(Some(float1(*raw)?)),
                                    _ => Ok(None),
                                }
                            };
                            match track.get(&1) {
                                Some(LVal::Int(TRACK_FRIENDSHIP)) => friend = value(&track)?,
                                Some(LVal::Int(TRACK_ROMANCE)) => romance = value(&track)?,
                                _ => {}
                            }
                        }
                    }
                    _ => {}
                }
            }
            if let (Some(a), Some(b)) = (a, b) {
                index.relations.entry(a).or_default().push((b, friend, romance));
                index.relations.entry(b).or_default().push((a, friend, romance));
            }
        }
    }
    Ok(index)
}

pub fn load_save(path: &str) -> Result<Option<Arc<SaveIndex>>> {
    let mtime = pyos::getmtime(path)?;
    if let Some((t, idx)) = cache().lock().get(path) {
        if *t == mtime {
            return Ok(Some(idx.clone()));
        }
    }
    let data = {
        let mut pk = Package::open(path)?;
        let Some(entry) = pk.entries.iter().find(|e| e.type_ == T_SAVE_DATA).copied() else {
            return Ok(None);
        };
        pk.read(&entry)?
    };
    let index =
        Arc::new(parse_save(path, &data).map_err(|_| Error::msg("세이브 데이터를 해석할 수 없습니다."))?);
    cache().lock().insert(path.to_string(), (mtime, index.clone()));
    Ok(Some(index))
}

/// `Slot_*.save` 를 최근 수정 순으로.
pub fn save_files(saves_dir: &str) -> Vec<String> {
    let mut files: Vec<(f64, String)> = Vec::new();
    for name in pyos::listdir(saves_dir).unwrap_or_default() {
        let low = name.to_lowercase();
        if low.starts_with("slot_") && low.ends_with(".save") {
            let p = pyos::join(saves_dir, &name);
            if pyos::isfile(&p) {
                if let Ok(t) = pyos::getmtime(&p) {
                    files.push((t, p));
                }
            }
        }
    }
    files.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    files.into_iter().map(|x| x.1).collect()
}

/// 가장 최근 세이브에서 이 이름의 심을 찾아 관계 목록을 돌려준다.
pub fn find_relations(saves_dir: &str, first: &str, last: &str) -> Option<(String, Vec<Relation>)> {
    let key = name_key(first, last);
    for path in save_files(saves_dir) {
        let Ok(Some(index)) = load_save(&path) else { continue };
        let Some(ids) = index.by_name.get(&key) else { continue };
        let sid = ids[0];
        let mut rels: Vec<Relation> = index
            .relations
            .get(&sid)
            .map(|v| v.as_slice())
            .unwrap_or(&[])
            .iter()
            .map(|(other, fr, ro)| {
                let name = index.names.get(other).filter(|n| !n.is_empty()).cloned();
                Relation { name: name.unwrap_or_else(|| "(이름 없음)".to_string()), friendship: *fr, romance: *ro }
            })
            .collect();
        rels.sort_by(|a, b| {
            let ka = (a.friendship.is_none(), -a.friendship.unwrap_or(0.0), -a.romance.unwrap_or(0.0));
            let kb = (b.friendship.is_none(), -b.friendship.unwrap_or(0.0), -b.romance.unwrap_or(0.0));
            ka.partial_cmp(&kb).unwrap_or(std::cmp::Ordering::Equal)
        });
        return Some((path, rels));
    }
    None
}
