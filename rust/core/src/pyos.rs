//! Python `os` / `os.path`(ntpath) 동작을 그대로 흉내 내는 문자열 기반 경로 도우미.
//!
//! 원본 프로그램은 경로를 전부 `str` 로 다루고 DB 에도 `/` 구분 상대경로를 저장한다.
//! 결과가 바이트 단위로 같아지도록 `PathBuf` 대신 같은 규칙의 문자열 연산을 쓴다.

use std::fs;
use std::io;
use std::time::UNIX_EPOCH;

pub const SEP: char = '\\';

fn is_sep(c: char) -> bool {
    c == '\\' || c == '/'
}

/// `ntpath.splitdrive` (드라이브 문자와 UNC 만 처리).
pub fn splitdrive(p: &str) -> (&str, &str) {
    let b = p.as_bytes();
    if b.len() >= 2 {
        let norm: Vec<u8> = b.iter().take(3).map(|&c| if c == b'/' { b'\\' } else { c }).collect();
        if norm.len() >= 2 && norm[0] == b'\\' && norm[1] == b'\\' && (norm.len() < 3 || norm[2] != b'\\') {
            // UNC: \\host\share
            let rest = &p[2..];
            if let Some(i) = rest.find(is_sep) {
                let after = &rest[i + 1..];
                let j = after.find(is_sep).map(|j| 2 + i + 1 + j).unwrap_or(p.len());
                if j != 2 + i + 1 {
                    return (&p[..j], &p[j..]);
                }
            }
            return ("", p);
        }
        if b[1] == b':' {
            return (&p[..2], &p[2..]);
        }
    }
    ("", p)
}

/// `ntpath.split`
pub fn split(p: &str) -> (String, String) {
    let (d, rest) = splitdrive(p);
    let i = rest.rfind(is_sep).map(|i| i + 1).unwrap_or(0);
    let (head, tail) = (&rest[..i], &rest[i..]);
    let stripped = head.trim_end_matches(is_sep);
    let head = if stripped.is_empty() { head } else { stripped };
    (format!("{d}{head}"), tail.to_string())
}

pub fn basename(p: &str) -> String {
    split(p).1
}

pub fn dirname(p: &str) -> String {
    split(p).0
}

/// `ntpath.splitext` — 이름 앞쪽의 점은 확장자로 치지 않는다.
pub fn splitext(p: &str) -> (String, String) {
    let sep_idx = p.rfind(is_sep).map(|i| i as isize).unwrap_or(-1);
    if let Some(dot) = p.rfind('.') {
        if dot as isize > sep_idx {
            let name_start = (sep_idx + 1) as usize;
            let b = p.as_bytes();
            let mut k = name_start;
            while k < dot {
                if b[k] != b'.' {
                    return (p[..dot].to_string(), p[dot..].to_string());
                }
                k += 1;
            }
        }
    }
    (p.to_string(), String::new())
}

pub fn isabs_or_drive(p: &str) -> bool {
    let (d, rest) = splitdrive(p);
    !d.is_empty() || rest.starts_with(is_sep)
}

/// `ntpath.join(a, b)`
pub fn join(a: &str, b: &str) -> String {
    let (ad, ap) = splitdrive(a);
    let (bd, bp) = splitdrive(b);
    if !bp.is_empty() && bp.starts_with(is_sep) {
        // b 가 루트 경로
        if !bd.is_empty() || ad.is_empty() {
            return format!("{bd}{bp}");
        }
        return format!("{ad}{bp}");
    }
    if !bd.is_empty() && bd.to_lowercase() != ad.to_lowercase() {
        return format!("{bd}{bp}");
    }
    let drive = if bd.is_empty() { ad } else { bd };
    let mut path = ap.to_string();
    if !path.is_empty() && !path.ends_with(is_sep) {
        path.push(SEP);
    }
    path.push_str(bp);
    if !path.is_empty() && !path.starts_with(is_sep) && !drive.is_empty() && !drive.ends_with(':') {
        return format!("{drive}{SEP}{path}");
    }
    format!("{drive}{path}")
}

pub fn join3(a: &str, b: &str, c: &str) -> String {
    join(&join(a, b), c)
}

/// `ntpath.normpath`
pub fn normpath(p: &str) -> String {
    let p = p.replace('/', "\\");
    let (drive, rest) = {
        let (d, r) = splitdrive(&p);
        (d.to_string(), r.to_string())
    };
    let rooted = rest.starts_with('\\');
    let mut comps: Vec<&str> = Vec::new();
    for c in rest.split('\\') {
        if c.is_empty() || c == "." {
            continue;
        }
        if c == ".." {
            if let Some(last) = comps.last() {
                if *last != ".." {
                    comps.pop();
                    continue;
                }
            }
            if rooted {
                continue;
            }
            comps.push(c);
        } else {
            comps.push(c);
        }
    }
    let mut out = drive;
    if rooted {
        out.push('\\');
    }
    out.push_str(&comps.join("\\"));
    if out.is_empty() {
        out.push('.');
    }
    out
}

/// `ntpath.normcase`
pub fn normcase(p: &str) -> String {
    p.replace('/', "\\").to_lowercase()
}

pub fn abspath(p: &str) -> String {
    if isabs_or_drive(p) && !splitdrive(p).0.is_empty() && splitdrive(p).1.starts_with(is_sep) {
        return normpath(p);
    }
    let cwd = std::env::current_dir().map(|c| c.to_string_lossy().into_owned()).unwrap_or_default();
    normpath(&join(&cwd, p))
}

/// `ntpath.relpath(path, start)`
pub fn relpath(path: &str, start: &str) -> String {
    let pa = abspath(path);
    let sa = abspath(start);
    let (pd, pr) = splitdrive(&pa);
    let (sd, sr) = splitdrive(&sa);
    if normcase(pd) != normcase(sd) {
        return pa.clone();
    }
    let pl: Vec<&str> = pr.split('\\').filter(|x| !x.is_empty()).collect();
    let sl: Vec<&str> = sr.split('\\').filter(|x| !x.is_empty()).collect();
    let mut i = 0;
    while i < pl.len() && i < sl.len() && normcase(pl[i]) == normcase(sl[i]) {
        i += 1;
    }
    let mut parts: Vec<&str> = vec![".."; sl.len() - i];
    parts.extend_from_slice(&pl[i..]);
    if parts.is_empty() {
        ".".to_string()
    } else {
        parts.join("\\")
    }
}

pub fn exists(p: &str) -> bool {
    fs::metadata(p).is_ok()
}

pub fn isdir(p: &str) -> bool {
    fs::metadata(p).map(|m| m.is_dir()).unwrap_or(false)
}

pub fn isfile(p: &str) -> bool {
    fs::metadata(p).map(|m| m.is_file()).unwrap_or(false)
}

pub fn getsize(p: &str) -> io::Result<u64> {
    Ok(fs::metadata(p)?.len())
}

/// `st_mtime` 과 같은 방식(sec + 1e-9 * nsec)으로 만든 float 초.
pub fn mtime_of(meta: &fs::Metadata) -> io::Result<f64> {
    let t = meta.modified()?;
    Ok(match t.duration_since(UNIX_EPOCH) {
        Ok(d) => d.as_secs() as f64 + 1e-9 * d.subsec_nanos() as f64,
        Err(e) => {
            let d = e.duration();
            -(d.as_secs() as f64 + 1e-9 * d.subsec_nanos() as f64)
        }
    })
}

pub fn getmtime(p: &str) -> io::Result<f64> {
    mtime_of(&fs::metadata(p)?)
}

/// `os.listdir` — 이름만, 파일시스템이 주는 순서 그대로.
pub fn listdir(p: &str) -> io::Result<Vec<String>> {
    let mut out = Vec::new();
    for e in fs::read_dir(p)? {
        out.push(e?.file_name().to_string_lossy().into_owned());
    }
    Ok(out)
}

pub struct WalkEntry {
    pub dirpath: String,
    pub dirnames: Vec<String>,
    pub filenames: Vec<String>,
}

/// `os.walk(top)` (top-down, 심볼릭 링크 폴더는 따라가지 않음, 오류는 무시).
pub fn walk(top: &str) -> Vec<WalkEntry> {
    let mut out = Vec::new();
    walk_into(top, &mut out);
    out
}

fn walk_into(dir: &str, out: &mut Vec<WalkEntry>) {
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(_) => return,
    };
    let mut dirnames = Vec::new();
    let mut filenames = Vec::new();
    let mut recurse = Vec::new();
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let ft = e.file_type().ok();
        let is_link = ft.map(|t| t.is_symlink()).unwrap_or(false);
        let is_dir = if is_link {
            fs::metadata(e.path()).map(|m| m.is_dir()).unwrap_or(false)
        } else {
            ft.map(|t| t.is_dir()).unwrap_or(false)
        };
        if is_dir {
            if !is_link {
                recurse.push(name.clone());
            }
            dirnames.push(name);
        } else {
            filenames.push(name);
        }
    }
    out.push(WalkEntry { dirpath: dir.to_string(), dirnames, filenames });
    for d in recurse {
        walk_into(&join(dir, &d), out);
    }
}

/// `os.makedirs(p, exist_ok=True)`
pub fn makedirs(p: &str) -> io::Result<()> {
    if p.is_empty() {
        return Ok(());
    }
    fs::create_dir_all(p)
}

/// `shutil.move(src, dst)` — dst 는 아직 없는 경로라고 가정한다.
pub fn move_path(src: &str, dst: &str) -> io::Result<()> {
    if fs::rename(src, dst).is_ok() {
        return Ok(());
    }
    if isdir(src) {
        copy_tree(src, dst)?;
        fs::remove_dir_all(src)
    } else {
        fs::copy(src, dst)?;
        fs::remove_file(src)
    }
}

fn copy_tree(src: &str, dst: &str) -> io::Result<()> {
    fs::create_dir_all(dst)?;
    for name in listdir(src)? {
        let (s, d) = (join(src, &name), join(dst, &name));
        if isdir(&s) {
            copy_tree(&s, &d)?;
        } else {
            fs::copy(&s, &d)?;
        }
    }
    Ok(())
}

pub fn now() -> f64 {
    let d = std::time::SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    d.as_secs() as f64 + d.subsec_nanos() as f64 / 1e9
}

/// `str.splitlines()`
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = text.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        let brk = matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}');
        if brk {
            out.push(&text[start..i]);
            let mut next = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(j, '\n')) = it.peek() {
                    it.next();
                    next = j + 1;
                }
            }
            start = next;
        }
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// 텍스트 모드로 읽을 때의 줄바꿈 변환(\r\n, \r → \n).
pub fn universal_newlines(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

pub fn ends_with_any(low: &str, exts: &[&str]) -> bool {
    exts.iter().any(|e| low.ends_with(e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths() {
        assert_eq!(splitext("a/b/c.package"), ("a/b/c".into(), ".package".into()));
        assert_eq!(splitext(".bashrc"), (".bashrc".into(), "".into()));
        assert_eq!(splitext("a.b/c"), ("a.b/c".into(), "".into()));
        assert_eq!(basename("C:\\x\\y.txt"), "y.txt");
        assert_eq!(dirname("C:\\x\\y.txt"), "C:\\x");
        assert_eq!(dirname("C:\\x"), "C:\\");
        assert_eq!(dirname("a/b/c"), "a/b");
        assert_eq!(dirname("c"), "");
        assert_eq!(join("C:\\a", "b/c"), "C:\\a\\b/c");
        assert_eq!(join("C:\\a\\", "b"), "C:\\a\\b");
        assert_eq!(join("C:\\a", "D:\\b"), "D:\\b");
        assert_eq!(join("", "b"), "b");
        assert_eq!(normpath("C:/a/./b/../c/"), "C:\\a\\c");
        assert_eq!(normpath(""), ".");
        assert_eq!(relpath("C:\\a\\b\\c", "C:\\a"), "b\\c");
        assert_eq!(relpath("C:\\a", "C:\\a"), ".");
        assert_eq!(relpath("C:\\a\\x", "C:\\a\\b"), "..\\x");
        assert_eq!(splitlines("a\r\nb\n\nc"), vec!["a", "b", "", "c"]);
        assert_eq!(splitlines("a\n"), vec!["a"]);
    }
}
