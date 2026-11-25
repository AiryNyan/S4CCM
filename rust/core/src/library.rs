//! 설치된 모드 목록: 스캔, 이동(정리·활성/비활성), 되돌리기, 삭제.

use std::collections::HashSet;
use std::hash::{Hash, Hasher};
use std::io::{Read, Seek, SeekFrom};

use indexmap::IndexMap;
use parking_lot::{Mutex, ReentrantMutex, RwLock};
use rayon::prelude::*;
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use crate::categories as cats;
use crate::classify::{self, Classification};
use crate::config::{app_dir, thumbs_dir, Config};
use crate::db::{Db, Fields, Item, ItemRow};
use crate::dbpf::Package;
use crate::error::{Error, Result};
use crate::paths::{DISABLED_DIR, QUARANTINE_DIR};
use crate::poseicon;
use crate::pyos;
use crate::scriptmods;
use crate::thumbs::{self, ThumbCache, THUMB_EXT};

pub const ROOTS: [(&str, &str); 3] = [("Mods", "active"), (DISABLED_DIR, "disabled"), (QUARANTINE_DIR, "quarantine")];
pub const IMAGE_EXT: [&str; 5] = [".png", ".jpg", ".jpeg", ".webp", ".bmp"];
const LEGACY_OVERRIDE_DIRS: [&str; 2] = ["overrides", "00_overrides"];
pub const ORGANIZE_LABEL: &str = "자동 정리";
const DEEP_SCRIPT_WARNING: &str = ".ts4script가 Mods 기준 2단계 이상 깊어 게임이 읽지 못합니다.";

/// (완료 수, 전체 수, 현재 항목)
pub type Progress<'a> = Option<&'a mut (dyn FnMut(usize, usize, &str) + 'a)>;

/// 콜백을 넘겨주고도 계속 쓸 수 있게 잠깐 빌린다.
pub fn reborrow<'s>(p: &'s mut Progress<'_>) -> Progress<'s> {
    match p {
        Some(f) => Some(&mut **f),
        None => None,
    }
}

fn report(progress: &mut Progress, done: usize, total: usize, what: &str) {
    if let Some(p) = progress.as_deref_mut() {
        p(done, total, what);
    }
}

pub fn status_root(status: &str) -> Option<&'static str> {
    ROOTS.iter().find(|r| r.1 == status).map(|r| r.0)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 크기 + 앞뒤 64KB 로 만드는 빠른 지문(24자).
pub fn fingerprint(path: &str) -> std::io::Result<String> {
    let mut f = std::fs::File::open(path)?;
    let size = f.metadata()?.len();
    let mut h = Sha1::new();
    h.update(size.to_string().as_bytes());
    let mut buf = Vec::with_capacity(65536);
    (&mut f).take(65536).read_to_end(&mut buf)?;
    h.update(&buf);
    if size > 131072 {
        f.seek(SeekFrom::End(-65536))?;
        buf.clear();
        (&mut f).take(65536).read_to_end(&mut buf)?;
        h.update(&buf);
    }
    Ok(hex(&h.finalize())[..24].to_string())
}

/// 파일 읽기 전용 스레드 묶음. 스캔은 디스크를 기다리는 시간이 대부분이라 CPU 수보다 많이 동시에 읽는다.
/// (CPU 수 × 2, 최소 8 · 최대 16. 측정용으로 환경 변수 `S4CCM_IO_THREADS` 로 바꿀 수 있다.)
pub fn io_pool() -> &'static rayon::ThreadPool {
    static POOL: std::sync::OnceLock<rayon::ThreadPool> = std::sync::OnceLock::new();
    POOL.get_or_init(|| {
        let cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4);
        let n = std::env::var("S4CCM_IO_THREADS")
            .ok()
            .and_then(|v| v.parse::<usize>().ok())
            .filter(|&n| (1..=64).contains(&n))
            .unwrap_or_else(|| (cpus * 2).clamp(8, 16));
        rayon::ThreadPoolBuilder::new()
            .num_threads(n)
            .thread_name(|i| format!("s4ccm-io-{i}"))
            .build()
            .expect("io thread pool")
    })
}

/// 지문을 계산하면서 같은 파일 핸들과 읽은 앞·뒷부분으로 패키지도 연다 (스캔용).
/// 지문은 `fingerprint` 와 똑같다. 패키지가 아니거나 깨졌으면 두 번째 값이 오류다.
pub fn open_fingerprinted(path: &str) -> std::io::Result<(String, std::result::Result<Package, crate::dbpf::DbpfError>)> {
    let mut f = std::fs::File::open(path)?;
    let size = f.metadata()?.len();
    let mut h = Sha1::new();
    h.update(size.to_string().as_bytes());
    let mut head = Vec::with_capacity(65536);
    (&mut f).take(65536).read_to_end(&mut head)?;
    h.update(&head);
    let mut tail = None;
    if size > 131072 {
        f.seek(SeekFrom::End(-65536))?;
        let mut buf = Vec::with_capacity(65536);
        (&mut f).take(65536).read_to_end(&mut buf)?;
        h.update(&buf);
        tail = Some((size - buf.len() as u64, buf));
    }
    let fp = hex(&h.finalize())[..24].to_string();
    let pk = Package::open_from_parts(path, f, &head, tail.as_ref().map(|(s, b)| (*s, b.as_slice())));
    Ok((fp, pk))
}

/// 이름의 첫 구분자(`_ - . 공백`) 앞부분, 소문자.
pub fn token(name: &str) -> String {
    name.split(|c: char| c == '_' || c == '-' || c == '.' || c.is_whitespace()).next().unwrap_or("").to_lowercase()
}

fn is_script(f: &str) -> bool {
    f.to_lowercase().ends_with(".ts4script")
}

fn is_package(f: &str) -> bool {
    f.to_lowercase().ends_with(".package")
}

fn script_groups(files: &[String]) -> (Vec<Vec<String>>, HashSet<String>) {
    let mut by_tok: IndexMap<String, Vec<String>> = IndexMap::new();
    for f in files {
        if is_script(f) {
            by_tok.entry(token(&pyos::basename(f))).or_default().push(f.clone());
        }
    }
    let mut groups = Vec::new();
    let mut claimed = HashSet::new();
    for (tok, scripts) in by_tok {
        let mut members = scripts;
        members.sort();
        members.extend(files.iter().filter(|f| !is_script(f) && token(&pyos::basename(f)) == tok).cloned());
        claimed.extend(members.iter().cloned());
        groups.push(members);
    }
    (groups, claimed)
}

fn is_own_folder(files: &[String]) -> bool {
    let scripts: Vec<&String> = files.iter().filter(|f| is_script(f)).collect();
    let dirs: HashSet<String> = scripts.iter().map(|f| pyos::dirname(f)).collect();
    let toks: HashSet<String> = scripts.iter().map(|f| token(&pyos::basename(f))).collect();
    files
        .iter()
        .filter(|f| is_package(f) && dirs.contains(&pyos::dirname(f)))
        .all(|f| toks.contains(&token(&pyos::basename(f))))
}

pub fn to_rel(sims_dir: &str, abs_path: &str) -> String {
    pyos::relpath(abs_path, sims_dir).replace('\\', "/")
}

#[derive(Debug, Clone)]
pub struct MovePlan {
    pub item: Item,
    pub target_rel: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ScanStats {
    pub new: usize,
    pub updated: usize,
    pub removed: usize,
    pub total: usize,
}

/// 스크립트 모드 한 단위: (rel_path, status, members, warning)
type Unit = (String, &'static str, Vec<String>, Option<&'static str>);

/// 패키지 한 개에 대해 미리(병렬로) 계산해 둔 결과.
enum Work {
    Missing,
    Unchanged { backfill: Option<Backfill> },
    Failed(String),
    SameFp { fp: String, size: i64, mtime: f64 },
    Fresh(Box<Fresh>),
}

struct Backfill {
    tex: Option<Option<(u32, u32)>>,
    ids: Vec<u64>,
    /// 포즈 팩 아이콘을 찾아봤으면 Some (찾은 썸네일 경로).
    pose: Option<Option<String>>,
    /// 중복 검사용 아이템 키를 새로 읽었으면 Some.
    keys: Option<Vec<(u32, u64)>>,
}

struct Fresh {
    fp: String,
    size: i64,
    mtime: f64,
    c: Classification,
    thumb: Option<String>,
    tex: Option<(u32, u32)>,
    ids: Vec<u64>,
    keys: Vec<(u32, u64)>,
}

fn tex_json(tex: Option<(u32, u32)>) -> Value {
    match tex {
        Some((w, h)) => json!([w, h]),
        None => Value::Null,
    }
}

/// 같은 지문의 썸네일을 두 스레드가 동시에 쓰지 않게 하는 잠금.
fn thumb_lock(fp: &str) -> &'static Mutex<()> {
    static LOCKS: [Mutex<()>; 64] = [const { Mutex::new(()) }; 64];
    let mut h = std::collections::hash_map::DefaultHasher::new();
    fp.hash(&mut h);
    &LOCKS[(h.finish() % 64) as usize]
}

pub enum UserImage<'a> {
    Path(&'a str),
    Bytes(&'a [u8]),
}

pub struct Library {
    pub config: RwLock<Config>,
    op_lock: ReentrantMutex<()>,
    pub db: Db,
    pub thumbcache: ThumbCache,
}

impl Library {
    pub fn new(config: Config) -> Result<Library> {
        let db = Db::open(&pyos::join(&app_dir(), "s4ccm.db"))?;
        let cache = if config.sims_dir.is_empty() {
            None
        } else {
            Some(pyos::join(&config.sims_dir, "localthumbcache.package"))
        };
        let lib = Library {
            config: RwLock::new(config),
            op_lock: ReentrantMutex::new(()),
            db,
            thumbcache: ThumbCache::new(cache),
        };
        lib.migrate_thumbs()?;
        Ok(lib)
    }

    /// 다른 작업과 겹치지 않게 잡는 잠금 (같은 스레드에서는 다시 잡을 수 있다).
    pub fn lock_ops(&self) -> parking_lot::ReentrantMutexGuard<'_, ()> {
        self.op_lock.lock()
    }

    fn migrate_thumbs(&self) -> Result<()> {
        if self.db.kv_get("thumbs_jpg")?.map_or(false, |v| truthy(&v)) {
            return Ok(());
        }
        let mut converted = Vec::new();
        let dir = thumbs_dir();
        for name in pyos::listdir(&dir)? {
            if name.to_lowercase().ends_with(".png") {
                let src = pyos::join(&dir, &name);
                if let Some(dst) = thumbs::convert_to_jpg(&src) {
                    converted.push((src, dst));
                }
            }
        }
        self.db.replace_thumb_paths(&converted)?;
        self.db.kv_set("thumbs_jpg", &Value::Bool(true))
    }

    pub fn sims_dir(&self) -> String {
        self.config.read().sims_dir.clone()
    }

    pub fn abs(&self, rel: &str) -> String {
        pyos::join(&self.sims_dir(), &rel.replace('/', "\\"))
    }

    pub fn folder_for(&self, category: &str) -> String {
        match self.config.read().folder_overrides.get(category).filter(|s| !s.is_empty()) {
            Some(f) => f.clone(),
            None => cats::folder_of(category).to_string(),
        }
    }

    fn discover(&self) -> (Vec<(String, &'static str)>, Vec<Unit>) {
        let sims = self.sims_dir();
        let rel = |p: &str| to_rel(&sims, p);
        let mut packages: Vec<(String, &'static str)> = Vec::new();
        let mut units: Vec<Unit> = Vec::new();
        for (root_name, status) in ROOTS {
            let root = pyos::join(&sims, root_name);
            if !pyos::isdir(&root) {
                continue;
            }
            let depth_of = |dp: &str| pyos::relpath(dp, &root).matches('\\').count() + 1;
            let mut root_files: Vec<String> = Vec::new();
            let Ok(rd) = std::fs::read_dir(&root) else { continue };
            for entry in rd.flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                let path = pyos::join(&root, &name);
                let Ok(meta) = std::fs::metadata(entry.path()) else { continue };
                if meta.is_dir() {
                    let mut files: Vec<String> = Vec::new();
                    let mut script_depth: Option<usize> = None;
                    for w in pyos::walk(&path) {
                        for n in &w.filenames {
                            files.push(pyos::join(&w.dirpath, n));
                            if is_script(n) {
                                let depth = depth_of(&w.dirpath);
                                script_depth = Some(script_depth.map_or(depth, |d| d.min(depth)));
                            }
                        }
                    }
                    let pkgs = |files: &[String], claimed: &HashSet<String>| -> Vec<(String, &'static str)> {
                        files.iter().filter(|f| is_package(f) && !claimed.contains(*f)).map(|f| (rel(f), status)).collect()
                    };
                    match script_depth {
                        Some(depth) if is_own_folder(&files) => {
                            let warn = if depth <= 1 { None } else { Some(DEEP_SCRIPT_WARNING) };
                            units.push((rel(&path), status, files.iter().map(|f| rel(f)).collect(), warn));
                        }
                        Some(_) => {
                            let mut claimed: HashSet<String> = HashSet::new();
                            let mut dirs: Vec<String> = files.iter().map(|f| pyos::dirname(f)).collect();
                            dirs.sort();
                            dirs.dedup();
                            for dp in dirs {
                                let here: Vec<String> =
                                    files.iter().filter(|f| pyos::dirname(f) == dp).cloned().collect();
                                let (groups, got) = script_groups(&here);
                                claimed.extend(got);
                                let warn = if depth_of(&dp) <= 1 { None } else { Some(DEEP_SCRIPT_WARNING) };
                                for members in groups {
                                    units.push((rel(&members[0]), status, members.iter().map(|m| rel(m)).collect(), warn));
                                }
                            }
                            packages.extend(pkgs(&files, &claimed));
                        }
                        None => packages.extend(pkgs(&files, &HashSet::new())),
                    }
                } else if meta.is_file() {
                    root_files.push(path);
                }
            }
            let (groups, claimed) = script_groups(&root_files);
            for members in groups {
                units.push((rel(&members[0]), status, members.iter().map(|m| rel(m)).collect(), None));
            }
            packages.extend(
                root_files.iter().filter(|f| is_package(f) && !claimed.contains(*f)).map(|f| (rel(f), status)),
            );
        }
        (packages, units)
    }

    fn thumb_for(&self, mut pk: Option<&mut Package>, fp: &str, instances: &[u64]) -> Option<String> {
        let out = pyos::join(&thumbs_dir(), &format!("{fp}{THUMB_EXT}"));
        let _guard = thumb_lock(fp).lock();
        if pyos::exists(&out) {
            return Some(out);
        }
        if let Some(pk) = pk.as_deref_mut() {
            if let Some(t) = thumbs::extract_from_open(pk, &out, instances) {
                return Some(t);
            }
        }
        self.thumbcache.extract(instances, &out).or_else(|| pk.and_then(|pk| poseicon::extract(pk, &out)))
    }

    /// 예전 스캔에서 썸네일 없이 들어간 포즈 팩에 아이콘을 채운다 (한 번만 확인한다).
    fn pose_thumb(&self, pk: &mut Package, fp: &str) -> Option<String> {
        let out = pyos::join(&thumbs_dir(), &format!("{fp}{THUMB_EXT}"));
        let _guard = thumb_lock(fp).lock();
        if pyos::exists(&out) {
            return Some(out);
        }
        poseicon::extract(pk, &out)
    }

    fn folder_category(&self, rel: &str) -> Option<&'static str> {
        let parts: Vec<&str> = rel.split('/').collect();
        let inner = if parts.len() > 2 { parts[1..parts.len() - 1].join("/") } else { String::new() };
        let head = inner.split('/').next().unwrap_or("").to_lowercase();
        if LEGACY_OVERRIDE_DIRS.contains(&head.as_str()) {
            return Some("overrides");
        }
        cats::category_for_folder(&inner)
    }

    /// 패키지 하나를 읽어 스캔에 필요한 값을 계산한다. DB 는 건드리지 않아 병렬로 돌릴 수 있다.
    fn examine(&self, rel: &str, status: &str, row: Option<&ItemRow>) -> Work {
        let abs_p = self.abs(rel);
        let Ok(st) = std::fs::metadata(&abs_p) else { return Work::Missing };
        let size = st.len() as i64;
        let mtime = pyos::mtime_of(&st).unwrap_or(0.0);
        if let Some(row) = row {
            if row.size == size && (row.mtime - mtime).abs() < 1.0 && row.status == status {
                let meta = parse_meta(row.meta.as_deref());
                let has_tex = meta.contains_key("tex");
                let need_pose = row.category == "animations" && !meta.contains_key("pose_icon");
                let need_keys = !meta.contains_key("keys");
                if has_tex && meta.get("ids").map_or(false, truthy) && !need_pose && !need_keys {
                    return Work::Unchanged { backfill: None };
                }
                let (tex, ids, pose, keys) = match Package::open(&abs_p) {
                    Ok(mut pk) => {
                        let tex = if has_tex { None } else { Some(classify::texture_size_of(&mut pk)) };
                        let pose = if need_pose { Some(self.pose_thumb(&mut pk, &row.fingerprint)) } else { None };
                        let keys = need_keys.then(|| crate::duplicates::keys_of(&pk));
                        (tex, classify::resource_ids_of(&pk), pose, keys)
                    }
                    Err(_) => (
                        if has_tex { None } else { Some(None) },
                        Vec::new(),
                        need_pose.then_some(None),
                        need_keys.then(Vec::new),
                    ),
                };
                return Work::Unchanged { backfill: Some(Backfill { tex, ids, pose, keys }) };
            }
        }
        // 지문 계산과 패키지 열기를 한 번에 해서 파일 읽기 횟수를 줄인다.
        let (fp, opened) = match open_fingerprinted(&abs_p) {
            Ok(x) => x,
            Err(e) => return Work::Failed(e.to_string()),
        };
        if row.map_or(false, |r| r.fingerprint == fp) {
            return Work::SameFp { fp, size, mtime };
        }
        let fresh = match opened {
            Ok(mut pk) => {
                let c = classify::classify_open(&mut pk);
                let instances: Vec<u64> = c.casp_instances.iter().chain(&c.object_instances).copied().collect();
                let thumb = self.thumb_for(Some(&mut pk), &fp, &instances);
                let tex = classify::texture_size_of(&mut pk);
                let ids = classify::resource_ids_of(&pk);
                let keys = crate::duplicates::keys_of(&pk);
                Fresh { fp, size, mtime, c, thumb, tex, ids, keys }
            }
            Err(ex) => {
                let c = classify::classify_failed(&ex);
                let thumb = self.thumb_for(None, &fp, &[]);
                Fresh { fp, size, mtime, c, thumb, tex: None, ids: Vec::new(), keys: Vec::new() }
            }
        };
        Work::Fresh(Box::new(fresh))
    }

    /// 폴더를 훑어 DB 를 실제 파일 상태와 맞춘다.
    pub fn scan(&self, mut progress: Progress) -> Result<ScanStats> {
        let _g = self.lock_ops();
        let sims = self.sims_dir();
        if !pyos::isdir(&sims) {
            return Ok(ScanStats::default());
        }
        let (packages, units) = self.discover();
        let known = self.db.by_path()?;
        let mut seen: HashSet<&str> = HashSet::new();
        let total = packages.len() + units.len();
        let mut stats = ScanStats { total, ..Default::default() };

        for (i, (rel, status, members, warn)) in units.iter().enumerate() {
            seen.insert(rel);
            report(&mut progress, i, total, rel);
            let abs_p = self.abs(rel);
            let mut scripts: Vec<&String> = members.iter().filter(|m| is_script(m)).collect();
            scripts.sort();
            let mut h = Sha1::new();
            for m in &scripts {
                match fingerprint(&self.abs(m)) {
                    Ok(fp) => h.update(fp.as_bytes()),
                    Err(_) => h.update(format!("unreadable:{m}").as_bytes()),
                }
            }
            let fp = hex(&h.finalize())[..24].to_string();
            let size: u64 = members.iter().filter_map(|m| pyos::getsize(&self.abs(m)).ok()).sum();
            let mut thumb = None;
            for m in members {
                if pyos::ends_with_any(&m.to_lowercase(), &IMAGE_EXT) {
                    thumb = thumbs::save_user_image_path(
                        &self.abs(m),
                        &pyos::join(&thumbs_dir(), &format!("{fp}{THUMB_EXT}")),
                    );
                    if thumb.is_some() {
                        break;
                    }
                }
            }
            let meta = match warn {
                Some(w) => json!({ "warning": w }),
                None => json!({}),
            };
            let mut fields = Fields::new()
                .set("fingerprint", fp)
                .set("rel_path", rel.clone())
                .set("kind", "script".to_string())
                .set("name", pyos::splitext(&pyos::basename(rel)).0)
                .set("category", cats::SCRIPT_MOD.to_string())
                .set("auto_category", cats::SCRIPT_MOD.to_string())
                .set("confidence", 1.0)
                .set("reason", format!("스크립트 모드 ({}개 .ts4script)", scripts.len()))
                .set("size", size as i64)
                .set("mtime", pyos::getmtime(&abs_p)?)
                .set("status", status.to_string())
                .list("members", members)
                .json("meta", &meta);
            if let Some(t) = thumb {
                fields.put("thumb", t);
            }
            if !known.contains_key(rel) {
                stats.new += 1;
            }
            self.db.upsert(fields)?;
        }

        // 무거운 읽기(지문·분류·썸네일·텍스처)는 묶음 단위로 병렬 처리하고,
        // DB 반영은 원래 순서대로 한다.
        const CHUNK: usize = 128;
        for (ci, chunk) in packages.chunks(CHUNK).enumerate() {
            let works: Vec<Work> = io_pool()
                .install(|| chunk.par_iter().map(|(rel, status)| self.examine(rel, status, known.get(rel))).collect());
            for (k, ((rel, status), work)) in chunk.iter().zip(works).enumerate() {
                let j = ci * CHUNK + k;
                seen.insert(rel);
                if j % 5 == 0 {
                    report(&mut progress, units.len() + j, total, rel);
                }
                let row = known.get(rel);
                match work {
                    Work::Missing | Work::Unchanged { backfill: None } => {}
                    Work::Failed(msg) => return Err(Error(msg)),
                    Work::Unchanged { backfill: Some(b) } => {
                        let row = row.expect("unchanged row");
                        let mut meta = parse_meta(row.meta.as_deref());
                        if let Some(tex) = b.tex {
                            meta.insert("tex".into(), tex_json(tex));
                            meta.insert("hq".into(), Value::Bool(classify::is_hq(tex)));
                        }
                        self.db.set_res_ids(&row.fingerprint, &b.ids)?;
                        meta.insert("ids".into(), Value::Bool(true));
                        if let Some(keys) = &b.keys {
                            self.db.set_item_keys(&row.fingerprint, keys)?;
                            meta.insert("keys".into(), Value::Bool(true));
                        }
                        let mut fields = Fields::new();
                        if let Some(pose) = b.pose {
                            meta.insert("pose_icon".into(), Value::Bool(true));
                            if pose.is_some() {
                                fields = fields.opt("thumb", pose);
                            }
                        }
                        self.db.update_item_deferred(row.id, fields.json("meta", &Value::Object(meta)))?;
                    }
                    Work::SameFp { fp, size, mtime } => {
                        let row = row.expect("same-fingerprint row");
                        self.db.upsert(
                            Fields::new()
                                .set("rel_path", rel.clone())
                                .set("fingerprint", fp)
                                .set("kind", "package".to_string())
                                .set("name", row.name.clone())
                                .set("category", row.category.clone())
                                .set("size", size)
                                .set("mtime", mtime)
                                .set("status", status.to_string()),
                        )?;
                    }
                    Work::Fresh(f) => {
                        let Fresh { fp, size, mtime, c, thumb, tex, ids, keys } = *f;
                        let same = self.db.by_fingerprint(&fp)?;
                        let mut category = c.category.clone();
                        let mut user_set = 0i64;
                        if self.folder_category(rel) == Some("overrides") {
                            category = "overrides".to_string();
                            user_set = 1;
                        }
                        let prev = same.first();
                        let mem = if prev.is_some() { None } else { self.db.recall(&fp)? };
                        if let Some(p) = prev.filter(|p| p.user_set) {
                            category = p.category.clone();
                            user_set = 1;
                        } else if let Some(m) = mem.as_ref().filter(|m| m.user_set) {
                            category = m.category.clone();
                            user_set = 1;
                        }
                        if let Some(r) = row.filter(|r| r.user_set) {
                            category = r.category.clone();
                            user_set = 1;
                        }
                        self.db.set_res_ids(&fp, &ids)?;
                        self.db.set_item_keys(&fp, &keys)?;
                        let added_at =
                            prev.map(|p| p.added_at).or(mem.as_ref().map(|m| m.added_at)).unwrap_or_else(pyos::now);
                        let instances: Vec<String> = c.casp_instances.iter().take(50).map(|x| x.to_string()).collect();
                        let meta = json!({
                            "casp_names": c.casp_names,
                            "instances": instances,
                            "resources": c.resource_count,
                            "tex": tex_json(tex),
                            "hq": classify::is_hq(tex),
                            "ids": true,
                            "keys": true,
                        });
                        self.db.upsert(
                            Fields::new()
                                .set("fingerprint", fp)
                                .set("rel_path", rel.clone())
                                .set("kind", "package".to_string())
                                .set("name", pyos::splitext(&pyos::basename(rel)).0)
                                .set("category", category)
                                .set("auto_category", c.category.clone())
                                .set("user_set", user_set)
                                .set("confidence", c.confidence)
                                .set("reason", c.reason.clone())
                                .set("size", size)
                                .set("mtime", mtime)
                                .set("status", status.to_string())
                                .opt("thumb", thumb)
                                .set("added_at", added_at)
                                .json("meta", &meta),
                        )?;
                        if row.is_none() {
                            stats.new += 1;
                        } else {
                            stats.updated += 1;
                        }
                    }
                }
            }
        }

        let gone: Vec<&String> = known.keys().filter(|r| !seen.contains(r.as_str())).collect();
        self.db.remember_paths(&gone)?;
        self.db.delete_paths(&gone)?;
        stats.removed = gone.len();
        self.db.commit()?;
        report(&mut progress, total, total, "완료");
        Ok(stats)
    }

    pub fn items(&self) -> Result<Vec<Item>> {
        self.db.all_items()
    }

    /// 이미 있는 이름이면 ` (2)`, ` (3)` … 을 붙인다.
    pub fn unique(&self, dst: &str) -> String {
        if !pyos::exists(dst) {
            return dst.to_string();
        }
        let (base, ext) = pyos::splitext(dst);
        let mut n = 2;
        while pyos::exists(&format!("{base} ({n}){ext}")) {
            n += 1;
        }
        format!("{base} ({n}){ext}")
    }

    fn cleanup_empty(&self, start_dir: &str) -> Result<()> {
        let sims = self.sims_dir();
        let stops: Vec<String> = ROOTS.iter().map(|(r, _)| pyos::normcase(&pyos::join(&sims, r))).collect();
        let mut d = start_dir.to_string();
        while !stops.contains(&pyos::normcase(&d))
            && pyos::isdir(&d)
            && pyos::listdir(&d).map(|l| l.is_empty()).unwrap_or(false)
        {
            std::fs::remove_dir(&d)?;
            d = pyos::dirname(&d);
        }
        Ok(())
    }

    fn same_path(a: &str, b: &str) -> bool {
        pyos::normcase(&pyos::abspath(a)) == pyos::normcase(&pyos::abspath(b))
    }

    fn move_abs(&self, src: &str, dst: &str) -> Result<String> {
        if Self::same_path(src, dst) {
            return Ok(src.to_string());
        }
        let dst = self.unique(dst);
        pyos::makedirs(&pyos::dirname(&dst))?;
        pyos::move_path(src, &dst)?;
        self.cleanup_empty(&pyos::dirname(src))?;
        Ok(dst)
    }

    fn move_item(
        &self,
        item: &mut Item,
        target_rel: &str,
        moves: &mut Vec<(String, String)>,
        status: Option<&str>,
    ) -> Result<()> {
        let sims = self.sims_dir();
        let src = self.abs(&item.rel_path);
        let dst = self.move_abs(&src, &self.abs(target_rel))?;
        moves.push((src, dst.clone()));
        let new_rel = to_rel(&sims, &dst);
        let mut members = item.members.clone();
        if item.kind == "script" && !members.is_empty() {
            if pyos::isdir(&dst) {
                let skip = item.rel_path.chars().count();
                members = members.iter().map(|m| format!("{new_rel}{}", m.chars().skip(skip).collect::<String>())).collect();
            } else {
                let mut new_members = vec![new_rel.clone()];
                for m in &members[1..] {
                    let msrc = self.abs(m);
                    if pyos::exists(&msrc) {
                        let mdst = self.move_abs(&msrc, &pyos::join(&pyos::dirname(&dst), &pyos::basename(&msrc)))?;
                        moves.push((msrc, mdst.clone()));
                        new_members.push(to_rel(&sims, &mdst));
                    }
                }
                members = new_members;
            }
        }
        let mut extra = Fields::new();
        if item.kind == "script" {
            extra = extra.list("members", &members);
        }
        if let Some(s) = status {
            extra.put("status", s.to_string());
        }
        if let Ok(mt) = pyos::getmtime(&dst) {
            extra.put("mtime", mt);
        }
        self.db.rename_path(&item.rel_path, &new_rel, extra)?;
        item.rel_path = new_rel;
        Ok(())
    }

    fn status_rel(&self, item: &Item, status: &str) -> Result<String> {
        let root = status_root(status).ok_or_else(|| Error::msg(format!("알 수 없는 상태: {status}")))?;
        let rest = item.rel_path.split_once('/').map(|x| x.1).ok_or_else(|| Error::msg("list index out of range"))?;
        Ok(format!("{root}/{rest}"))
    }

    /// 항목을 사용 중/비활성/격리 폴더로 옮긴다. 옮긴 파일 수를 돌려준다.
    pub fn set_status(&self, items: &mut [Item], status: &str, label: &str, mut progress: Progress) -> Result<usize> {
        let _g = self.lock_ops();
        let mut moves = Vec::new();
        let n = items.len();
        for (i, it) in items.iter_mut().enumerate() {
            report(&mut progress, i, n, &it.rel_path);
            if it.status == status {
                continue;
            }
            let target = self.status_rel(it, status)?;
            self.move_item(it, &target, &mut moves, Some(status))?;
            it.status = status.to_string();
        }
        if !moves.is_empty() {
            self.db.log_op(label, &moves)?;
        }
        Ok(moves.len())
    }

    /// 분류에 맞는 폴더로 옮길 계획. `items` 가 없으면 전체.
    pub fn organize_plan(&self, items: Option<&[Item]>) -> Result<Vec<MovePlan>> {
        let owned;
        let items = match items {
            Some(i) => i,
            None => {
                owned = self.items()?;
                &owned
            }
        };
        let mut plans = Vec::new();
        for it in items {
            if it.status != "active" {
                continue;
            }
            if it.kind == "script" {
                if let Some(p) = self.script_plan(it) {
                    plans.push(p);
                }
                continue;
            }
            let target_dir = format!("Mods/{}", self.folder_for(&it.category));
            let (cur_dir, file) = rsplit_slash(&it.rel_path);
            let (cur, tgt) = (cur_dir.to_lowercase(), target_dir.to_lowercase());
            if cur == tgt || cur.starts_with(&format!("{tgt}/")) {
                continue;
            }
            if it.category == "overrides" && self.folder_category(&it.rel_path) == Some("overrides") {
                continue;
            }
            plans.push(MovePlan { item: it.clone(), target_rel: format!("{target_dir}/{file}") });
        }
        Ok(plans)
    }

    fn script_plan(&self, it: &Item) -> Option<MovePlan> {
        let _g = self.lock_ops();
        if pyos::isdir(&self.abs(&it.rel_path)) || it.meta.get("warning").map_or(false, truthy) {
            return None;
        }
        let known = scriptmods::identify(&it.members);
        let target_dir = match known {
            Some(k) if k.folder.is_empty() => "Mods".to_string(),
            Some(k) => format!("Mods/{}", k.folder),
            None => format!("Mods/{}", cats::SCRIPT_FOLDER),
        };
        let (cur_dir, file) = rsplit_slash(&it.rel_path);
        if cur_dir.to_lowercase() == target_dir.to_lowercase() {
            return None;
        }
        Some(MovePlan { item: it.clone(), target_rel: format!("{target_dir}/{file}") })
    }

    pub fn apply_moves(&self, plans: &mut [MovePlan], label: &str, mut progress: Progress) -> Result<usize> {
        let _g = self.lock_ops();
        let mut moves = Vec::new();
        let n = plans.len();
        for (i, p) in plans.iter_mut().enumerate() {
            report(&mut progress, i, n, &p.item.rel_path);
            let src = self.abs(&p.item.rel_path);
            if pyos::exists(&src) && !Self::same_path(&src, &self.abs(&p.target_rel)) {
                let target = p.target_rel.clone();
                self.move_item(&mut p.item, &target, &mut moves, None)?;
            }
        }
        if !moves.is_empty() {
            self.db.log_op(label, &moves)?;
        }
        report(&mut progress, n, n, "");
        Ok(moves.len())
    }

    pub fn set_category(&self, item: &mut Item, category: &str, move_file: bool) -> Result<()> {
        self.db.update_item(item.id, Fields::new().set("category", category.to_string()).set("user_set", 1i64))?;
        item.category = category.to_string();
        item.user_set = true;
        if move_file && item.kind == "package" && item.status == "active" {
            let mut plans = self.organize_plan(Some(std::slice::from_ref(item)))?;
            if !plans.is_empty() {
                self.apply_moves(&mut plans, &format!("분류 변경: {}", item.name), None)?;
                item.rel_path = plans[0].item.rel_path.clone();
            }
        }
        Ok(())
    }

    /// (되돌릴 수 있는 정리 횟수, 옮겨진 파일 수)
    pub fn organize_history(&self) -> Result<(usize, usize)> {
        let ops = self.db.open_ops(ORGANIZE_LABEL)?;
        Ok((ops.len(), ops.iter().map(|o| o.2.len()).sum()))
    }

    pub fn undo_organize(&self, mut progress: Progress) -> Result<usize> {
        let _g = self.lock_ops();
        let mut n = 0;
        let ops = self.db.open_ops(ORGANIZE_LABEL)?;
        let total: usize = ops.iter().map(|o| o.2.len()).sum();
        let mut done = 0;
        for (op_id, _label, moves) in &ops {
            for (src, dst) in moves.iter().rev() {
                report(&mut progress, done, total, src);
                done += 1;
                if pyos::exists(dst) && !pyos::exists(src) {
                    pyos::makedirs(&pyos::dirname(src))?;
                    pyos::move_path(dst, src)?;
                    self.cleanup_empty(&pyos::dirname(dst))?;
                    n += 1;
                }
            }
            self.db.mark_undone(*op_id)?;
        }
        Ok(n)
    }

    /// 휴지통으로 보낸다. (성공 수, 실패 메시지들)
    pub fn trash(&self, items: &[Item]) -> Result<(usize, Vec<String>)> {
        let _g = self.lock_ops();
        let mut n = 0;
        let mut failed = Vec::new();
        for it in items {
            let mut paths = vec![self.abs(&it.rel_path)];
            if it.kind == "script" && !pyos::isdir(&paths[0]) {
                paths = it.members.iter().map(|m| self.abs(m)).collect();
            }
            let res: std::result::Result<(), trash::Error> =
                paths.iter().filter(|p| pyos::exists(p)).try_for_each(trash::delete);
            if let Err(ex) = res {
                failed.push(format!("{}: {ex}", it.name));
                continue;
            }
            if let Some(first) = paths.first() {
                self.cleanup_empty(&pyos::dirname(first))?;
            }
            self.db.delete_paths(&[&it.rel_path])?;
            n += 1;
        }
        Ok((n, failed))
    }

    pub fn save_note(&self, item: &Item, memo: &str) -> Result<()> {
        self.db.set_note(&item.fingerprint, Fields::new().set("memo", memo.to_string()))
    }

    pub fn set_user_thumb(&self, item: &Item, image: UserImage) -> Result<Option<String>> {
        let out = pyos::join(&thumbs_dir(), &format!("{}_user{THUMB_EXT}", item.fingerprint));
        let res = match image {
            UserImage::Path(p) => thumbs::save_user_image_path(p, &out),
            UserImage::Bytes(b) => thumbs::save_user_image_bytes(b, &out),
        };
        if let Some(r) = &res {
            self.db.set_note(&item.fingerprint, Fields::new().set("user_thumb", r.clone()))?;
        }
        Ok(res)
    }
}

/// `s.rsplit("/", 1)` 의 (앞, 뒤).
fn rsplit_slash(s: &str) -> (&str, &str) {
    s.rsplit_once('/').unwrap_or((s, ""))
}

fn parse_meta(meta: Option<&str>) -> Map<String, Value> {
    meta.filter(|s| !s.is_empty()).and_then(|s| serde_json::from_str(s).ok()).unwrap_or_default()
}

/// Python 의 참/거짓 판정.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map_or(true, |x| x != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
