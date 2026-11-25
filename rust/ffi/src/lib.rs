//! Qt 화면(C++)과 Rust 코어 사이의 경계.
//!
//! `s4_call(메서드, JSON 인자, 진행 콜백)` 하나로 모든 기능을 부른다. 결과는
//! `{"ok": 값}` 또는 `{"err": "메시지"}` 형태의 JSON 문자열이고, `s4_free` 로 돌려준다.

use std::collections::{HashMap, HashSet};
use std::ffi::{c_char, c_void, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;

use parking_lot::Mutex;
use serde_json::{json, Map, Value};

use s4ccm_core::bisect::Bisect;
use s4ccm_core::config::{app_dir, thumbs_dir, Config};
use s4ccm_core::db::Item;
use s4ccm_core::importer::{self, ImportPlan};
use s4ccm_core::library::{truthy, Library, MovePlan, Progress, UserImage};
use s4ccm_core::resourcecfg::{self as rc, ResourceCfg, Rule};
use s4ccm_core::tray::{self, TrayEntry};
use s4ccm_core::{categories as cats, duplicates, launcher, paths, pyos, relocate, update};
use s4ccm_core::{Error, Result};

pub type ProgressCb = Option<unsafe extern "C" fn(*mut c_void, u64, u64, *const c_char)>;

#[derive(Default)]
struct TrayState {
    entries: Vec<TrayEntry>,
    id_map: Option<HashMap<u64, HashSet<String>>>,
}

struct State {
    lib: &'static Library,
    bisect: Mutex<Bisect<'static>>,
    plans: Mutex<HashMap<u64, ImportPlan>>,
    tray: Mutex<TrayState>,
    next_handle: AtomicU64,
}

static STATE: OnceLock<State> = OnceLock::new();

fn state() -> Result<&'static State> {
    STATE.get().ok_or_else(|| Error::msg("초기화되지 않았습니다."))
}

// ---------------------------------------------------------------- 인자 읽기

fn s(a: &Value, k: &str) -> String {
    a.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string()
}

fn opt_s(a: &Value, k: &str) -> Option<String> {
    a.get(k).and_then(|v| v.as_str()).map(|x| x.to_string())
}

fn i(a: &Value, k: &str) -> i64 {
    a.get(k).and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))).unwrap_or(0)
}

fn b(a: &Value, k: &str) -> bool {
    a.get(k).and_then(|v| v.as_bool()).unwrap_or(false)
}

fn ids(a: &Value, k: &str) -> Vec<i64> {
    a.get(k)
        .and_then(|v| v.as_array())
        .map(|v| v.iter().filter_map(|x| x.as_i64().or_else(|| x.as_f64().map(|f| f as i64))).collect())
        .unwrap_or_default()
}

fn arr<'a>(a: &'a Value, k: &str) -> &'a [Value] {
    a.get(k).and_then(|v| v.as_array()).map(|v| v.as_slice()).unwrap_or(&[])
}

fn unhex(text: &str) -> Vec<u8> {
    let d = |c: u8| match c {
        b'0'..=b'9' => c - b'0',
        b'a'..=b'f' => c - b'a' + 10,
        b'A'..=b'F' => c - b'A' + 10,
        _ => 0,
    };
    text.as_bytes().chunks_exact(2).map(|p| d(p[0]) << 4 | d(p[1])).collect()
}

// ---------------------------------------------------------------- JSON 으로 바꾸기

fn item_json(it: &Item) -> Value {
    json!({
        "id": it.id,
        "name": it.name,
        "rel_path": it.rel_path,
        "kind": it.kind,
        "category": it.category,
        "status": it.status,
        "size": it.size,
        "mtime": it.mtime,
        "added_at": it.added_at,
        "memo": it.memo,
        "reason": it.reason,
        "confidence": it.confidence,
        "user_set": it.user_set,
        "user_thumb": it.user_thumb,
        "display_thumb": it.display_thumb(),
        "members": it.members.len(),
        "casp_names": it.meta.get("casp_names").cloned().unwrap_or(Value::Null),
        "hq": it.meta.get("hq").map_or(false, truthy),
        "tex": it.meta.get("tex").cloned().unwrap_or(Value::Null),
        "warning": it.meta.get("warning").filter(|v| truthy(v)).cloned().unwrap_or(Value::Null),
    })
}

fn items_json(items: &[Item]) -> Value {
    Value::Array(items.iter().map(item_json).collect())
}

fn config_json(c: &Config) -> Value {
    let mut v = c.to_json();
    v["mods_dir"] = json!(c.mods_dir());
    v
}

fn cfg_json(cfg: &ResourceCfg) -> Value {
    json!({
        "rules": cfg.rules.iter().map(|r| json!([r.priority, r.folder, r.depth])).collect::<Vec<_>>(),
        "extra": cfg.extra.iter().map(|(n, t)| json!([n, t])).collect::<Vec<_>>(),
    })
}

fn cfg_from(a: &Value) -> ResourceCfg {
    ResourceCfg {
        rules: arr(a, "rules")
            .iter()
            .map(|r| Rule {
                priority: r.get(0).and_then(|v| v.as_i64()).unwrap_or(0),
                folder: r.get(1).and_then(|v| v.as_str()).unwrap_or("").to_string(),
                depth: r.get(2).and_then(|v| v.as_i64()).unwrap_or(0),
            })
            .collect(),
        extra: arr(a, "extra")
            .iter()
            .map(|r| {
                (r.get(0).and_then(|v| v.as_i64()).unwrap_or(0), r.get(1).and_then(|v| v.as_str()).unwrap_or("").to_string())
            })
            .collect(),
    }
}

fn plan_json(handle: u64, plan: &ImportPlan) -> Value {
    let groups: Vec<Value> = plan
        .groups
        .iter()
        .map(|g| {
            json!({
                "name": g.name,
                "mode": g.mode,
                "mode_reason": g.mode_reason,
                "folder": g.folder,
                "has_mods": !g.packages().is_empty() || !g.scripts().is_empty(),
                "tray_items": g.tray_items.iter().map(|t| json!({
                    "name": t.name, "kind_label": t.kind_label, "files": t.entries.len(),
                    "include": t.include, "exists": t.exists,
                })).collect::<Vec<_>>(),
                "entries": g.entries.iter().map(|e| json!({
                    "rel": e.rel, "kind": e.kind, "category": e.category, "reason": e.reason,
                    "confidence": e.confidence, "include": e.include, "duplicate": e.duplicate,
                })).collect::<Vec<_>>(),
                "replaces": g.replaces.as_ref().map(|it| it.name.clone()),
                "replace": g.replace,
                "keep_note": g.known.map(|k| k.keep_note).unwrap_or(""),
                "missing_libs": g.missing_libs.iter().map(|m| json!({"name": m.name, "url": m.url})).collect::<Vec<_>>(),
            })
        })
        .collect();
    json!({"handle": handle, "groups": groups, "errors": plan.errors})
}

/// 가져오기 창에서 사용자가 고친 내용을 계획에 반영한다 (Python 판 `ImportDialog._accept`).
fn apply_plan_edits(plan: &mut ImportPlan, edits: &[Value]) {
    for (g, e) in plan.groups.iter_mut().zip(edits) {
        for (t, on) in g.tray_items.iter_mut().zip(arr(e, "trays")) {
            t.include = on.as_bool().unwrap_or(t.include);
        }
        g.mode = s(e, "mode");
        g.folder = s(e, "folder");
        if let Some(r) = e.get("replace").and_then(|v| v.as_bool()) {
            g.replace = r;
        }
        for (entry, ed) in g.entries.iter_mut().zip(arr(e, "entries")) {
            if ed.is_null() {
                continue;
            }
            entry.include = b(ed, "include");
            if let Some(cat) = opt_s(ed, "category") {
                if cat != entry.category {
                    entry.category = cat;
                    entry.user_changed = true;
                }
            }
        }
        if g.mode == importer::MODE_SCRIPT {
            for entry in g.entries.iter_mut() {
                if entry.kind != "package" && entry.kind != "script" {
                    entry.include = true;
                }
            }
        }
    }
}

fn bisect_json(st: &State) -> Value {
    let bs = st.bisect.lock();
    let s = &bs.state;
    json!({
        "active": s.active(), "testing": s.testing(), "found": s.found, "step": s.step, "total": s.total,
        "suspects": s.suspects, "cleared": s.cleared, "out": s.out,
        "suspects_in": s.suspects_in(), "suspects_out": s.suspects_out(),
        "remaining_steps": s.remaining_steps(),
    })
}

fn entry_json(e: &TrayEntry, thumb: Option<String>, relations: Vec<Value>) -> Value {
    let sims: Vec<Value> = e
        .sims
        .iter()
        .zip(relations)
        .map(|(sim, rel)| {
            json!({
                "full_name": sim.full_name(),
                "summary": sim.summary(),
                "traits": sim.traits,
                "aspiration": sim.aspiration,
                "likes": sim.likes,
                "dislikes": sim.dislikes,
                "skills": sim.skills.iter().map(|k| json!({"name": k.name, "level": k.level, "max_level": k.max_level})).collect::<Vec<_>>(),
                "relations": rel,
            })
        })
        .collect();
    json!({
        "key": e.key,
        "kind": e.kind,
        "kind_label": e.kind_label(),
        "name": e.name,
        "creator": e.creator,
        "description": e.description,
        "saved_at": e.saved_at.map(|d| d.format("%Y-%m-%d %H:%M 저장").to_string()),
        "thumb": thumb,
        "files": e.files.len(),
        "has_main_images": !e.main_images().is_empty(),
        "export_name": e.export_name(),
        "sims": sims,
    })
}

fn tray_entry(st: &State, key: &str) -> Result<TrayEntry> {
    st.tray.lock().entries.iter().find(|e| e.key == key).cloned().ok_or_else(|| Error::msg("트레이 항목을 찾지 못했습니다."))
}

fn items_by_id(lib: &Library, list: &[i64]) -> Result<Vec<Item>> {
    let mut out = Vec::with_capacity(list.len());
    for &id in list {
        if let Some(it) = lib.db.get(id)? {
            out.push(it);
        }
    }
    Ok(out)
}

fn need_item(lib: &Library, id: i64) -> Result<Item> {
    lib.db.get(id)?.ok_or_else(|| Error::msg("항목을 찾지 못했습니다. [다시 스캔]을 눌러 주세요."))
}

fn cats_json() -> Value {
    json!({
        "script": cats::SCRIPT_MOD,
        "unsorted": cats::UNSORTED,
        "groups": cats::GROUPS.iter().map(|(k, l)| json!([k, l])).collect::<Vec<_>>(),
        "categories": cats::CATEGORIES.iter().map(|c| json!({"key": c.key, "label": c.label, "group": c.group})).collect::<Vec<_>>(),
    })
}

/// 단계 이름을 진행 문자열 앞에 붙인다. 화면 쪽은 '\x01' 앞을 단계 이름으로 읽는다.
fn phased_text(phase: &str, text: &str) -> String {
    format!("{phase}\u{1}{text}")
}

// ---------------------------------------------------------------- 메서드

fn init() -> Result<Value> {
    if STATE.get().is_none() {
        let lib: &'static Library = Box::leak(Box::new(Library::new(Config::load())?));
        let bisect = Bisect::new(lib)?;
        let _ = STATE.set(State {
            lib,
            bisect: Mutex::new(bisect),
            plans: Mutex::new(HashMap::new()),
            tray: Mutex::new(TrayState::default()),
            next_handle: AtomicU64::new(1),
        });
    }
    let st = state()?;
    Ok(json!({
        "config": config_json(&st.lib.config.read()),
        "app_dir": app_dir(),
        "thumbs_dir": thumbs_dir(),
        "cats": cats_json(),
        "disabled_dir": paths::DISABLED_DIR,
        "quarantine_dir": paths::QUARANTINE_DIR,
        "max_depth": rc::MAX_DEPTH,
        "organize_label": s4ccm_core::library::ORGANIZE_LABEL,
        "cleared_labels": tray::CLEARED_LABELS,
        "kept_labels": tray::KEPT_LABELS,
        "version": s4ccm_core::config::VERSION,
    }))
}

fn dispatch(method: &str, a: &Value, mut progress: Progress) -> Result<Value> {
    match method {
        "init" => return init(),
        "app_dir" => return Ok(json!(app_dir())),
        "paths.validate" => {
            let (ok, msg) = paths::validate(&s(a, "dir"));
            return Ok(json!([ok, msg]));
        }
        "paths.find_sims_folders" => {
            return Ok(Value::Array(
                paths::find_sims_folders()
                    .iter()
                    .map(|f| json!({"root": f.root, "mods": f.mods(), "cc_count": f.cc_count}))
                    .collect(),
            ));
        }
        "paths.find_game_exe" => return Ok(json!(paths::find_game_exe())),
        "paths.normalize" => return Ok(json!(paths::normalize_user_choice(&s(a, "text")))),
        "paths.overlaps" => {
            // 가져올 경로가 관리 폴더(Mods, Tray, 비활성, 충돌 테스트)와 겹치는지.
            let sims = s(a, "sims_dir");
            let managed: Vec<String> = ["Mods", "Tray", paths::DISABLED_DIR, paths::QUARANTINE_DIR]
                .iter()
                .map(|d| pyos::normcase(&pyos::abspath(&pyos::join(&sims, d))))
                .collect();
            let p = pyos::normcase(&pyos::abspath(&s(a, "path")));
            let hit = managed
                .iter()
                .any(|m| &p == m || p.starts_with(&format!("{m}\\")) || m.starts_with(&format!("{p}\\")));
            return Ok(json!(hit));
        }
        "rc.default" => return Ok(cfg_json(&ResourceCfg::default_cfg())),
        "rc.render" => return Ok(json!(cfg_from(a).render())),
        "rc.load" => {
            let (cfg, text) = rc::load(&s(a, "mods"))?;
            let mut v = cfg_json(&cfg);
            v["has_text"] = json!(!text.is_empty());
            return Ok(v);
        }
        "rc.save" => return Ok(json!(rc::save(&s(a, "mods"), &cfg_from(a))?)),
        "rc.read_options" => {
            let opts = rc::read_options(&s(a, "sims"))?;
            return Ok(Value::Object(opts.into_iter().map(|(k, v)| (k, Value::String(v))).collect::<Map<_, _>>()));
        }
        "rc.set_option" => return Ok(json!(rc::set_option(&s(a, "sims"), &s(a, "key"), &s(a, "value"))?)),
        "launcher.running" => return Ok(json!(launcher::game_running())),
        // ---- 자동 업데이트 (GitHub 릴리스)
        "update.check" => {
            // 새 버전이 없으면 null. 있으면 릴리스 정보와 이 설치 방식에 맞는 파일.
            let current = s(a, "current");
            let rel = update::latest(update::REPO)?;
            if !update::is_newer(&rel.tag, &current) {
                return Ok(Value::Null);
            }
            let kind = update::install_kind(&s(a, "app_dir"));
            let asset = update::pick_asset(&rel, kind)
                .map(|x| json!({"name": x.name, "url": x.url, "size": x.size, "sha256": x.sha256}));
            return Ok(json!({
                "tag": rel.tag, "name": rel.name, "body": rel.body, "html_url": rel.html_url,
                "kind": kind, "asset": asset,
            }));
        }
        "update.download" => {
            let asset = update::Asset {
                name: s(a, "name"),
                url: s(a, "url"),
                size: a.get("size").and_then(|v| v.as_u64()).unwrap_or(0),
                sha256: opt_s(a, "sha256").map(|x| x.to_lowercase()),
            };
            return Ok(json!(update::download(&asset, progress)?));
        }
        "update.apply_portable" => return Ok(json!(update::apply_portable(&s(a, "zip"), &s(a, "app_dir"), progress)?)),
        "update.cleanup" => {
            update::cleanup(&s(a, "app_dir"));
            return Ok(Value::Null);
        }
        "launcher.launch" => {
            launcher::launch(&s(a, "exe"), &s(a, "sims"), b(a, "clear"))?;
            return Ok(Value::Null);
        }
        _ => {}
    }

    let st = state()?;
    let lib = st.lib;
    Ok(match method {
        "config.get" => config_json(&lib.config.read()),
        "config.set" => {
            let mut c = lib.config.write();
            if let Some(Value::Object(values)) = a.get("values") {
                for (k, v) in values {
                    match (k.as_str(), v) {
                        ("sims_dir", Value::String(x)) => c.sims_dir = x.clone(),
                        ("game_exe", Value::String(x)) => c.game_exe = x.clone(),
                        ("theme", Value::String(x)) => c.theme = x.clone(),
                        ("view_mode", Value::String(x)) => c.view_mode = x.clone(),
                        ("skip_version", Value::String(x)) => c.skip_version = x.clone(),
                        ("clear_thumbcache_on_launch", Value::Bool(x)) => c.clear_thumbcache_on_launch = *x,
                        ("thumb_size", x) => c.thumb_size = x.as_i64().unwrap_or(c.thumb_size),
                        _ => {}
                    }
                }
            }
            if b(a, "save") {
                c.save()?;
            }
            config_json(&c)
        }
        "lib.sims_dir_changed" => {
            let sims = lib.sims_dir();
            lib.thumbcache.set_path(Some(pyos::join(&sims, "localthumbcache.package")));
            Value::Null
        }
        "lib.scan" => {
            lib.scan(progress)?;
            Value::Null
        }
        "lib.items" => items_json(&lib.items()?),
        "lib.abs" => json!(lib.abs(&s(a, "rel"))),
        "lib.folder_for" => json!(lib.folder_for(&s(a, "key"))),
        "lib.get" => lib.db.get(i(a, "id"))?.as_ref().map(item_json).unwrap_or(Value::Null),
        "lib.names" => {
            // 충돌 테스트 목록용: id → [이름, 분류] (없는 항목은 null)
            let mut out = Vec::new();
            for id in ids(a, "ids") {
                out.push(match lib.db.get(id)? {
                    Some(it) => json!([it.name, it.category]),
                    None => Value::Null,
                });
            }
            Value::Array(out)
        }
        "lib.set_status" => {
            let mut items = items_by_id(lib, &ids(a, "ids"))?;
            json!(lib.set_status(&mut items, &s(a, "to"), &s(a, "label"), progress)?)
        }
        "lib.trash" => {
            let items = items_by_id(lib, &ids(a, "ids"))?;
            let (n, failed) = lib.trash(&items)?;
            json!({"n": n, "failed": failed})
        }
        "lib.save_note" => {
            let it = need_item(lib, i(a, "id"))?;
            lib.save_note(&it, &s(a, "memo"))?;
            Value::Null
        }
        "lib.set_category" => {
            let mut it = need_item(lib, i(a, "id"))?;
            lib.set_category(&mut it, &s(a, "key"), a.get("move").and_then(|v| v.as_bool()).unwrap_or(true))?;
            item_json(&it)
        }
        "lib.set_user_thumb" => {
            let it = need_item(lib, i(a, "id"))?;
            let res = match opt_s(a, "hex") {
                Some(hex) => lib.set_user_thumb(&it, UserImage::Bytes(&unhex(&hex)))?,
                None => lib.set_user_thumb(&it, UserImage::Path(&s(a, "path")))?,
            };
            json!(res)
        }
        "lib.organize_plan" => Value::Array(
            lib.organize_plan(None)?.iter().map(|p| json!({"item": item_json(&p.item), "target_rel": p.target_rel})).collect(),
        ),
        "lib.apply_moves" => {
            let mut plans = Vec::new();
            for p in arr(a, "plans") {
                if let Some(item) = lib.db.get(i(p, "id"))? {
                    plans.push(MovePlan { item, target_rel: s(p, "target_rel") });
                }
            }
            json!(lib.apply_moves(&mut plans, &s(a, "label"), progress)?)
        }
        "lib.organize_history" => {
            let (runs, files) = lib.organize_history()?;
            json!([runs, files])
        }
        "lib.undo_organize" => json!(lib.undo_organize(progress)?),

        "import.analyze" => {
            let list: Vec<String> = arr(a, "paths").iter().filter_map(|v| v.as_str().map(|x| x.to_string())).collect();
            let mut steps = |phase: &str, i: usize, n: usize, text: &str| {
                if let Some(p) = progress.as_mut() {
                    p(i, n, &phased_text(phase, text));
                }
            };
            let plan = importer::analyze_with(lib, &list, Some(&mut steps));
            let handle = st.next_handle.fetch_add(1, Ordering::Relaxed);
            let v = plan_json(handle, &plan);
            st.plans.lock().insert(handle, plan);
            v
        }
        "import.cleanup" => {
            if let Some(mut plan) = st.plans.lock().remove(&(i(a, "handle") as u64)) {
                plan.cleanup();
            }
            Value::Null
        }
        "import.execute" => {
            let Some(mut plan) = st.plans.lock().remove(&(i(a, "handle") as u64)) else {
                return Err(Error::msg("가져오기 계획을 찾지 못했습니다."));
            };
            apply_plan_edits(&mut plan, arr(a, "groups"));
            let mut steps = |phase: &str, i: usize, n: usize, text: &str| {
                if let Some(p) = progress.as_mut() {
                    p(i, n, &phased_text(phase, text));
                }
            };
            match importer::execute_with(lib, &mut plan, Some(&mut steps)) {
                Ok((n, _added)) => json!({
                    "n": n, "mods_added": plan.mods_added, "trays_added": plan.trays_added,
                    "has_script": plan.groups.iter().any(|g| g.mode == importer::MODE_SCRIPT),
                }),
                Err(e) => {
                    plan.cleanup();
                    return Err(e);
                }
            }
        }

        "lib.pose_images" => {
            // 포즈 팩 아이콘 전부 (처음 볼 때 꺼내 thumbs\poses\<지문> 에 저장해 둔다).
            let it = need_item(lib, i(a, "id"))?;
            let dir = pyos::join(&pyos::join(&thumbs_dir(), "poses"), &it.fingerprint);
            json!(s4ccm_core::poseicon::extract_all(&lib.abs(&it.rel_path), &dir))
        }
        "relocate.status" => {
            let sims = lib.sims_dir();
            Value::Array(
                relocate::status(&sims)
                    .iter()
                    .map(|s| json!({"name": s.name, "path": s.path, "exists": s.exists, "link": s.is_link, "target": s.target}))
                    .collect(),
            )
        }
        "relocate.can_symlink" => json!(relocate::can_symlink()),
        "relocate.measure" => {
            let m = relocate::measure(&lib.sims_dir(), &s(a, "dest"))?;
            json!({"files": m.files, "bytes": m.bytes, "same_volume": m.same_volume, "free": m.free})
        }
        "relocate.run" | "relocate.restore" => {
            let mut steps = |phase: &str, i: usize, n: usize, text: &str| {
                if let Some(p) = progress.as_mut() {
                    p(i, n, &phased_text(phase, text));
                }
            };
            if method == "relocate.run" {
                relocate::relocate(lib, &s(a, "dest"), Some(&mut steps))?;
            } else {
                relocate::restore(lib, Some(&mut steps))?;
            }
            Value::Null
        }

        "bisect.state" => bisect_json(st),
        "bisect.start" => json!(st.bisect.lock().start(&ids(a, "ids"), progress)?),
        "bisect.report" => json!(st.bisect.lock().report(b(a, "worked"), progress)?),
        "bisect.end" => {
            st.bisect.lock().end(progress)?;
            Value::Null
        }

        "dupes.find" => Value::Array(
            duplicates::find_duplicates(lib, progress)?
                .iter()
                .map(|g| {
                    json!({
                        "kind": g.kind,
                        "title": g.title(),
                        "items": items_json(&g.items),
                        "keep": g.keep,
                        "overlap": g.overlap.iter().map(|(id, (shared, total))| (id.to_string(), json!([shared, total]))).collect::<Map<_, _>>(),
                    })
                })
                .collect(),
        ),

        "tray.load" => {
            let entries = tray::load_tray(&s(a, "tray_dir"));
            let cache = thumbs_dir();
            let n = entries.len();
            let mut out = Vec::with_capacity(n);
            for (idx, e) in entries.iter().enumerate() {
                if let Some(p) = progress.as_mut() {
                    p(idx, n, &e.name);
                }
                let thumb = tray::entry_thumb(e, &cache)?;
                // 관계표는 화면에서 뺐으므로 세이브 파일은 읽지 않는다.
                out.push(entry_json(e, thumb, vec![Value::Null; e.sims.len()]));
            }
            let id_map = lib.db.res_id_map()?;
            let mut t = st.tray.lock();
            t.entries = entries;
            t.id_map = Some(id_map);
            Value::Array(out)
        }
        "tray.clear" => {
            *st.tray.lock() = TrayState::default();
            Value::Null
        }
        "tray.refresh_idmap" => {
            let id_map = lib.db.res_id_map()?;
            st.tray.lock().id_map = Some(id_map);
            Value::Null
        }
        "tray.sim_thumbs" => json!(tray::sim_thumbs(&tray_entry(st, &s(a, "key"))?, &thumbs_dir())?),
        "tray.used_cc" => {
            let e = tray_entry(st, &s(a, "key"))?;
            let t = st.tray.lock();
            items_json(&tray::used_cc(lib, &e, t.id_map.as_ref())?)
        }
        "tray.delete" => {
            tray::delete_entry(&tray_entry(st, &s(a, "key"))?)?;
            Value::Null
        }
        "tray.save_copy" => {
            let e = tray_entry(st, &s(a, "key"))?;
            let res = tray::save_copy(
                &e,
                &s(a, "tray_dir"),
                &s(a, "name"),
                opt_s(a, "thumb").as_deref(),
                b(a, "clean"),
                opt_s(a, "desc").as_deref(),
            )?;
            json!({"key": res.key, "thumbs": res.thumbs})
        }
        "tray.export_zip" => {
            let e = tray_entry(st, &s(a, "key"))?;
            let cc = if b(a, "with_cc") {
                let t = st.tray.lock();
                Some(tray::used_cc(lib, &e, t.id_map.as_ref())?)
            } else {
                None
            };
            json!(tray::export_zip(&e, &s(a, "out_dir"), cc.as_deref(), lib)?)
        }
        _ => return Err(Error::msg(format!("알 수 없는 호출: {method}"))),
    })
}

// ---------------------------------------------------------------- C 경계

fn to_c(v: &Value) -> *mut c_char {
    let text = serde_json::to_string(v).unwrap_or_else(|_| "{\"err\":\"json\"}".to_string());
    CString::new(text.replace('\0', "")).map(|c| c.into_raw()).unwrap_or(std::ptr::null_mut())
}

/// # Safety
/// `method` 와 `args` 는 NUL 로 끝나는 UTF-8 문자열이어야 한다. 돌려받은 포인터는 `s4_free` 로 해제한다.
#[no_mangle]
pub unsafe extern "C" fn s4_call(method: *const c_char, args: *const c_char, cb: ProgressCb, ud: *mut c_void) -> *mut c_char {
    let method = CStr::from_ptr(method).to_string_lossy().into_owned();
    let args_text = if args.is_null() { String::new() } else { CStr::from_ptr(args).to_string_lossy().into_owned() };
    let result = catch_unwind(AssertUnwindSafe(|| {
        let a: Value = if args_text.is_empty() { Value::Null } else { serde_json::from_str(&args_text).unwrap_or(Value::Null) };
        let mut forward = |i: usize, n: usize, text: &str| {
            if let Some(f) = cb {
                if let Ok(c) = CString::new(text.replace('\0', "")) {
                    f(ud, i as u64, n as u64, c.as_ptr());
                }
            }
        };
        let progress: Progress = if cb.is_some() { Some(&mut forward) } else { None };
        dispatch(&method, &a, progress)
    }));
    let v = match result {
        Ok(Ok(v)) => json!({"ok": v}),
        Ok(Err(e)) => json!({"err": e.to_string()}),
        Err(p) => {
            let msg = p.downcast_ref::<String>().cloned().or_else(|| p.downcast_ref::<&str>().map(|x| x.to_string()));
            json!({"err": format!("내부 오류: {}", msg.unwrap_or_default())})
        }
    };
    to_c(&v)
}

/// # Safety
/// `s4_call` 이 돌려준 포인터만 넘겨야 한다.
#[no_mangle]
pub unsafe extern "C" fn s4_free(p: *mut c_char) {
    if !p.is_null() {
        drop(CString::from_raw(p));
    }
}
