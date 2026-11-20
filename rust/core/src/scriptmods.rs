//! 널리 쓰이는 스크립트 모드 인식과 필요한 라이브러리 확인.

use std::collections::BTreeSet;
use std::sync::OnceLock;

use indexmap::IndexMap;
use regex::Regex;

use crate::db::Item;
use crate::dbpf::Package;
use crate::pyos;

pub const MOD_EXT: [&str; 2] = [".package", ".ts4script"];

#[derive(Debug, PartialEq)]
pub struct KnownMod {
    pub id: &'static str,
    pub name: &'static str,
    pub folder: &'static str,
    pub script_pattern: &'static str,
    pub keep_note: &'static str,
    pub is_library: bool,
    pub tuning_module: Option<&'static str>,
    pub url: &'static str,
}

const fn km(id: &'static str, name: &'static str, folder: &'static str, pat: &'static str) -> KnownMod {
    KnownMod { id, name, folder, script_pattern: pat, keep_note: "", is_library: false, tuning_module: None, url: "" }
}

const fn lib(id: &'static str, name: &'static str, pat: &'static str, module: &'static str, url: &'static str) -> KnownMod {
    KnownMod {
        id,
        name,
        folder: "",
        script_pattern: pat,
        keep_note: "",
        is_library: true,
        tuning_module: Some(module),
        url,
    }
}

pub static KNOWN_MODS: &[KnownMod] = &[
    KnownMod {
        id: "mccc",
        name: "MC Command Center",
        folder: "MCCC",
        script_pattern: r"^mc_cmd_center",
        keep_note: "설정 파일(mc_settings.cfg 등)은 그대로 유지합니다.",
        is_library: false,
        tuning_module: None,
        url: "",
    },
    km("wickedwhims", "WickedWhims", "WickedWhimsMod", r"^turbodriver_wickedwhims"),
    km("wonderfulwhims", "WonderfulWhims", "WonderfulWhimsMod", r"^turbodriver_wonderfulwhims"),
    km("uicheats", "UI Cheats Extension", "UICheatsExtension", r"^ui_cheats_extension"),
    km("basemental_drugs", "Basemental Drugs", "BasementalDrugs", r"^basementaldrugs|^basemental.?drugs"),
    km("basemental_gangs", "Basemental Gangs", "BasementalGangs", r"^basementalgangs|^basemental.?gangs"),
    km("sliceoflife", "Slice of Life", "SliceOfLife", r"slice.?of.?life"),
    km("meaningfulstories", "Meaningful Stories", "MeaningfulStories", r"meaningfulstories"),
    km("betterbuildbuy", "Better BuildBuy", "Tmex-BetterBuildBuy", r"^tmex-betterbuildbuy"),
    km("tool", "T.O.O.L.", "Tmex-TOOL", r"^tmex-tool"),
    km("betterexceptions", "Better Exceptions", "", r"^tmex-betterexceptions"),
    lib("xmlinjector", "XML Injector", r"^xmlinjector", "xml_injector", ""),
    lib("lot51core", "Lot 51 Core Library", r"^lot51_core", "lot51_core", "https://lot51.cc/core"),
    lib("s4cl", "Sims 4 Community Library", r"^sims4communitylib", "sims4communitylib", ""),
];

pub fn by_id(id: &str) -> Option<&'static KnownMod> {
    KNOWN_MODS.iter().find(|m| m.id == id)
}

fn lib_by_module(module: &str) -> Option<&'static KnownMod> {
    KNOWN_MODS.iter().find(|m| m.tuning_module == Some(module))
}

fn script_patterns() -> &'static Vec<Regex> {
    static P: OnceLock<Vec<Regex>> = OnceLock::new();
    P.get_or_init(|| KNOWN_MODS.iter().map(|m| Regex::new(m.script_pattern).unwrap()).collect())
}

fn tuning_rx() -> &'static regex::bytes::Regex {
    static R: OnceLock<regex::bytes::Regex> = OnceLock::new();
    R.get_or_init(|| {
        let mods: Vec<String> =
            KNOWN_MODS.iter().filter_map(|m| m.tuning_module).map(regex::escape).collect();
        regex::bytes::Regex::new(&format!(r#"(?-u)m="({})[."]"#, mods.join("|"))).unwrap()
    })
}

pub fn identify<S: AsRef<str>>(file_names: &[S]) -> Option<&'static KnownMod> {
    let scripts: Vec<String> = file_names
        .iter()
        .map(|f| f.as_ref())
        .filter(|f| f.to_lowercase().ends_with(".ts4script"))
        .map(|f| pyos::basename(f).to_lowercase())
        .collect();
    for (m, rx) in KNOWN_MODS.iter().zip(script_patterns()) {
        if scripts.iter().any(|s| rx.is_match(s)) {
            return Some(m);
        }
    }
    None
}

/// 파일 이름에서 확장자와 끝의 버전 표기를 뗀 소문자 줄기.
pub fn script_stem(name: &str) -> String {
    static R: OnceLock<Regex> = OnceLock::new();
    let rx = R.get_or_init(|| Regex::new(r"(?i)[_\-\s.]*v?\d+([._]\d+)*[a-z]?$").unwrap());
    let stem = pyos::splitext(&pyos::basename(name)).0.to_lowercase();
    rx.replace_all(&stem, "").into_owned()
}

/// 패키지 튜닝이 요구하는 라이브러리 모드 id 집합.
pub fn required_libraries<S: AsRef<str>>(package_paths: &[S]) -> BTreeSet<&'static str> {
    let mut found = BTreeSet::new();
    for p in package_paths {
        let Ok(mut pk) = Package::open(p.as_ref()) else { continue };
        let entries: Vec<_> = pk.entries.iter().take(600).copied().collect();
        for e in entries {
            if e.mem_size > 1_500_000 {
                continue;
            }
            let Ok(data) = pk.read(&e) else { continue };
            for m in tuning_rx().captures_iter(&data) {
                if let Some(lib) = std::str::from_utf8(&m[1]).ok().and_then(lib_by_module) {
                    found.insert(lib.id);
                }
            }
        }
    }
    found
}

/// 설치된 알려진 모드 → 그 항목. 같은 모드가 여럿이면 사용 중인 것을 우선한다.
pub fn installed_mod_ids<'a>(items: &'a [Item], active_only: bool) -> IndexMap<&'static str, &'a Item> {
    let mut out: IndexMap<&'static str, &'a Item> = IndexMap::new();
    for it in items {
        if it.kind != "script" || it.status == "quarantine" || (active_only && it.status != "active") {
            continue;
        }
        if let Some(m) = identify(&it.members) {
            if !out.contains_key(m.id) || it.status == "active" {
                out.insert(m.id, it);
            }
        }
    }
    out
}

pub fn find_installed<'a, S: AsRef<str>>(
    items: &'a [Item],
    known: Option<&'static KnownMod>,
    script_names: &[S],
) -> Option<&'a Item> {
    if let Some(k) = known {
        if let Some(hit) = installed_mod_ids(items, false).get(k.id) {
            return Some(hit);
        }
    }
    let is_script = |n: &str| n.to_lowercase().ends_with(".ts4script");
    let stems: BTreeSet<String> =
        script_names.iter().map(|n| n.as_ref()).filter(|n| is_script(n)).map(script_stem).collect();
    items.iter().find(|it| {
        it.kind == "script"
            && it.status != "quarantine"
            && it.members.iter().filter(|m| is_script(m)).any(|m| stems.contains(&script_stem(m)))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stems() {
        assert_eq!(script_stem("Mods/X/mc_cmd_center_v2024.1.ts4script"), "mc_cmd_center");
        assert_eq!(script_stem("XmlInjector_Script_v4.2.ts4script"), "xmlinjector_script");
        assert_eq!(script_stem("plain.ts4script"), "plain");
        assert_eq!(identify(&["a/XmlInjector_Script_v4.2.ts4script"]).map(|m| m.id), Some("xmlinjector"));
        assert_eq!(identify(&["a/x.package"]).map(|m| m.id), None);
    }
}
