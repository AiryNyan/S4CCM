//! 파일·폴더·압축파일에서 모드와 트레이 항목 가져오기.

use std::collections::{BTreeSet, HashSet};
use std::process::Command;

use indexmap::IndexMap;

use crate::categories as cats;
use crate::classify::classify_package;
use crate::db::Item;
use crate::error::{Error, Result};
use crate::library::{fingerprint, token, Library, UserImage, IMAGE_EXT};
use crate::pyos;
use crate::scriptmods::{self, KnownMod};
use crate::tray;

pub const ARCHIVE_EXT: [&str; 3] = [".zip", ".7z", ".rar"];
pub const MOD_EXT: [&str; 2] = [".package", ".ts4script"];
/// 단계 이름과 함께 진행 상황을 알리는 콜백: (단계, i, n, 지금 다루는 이름).
pub type Steps<'a> = Option<&'a mut (dyn FnMut(&str, usize, usize, &str) + 'a)>;

pub const PHASE_EXTRACT: &str = "압축 파일을 푸는 중";
pub const PHASE_LOOK: &str = "파일을 살펴보는 중";
pub const PHASE_CLASSIFY: &str = "CC 종류를 판정하는 중";
pub const PHASE_COPY: &str = "파일을 복사하는 중";
pub const PHASE_SCAN: &str = "추가한 파일을 목록에 반영하는 중";

fn report(steps: &mut Steps, phase: &str, i: usize, n: usize, text: &str) {
    if let Some(f) = steps.as_mut() {
        f(phase, i, n, text);
    }
}

const SEVEN_ZIP: [&str; 2] = [r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"];

pub const MODE_SCRIPT: &str = "script";
pub const MODE_CC: &str = "cc";

#[derive(Debug, Clone, PartialEq)]
pub struct ImportEntry {
    pub src: String,
    pub rel: String,
    pub kind: String,
    pub category: String,
    pub reason: String,
    pub confidence: f64,
    pub include: bool,
    pub duplicate: Option<String>,
    pub user_changed: bool,
}

impl ImportEntry {
    pub fn new(src: String, rel: String, kind: &str) -> Self {
        ImportEntry {
            src,
            rel,
            kind: kind.to_string(),
            category: cats::UNSORTED.to_string(),
            reason: String::new(),
            confidence: 0.0,
            include: true,
            duplicate: None,
            user_changed: false,
        }
    }
}

#[derive(Debug, Clone)]
pub struct TrayImport {
    pub key: String,
    pub name: String,
    pub kind_label: String,
    /// 이 항목에 속한 파일들 (`ImportGroup::entries` 의 인덱스).
    pub entries: Vec<usize>,
    pub include: bool,
    pub exists: bool,
}

#[derive(Debug, Clone)]
pub struct ImportGroup {
    pub name: String,
    pub source: String,
    pub mode: String,
    pub entries: Vec<ImportEntry>,
    pub mode_reason: String,
    pub known: Option<&'static KnownMod>,
    pub replaces: Option<Item>,
    pub replace: bool,
    pub missing_libs: Vec<&'static KnownMod>,
    pub folder: String,
    pub tray_items: Vec<TrayImport>,
}

impl ImportGroup {
    fn new(name: String, source: String, mode: &str, entries: Vec<ImportEntry>) -> Self {
        ImportGroup {
            name,
            source,
            mode: mode.to_string(),
            entries,
            mode_reason: String::new(),
            known: None,
            replaces: None,
            replace: true,
            missing_libs: Vec::new(),
            folder: String::new(),
            tray_items: Vec::new(),
        }
    }

    pub fn of_kind(&self, kind: &str) -> Vec<usize> {
        self.entries.iter().enumerate().filter(|(_, e)| e.kind == kind).map(|(i, _)| i).collect()
    }

    pub fn trays(&self) -> Vec<usize> {
        self.of_kind("tray")
    }

    pub fn packages(&self) -> Vec<usize> {
        self.of_kind("package")
    }

    pub fn scripts(&self) -> Vec<usize> {
        self.of_kind("script")
    }

    pub fn images(&self) -> Vec<usize> {
        self.of_kind("image")
    }

    fn packages_then_scripts(&self) -> Vec<usize> {
        let mut v = self.packages();
        v.extend(self.scripts());
        v
    }
}

#[derive(Debug, Clone, Default)]
pub struct ImportPlan {
    pub groups: Vec<ImportGroup>,
    pub temp_dirs: Vec<String>,
    pub errors: Vec<String>,
    pub trays_added: usize,
    pub mods_added: usize,
}

impl ImportPlan {
    pub fn cleanup(&mut self) {
        for d in self.temp_dirs.drain(..) {
            let _ = std::fs::remove_dir_all(d);
        }
    }
}

fn kind_of(name: &str) -> &'static str {
    let low = name.to_lowercase();
    if tray::is_tray_file(name) {
        "tray"
    } else if low.ends_with(".package") {
        "package"
    } else if low.ends_with(".ts4script") {
        "script"
    } else if pyos::ends_with_any(&low, &IMAGE_EXT) {
        "image"
    } else {
        "other"
    }
}

fn safe_name(name: &str) -> String {
    tray::safe_name(name, "mod")
}

/// UTF-8 표시가 없는 zip 의 한글 파일명(CP949)을 살린다.
fn zip_entry_name(raw: &[u8], name: &str) -> String {
    if raw.is_ascii() {
        return String::from_utf8_lossy(raw).into_owned();
    }
    let as_cp437: String = raw.iter().map(|&b| cp437_char(b)).collect();
    if name != as_cp437 {
        // UTF-8 플래그가 선 항목.
        return name.to_string();
    }
    let (decoded, _, had_errors) = encoding_rs::EUC_KR.decode(raw);
    if had_errors {
        as_cp437
    } else {
        decoded.into_owned()
    }
}

fn cp437_char(b: u8) -> char {
    const HIGH: &str = "ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■\u{a0}";
    if b < 0x80 {
        b as char
    } else {
        HIGH.chars().nth((b - 0x80) as usize).unwrap_or('\u{FFFD}')
    }
}

fn extract_zip(path: &str, out: &str, tick: &mut dyn FnMut(usize, usize, &str)) -> Result<()> {
    let mut z = zip::ZipArchive::new(std::fs::File::open(path)?)?;
    let out_norm = pyos::normpath(out);
    let total = z.len();
    for i in 0..total {
        let mut info = z.by_index(i)?;
        let name = zip_entry_name(info.name_raw(), info.name());
        tick(i, total, &name);
        let target = pyos::normpath(&pyos::join(out, &name));
        if !target.starts_with(&format!("{out_norm}\\")) {
            continue;
        }
        if name.ends_with('/') {
            pyos::makedirs(&target)?;
            continue;
        }
        pyos::makedirs(&pyos::dirname(&target))?;
        std::io::copy(&mut info, &mut std::fs::File::create(&target)?)?;
    }
    Ok(())
}

fn extract(path: &str, out: &str, tick: &mut dyn FnMut(usize, usize, &str)) -> Result<()> {
    let low = path.to_lowercase();
    if low.ends_with(".zip") {
        return extract_zip(path, out, tick);
    }
    // 7z/rar 은 항목별 진행을 알 수 없어 이름만 알린다.
    tick(0, 0, &pyos::basename(path));
    if low.ends_with(".7z") {
        pyos::makedirs(out)?;
        sevenz_rust::decompress_file(path, out)?;
        return Ok(());
    }
    let Some(exe) = SEVEN_ZIP.iter().find(|p| pyos::isfile(p)) else {
        return Err(Error::msg("RAR/7z 압축을 풀려면 7-Zip(https://7-zip.org) 설치가 필요합니다."));
    };
    let mut cmd = Command::new(exe);
    cmd.args(["x", "-y", &format!("-o{out}"), path]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }
    let r = cmd.output()?;
    if !r.status.success() {
        let stderr = String::from_utf8_lossy(&r.stderr).trim().to_string();
        let msg = if stderr.is_empty() {
            let so = String::from_utf8_lossy(&r.stdout).trim().to_string();
            let n = so.chars().count();
            so.chars().skip(n.saturating_sub(200)).collect()
        } else {
            stderr
        };
        return Err(Error::msg(format!("압축 해제 실패: {msg}")));
    }
    Ok(())
}

fn expand_nested(base: &str, rounds: usize) {
    for _ in 0..rounds {
        let nested: Vec<String> = pyos::walk(base)
            .iter()
            .flat_map(|w| w.filenames.iter().map(|n| pyos::join(&w.dirpath, n)).collect::<Vec<_>>())
            .filter(|p| pyos::ends_with_any(&p.to_lowercase(), &ARCHIVE_EXT))
            .collect();
        if nested.is_empty() {
            return;
        }
        for p in nested {
            if extract(&p, &pyos::splitext(&p).0, &mut |_, _, _| {}).is_ok() {
                let _ = std::fs::remove_file(&p);
            }
        }
    }
}

fn collect(base: &str) -> Vec<ImportEntry> {
    let mut out = Vec::new();
    for w in pyos::walk(base) {
        for n in &w.filenames {
            let p = pyos::join(&w.dirpath, n);
            if p.contains("__MACOSX") {
                continue;
            }
            let rel = pyos::relpath(&p, base).replace('\\', "/");
            out.push(ImportEntry::new(p, rel, kind_of(n)));
        }
    }
    out
}

fn group_trays(lib: &Library, g: &mut ImportGroup) {
    let mut by_key: IndexMap<u64, Vec<usize>> = IndexMap::new();
    for i in g.trays() {
        by_key.entry(tray::group_key(tray::instance_of(&g.entries[i].src))).or_default().push(i);
    }
    let tray_dir = pyos::join(&lib.sims_dir(), "Tray");
    g.tray_items = Vec::new();
    for (key, idxs) in by_key {
        let files: Vec<String> = idxs.iter().map(|&i| g.entries[i].src.clone()).collect();
        let mut info = tray::TrayEntry::new(format!("{key:016x}"));
        info.files = files.clone();
        let Some(meta) = files.iter().find(|f| f.to_lowercase().ends_with(".trayitem")) else { continue };
        let _ = tray::parse_trayitem(meta, &mut info);
        let exists = files.iter().any(|f| pyos::exists(&pyos::join(&tray_dir, &pyos::basename(f))));
        let name = if info.name.is_empty() { pyos::basename(meta) } else { info.name.clone() };
        g.tray_items.push(TrayImport {
            key: info.key.clone(),
            name,
            kind_label: info.kind_label().to_string(),
            entries: idxs,
            include: !exists,
            exists,
        });
    }
}

fn own_folder_name(g: &ImportGroup) -> Option<String> {
    if pyos::isdir(&g.source) {
        return Some(safe_name(&pyos::basename(g.source.trim_end_matches(['\\', '/']))));
    }
    let tops: HashSet<&str> =
        g.entries.iter().filter(|e| e.rel.contains('/')).map(|e| e.rel.split('/').next().unwrap_or("")).collect();
    if tops.len() == 1 && g.entries.iter().all(|e| e.rel.contains('/')) {
        return tops.into_iter().next().map(safe_name);
    }
    None
}

fn classify_group(lib: &Library, g: &mut ImportGroup, tick: &mut dyn FnMut(&str)) -> Result<()> {
    group_trays(lib, g);
    for i in g.packages() {
        tick(&g.entries[i].rel);
        let c = classify_package(&g.entries[i].src);
        let e = &mut g.entries[i];
        e.category = c.category;
        e.reason = c.reason;
        e.confidence = c.confidence;
    }
    for i in g.scripts() {
        tick(&g.entries[i].rel);
    }
    for i in g.packages_then_scripts() {
        let dup = lib.db.by_fingerprint(&fingerprint(&g.entries[i].src)?)?;
        if let Some(d) = dup.first() {
            g.entries[i].duplicate = Some(d.name.clone());
            g.entries[i].include = false;
        }
    }
    let (scripts, packages) = (g.scripts(), g.packages());
    if !scripts.is_empty() {
        g.mode = MODE_SCRIPT.to_string();
        let names: Vec<String> = scripts.iter().chain(&packages).map(|&i| g.entries[i].rel.clone()).collect();
        g.known = scriptmods::identify(&names);
        let own = own_folder_name(g);
        g.folder = match g.known {
            Some(k) => k.folder.to_string(),
            None => own.filter(|s| !s.is_empty()).unwrap_or_else(|| cats::SCRIPT_FOLDER.to_string()),
        };
        g.mode_reason = match g.known {
            Some(k) => format!("{}(으)로 인식했습니다.", k.name),
            None => format!(".ts4script {}개 포함 → 스크립트 모드로 보입니다.", scripts.len()),
        };
        let items = lib.items()?;
        g.replaces = scriptmods::find_installed(&items, g.known, &names).cloned();
        if let Some(r) = &g.replaces {
            g.mode_reason += &format!(" 이미 설치된 '{}'의 새 버전으로 보입니다.", r.name);
            for i in g.packages_then_scripts() {
                if g.entries[i].duplicate.is_some() {
                    g.entries[i].include = true;
                }
            }
        }
    } else if !packages.is_empty() {
        g.mode = MODE_CC.to_string();
        let kinds: BTreeSet<String> = packages.iter().map(|&i| cats::label(&g.entries[i].category)).collect();
        let shown: Vec<String> = kinds.into_iter().take(4).collect();
        g.mode_reason = format!("CC {}개 ({})", packages.len(), shown.join(", "));
    } else {
        g.mode = MODE_CC.to_string();
    }
    if !g.tray_items.is_empty() {
        let note = format!("트레이 항목 {}개 → Tray 폴더", g.tray_items.len());
        g.mode_reason = if g.mode_reason.is_empty() { note } else { format!("{note} · {}", g.mode_reason) };
    }
    Ok(())
}

/// 끌어다 놓은 경로들을 살펴 무엇을 어디에 넣을지 계획을 만든다.
pub fn analyze<S: AsRef<str>>(lib: &Library, paths: &[S]) -> ImportPlan {
    analyze_with(lib, paths, None)
}

/// `analyze` 와 같고, 단계별 진행 상황을 알린다.
pub fn analyze_with<S: AsRef<str>>(lib: &Library, paths: &[S], mut steps: Steps) -> ImportPlan {
    let mut plan = ImportPlan::default();
    let mut loose: Vec<String> = Vec::new();
    let mut loose_tray: Vec<String> = Vec::new();
    for p in paths {
        let p = p.as_ref();
        let low = p.to_lowercase();
        let mut one = |plan: &mut ImportPlan| -> Result<()> {
            if pyos::isdir(p) {
                report(&mut steps, PHASE_LOOK, 0, 0, p);
                let name = pyos::basename(p.trim_end_matches(['\\', '/']));
                plan.groups.push(ImportGroup::new(name, p.to_string(), MODE_CC, collect(p)));
            } else if pyos::ends_with_any(&low, &ARCHIVE_EXT) {
                let tmp = tempfile::Builder::new().prefix("s4ccm_").tempdir()?.keep();
                let tmp = tmp.to_string_lossy().into_owned();
                plan.temp_dirs.push(tmp.clone());
                let archive = pyos::basename(p);
                extract(p, &tmp, &mut |i, n, entry| {
                    let shown = if n > 0 { format!("{archive} › {entry}") } else { entry.to_string() };
                    report(&mut steps, PHASE_EXTRACT, i, n, &shown);
                })?;
                expand_nested(&tmp, 2);
                let name = pyos::splitext(&pyos::basename(p)).0;
                plan.groups.push(ImportGroup::new(name, p.to_string(), MODE_CC, collect(&tmp)));
            } else if pyos::ends_with_any(&low, &MOD_EXT) {
                loose.push(p.to_string());
            } else if tray::is_tray_file(p) {
                loose_tray.push(p.to_string());
            } else {
                plan.errors.push(format!("지원하지 않는 파일: {}", pyos::basename(p)));
            }
            Ok(())
        };
        if let Err(ex) = one(&mut plan) {
            plan.errors.push(format!("{}: {ex}", pyos::basename(p)));
        }
    }

    let mut used: HashSet<String> = HashSet::new();
    let tok = |f: &str| token(&pyos::basename(f));
    let is_script = |f: &str| f.to_lowercase().ends_with(".ts4script");
    let is_package = |f: &str| f.to_lowercase().ends_with(".package");
    let mut by_tok: IndexMap<String, Vec<String>> = IndexMap::new();
    for s in loose.iter().filter(|f| is_script(f)) {
        by_tok.entry(tok(s)).or_default().push(s.clone());
    }
    for (t, scripts) in &by_tok {
        let mut members = scripts.clone();
        members.extend(loose.iter().filter(|f| is_package(f) && tok(f) == *t).cloned());
        used.extend(members.iter().cloned());
        let entries = members.iter().map(|m| ImportEntry::new(m.clone(), pyos::basename(m), kind_of(m))).collect();
        plan.groups.push(ImportGroup::new(
            pyos::splitext(&pyos::basename(&scripts[0])).0,
            scripts[0].clone(),
            MODE_SCRIPT,
            entries,
        ));
    }
    for f in &loose {
        if !used.contains(f) {
            plan.groups.push(ImportGroup::new(
                pyos::splitext(&pyos::basename(f)).0,
                f.clone(),
                MODE_CC,
                vec![ImportEntry::new(f.clone(), pyos::basename(f), "package")],
            ));
        }
    }

    if !loose_tray.is_empty() {
        let entries = loose_tray.iter().map(|f| ImportEntry::new(f.clone(), pyos::basename(f), "tray")).collect();
        plan.groups.push(ImportGroup::new("트레이 파일".to_string(), pyos::dirname(&loose_tray[0]), MODE_CC, entries));
    }

    let groups = std::mem::take(&mut plan.groups);
    let total: usize = groups.iter().map(|g| g.packages().len() + g.scripts().len()).sum();
    let mut done = 0usize;
    for mut g in groups {
        if g.packages().is_empty() && g.scripts().is_empty() && g.trays().is_empty() {
            plan.errors.push(format!("{}: 모드 파일(.package/.ts4script)이나 트레이 파일이 없습니다.", g.name));
            continue;
        }
        let mut tick = |rel: &str| {
            report(&mut steps, PHASE_CLASSIFY, done, total, rel);
            done += 1;
        };
        if let Err(ex) = classify_group(lib, &mut g, &mut tick) {
            plan.errors.push(format!("{}: {ex}", g.name));
            continue;
        }
        if !g.packages().is_empty() || !g.scripts().is_empty() || !g.tray_items.is_empty() {
            plan.groups.push(g);
        }
    }
    report(&mut steps, PHASE_CLASSIFY, total, total, "");
    if let Err(ex) = check_libraries(lib, &mut plan) {
        plan.errors.push(ex.to_string());
    }
    plan
}

fn check_libraries(lib: &Library, plan: &mut ImportPlan) -> Result<()> {
    let items = lib.items()?;
    let mut have: HashSet<&str> = scriptmods::installed_mod_ids(&items, true).keys().copied().collect();
    have.extend(plan.groups.iter().filter_map(|g| g.known.map(|k| k.id)));
    for g in &mut plan.groups {
        let srcs: Vec<&str> = g.packages().iter().map(|&i| g.entries[i].src.as_str()).collect();
        let mut needed = scriptmods::required_libraries(&srcs);
        if let Some(k) = g.known {
            needed.remove(k.id);
        }
        g.missing_libs = needed.into_iter().filter(|i| !have.contains(i)).filter_map(scriptmods::by_id).collect();
    }
    Ok(())
}

fn strip_common_root(rels: &[String]) -> Vec<String> {
    let mut parts: Vec<Vec<&str>> = rels.iter().map(|r| r.split('/').collect()).collect();
    while !parts.is_empty()
        && parts.iter().all(|p| p.len() > 1)
        && parts.iter().map(|p| p[0]).collect::<HashSet<_>>().len() == 1
    {
        for p in parts.iter_mut() {
            p.remove(0);
        }
    }
    parts.into_iter().map(|p| p.join("/")).collect()
}

fn remove_old_version(lib: &Library, old: &Item, new_entries: &[&ImportEntry]) -> Result<Option<String>> {
    let mut old = old.clone();
    if old.status == "disabled" {
        let label = format!("업데이트 준비: {} 다시 활성화", old.name);
        let mut one = [old.clone()];
        lib.set_status(&mut one, "active", &label, None)?;
        let [moved] = one;
        old = lib.db.get(old.id)?.unwrap_or(moved);
    }
    let new_stems: HashSet<String> = new_entries
        .iter()
        .filter(|e| e.kind == "package" || e.kind == "script")
        .map(|e| scriptmods::script_stem(&e.rel))
        .collect();
    for m in &old.members {
        if pyos::ends_with_any(&m.to_lowercase(), &scriptmods::MOD_EXT) && new_stems.contains(&scriptmods::script_stem(m)) {
            let p = lib.abs(m);
            if pyos::exists(&p) {
                trash::delete(&p)?;
            }
        }
    }
    let old_abs = lib.abs(&old.rel_path);
    if pyos::isdir(&old_abs) && old.status == "active" {
        return Ok(Some(old.rel_path.clone()));
    }
    Ok(None)
}

fn copy_trays(lib: &Library, g: &ImportGroup, tick: &mut dyn FnMut(&str)) -> Result<Vec<String>> {
    let tray_dir = pyos::join(&lib.sims_dir(), "Tray");
    let mut out = Vec::new();
    for t in &g.tray_items {
        if !t.include {
            continue;
        }
        pyos::makedirs(&tray_dir)?;
        for &i in &t.entries {
            let src = &g.entries[i].src;
            let base = pyos::basename(src);
            tick(&base);
            std::fs::copy(src, pyos::join(&tray_dir, &base))?;
            out.push(format!("Tray/{base}"));
        }
    }
    Ok(out)
}

/// 계획대로 복사해 넣는다. (추가한 파일 수, 추가한 상대경로들)
pub fn execute(lib: &Library, plan: &mut ImportPlan) -> Result<(usize, Vec<String>)> {
    execute_with(lib, plan, None)
}

/// 복사할 파일 수 (진행 표시용). 이미 있는 설정 파일처럼 건너뛰는 파일이 있어 대략적인 값이다.
fn copy_count(plan: &ImportPlan) -> usize {
    let mut n = 0;
    for g in &plan.groups {
        n += g.tray_items.iter().filter(|t| t.include).map(|t| t.entries.len()).sum::<usize>();
        n += if g.mode == MODE_SCRIPT {
            g.entries.iter().filter(|e| e.include && e.kind != "tray").count()
        } else {
            g.packages_then_scripts().iter().filter(|&&i| g.entries[i].include).count()
        };
    }
    n
}

/// `execute` 와 같고, 단계별 진행 상황을 알린다.
pub fn execute_with(lib: &Library, plan: &mut ImportPlan, mut steps: Steps) -> Result<(usize, Vec<String>)> {
    let _g = lib.lock_ops();
    let total = copy_count(plan);
    let mut copied = 0usize;
    let sims = lib.sims_dir();
    let rel_of = |dst: &str| pyos::relpath(dst, &sims).replace('\\', "/");
    let mut added: Vec<String> = Vec::new();
    let mut user_cats: IndexMap<String, String> = IndexMap::new();
    let mut thumbs: IndexMap<String, String> = IndexMap::new();
    let mut trays_total = 0;
    for g in &plan.groups {
        let trays_added = copy_trays(lib, g, &mut |name: &str| {
            report(&mut steps, PHASE_COPY, copied.min(total), total, name);
            copied += 1;
        })?;
        trays_total += trays_added
            .iter()
            .filter(|a| a.to_lowercase().ends_with(".trayitem"))
            .map(|a| pyos::basename(a))
            .collect::<HashSet<_>>()
            .len();
        added.extend(trays_added);
        if g.mode == MODE_SCRIPT {
            let entries: Vec<&ImportEntry> = g.entries.iter().filter(|e| e.include && e.kind != "tray").collect();
            let mut folder = if g.folder.is_empty() { "Mods".to_string() } else { format!("Mods/{}", safe_name(&g.folder)) };
            if let (Some(old), true) = (&g.replaces, g.replace) {
                if let Some(f) = remove_old_version(lib, old, &entries)? {
                    folder = f;
                }
            }
            let rels = strip_common_root(&entries.iter().map(|e| e.rel.clone()).collect::<Vec<_>>());
            for (e, rel) in entries.iter().zip(rels) {
                let rel = if e.kind == "script" { pyos::basename(&rel) } else { rel };
                let mut dst = lib.abs(&format!("{folder}/{rel}"));
                if pyos::exists(&dst) {
                    if e.kind != "package" && e.kind != "script" {
                        continue;
                    }
                    dst = lib.unique(&dst);
                }
                report(&mut steps, PHASE_COPY, copied.min(total), total, &rel);
                copied += 1;
                pyos::makedirs(&pyos::dirname(&dst))?;
                std::fs::copy(&e.src, &dst)?;
                added.push(rel_of(&dst));
            }
        } else {
            let (images, packages) = (g.images(), g.packages());
            let img = if !images.is_empty() && packages.len() <= 3 { Some(g.entries[images[0]].src.clone()) } else { None };
            for &i in &packages {
                let e = &g.entries[i];
                if !e.include {
                    continue;
                }
                let folder = format!("Mods/{}", lib.folder_for(&e.category));
                let dst = lib.unique(&lib.abs(&format!("{folder}/{}", pyos::basename(&e.src))));
                report(&mut steps, PHASE_COPY, copied.min(total), total, &e.rel);
                copied += 1;
                pyos::makedirs(&pyos::dirname(&dst))?;
                std::fs::copy(&e.src, &dst)?;
                let rel = rel_of(&dst);
                added.push(rel.clone());
                if e.user_changed {
                    user_cats.insert(rel.clone(), e.category.clone());
                }
                if let Some(img) = &img {
                    thumbs.insert(rel, img.clone());
                }
            }
            for &i in &g.scripts() {
                let e = &g.entries[i];
                if !e.include {
                    continue;
                }
                let dst = lib.unique(&lib.abs(&format!("Mods/{}/{}", cats::SCRIPT_FOLDER, pyos::basename(&e.src))));
                report(&mut steps, PHASE_COPY, copied.min(total), total, &e.rel);
                copied += 1;
                pyos::makedirs(&pyos::dirname(&dst))?;
                std::fs::copy(&e.src, &dst)?;
                added.push(rel_of(&dst));
            }
        }
    }
    plan.trays_added += trays_total;
    plan.mods_added = added.iter().filter(|a| !a.starts_with("Tray/")).count();
    report(&mut steps, PHASE_COPY, total, total, "");
    if plan.mods_added > 0 {
        let mut fwd = |i: usize, n: usize, text: &str| report(&mut steps, PHASE_SCAN, i, n, text);
        lib.scan(Some(&mut fwd))?;
    }
    let mut by_rel: IndexMap<String, Item> = lib.items()?.into_iter().map(|it| (it.rel_path.clone(), it)).collect();
    for (rel, cat) in &user_cats {
        if let Some(it) = by_rel.get_mut(rel) {
            lib.set_category(it, cat, false)?;
        }
    }
    for (rel, img) in &thumbs {
        if let Some(it) = by_rel.get(rel) {
            if it.thumb.as_deref().map_or(true, |t| t.is_empty()) {
                lib.set_user_thumb(it, UserImage::Path(img))?;
            }
        }
    }
    plan.cleanup();
    Ok((added.len(), added))
}
