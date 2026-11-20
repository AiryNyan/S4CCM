//! 중복 찾기: 완전히 같은 파일, 같은 아이템(리소스)을 담은 파일.

use std::collections::{HashMap, HashSet};
use std::io::Read;

use indexmap::IndexMap;
use rayon::prelude::*;
use sha1::{Digest, Sha1};

use crate::classify::{T_CASP, T_COBJ, T_OBJD};
use crate::db::Item;
use crate::dbpf::Package;
use crate::error::Result;
use crate::library::{Library, Progress};

pub const SAME_FILE: &str = "same_file";
pub const SAME_ITEM: &str = "same_item";
const ITEM_TYPES: [u32; 3] = [T_CASP, T_OBJD, T_COBJ];

#[derive(Debug, Clone)]
pub struct DupGroup {
    pub kind: &'static str,
    pub items: Vec<Item>,
    pub shared: usize,
    /// 남기라고 권하는 항목의 id.
    pub keep: Option<i64>,
    /// item id → (다른 파일과 겹치는 아이템 수, 이 파일의 아이템 수)
    pub overlap: IndexMap<i64, (usize, usize)>,
}

impl DupGroup {
    pub fn title(&self) -> String {
        if self.kind == SAME_FILE {
            return format!("완전히 같은 파일 {}개", self.items.len());
        }
        format!("같은 아이템이 들어 있는 파일 {}개 (아이템 {}개 겹침)", self.items.len(), self.shared)
    }
}

fn full_hash(path: &str) -> std::io::Result<String> {
    let mut h = Sha1::new();
    let mut f = std::fs::File::open(path)?;
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// 사용 중인 것, 그다음 최근 것. 동률이면 앞의 것.
fn suggest_keep(items: &[Item]) -> Option<i64> {
    let mut best: Option<&Item> = None;
    for it in items {
        let better = match best {
            None => true,
            Some(b) => (it.status == "active", it.mtime) > (b.status == "active", b.mtime),
        };
        if better {
            best = Some(it);
        }
    }
    best.map(|b| b.id)
}

fn unit_content(lib: &Library, it: &Item) -> Option<Vec<String>> {
    let mut files: Vec<&String> = it
        .members
        .iter()
        .filter(|m| {
            let low = m.to_lowercase();
            low.ends_with(".ts4script") || low.ends_with(".package")
        })
        .collect();
    if files.is_empty() {
        files.push(&it.rel_path);
    }
    let mut hashes: Vec<String> = files.iter().map(|m| full_hash(&lib.abs(m))).collect::<std::io::Result<_>>().ok()?;
    hashes.sort();
    Some(hashes)
}

fn find(parent: &mut HashMap<i64, i64>, mut x: i64) -> i64 {
    loop {
        let p = *parent.entry(x).or_insert(x);
        if p == x {
            return x;
        }
        let gp = *parent.entry(p).or_insert(p);
        parent.insert(x, gp);
        x = gp;
    }
}

pub fn find_duplicates(lib: &Library, mut progress: Progress) -> Result<Vec<DupGroup>> {
    let items: Vec<Item> =
        lib.items()?.into_iter().filter(|i| i.status == "active" || i.status == "disabled").collect();
    let mut groups: Vec<DupGroup> = Vec::new();

    let mut by_fp: IndexMap<&str, Vec<&Item>> = IndexMap::new();
    for it in &items {
        by_fp.entry(&it.fingerprint).or_default().push(it);
    }
    let mut exact_ids: HashSet<Vec<i64>> = HashSet::new();
    for same in by_fp.values().filter(|v| v.len() > 1) {
        let mut clusters: IndexMap<Vec<String>, Vec<&Item>> = IndexMap::new();
        if same[0].kind == "package" {
            let hashes: Vec<Option<String>> =
                same.par_iter().map(|it| full_hash(&lib.abs(&it.rel_path)).ok()).collect();
            for (it, h) in same.iter().zip(hashes) {
                if let Some(h) = h {
                    clusters.entry(vec![h]).or_default().push(it);
                }
            }
        } else {
            for it in same {
                if let Some(key) = unit_content(lib, it) {
                    clusters.entry(key).or_default().push(it);
                }
            }
        }
        for c in clusters.into_values().filter(|v| v.len() > 1) {
            let members: Vec<Item> = c.into_iter().cloned().collect();
            let mut ids: Vec<i64> = members.iter().map(|i| i.id).collect();
            ids.sort_unstable();
            exact_ids.insert(ids);
            groups.push(DupGroup {
                kind: SAME_FILE,
                keep: suggest_keep(&members),
                items: members,
                shared: 0,
                overlap: IndexMap::new(),
            });
        }
    }

    let packages: Vec<&Item> = items.iter().filter(|i| i.kind == "package").collect();
    let mut owners: IndexMap<(u32, u64), Vec<i64>> = IndexMap::new();
    let mut item_keys: HashMap<i64, HashSet<(u32, u64)>> = HashMap::new();
    const CHUNK: usize = 256;
    for (ci, chunk) in packages.chunks(CHUNK).enumerate() {
        let read: Vec<Option<Vec<(u32, u64)>>> = chunk
            .par_iter()
            .map(|it| {
                let pk = Package::open(&lib.abs(&it.rel_path)).ok()?;
                Some(pk.by_type(&ITEM_TYPES).map(|e| (e.type_, e.instance)).collect())
            })
            .collect();
        for (k, (it, keys)) in chunk.iter().zip(read).enumerate() {
            let n = ci * CHUNK + k;
            if n % 10 == 0 {
                if let Some(p) = progress.as_deref_mut() {
                    p(n, packages.len(), &it.name);
                }
            }
            let Some(keys) = keys else { continue };
            let mut set: HashSet<(u32, u64)> = HashSet::new();
            for key in keys {
                if set.insert(key) {
                    owners.entry(key).or_default().push(it.id);
                }
            }
            item_keys.insert(it.id, set);
        }
    }

    let mut parent: HashMap<i64, i64> = HashMap::new();
    for ids in owners.values().filter(|ids| ids.len() >= 2) {
        for other in &ids[1..] {
            let (a, b) = (find(&mut parent, *other), find(&mut parent, ids[0]));
            parent.insert(a, b);
        }
    }
    let mut shared_keys: HashMap<i64, HashSet<(u32, u64)>> = HashMap::new();
    for (k, ids) in owners.iter().filter(|(_, ids)| ids.len() >= 2) {
        let root = find(&mut parent, ids[0]);
        shared_keys.entry(root).or_default().insert(*k);
    }

    let mut clusters: IndexMap<i64, Vec<&Item>> = IndexMap::new();
    for it in &packages {
        if parent.contains_key(&it.id) {
            let root = find(&mut parent, it.id);
            clusters.entry(root).or_default().push(it);
        }
    }
    let empty = HashSet::new();
    for (root, members) in clusters {
        let mut ids: Vec<i64> = members.iter().map(|i| i.id).collect();
        ids.sort_unstable();
        if members.len() < 2 || exact_ids.contains(&ids) {
            continue;
        }
        let owned: Vec<Item> = members.iter().map(|m| (*m).clone()).collect();
        let mut g = DupGroup {
            kind: SAME_ITEM,
            keep: suggest_keep(&owned),
            shared: shared_keys.get(&root).map_or(0, |s| s.len()),
            items: owned,
            overlap: IndexMap::new(),
        };
        for m in &members {
            let mine = item_keys.get(&m.id).unwrap_or(&empty);
            let others: HashSet<&(u32, u64)> = members
                .iter()
                .filter(|o| o.id != m.id)
                .flat_map(|o| item_keys.get(&o.id).unwrap_or(&empty).iter())
                .collect();
            g.overlap.insert(m.id, (mine.iter().filter(|k| others.contains(k)).count(), mine.len()));
        }
        groups.push(g);
    }

    if let Some(p) = progress.as_deref_mut() {
        p(packages.len(), packages.len(), "완료");
    }
    groups.sort_by_key(|g| (g.kind != SAME_FILE, std::cmp::Reverse(g.items.len())));
    Ok(groups)
}
