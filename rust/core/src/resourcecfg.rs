//! `Mods/Resource.cfg` 와 `Options.ini` 읽기·쓰기.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use indexmap::IndexMap;
use regex::Regex;

use crate::error::Result;
use crate::pyos;

pub const MAX_DEPTH: i64 = 5;

#[derive(Debug, Clone, PartialEq)]
pub struct Rule {
    pub priority: i64,
    pub folder: String,
    pub depth: i64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ResourceCfg {
    pub rules: Vec<Rule>,
    pub extra: Vec<(i64, String)>,
}

fn rx(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pat).unwrap())
}

impl ResourceCfg {
    pub fn default_cfg() -> Self {
        ResourceCfg {
            rules: vec![
                Rule { priority: 1000, folder: "00_Overrides".into(), depth: 4 },
                Rule { priority: 500, folder: "".into(), depth: 4 },
            ],
            extra: Vec::new(),
        }
    }

    pub fn parse(text: &str) -> Self {
        static PRIO: OnceLock<Regex> = OnceLock::new();
        static PACKED: OnceLock<Regex> = OnceLock::new();
        static GLOB: OnceLock<Regex> = OnceLock::new();
        let prio = rx(&PRIO, r"(?i)^priority\s+(-?[0-9]+)$");
        let packed = rx(&PACKED, r"(?i)^packedfile\s+(.+)$");
        let glob_rx = rx(&GLOB, r"(?i)^(.*?)((?:\*\\)*)\*\.package$");

        let mut cfg = ResourceCfg::default();
        let mut priority: i64 = 0;
        let mut groups: IndexMap<(i64, String), Vec<i64>> = IndexMap::new();
        for raw in pyos::splitlines(text) {
            let line = raw.trim();
            if line.is_empty() {
                continue;
            }
            if let Some(m) = prio.captures(line) {
                if let Ok(p) = m[1].parse::<i64>() {
                    priority = p;
                    continue;
                }
            }
            if let Some(m) = packed.captures(line) {
                let glob = m[1].trim().replace('/', "\\");
                if let Some(gm) = glob_rx.captures(&glob) {
                    let folder = gm[1].trim_end_matches('\\').to_string();
                    if !folder.contains('*') {
                        let depth = gm[2].matches("*\\").count() as i64;
                        groups.entry((priority, folder)).or_default().push(depth);
                        continue;
                    }
                }
            }
            cfg.extra.push((priority, line.to_string()));
        }
        for ((pr, folder), depths) in groups {
            cfg.rules.push(Rule { priority: pr, folder, depth: depths.into_iter().max().unwrap_or(0) });
        }
        cfg
    }

    pub fn render(&self) -> String {
        let mut out: Vec<String> = Vec::new();
        let prios: BTreeSet<i64> =
            self.rules.iter().map(|r| r.priority).chain(self.extra.iter().map(|e| e.0)).collect();
        for pr in prios.into_iter().rev() {
            out.push(format!("Priority {pr}"));
            for r in self.rules.iter().filter(|r| r.priority == pr) {
                let prefix = r.folder.replace('/', "\\");
                let prefix = prefix.trim_matches('\\');
                let prefix = if prefix.is_empty() { String::new() } else { format!("{prefix}\\") };
                for d in 0..=r.depth.min(MAX_DEPTH) {
                    out.push(format!("PackedFile {prefix}{}*.package", "*\\".repeat(d.max(0) as usize)));
                }
            }
            out.extend(self.extra.iter().filter(|e| e.0 == pr).map(|e| e.1.clone()));
            out.push(String::new());
        }
        out.join("\n").trim_end().to_string() + "\n"
    }
}

pub fn cfg_path(mods_dir: &str) -> String {
    pyos::join(mods_dir, "Resource.cfg")
}

fn read_text(path: &str, strip_bom: bool) -> Result<String> {
    let bytes = std::fs::read(path)?;
    let bytes = if strip_bom && bytes.starts_with(&[0xEF, 0xBB, 0xBF]) { &bytes[3..] } else { &bytes[..] };
    Ok(pyos::universal_newlines(&String::from_utf8_lossy(bytes)))
}

/// (설정, 원문). 파일이 없으면 기본값과 빈 문자열.
pub fn load(mods_dir: &str) -> Result<(ResourceCfg, String)> {
    let p = cfg_path(mods_dir);
    if !pyos::isfile(&p) {
        return Ok((ResourceCfg::default_cfg(), String::new()));
    }
    let text = read_text(&p, true)?;
    Ok((ResourceCfg::parse(&text), text))
}

/// 저장하고, 기존 파일이 있었다면 백업 경로를 돌려준다.
pub fn save(mods_dir: &str, cfg: &ResourceCfg) -> Result<Option<String>> {
    let p = cfg_path(mods_dir);
    let mut backup = None;
    if pyos::isfile(&p) {
        let bdir = pyos::join(&pyos::dirname(mods_dir), "S4CCM_Backups");
        pyos::makedirs(&bdir)?;
        let name = chrono::Local::now().format("Resource_%Y%m%d_%H%M%S.cfg").to_string();
        let b = pyos::join(&bdir, &name);
        std::fs::copy(&p, &b)?;
        backup = Some(b);
    }
    std::fs::write(&p, cfg.render().replace('\n', "\r\n"))?;
    Ok(backup)
}

pub fn read_options(sims_dir: &str) -> Result<IndexMap<String, String>> {
    let p = pyos::join(sims_dir, "Options.ini");
    let mut out = IndexMap::new();
    if !pyos::isfile(&p) {
        return Ok(out);
    }
    for line in read_text(&p, false)?.split('\n') {
        if let Some((k, v)) = line.split_once('=') {
            out.insert(k.trim().to_lowercase(), v.trim().to_string());
        }
    }
    Ok(out)
}

pub fn set_option(sims_dir: &str, key: &str, value: &str) -> Result<bool> {
    let p = pyos::join(sims_dir, "Options.ini");
    if !pyos::isfile(&p) {
        return Ok(false);
    }
    let text = read_text(&p, false)?;
    let mut lines: Vec<String> = text.split_inclusive('\n').map(|s| s.to_string()).collect();
    std::fs::copy(&p, format!("{p}.s4ccm.bak"))?;
    let mut found = false;
    for line in lines.iter_mut() {
        if let Some((k, _)) = line.split_once('=') {
            if k.trim().to_lowercase() == key {
                *line = format!("{key} = {value}\n");
                found = true;
            }
        }
    }
    if !found {
        let idx = lines.iter().position(|l| l.trim().to_lowercase() == "[options]").map(|i| i + 1).unwrap_or(lines.len());
        lines.insert(idx, format!("{key} = {value}\n"));
    }
    std::fs::write(&p, lines.concat().replace('\n', "\r\n"))?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let text = "Priority 1000\nPackedFile 00_Overrides\\*.package\nPackedFile 00_Overrides\\*\\*.package\n\nPriority 500\nPackedFile *.package\nPackedFile */*.package\nDirectoryFiles x\n";
        let cfg = ResourceCfg::parse(text);
        assert_eq!(
            cfg.rules,
            vec![
                Rule { priority: 1000, folder: "00_Overrides".into(), depth: 1 },
                Rule { priority: 500, folder: "".into(), depth: 1 }
            ]
        );
        assert_eq!(cfg.extra, vec![(500, "DirectoryFiles x".to_string())]);
        assert_eq!(
            cfg.render(),
            "Priority 1000\nPackedFile 00_Overrides\\*.package\nPackedFile 00_Overrides\\*\\*.package\n\nPriority 500\nPackedFile *.package\nPackedFile *\\*.package\nDirectoryFiles x\n"
        );
    }
}
