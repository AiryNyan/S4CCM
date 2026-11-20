//! Mods 폴더를 다른 곳으로 옮기고 원래 자리에 심볼릭 링크를 남긴다 (도구 › 심볼릭 링크).
//!
//! Mods 와 함께 비활성(S4CCM_Disabled)·충돌 테스트(S4CCM_Quarantine) 폴더도 같은 곳으로 옮긴다.
//! 셋이 서로 다른 드라이브에 있으면 비활성화·충돌 테스트 때마다 파일을 복사하게 되기 때문이다.

use std::fs;
use std::io;
use std::path::Path;

use crate::error::{Error, Result};
use crate::importer::Steps;
use crate::library::Library;
use crate::paths::{DISABLED_DIR, QUARANTINE_DIR};
use crate::pyos;

/// 함께 옮기는 폴더들 (Mods 가 맨 앞).
pub const LINK_DIRS: [&str; 3] = ["Mods", DISABLED_DIR, QUARANTINE_DIR];

pub const PHASE_CHECK: &str = "옮길 파일을 세는 중";
pub const PHASE_COPY: &str = "파일을 복사하는 중";
pub const PHASE_VERIFY: &str = "복사한 파일을 대조하는 중";
pub const PHASE_LINK: &str = "심볼릭 링크를 만드는 중";
pub const PHASE_CLEAN: &str = "원래 자리의 파일을 지우는 중";
pub const PHASE_MOVE: &str = "폴더를 옮기는 중";
pub const PHASE_CLEAN_MOVED: &str = "옮겨 두었던 폴더를 지우는 중";

const OLD_SUFFIX: &str = ".s4ccm_old";

#[derive(Debug, Clone)]
pub struct DirState {
    pub name: &'static str,
    pub path: String,
    pub exists: bool,
    pub is_link: bool,
    pub target: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct Measure {
    pub files: usize,
    pub bytes: u64,
    pub same_volume: bool,
    pub free: Option<u64>,
}

fn report(steps: &mut Steps, phase: &str, i: usize, n: usize, text: &str) {
    if let Some(f) = steps.as_mut() {
        f(phase, i, n, text);
    }
}

fn is_link(p: &str) -> bool {
    fs::symlink_metadata(p).map(|m| m.file_type().is_symlink()).unwrap_or(false)
}

/// `\\?\` 접두어를 떼어 사람이 읽는 경로로.
fn plain(p: &Path) -> String {
    let s = p.to_string_lossy().into_owned();
    s.strip_prefix(r"\\?\").map(|x| x.to_string()).unwrap_or(s)
}

pub fn status(sims_dir: &str) -> Vec<DirState> {
    LINK_DIRS
        .iter()
        .map(|&name| {
            let path = pyos::join(sims_dir, name);
            let link = is_link(&path);
            DirState {
                name,
                exists: link || pyos::isdir(&path),
                is_link: link,
                target: if link { fs::read_link(&path).ok().map(|t| plain(&t)) } else { None },
                path,
            }
        })
        .collect()
}

/// 지금 권한으로 디렉터리 심볼릭 링크를 만들 수 있는지 (관리자이거나 Windows 개발자 모드).
pub fn can_symlink() -> bool {
    let Ok(dir) = tempfile::Builder::new().prefix("s4ccm_link_").tempdir() else { return false };
    let target = dir.path().join("t");
    let link = dir.path().join("l");
    if fs::create_dir(&target).is_err() {
        return false;
    }
    symlink_dir(&target, &link).is_ok()
}

#[cfg(windows)]
fn symlink_dir(target: &Path, link: &Path) -> io::Result<()> {
    std::os::windows::fs::symlink_dir(target, link)
}

#[cfg(not(windows))]
fn symlink_dir(target: &Path, link: &Path) -> io::Result<()> {
    std::os::unix::fs::symlink(target, link)
}

#[cfg(windows)]
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// 경로가 속한 볼륨의 루트 (`C:\`, `D:\` 등). 없는 경로면 있는 상위 폴더로 판단한다.
#[cfg(windows)]
fn volume_of(p: &str) -> String {
    use windows_sys::Win32::Storage::FileSystem::GetVolumePathNameW;
    let mut cur = pyos::abspath(p);
    while !pyos::exists(&cur) {
        let up = pyos::dirname(&cur);
        if up == cur {
            break;
        }
        cur = up;
    }
    let mut buf = vec![0u16; 1024];
    let ok = unsafe { GetVolumePathNameW(wide(&cur).as_ptr(), buf.as_mut_ptr(), buf.len() as u32) };
    if ok == 0 {
        return pyos::splitdrive(&cur).0.to_uppercase();
    }
    let n = buf.iter().position(|&c| c == 0).unwrap_or(0);
    String::from_utf16_lossy(&buf[..n]).to_uppercase()
}

#[cfg(windows)]
fn free_space(p: &str) -> Option<u64> {
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let root = volume_of(p);
    let mut free: u64 = 0;
    let ok = unsafe { GetDiskFreeSpaceExW(wide(&root).as_ptr(), &mut free, std::ptr::null_mut(), std::ptr::null_mut()) };
    (ok != 0).then_some(free)
}

#[cfg(not(windows))]
fn volume_of(_p: &str) -> String {
    String::new()
}

#[cfg(not(windows))]
fn free_space(_p: &str) -> Option<u64> {
    None
}

/// 폴더 안의 파일 목록 (상대 경로, 크기). 안에 다른 링크가 있으면 오류.
fn files_of(root: &str) -> Result<Vec<(String, u64)>> {
    let mut out = Vec::new();
    for w in pyos::walk(root) {
        for d in &w.dirnames {
            if is_link(&pyos::join(&w.dirpath, d)) {
                return Err(Error::msg(format!(
                    "{} 안에 다른 링크 폴더({d})가 있어 옮길 수 없습니다.",
                    pyos::basename(root)
                )));
            }
        }
        for n in &w.filenames {
            let p = pyos::join(&w.dirpath, n);
            let size = fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
            out.push((pyos::relpath(&p, root), size));
        }
    }
    Ok(out)
}

fn is_empty_dir(p: &str) -> bool {
    fs::read_dir(p).map(|mut rd| rd.next().is_none()).unwrap_or(false)
}

fn same_path(a: &str, b: &str) -> bool {
    pyos::normcase(&pyos::abspath(a)) == pyos::normcase(&pyos::abspath(b))
}

fn inside(child: &str, parent: &str) -> bool {
    let c = pyos::normcase(&pyos::abspath(child));
    let p = pyos::normcase(&pyos::abspath(parent));
    c == p || c.starts_with(&format!("{}\\", p.trim_end_matches('\\')))
}

/// 옮기기 전에 확인한다. 옮길 수 없으면 사람이 읽을 이유를 담은 오류.
pub fn check(sims_dir: &str, dest_root: &str) -> Result<()> {
    if dest_root.trim().is_empty() {
        return Err(Error::msg("옮길 경로를 먼저 선택하세요."));
    }
    if !Path::new(dest_root).is_absolute() {
        return Err(Error::msg("옮길 경로는 드라이브부터 적힌 전체 경로여야 합니다."));
    }
    let states = status(sims_dir);
    let mods = &states[0];
    if mods.is_link {
        return Err(Error::msg(format!(
            "Mods 폴더가 이미 심볼릭 링크입니다 (실제 위치: {}).\n먼저 [원래 위치로 되돌리기]를 하세요.",
            mods.target.clone().unwrap_or_default()
        )));
    }
    if !mods.exists {
        return Err(Error::msg(format!("Mods 폴더가 없습니다: {}", mods.path)));
    }
    if inside(dest_root, sims_dir) {
        return Err(Error::msg("옮길 경로가 심즈 4 폴더 안에 있습니다. 다른 폴더를 고르세요."));
    }
    for s in &states {
        if s.is_link {
            return Err(Error::msg(format!(
                "{} 폴더가 이미 링크입니다. 먼저 [원래 위치로 되돌리기]를 하세요.",
                s.name
            )));
        }
        let dst = pyos::join(dest_root, s.name);
        if is_link(&dst) || (pyos::exists(&dst) && !(pyos::isdir(&dst) && is_empty_dir(&dst))) {
            return Err(Error::msg(format!("옮길 곳에 이미 {} 폴더가 있습니다: {dst}\n빈 폴더를 고르세요.", s.name)));
        }
    }
    Ok(())
}

/// 옮길 양과 대상 드라이브 정보.
pub fn measure(sims_dir: &str, dest_root: &str) -> Result<Measure> {
    check(sims_dir, dest_root)?;
    let mut m = Measure::default();
    for s in status(sims_dir) {
        if s.exists {
            for (_, size) in files_of(&s.path)? {
                m.files += 1;
                m.bytes += size;
            }
        }
    }
    m.same_volume = volume_of(sims_dir) == volume_of(dest_root);
    m.free = free_space(dest_root);
    Ok(m)
}

/// 쓰기 금지 속성을 풀고 지운다.
fn remove_tree(p: &str) -> io::Result<()> {
    for w in pyos::walk(p) {
        for n in &w.filenames {
            let f = pyos::join(&w.dirpath, n);
            if let Ok(meta) = fs::metadata(&f) {
                let mut perm = meta.permissions();
                if perm.readonly() {
                    #[allow(clippy::permissions_set_readonly_false)]
                    perm.set_readonly(false);
                    let _ = fs::set_permissions(&f, perm);
                }
            }
        }
    }
    fs::remove_dir_all(p)
}

/// src 폴더를 dst 로 복사하고 파일마다 크기를 대조한다. (복사한 파일 수)
fn copy_verified(
    src: &str,
    dst: &str,
    files: &[(String, u64)],
    done: &mut usize,
    total: usize,
    steps: &mut Steps,
) -> Result<()> {
    pyos::makedirs(dst)?;
    for w in pyos::walk(src) {
        pyos::makedirs(&pyos::join(dst, &pyos::relpath(&w.dirpath, src)))?;
    }
    for (rel, _) in files {
        report(steps, PHASE_COPY, *done, total, rel);
        let (s, d) = (pyos::join(src, rel), pyos::join(dst, rel));
        pyos::makedirs(&pyos::dirname(&d))?;
        fs::copy(&s, &d).map_err(|e| Error::msg(format!("복사하지 못했습니다: {rel}\n{e}")))?;
        *done += 1;
    }
    for (i, (rel, size)) in files.iter().enumerate() {
        report(steps, PHASE_VERIFY, i, files.len(), rel);
        let got = fs::metadata(pyos::join(dst, rel)).map(|m| m.len()).ok();
        if got != Some(*size) {
            return Err(Error::msg(format!("복사한 파일 크기가 다릅니다: {rel}")));
        }
    }
    Ok(())
}

fn busy_hint(e: impl std::fmt::Display) -> Error {
    Error::msg(format!(
        "폴더를 옮기지 못했습니다. 이 폴더를 연 탐색기 창이나 게임을 닫고 다시 시도하세요.\n({e})"
    ))
}

/// 한 폴더를 옮긴 방법 (실패하면 되돌리는 데 쓴다).
enum Done {
    /// 빈 폴더를 새로 만들어 링크했다 (원래 폴더가 없었음).
    Created { dst: String },
    /// 같은 드라이브라 폴더째 옮겼다.
    Renamed { src: String, dst: String },
    /// 복사한 뒤 원래 폴더 이름을 바꿔 두었다.
    Copied { src: String, old: String, dst: String },
}

fn undo(done: &[Done]) {
    for d in done.iter().rev() {
        match d {
            Done::Created { dst } => {
                let _ = fs::remove_dir(dst);
            }
            Done::Renamed { src, dst } => {
                if is_link(src) {
                    let _ = fs::remove_dir(src);
                }
                let _ = fs::rename(dst, src);
            }
            Done::Copied { src, old, dst } => {
                if is_link(src) {
                    let _ = fs::remove_dir(src);
                }
                if pyos::exists(old) {
                    let _ = fs::rename(old, src);
                }
                let _ = remove_tree(dst);
            }
        }
    }
}

/// Mods(와 비활성·충돌 테스트 폴더)를 dest_root 아래로 옮기고 원래 자리에 링크를 만든다.
pub fn relocate(lib: &Library, dest_root: &str, mut steps: Steps) -> Result<()> {
    let _g = lib.lock_ops();
    let sims = lib.sims_dir();
    check(&sims, dest_root)?;
    pyos::makedirs(dest_root)?;
    let same_volume = volume_of(&sims) == volume_of(dest_root);

    report(&mut steps, PHASE_CHECK, 0, 0, "");
    let states = status(&sims);
    let mut plan: Vec<(DirState, Vec<(String, u64)>)> = Vec::new();
    for s in states {
        let files = if s.exists { files_of(&s.path)? } else { Vec::new() };
        plan.push((s, files));
    }
    if !same_volume {
        let need: u64 = plan.iter().flat_map(|(_, f)| f.iter().map(|x| x.1)).sum();
        if let Some(free) = free_space(dest_root) {
            if free < need + 64 * 1024 * 1024 {
                return Err(Error::msg(format!(
                    "옮길 곳의 빈 공간이 부족합니다. (필요 {:.1} GB, 남은 공간 {:.1} GB)",
                    need as f64 / 1073741824.0,
                    free as f64 / 1073741824.0
                )));
            }
        }
    }
    let total: usize = plan.iter().map(|(_, f)| f.len()).sum();

    let mut done: Vec<Done> = Vec::new();
    let mut copied = 0usize;
    let result = (|| -> Result<()> {
        // 1) 옮기거나 복사한다.
        for (s, files) in &plan {
            let dst = pyos::join(dest_root, s.name);
            if pyos::isdir(&dst) {
                fs::remove_dir(&dst)?; // 빈 폴더임을 check 에서 확인했다
            }
            if !s.exists {
                pyos::makedirs(&dst)?;
                done.push(Done::Created { dst });
            } else if same_volume {
                report(&mut steps, PHASE_MOVE, 0, 0, s.name);
                fs::rename(&s.path, &dst).map_err(busy_hint)?;
                done.push(Done::Renamed { src: s.path.clone(), dst });
            } else {
                copy_verified(&s.path, &dst, files, &mut copied, total, &mut steps)?;
                let old = format!("{}{OLD_SUFFIX}", s.path);
                fs::rename(&s.path, &old).map_err(|e| {
                    let _ = remove_tree(&dst);
                    busy_hint(e)
                })?;
                done.push(Done::Copied { src: s.path.clone(), old, dst });
            }
        }
        // 2) 원래 자리에 링크를 만들고 실제로 열리는지 확인한다.
        for (i, (s, _)) in plan.iter().enumerate() {
            report(&mut steps, PHASE_LINK, i, plan.len(), s.name);
            let dst = pyos::join(dest_root, s.name);
            symlink_dir(Path::new(&dst), Path::new(&s.path)).map_err(|e| {
                if e.raw_os_error() == Some(1314) {
                    Error::msg("심볼릭 링크를 만들 권한이 없습니다. 관리자 권한으로 실행해야 합니다.")
                } else {
                    Error::msg(format!("심볼릭 링크를 만들지 못했습니다: {e}"))
                }
            })?;
            fs::read_dir(&s.path).map_err(|e| Error::msg(format!("만든 링크가 열리지 않습니다: {e}")))?;
        }
        Ok(())
    })();
    if let Err(e) = result {
        undo(&done);
        return Err(e);
    }
    // 3) 링크가 모두 된 뒤에 원래 자리의 사본을 지운다.
    let olds: Vec<String> =
        done.iter().filter_map(|d| if let Done::Copied { old, .. } = d { Some(old.clone()) } else { None }).collect();
    for (i, old) in olds.iter().enumerate() {
        report(&mut steps, PHASE_CLEAN, i, olds.len(), &pyos::basename(old));
        remove_tree(old).map_err(|e| {
            Error::msg(format!("옮기기는 끝났지만 원래 자리의 파일을 다 지우지 못했습니다. 직접 지워 주세요:\n{old}\n({e})"))
        })?;
    }
    Ok(())
}

/// 링크를 지우고 실제 파일을 원래 자리로 되돌린다. 관리자 권한이 필요 없다.
pub fn restore(lib: &Library, mut steps: Steps) -> Result<()> {
    let _g = lib.lock_ops();
    let sims = lib.sims_dir();
    let links: Vec<DirState> = status(&sims).into_iter().filter(|s| s.is_link).collect();
    if links.is_empty() {
        return Err(Error::msg("되돌릴 링크가 없습니다. Mods 폴더가 이미 원래 위치에 있습니다."));
    }
    let mut plan = Vec::new();
    for s in links {
        let target = s.target.clone().unwrap_or_default();
        if !pyos::isdir(&target) {
            return Err(Error::msg(format!("{}의 실제 폴더를 찾지 못했습니다: {target}", s.name)));
        }
        let files = files_of(&target)?;
        plan.push((s, target, files));
    }
    let total: usize = plan.iter().map(|p| p.2.len()).sum();
    let mut copied = 0usize;
    for (s, target, files) in &plan {
        let same_volume = volume_of(&sims) == volume_of(target);
        fs::remove_dir(&s.path).map_err(busy_hint)?;
        // 실패하면 링크를 되살려 옮긴 상태 그대로 둔다.
        let relink = || {
            let _ = symlink_dir(Path::new(target), Path::new(&s.path));
        };
        if same_volume {
            report(&mut steps, PHASE_MOVE, 0, 0, s.name);
            if let Err(e) = fs::rename(target, &s.path) {
                relink();
                return Err(busy_hint(e));
            }
            continue;
        }
        if let Err(e) = copy_verified(target, &s.path, files, &mut copied, total, &mut steps) {
            // 원본(target)은 그대로 있으니 덜 된 사본만 지운다.
            if !same_path(&s.path, target) {
                let _ = remove_tree(&s.path);
            }
            relink();
            return Err(e);
        }
        // 복사가 다 됐으니 이제부터는 원래 자리의 파일이 기준이다. 정리에 실패해도 되돌리지 않는다.
        report(&mut steps, PHASE_CLEAN_MOVED, 0, 0, s.name);
        remove_tree(target).map_err(|e| {
            Error::msg(format!(
                "되돌리기는 끝났지만 옮겨 두었던 폴더를 다 지우지 못했습니다. 직접 지워 주세요:\n{target}\n({e})"
            ))
        })?;
    }
    Ok(())
}
