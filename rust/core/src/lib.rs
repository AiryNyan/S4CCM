//! S4CCM 코어: 심즈 4 모드/CC 관리 로직 (Python 판 `s4ccm` 패키지의 Rust 이식).

pub mod bisect;
pub mod casp;
pub mod categories;
pub mod classify;
pub mod config;
pub mod db;
pub mod dbpf;
pub mod duplicates;
pub mod error;
pub mod importer;
pub mod launcher;
pub mod library;
pub mod paths;
pub mod poseicon;
pub mod proto;
pub mod relocate;
pub mod pyos;
pub mod resourcecfg;
pub mod saves;
pub mod scriptmods;
pub mod thumbs;
pub mod tray;
pub mod update;

pub use error::{Error, Result};
