//! 충돌 테스트(절반씩 빼 보며 문제 모드 찾기).

use serde_json::{json, Value};

use crate::db::Item;
use crate::error::Result;
use crate::library::{reborrow, Library, Progress};

const STATE_KEY: &str = "bisect_state";

#[derive(Debug, Clone, Default, PartialEq)]
pub struct BisectState {
    pub suspects: Vec<i64>,
    pub cleared: Vec<i64>,
    pub out: Vec<i64>,
    pub step: i64,
    pub found: Option<i64>,
    pub total: i64,
}

impl BisectState {
    pub fn active(&self) -> bool {
        !self.suspects.is_empty() || self.found.is_some()
    }

    pub fn testing(&self) -> bool {
        self.active() && self.found.is_none()
    }

    pub fn suspects_in(&self) -> Vec<i64> {
        self.suspects.iter().copied().filter(|i| !self.out.contains(i)).collect()
    }

    pub fn suspects_out(&self) -> Vec<i64> {
        self.suspects.iter().copied().filter(|i| self.out.contains(i)).collect()
    }

    pub fn remaining_steps(&self) -> i64 {
        let n = self.suspects.len();
        if n <= 1 {
            0
        } else {
            (n as f64).log2().ceil() as i64
        }
    }

    fn from_json(v: &Value) -> Self {
        let ids = |k: &str| -> Vec<i64> {
            v.get(k).and_then(|x| x.as_array()).map(|a| a.iter().filter_map(|x| x.as_i64()).collect()).unwrap_or_default()
        };
        BisectState {
            suspects: ids("suspects"),
            cleared: ids("cleared"),
            out: ids("out"),
            step: v.get("step").and_then(|x| x.as_i64()).unwrap_or(0),
            found: v.get("found").and_then(|x| x.as_i64()),
            total: v.get("total").and_then(|x| x.as_i64()).unwrap_or(0),
        }
    }

    fn to_json(&self) -> Value {
        json!({
            "suspects": self.suspects,
            "cleared": self.cleared,
            "out": self.out,
            "step": self.step,
            "found": self.found,
            "total": self.total,
        })
    }
}

pub struct Bisect<'a> {
    lib: &'a Library,
    pub state: BisectState,
}

fn shuffled(ids: &[i64]) -> Vec<i64> {
    let mut v = ids.to_vec();
    fastrand::shuffle(&mut v);
    v
}

impl<'a> Bisect<'a> {
    pub fn new(lib: &'a Library) -> Result<Self> {
        let state = match lib.db.kv_get(STATE_KEY)? {
            Some(raw) if crate::library::truthy(&raw) => BisectState::from_json(&raw),
            _ => BisectState::default(),
        };
        Ok(Bisect { lib, state })
    }

    fn save(&self) -> Result<()> {
        self.lib.db.kv_set(STATE_KEY, &self.state.to_json())
    }

    fn items(&self, ids: &[i64]) -> Result<Vec<Item>> {
        let mut out = Vec::new();
        for &i in ids {
            if let Some(it) = self.lib.db.get(i)? {
                out.push(it);
            }
        }
        Ok(out)
    }

    fn sync_out(&mut self) -> Result<()> {
        let s = &self.state;
        let out: Vec<i64> = self
            .lib
            .items()?
            .into_iter()
            .filter(|it| it.status == "quarantine" && (s.suspects.contains(&it.id) || s.cleared.contains(&it.id)))
            .map(|it| it.id)
            .collect();
        self.state.out = out;
        Ok(())
    }

    /// 옮기기가 실패하더라도 실제 상태를 다시 읽어 저장한 뒤 오류를 돌려준다.
    fn move_to(&mut self, ids: &[i64], status: &str, what: &str, progress: Progress) -> Result<()> {
        let label = format!("충돌 테스트 {}단계: {what}", self.state.step + 1);
        let moved = self.items(ids).and_then(|mut items| self.lib.set_status(&mut items, status, &label, progress));
        let synced = self.sync_out();
        let saved = self.save();
        moved?;
        synced?;
        saved
    }

    fn move_out(&mut self, ids: &[i64], progress: Progress) -> Result<()> {
        self.move_to(ids, "quarantine", "절반 빼기", progress)
    }

    fn move_in(&mut self, ids: &[i64], progress: Progress) -> Result<()> {
        self.move_to(ids, "active", "절반 다시 넣기", progress)
    }

    /// 테스트를 시작하고, 빼 둔 항목 수를 돌려준다.
    pub fn start(&mut self, item_ids: &[i64], mut progress: Progress) -> Result<usize> {
        self.end(reborrow(&mut progress))?;
        let ids = shuffled(item_ids);
        self.state = BisectState { suspects: ids.clone(), total: ids.len() as i64, ..Default::default() };
        let half = &ids[..ids.len() / 2];
        self.move_out(half, progress)?;
        Ok(half.len())
    }

    /// 게임이 정상이었는지 알려 주면 다음 절반을 옮긴다. 옮긴 항목 수를 돌려준다.
    pub fn report(&mut self, worked: bool, progress: Progress) -> Result<usize> {
        if !self.state.testing() {
            return Ok(0);
        }
        let (inside, outside) = (self.state.suspects_in(), self.state.suspects_out());
        let newly_cleared;
        if worked {
            self.state.suspects = outside;
            newly_cleared = inside;
        } else {
            self.state.suspects = inside;
            newly_cleared = outside;
        }
        self.state.cleared.extend(newly_cleared);
        self.state.step += 1;
        if self.state.suspects.len() <= 1 {
            self.state.found = self.state.suspects.first().copied();
            if let Some(found) = self.state.found {
                if self.state.out.contains(&found) {
                    self.move_in(&[found], progress)?;
                }
            }
            self.save()?;
            return Ok(0);
        }
        let ids = shuffled(&self.state.suspects);
        let half = &ids[..ids.len() / 2];
        if worked {
            self.move_in(half, progress)?;
        } else {
            self.move_out(half, progress)?;
        }
        self.save()?;
        Ok(half.len())
    }

    /// 테스트를 끝내고 빼 둔 것을 모두 되돌린다.
    pub fn end(&mut self, mut progress: Progress) -> Result<()> {
        const LABEL: &str = "충돌 테스트 종료: 원위치";
        if !self.state.out.is_empty() {
            let mut items = self.items(&self.state.out)?;
            self.lib.set_status(&mut items, "active", LABEL, reborrow(&mut progress))?;
        }
        let mut stray: Vec<Item> = self.lib.items()?.into_iter().filter(|it| it.status == "quarantine").collect();
        if !stray.is_empty() {
            self.lib.set_status(&mut stray, "active", LABEL, progress)?;
        }
        self.state = BisectState::default();
        self.save()
    }
}
