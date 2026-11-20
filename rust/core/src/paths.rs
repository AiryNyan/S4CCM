//! 심즈 4 사용자 폴더와 게임 실행 파일 찾기.

use std::sync::OnceLock;

use regex::Regex;

use crate::pyos;

pub const MARKERS: [&str; 4] = ["Options.ini", "GameVersion.txt", "Mods", "Saves"];
pub const DISABLED_DIR: &str = "S4CCM_Disabled";
pub const QUARANTINE_DIR: &str = "S4CCM_Quarantine";

#[derive(Debug, Clone, PartialEq)]
pub struct SimsFolder {
    pub root: String,
    pub marker_count: usize,
    pub cc_count: usize,
    pub mtime: f64,
}

impl SimsFolder {
    pub fn mods(&self) -> String {
        pyos::join(&self.root, "Mods")
    }
}

#[cfg(windows)]
fn known_folder(id: &windows_sys::core::GUID) -> Option<String> {
    use windows_sys::Win32::System::Com::CoTaskMemFree;
    use windows_sys::Win32::UI::Shell::SHGetKnownFolderPath;
    let mut out: *mut u16 = std::ptr::null_mut();
    // SAFETY: out 은 성공 시 셸이 할당한 NUL 종료 와이드 문자열을 가리키며, 읽은 뒤 해제한다.
    unsafe {
        let hr = SHGetKnownFolderPath(id, 0, std::ptr::null_mut(), &mut out);
        if hr != 0 || out.is_null() {
            if !out.is_null() {
                CoTaskMemFree(out as _);
            }
            return None;
        }
        let mut len = 0;
        while *out.add(len) != 0 {
            len += 1;
        }
        let path = String::from_utf16_lossy(std::slice::from_raw_parts(out, len));
        CoTaskMemFree(out as _);
        Some(path)
    }
}

#[cfg(windows)]
fn documents() -> Option<String> {
    known_folder(&windows_sys::Win32::UI::Shell::FOLDERID_Documents)
}

#[cfg(windows)]
fn public_documents() -> Option<String> {
    known_folder(&windows_sys::Win32::UI::Shell::FOLDERID_PublicDocuments)
}

#[cfg(not(windows))]
fn documents() -> Option<String> {
    None
}

#[cfg(not(windows))]
fn public_documents() -> Option<String> {
    None
}

fn home() -> String {
    std::env::var("USERPROFILE").unwrap_or_default()
}

pub fn document_dirs() -> Vec<String> {
    let onedrive = std::env::var("OneDrive").ok().filter(|s| !s.is_empty());
    let cands = [
        documents(),
        onedrive.as_ref().map(|o| pyos::join(o, "Documents")),
        onedrive.as_ref().map(|o| pyos::join(o, "문서")),
        Some(pyos::join(&home(), "Documents")),
        public_documents(),
    ];
    let mut dirs: Vec<String> = Vec::new();
    for d in cands.into_iter().flatten() {
        if !d.is_empty() && pyos::isdir(&d) && !dirs.iter().any(|x| pyos::normcase(x) == pyos::normcase(&d)) {
            dirs.push(d);
        }
    }
    dirs
}

pub fn count_markers(root: &str) -> usize {
    MARKERS.iter().filter(|m| pyos::exists(&pyos::join(root, m))).count()
}

fn quick_cc_count(mods: &str, limit: usize) -> usize {
    // os.walk 와 같은 순서로 폴더 단위로 세다가 한도를 넘으면 멈춘다.
    fn go(dir: &str, n: &mut usize, limit: usize) -> bool {
        let Ok(rd) = std::fs::read_dir(dir) else { return true };
        let mut subdirs = Vec::new();
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            let ft = e.file_type().ok();
            let is_link = ft.map(|t| t.is_symlink()).unwrap_or(false);
            let is_dir = if is_link {
                std::fs::metadata(e.path()).map(|m| m.is_dir()).unwrap_or(false)
            } else {
                ft.map(|t| t.is_dir()).unwrap_or(false)
            };
            if is_dir {
                if !is_link {
                    subdirs.push(name);
                }
            } else {
                let low = name.to_lowercase();
                if low.ends_with(".package") || low.ends_with(".ts4script") {
                    *n += 1;
                }
            }
        }
        if *n > limit {
            return false;
        }
        for d in subdirs {
            if !go(&pyos::join(dir, &d), n, limit) {
                return false;
            }
        }
        true
    }
    let mut n = 0;
    go(mods, &mut n, limit);
    n
}

pub fn inspect(root: &str) -> SimsFolder {
    let mods = pyos::join(root, "Mods");
    SimsFolder {
        root: root.to_string(),
        marker_count: count_markers(root),
        cc_count: if pyos::isdir(&mods) { quick_cc_count(&mods, 20000) } else { 0 },
        mtime: pyos::getmtime(root).unwrap_or(0.0),
    }
}

pub fn find_sims_folders() -> Vec<SimsFolder> {
    let mut found = Vec::new();
    for docs in document_dirs() {
        let ea = pyos::join(&docs, "Electronic Arts");
        if !pyos::isdir(&ea) {
            continue;
        }
        for name in pyos::listdir(&ea).unwrap_or_default() {
            let cand = pyos::join(&ea, &name);
            if pyos::isdir(&cand) && count_markers(&cand) >= 2 {
                found.push(inspect(&cand));
            }
        }
    }
    found
}

pub fn normalize_user_choice(path: &str) -> String {
    let path = pyos::normpath(path.trim().trim_matches('"'));
    if pyos::basename(&path).to_lowercase() == "mods" {
        return pyos::dirname(&path);
    }
    path
}

pub fn validate(root: &str) -> (bool, String) {
    if !pyos::isdir(root) {
        return (false, "폴더가 존재하지 않습니다.".to_string());
    }
    let missing: Vec<&str> = MARKERS.iter().copied().filter(|m| !pyos::exists(&pyos::join(root, m))).collect();
    let has_mods = pyos::isdir(&pyos::join(root, "Mods"));
    if missing.len() <= 2 && has_mods {
        return (true, "정상".to_string());
    }
    if !has_mods {
        return (false, "Mods 폴더가 없습니다.".to_string());
    }
    (false, format!("심즈 폴더 표시 파일이 없습니다: {}", missing.join(", ")))
}

#[cfg(windows)]
fn reg_install_dirs() -> Vec<String> {
    use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
    use winreg::RegKey;
    let mut out = Vec::new();
    for hive in [HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER] {
        for key in [r"SOFTWARE\Maxis\The Sims 4", r"SOFTWARE\WOW6432Node\Maxis\The Sims 4"] {
            if let Ok(k) = RegKey::predef(hive).open_subkey(key) {
                if let Ok(v) = k.get_value::<String, _>("Install Dir") {
                    out.push(v);
                }
            }
        }
    }
    out
}

#[cfg(not(windows))]
fn reg_install_dirs() -> Vec<String> {
    Vec::new()
}

fn steam_dirs() -> Vec<String> {
    static RX: OnceLock<Regex> = OnceLock::new();
    let rx = RX.get_or_init(|| Regex::new(r#""path"\s+"([^"]+)""#).unwrap());
    let mut out = Vec::new();
    for base in [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"] {
        let vdf = pyos::join3(base, "steamapps", "libraryfolders.vdf");
        let mut libs = vec![base.to_string()];
        if pyos::isfile(&vdf) {
            if let Ok(bytes) = std::fs::read(&vdf) {
                let text = String::from_utf8_lossy(&bytes).replace('\u{FFFD}', "");
                libs.extend(rx.captures_iter(&text).map(|m| m[1].replace("\\\\", "\\")));
            }
        }
        out.extend(libs.iter().map(|l| pyos::join(&pyos::join3(l, "steamapps", "common"), "The Sims 4")));
    }
    out
}

pub fn find_game_exe() -> Option<String> {
    let mut cands = reg_install_dirs();
    cands.extend(
        [
            r"C:\Program Files\EA Games\The Sims 4",
            r"C:\Program Files (x86)\Origin Games\The Sims 4",
            r"C:\Program Files\Origin Games\The Sims 4",
        ]
        .map(String::from),
    );
    cands.extend(steam_dirs());
    cands.into_iter().map(|d| pyos::join(&pyos::join3(&d, "Game", "Bin"), "TS4_x64.exe")).find(|exe| pyos::isfile(exe))
}
