//! 사용자 설정(`%APPDATA%/S4CCM/config.json`).

use indexmap::IndexMap;
use serde_json::{json, Map, Value};

use crate::error::Result;
use crate::pyos;

pub const APP_NAME: &str = "S4CCM";
pub const VERSION: &str = "1.4.0";

pub fn app_dir() -> String {
    let base = match std::env::var("S4CCM_HOME").ok().filter(|s| !s.is_empty()) {
        Some(h) => h,
        None => {
            let appdata = std::env::var("APPDATA").unwrap_or_else(|_| std::env::var("USERPROFILE").unwrap_or_default());
            pyos::join(&appdata, APP_NAME)
        }
    };
    let _ = pyos::makedirs(&base);
    base
}

pub fn thumbs_dir() -> String {
    let d = pyos::join(&app_dir(), "thumbs");
    let _ = pyos::makedirs(&d);
    d
}

#[derive(Debug, Clone, PartialEq)]
pub struct Config {
    pub sims_dir: String,
    pub game_exe: String,
    pub clear_thumbcache_on_launch: bool,
    pub theme: String,
    pub view_mode: String,
    /// 업데이트 창에서 [이 버전 건너뛰기]를 누른 버전 (예: "v1.4.0"). Python 판에는 없는 값.
    pub skip_version: String,
    pub thumb_size: i64,
    pub folder_overrides: IndexMap<String, String>,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            sims_dir: String::new(),
            game_exe: String::new(),
            clear_thumbcache_on_launch: false,
            theme: "system".into(),
            view_mode: "grid".into(),
            skip_version: String::new(),
            thumb_size: 128,
            folder_overrides: IndexMap::new(),
        }
    }
}

impl Config {
    pub fn mods_dir(&self) -> String {
        if self.sims_dir.is_empty() {
            String::new()
        } else {
            pyos::join(&self.sims_dir, "Mods")
        }
    }

    pub fn path() -> String {
        pyos::join(&app_dir(), "config.json")
    }

    pub fn load() -> Config {
        Self::load_from(&Self::path())
    }

    pub fn load_from(path: &str) -> Config {
        let mut c = Config::default();
        let Ok(bytes) = std::fs::read(path) else { return c };
        let Ok(Value::Object(data)) = serde_json::from_slice::<Value>(&bytes) else { return c };
        let s = |k: &str, dst: &mut String| {
            if let Some(v) = data.get(k).and_then(|v| v.as_str()) {
                *dst = v.to_string();
            }
        };
        s("sims_dir", &mut c.sims_dir);
        s("game_exe", &mut c.game_exe);
        s("theme", &mut c.theme);
        s("view_mode", &mut c.view_mode);
        s("skip_version", &mut c.skip_version);
        if let Some(v) = data.get("clear_thumbcache_on_launch").and_then(|v| v.as_bool()) {
            c.clear_thumbcache_on_launch = v;
        }
        if let Some(v) = data.get("thumb_size").and_then(|v| v.as_i64()) {
            c.thumb_size = v;
        }
        if let Some(Value::Object(m)) = data.get("folder_overrides") {
            c.folder_overrides =
                m.iter().filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_string()))).collect();
        }
        c
    }

    pub fn to_json(&self) -> Value {
        let overrides: Map<String, Value> =
            self.folder_overrides.iter().map(|(k, v)| (k.clone(), Value::String(v.clone()))).collect();
        json!({
            "sims_dir": self.sims_dir,
            "game_exe": self.game_exe,
            "clear_thumbcache_on_launch": self.clear_thumbcache_on_launch,
            "theme": self.theme,
            "view_mode": self.view_mode,
            "skip_version": self.skip_version,
            "thumb_size": self.thumb_size,
            "folder_overrides": overrides,
        })
    }

    pub fn save(&self) -> Result<()> {
        self.save_to(&Self::path())
    }

    pub fn save_to(&self, path: &str) -> Result<()> {
        let tmp = format!("{path}.tmp");
        // Python 판이 텍스트 모드로 쓰던 것과 같게 CRLF 로 저장한다.
        std::fs::write(&tmp, serde_json::to_string_pretty(&self.to_json())?.replace('\n', "\r\n"))?;
        std::fs::rename(&tmp, path)?;
        Ok(())
    }
}
