//! GitHub 릴리스에서 새 버전을 확인하고 받아 오는 자동 업데이트. (Python 판에는 없음)
//!
//! - 확인: `GET https://api.github.com/repos/<repo>/releases/latest` (로그인 없이, IP당 시간당 60번)
//! - 받기: 릴리스 파일을 임시 폴더로 받고, GitHub 가 알려 주는 SHA-256 과 맞는지 검사한다.
//! - 설치판: 받은 설치 파일을 조용히 실행한다 (화면 쪽에서).
//! - 포터블판: 실행 중인 파일은 이름을 바꿔 두고(`*.s4ccm_old`), 새 파일로 교체한다. 다음 실행 때 옛 파일을 지운다.

use std::io::{Read, Write};
use std::sync::Arc;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::error::{Error, Result};
use crate::library::Progress;
use crate::pyos;

pub const REPO: &str = "AiryNyan/S4CCM";
const USER_AGENT: &str = "S4CCM-updater";
const OLD_SUFFIX: &str = ".s4ccm_old";

#[derive(Debug, Clone)]
pub struct Asset {
    pub name: String,
    pub url: String,
    pub size: u64,
    /// GitHub 가 계산해 둔 SHA-256 (16진수, 소문자). 없을 수도 있다.
    pub sha256: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Release {
    pub tag: String,
    pub name: String,
    pub body: String,
    pub html_url: String,
    pub assets: Vec<Asset>,
}

/// "v1.3.0", "1.3" → (1, 3, 0)
pub fn parse_version(s: &str) -> Option<(u32, u32, u32)> {
    let s = s.trim().trim_start_matches(['v', 'V']);
    let mut parts = s.split(['.', '-', '+']).map(|p| p.parse::<u32>());
    let major = parts.next()?.ok()?;
    let minor = parts.next().and_then(|p| p.ok()).unwrap_or(0);
    let patch = parts.next().and_then(|p| p.ok()).unwrap_or(0);
    Some((major, minor, patch))
}

pub fn is_newer(latest: &str, current: &str) -> bool {
    match (parse_version(latest), parse_version(current)) {
        (Some(a), Some(b)) => a > b,
        _ => false,
    }
}

fn agent() -> Result<ureq::Agent> {
    let tls = native_tls::TlsConnector::new().map_err(|e| Error::msg(format!("보안 연결을 준비하지 못했습니다: {e}")))?;
    Ok(ureq::AgentBuilder::new()
        .tls_connector(Arc::new(tls))
        .user_agent(USER_AGENT)
        .timeout_connect(std::time::Duration::from_secs(10))
        .timeout_read(std::time::Duration::from_secs(60))
        .build())
}

fn net_err(e: ureq::Error) -> Error {
    match e {
        ureq::Error::Status(code, _) if code == 403 || code == 429 => {
            Error::msg("GitHub 확인 횟수 제한에 걸렸습니다. 잠시 뒤 다시 시도하세요.".to_string())
        }
        ureq::Error::Status(code, _) => Error::msg(format!("GitHub 응답 오류 ({code})")),
        ureq::Error::Transport(t) => Error::msg(format!("인터넷에 연결하지 못했습니다: {t}")),
    }
}

/// 최신 릴리스 정보. 초안·사전 공개 릴리스는 GitHub 가 알아서 빼고 준다.
pub fn latest(repo: &str) -> Result<Release> {
    let url = format!("https://api.github.com/repos/{repo}/releases/latest");
    let resp = agent()?
        .get(&url)
        .set("Accept", "application/vnd.github+json")
        .set("X-GitHub-Api-Version", "2022-11-28")
        .call()
        .map_err(net_err)?;
    let mut text = String::new();
    resp.into_reader().take(4 << 20).read_to_string(&mut text)?;
    let v: Value = serde_json::from_str(&text)?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    let assets = v
        .get("assets")
        .and_then(|a| a.as_array())
        .map(|a| {
            a.iter()
                .map(|x| Asset {
                    name: x.get("name").and_then(|n| n.as_str()).unwrap_or("").to_string(),
                    url: x.get("browser_download_url").and_then(|n| n.as_str()).unwrap_or("").to_string(),
                    size: x.get("size").and_then(|n| n.as_u64()).unwrap_or(0),
                    sha256: x
                        .get("digest")
                        .and_then(|d| d.as_str())
                        .and_then(|d| d.strip_prefix("sha256:"))
                        .map(|d| d.to_lowercase()),
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(Release { tag: s("tag_name"), name: s("name"), body: s("body"), html_url: s("html_url"), assets })
}

/// 설치판인지 포터블인지: 설치 프로그램이 남기는 제거 파일이 있으면 설치판.
pub fn install_kind(app_dir: &str) -> &'static str {
    if pyos::isfile(&pyos::join(app_dir, "unins000.exe")) {
        "installer"
    } else {
        "portable"
    }
}

/// 이 설치 방식에 맞는 릴리스 파일.
pub fn pick_asset<'a>(release: &'a Release, kind: &str) -> Option<&'a Asset> {
    release.assets.iter().find(|a| {
        let n = a.name.to_lowercase();
        if kind == "installer" {
            n.starts_with("s4ccm_setup") && n.ends_with(".exe")
        } else {
            n == "s4ccm.zip"
        }
    })
}

pub fn download_dir() -> String {
    pyos::join(&std::env::temp_dir().to_string_lossy(), "S4CCM_update")
}

/// 파일을 받아 `download_dir()` 에 저장하고 SHA-256 을 검사한다. 진행은 KB 단위로 알린다.
pub fn download(asset: &Asset, mut progress: Progress) -> Result<String> {
    if !asset.url.starts_with("https://github.com/") && !asset.url.starts_with("https://objects.githubusercontent.com/") {
        return Err(Error::msg("GitHub 가 아닌 주소에서는 받지 않습니다."));
    }
    let dir = download_dir();
    pyos::makedirs(&dir)?;
    let name = pyos::basename(&asset.name.replace(['/', '\\'], "_"));
    let dest = pyos::join(&dir, &name);
    let part = format!("{dest}.part");
    let resp = agent()?.get(&asset.url).call().map_err(net_err)?;
    let total = resp.header("Content-Length").and_then(|v| v.parse::<u64>().ok()).unwrap_or(asset.size);
    let mut reader = resp.into_reader();
    let mut file = std::fs::File::create(&part)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    let mut got: u64 = 0;
    let mut last_kb = u64::MAX;
    loop {
        let n = reader.read(&mut buf).map_err(|e| Error::msg(format!("받는 중 연결이 끊겼습니다: {e}")))?;
        if n == 0 {
            break;
        }
        file.write_all(&buf[..n])?;
        hasher.update(&buf[..n]);
        got += n as u64;
        if got / 1024 / 256 != last_kb {
            last_kb = got / 1024 / 256;
            if let Some(p) = progress.as_mut() {
                p((got / 1024) as usize, (total / 1024) as usize, &name);
            }
        }
    }
    file.flush()?;
    drop(file);
    if total > 0 && got != total {
        let _ = std::fs::remove_file(&part);
        return Err(Error::msg("받은 파일 크기가 맞지 않습니다. 다시 시도하세요."));
    }
    let digest: String = hasher.finalize().iter().map(|b| format!("{b:02x}")).collect();
    if let Some(expected) = &asset.sha256 {
        if &digest != expected {
            let _ = std::fs::remove_file(&part);
            return Err(Error::msg("받은 파일의 검사값(SHA-256)이 GitHub 에 올라간 값과 다릅니다. 업데이트를 멈췄습니다."));
        }
    }
    if pyos::exists(&dest) {
        std::fs::remove_file(&dest)?;
    }
    std::fs::rename(&part, &dest)?;
    Ok(dest)
}

/// 포터블판: 받은 zip 의 파일로 프로그램 폴더를 교체한다.
/// 실행 중인 파일(exe, dll)도 이름은 바꿀 수 있으므로, 원래 파일은 `*.s4ccm_old` 로 바꿔 두고 새 파일을 쓴다.
/// 중간에 실패하면 바꿔 둔 파일을 되돌린다.
pub fn apply_portable(zip_path: &str, app_dir: &str, mut progress: Progress) -> Result<usize> {
    let mut z = zip::ZipArchive::new(std::fs::File::open(zip_path)?)?;
    // zip 안은 "S4CCM/..." 아래에 들어 있다.
    let mut files: Vec<(usize, String)> = Vec::new();
    for i in 0..z.len() {
        let e = z.by_index(i)?;
        if e.is_dir() {
            continue;
        }
        let name = e.name().replace('\\', "/");
        let rel = name.strip_prefix("S4CCM/").unwrap_or(&name).to_string();
        if rel.is_empty() || rel.split('/').any(|p| p == ".." || p.is_empty()) || rel.contains(':') {
            return Err(Error::msg(format!("업데이트 파일에 잘못된 경로가 있습니다: {name}")));
        }
        files.push((i, rel));
    }
    if !files.iter().any(|(_, r)| r.eq_ignore_ascii_case("S4CCM.exe")) {
        return Err(Error::msg("업데이트 파일에 S4CCM.exe 가 없습니다."));
    }
    let mut renamed: Vec<(String, String)> = Vec::new();
    let mut written: Vec<String> = Vec::new();
    let total = files.len();
    let result = (|| -> Result<()> {
        for (k, (i, rel)) in files.iter().enumerate() {
            if let Some(p) = progress.as_mut() {
                p(k, total, rel);
            }
            let dst = pyos::join(app_dir, &rel.replace('/', "\\"));
            pyos::makedirs(&pyos::dirname(&dst))?;
            if pyos::exists(&dst) {
                let old = format!("{dst}{OLD_SUFFIX}");
                if pyos::exists(&old) {
                    std::fs::remove_file(&old)?;
                }
                std::fs::rename(&dst, &old)
                    .map_err(|e| Error::msg(format!("{rel} 파일을 바꾸지 못했습니다: {e}")))?;
                renamed.push((old, dst.clone()));
            }
            let mut entry = z.by_index(*i)?;
            let mut out = std::fs::File::create(&dst)?;
            std::io::copy(&mut entry, &mut out)?;
            written.push(dst);
        }
        Ok(())
    })();
    if let Err(e) = result {
        for w in &written {
            let _ = std::fs::remove_file(w);
        }
        for (old, dst) in renamed.iter().rev() {
            let _ = std::fs::rename(old, dst);
        }
        return Err(e);
    }
    if let Some(p) = progress.as_mut() {
        p(total, total, "");
    }
    Ok(total)
}

/// 지난 업데이트에서 남은 옛 파일(`*.s4ccm_old`)과 받아 둔 파일을 지운다. 지우지 못한 것은 다음에 다시 시도한다.
pub fn cleanup(app_dir: &str) {
    for w in pyos::walk(app_dir) {
        for n in &w.filenames {
            if n.ends_with(OLD_SUFFIX) {
                let _ = std::fs::remove_file(pyos::join(&w.dirpath, n));
            }
        }
    }
    let _ = std::fs::remove_dir_all(download_dir());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions() {
        assert_eq!(parse_version("v1.3.0"), Some((1, 3, 0)));
        assert_eq!(parse_version("1.4"), Some((1, 4, 0)));
        assert!(is_newer("v1.10.0", "1.9.9"));
        assert!(is_newer("v1.3.1", "1.3.0"));
        assert!(!is_newer("v1.3.0", "1.3.0"));
        assert!(!is_newer("garbage", "1.3.0"));
    }
}
