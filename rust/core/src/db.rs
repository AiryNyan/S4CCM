//! SQLite 저장소. 스키마와 저장 형식은 Python 판과 같아서 기존 `s4ccm.db` 를 그대로 쓴다.

use std::collections::{HashMap, HashSet};
use std::io;

use indexmap::IndexMap;
use parking_lot::Mutex;
use rusqlite::types::Value as Sql;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde_json::{Map, Value};

use crate::error::Result;
use crate::pyos;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    rel_path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    auto_category TEXT,
    user_set INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    members TEXT,
    thumb TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS items_fp ON items(fingerprint);
CREATE TABLE IF NOT EXISTS notes (
    fingerprint TEXT PRIMARY KEY,
    memo TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    user_thumb TEXT
);
CREATE TABLE IF NOT EXISTS ops (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    label TEXT NOT NULL,
    moves TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS res_ids (fp TEXT NOT NULL, inst INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS res_ids_fp ON res_ids(fp);
CREATE TABLE IF NOT EXISTS remembered (
    fingerprint TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    user_set INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL
);
";

/// `json.dumps(v, ensure_ascii=False)` 와 같은 모양(구분자 `, ` / `: `)으로 직렬화한다.
pub fn py_json(v: &Value) -> String {
    struct PyFmt;
    impl serde_json::ser::Formatter for PyFmt {
        fn begin_array_value<W: ?Sized + io::Write>(&mut self, w: &mut W, first: bool) -> io::Result<()> {
            if first {
                Ok(())
            } else {
                w.write_all(b", ")
            }
        }
        fn begin_object_key<W: ?Sized + io::Write>(&mut self, w: &mut W, first: bool) -> io::Result<()> {
            if first {
                Ok(())
            } else {
                w.write_all(b", ")
            }
        }
        fn begin_object_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
            w.write_all(b": ")
        }
    }
    let mut buf = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, PyFmt);
    serde::Serialize::serialize(v, &mut ser).expect("json");
    String::from_utf8(buf).expect("utf8")
}

#[derive(Debug, Clone, PartialEq)]
pub struct Item {
    pub id: i64,
    pub fingerprint: String,
    pub rel_path: String,
    pub kind: String,
    pub name: String,
    pub category: String,
    pub auto_category: Option<String>,
    pub user_set: bool,
    pub confidence: f64,
    pub reason: String,
    pub size: i64,
    pub mtime: f64,
    pub added_at: f64,
    pub status: String,
    pub members: Vec<String>,
    pub thumb: Option<String>,
    pub meta: Map<String, Value>,
    pub memo: String,
    pub tags: String,
    pub source_url: String,
    pub user_thumb: Option<String>,
}

impl Item {
    pub fn display_thumb(&self) -> Option<&str> {
        self.user_thumb.as_deref().filter(|s| !s.is_empty()).or(self.thumb.as_deref())
    }
}

/// `items` 테이블의 원본 행 (스캔이 변경 여부를 판단할 때 쓴다).
#[derive(Debug, Clone)]
pub struct ItemRow {
    pub id: i64,
    pub fingerprint: String,
    pub name: String,
    pub category: String,
    pub user_set: bool,
    pub size: i64,
    pub mtime: f64,
    pub status: String,
    pub meta: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Remembered {
    pub category: String,
    pub user_set: bool,
    pub added_at: f64,
}

/// 동적으로 고른 열 묶음 (Python 의 `**fields`).
#[derive(Default)]
pub struct Fields(pub Vec<(&'static str, Sql)>);

impl Fields {
    pub fn new() -> Self {
        Fields(Vec::new())
    }

    pub fn set(mut self, k: &'static str, v: impl Into<Sql>) -> Self {
        self.put(k, v);
        self
    }

    pub fn put(&mut self, k: &'static str, v: impl Into<Sql>) {
        let v = v.into();
        if let Some(e) = self.0.iter_mut().find(|e| e.0 == k) {
            e.1 = v;
        } else {
            self.0.push((k, v));
        }
    }

    pub fn opt(self, k: &'static str, v: Option<String>) -> Self {
        self.set(k, v.map(Sql::Text).unwrap_or(Sql::Null))
    }

    pub fn list(self, k: &'static str, v: &[String]) -> Self {
        let arr = Value::Array(v.iter().cloned().map(Value::String).collect());
        self.set(k, py_json(&arr))
    }

    pub fn json(self, k: &'static str, v: &Value) -> Self {
        self.set(k, py_json(v))
    }

    fn has(&self, k: &str) -> bool {
        self.0.iter().any(|e| e.0 == k)
    }
}

pub struct Db {
    pub path: String,
    conn: Mutex<Connection>,
}

const SELECT: &str = "SELECT i.*, n.memo, n.tags, n.source_url, n.user_thumb FROM items i \
                      LEFT JOIN notes n ON n.fingerprint = i.fingerprint";

fn row_to_item(r: &Row) -> rusqlite::Result<Item> {
    let members: Option<String> = r.get("members")?;
    let meta: Option<String> = r.get("meta")?;
    let text = |k: &str| -> rusqlite::Result<String> { Ok(r.get::<_, Option<String>>(k)?.unwrap_or_default()) };
    Ok(Item {
        id: r.get("id")?,
        fingerprint: r.get("fingerprint")?,
        rel_path: r.get("rel_path")?,
        kind: r.get("kind")?,
        name: r.get("name")?,
        category: r.get("category")?,
        auto_category: r.get("auto_category")?,
        user_set: r.get::<_, i64>("user_set")? != 0,
        confidence: r.get("confidence")?,
        reason: text("reason")?,
        size: r.get("size")?,
        mtime: r.get("mtime")?,
        added_at: r.get("added_at")?,
        status: r.get("status")?,
        members: members
            .filter(|s| !s.is_empty())
            .and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
            .unwrap_or_default(),
        thumb: r.get("thumb")?,
        meta: meta
            .filter(|s| !s.is_empty())
            .and_then(|s| serde_json::from_str::<Map<String, Value>>(&s).ok())
            .unwrap_or_default(),
        memo: text("memo")?,
        tags: text("tags")?,
        source_url: text("source_url")?,
        user_thumb: r.get("user_thumb")?,
    })
}

/// Python sqlite3 처럼 쓰기 전에 트랜잭션을 열어 두고 `commit()` 때 한꺼번에 반영한다.
fn begin(conn: &Connection) -> rusqlite::Result<()> {
    if conn.is_autocommit() {
        conn.execute_batch("BEGIN")?;
    }
    Ok(())
}

fn commit(conn: &Connection) -> rusqlite::Result<()> {
    if !conn.is_autocommit() {
        conn.execute_batch("COMMIT")?;
    }
    Ok(())
}

impl Db {
    pub fn open(path: &str) -> Result<Db> {
        pyos::makedirs(&pyos::dirname(path))?;
        let conn = Connection::open(path)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Db { path: path.to_string(), conn: Mutex::new(conn) })
    }

    pub fn all_items(&self) -> Result<Vec<Item>> {
        let conn = self.conn.lock();
        let mut st = conn.prepare_cached(&format!("{SELECT} ORDER BY i.name COLLATE NOCASE"))?;
        let rows = st.query_map([], row_to_item)?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    pub fn get(&self, item_id: i64) -> Result<Option<Item>> {
        let conn = self.conn.lock();
        let mut st = conn.prepare_cached(&format!("{SELECT} WHERE i.id=?"))?;
        Ok(st.query_row([item_id], row_to_item).optional()?)
    }

    /// rel_path → 행 (DB 가 주는 순서 유지).
    pub fn by_path(&self) -> Result<IndexMap<String, ItemRow>> {
        let conn = self.conn.lock();
        let mut st = conn.prepare("SELECT * FROM items")?;
        let mut out = IndexMap::new();
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            out.insert(
                r.get::<_, String>("rel_path")?,
                ItemRow {
                    id: r.get("id")?,
                    fingerprint: r.get("fingerprint")?,
                    name: r.get("name")?,
                    category: r.get("category")?,
                    user_set: r.get::<_, i64>("user_set")? != 0,
                    size: r.get("size")?,
                    mtime: r.get("mtime")?,
                    status: r.get("status")?,
                    meta: r.get("meta")?,
                },
            );
        }
        Ok(out)
    }

    pub fn by_fingerprint(&self, fp: &str) -> Result<Vec<Item>> {
        let conn = self.conn.lock();
        let mut st = conn.prepare_cached(&format!("{SELECT} WHERE i.fingerprint=?"))?;
        let rows = st.query_map([fp], row_to_item)?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(rows)
    }

    /// rel_path 기준 삽입 또는 갱신. `added_at` 이 없으면 지금 시각.
    pub fn upsert(&self, mut f: Fields) -> Result<()> {
        if !f.has("added_at") {
            f.put("added_at", pyos::now());
        }
        let cols: Vec<&str> = f.0.iter().map(|e| e.0).collect();
        let qs = vec!["?"; cols.len()].join(", ");
        let upd: Vec<String> = cols
            .iter()
            .filter(|k| **k != "rel_path" && **k != "added_at")
            .map(|k| format!("{k}=excluded.{k}"))
            .collect();
        let sql = format!(
            "INSERT INTO items ({}) VALUES ({qs}) ON CONFLICT(rel_path) DO UPDATE SET {}",
            cols.join(", "),
            upd.join(", ")
        );
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.prepare_cached(&sql)?.execute(params_from_iter(f.0.iter().map(|e| &e.1)))?;
        Ok(())
    }

    pub fn update_item(&self, item_id: i64, f: Fields) -> Result<()> {
        let sets: Vec<String> = f.0.iter().map(|e| format!("{}=?", e.0)).collect();
        let mut vals: Vec<Sql> = f.0.into_iter().map(|e| e.1).collect();
        vals.push(Sql::Integer(item_id));
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.execute(&format!("UPDATE items SET {} WHERE id=?", sets.join(", ")), params_from_iter(vals.iter()))?;
        commit(&conn)?;
        Ok(())
    }

    pub fn rename_path(&self, old_rel: &str, new_rel: &str, extra: Fields) -> Result<()> {
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.execute("DELETE FROM items WHERE rel_path=? AND rel_path<>?", params![new_rel, old_rel])?;
        let mut sets = vec!["rel_path=?".to_string()];
        sets.extend(extra.0.iter().map(|e| format!("{}=?", e.0)));
        let mut vals: Vec<Sql> = vec![Sql::Text(new_rel.to_string())];
        vals.extend(extra.0.into_iter().map(|e| e.1));
        vals.push(Sql::Text(old_rel.to_string()));
        conn.execute(
            &format!("UPDATE items SET {} WHERE rel_path=?", sets.join(", ")),
            params_from_iter(vals.iter()),
        )?;
        commit(&conn)?;
        Ok(())
    }

    pub fn delete_paths<S: AsRef<str>>(&self, rels: &[S]) -> Result<()> {
        let conn = self.conn.lock();
        if !rels.is_empty() {
            begin(&conn)?;
            let mut st = conn.prepare_cached("DELETE FROM items WHERE rel_path=?")?;
            for r in rels {
                st.execute([r.as_ref()])?;
            }
        }
        commit(&conn)?;
        Ok(())
    }

    /// 사라진 패키지의 분류를 기억해 둔다 (다시 넣으면 복원).
    pub fn remember_paths<S: AsRef<str>>(&self, rels: &[S]) -> Result<()> {
        if rels.is_empty() {
            return Ok(());
        }
        let conn = self.conn.lock();
        begin(&conn)?;
        let mut st = conn.prepare_cached(
            "INSERT OR REPLACE INTO remembered (fingerprint, category, user_set, added_at) \
             SELECT fingerprint, category, user_set, added_at FROM items \
             WHERE rel_path=? AND kind='package'",
        )?;
        for r in rels {
            st.execute([r.as_ref()])?;
        }
        Ok(())
    }

    pub fn recall(&self, fingerprint: &str) -> Result<Option<Remembered>> {
        let conn = self.conn.lock();
        let mut st = conn.prepare_cached("SELECT category, user_set, added_at FROM remembered WHERE fingerprint=?")?;
        Ok(st
            .query_row([fingerprint], |r| {
                Ok(Remembered { category: r.get(0)?, user_set: r.get::<_, i64>(1)? != 0, added_at: r.get(2)? })
            })
            .optional()?)
    }

    pub fn commit(&self) -> Result<()> {
        commit(&self.conn.lock())?;
        Ok(())
    }

    pub fn set_note(&self, fingerprint: &str, f: Fields) -> Result<()> {
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.execute("INSERT OR IGNORE INTO notes (fingerprint) VALUES (?)", [fingerprint])?;
        let sets: Vec<String> = f.0.iter().map(|e| format!("{}=?", e.0)).collect();
        let mut vals: Vec<Sql> = f.0.into_iter().map(|e| e.1).collect();
        vals.push(Sql::Text(fingerprint.to_string()));
        conn.execute(
            &format!("UPDATE notes SET {} WHERE fingerprint=?", sets.join(", ")),
            params_from_iter(vals.iter()),
        )?;
        commit(&conn)?;
        Ok(())
    }

    pub fn log_op(&self, label: &str, moves: &[(String, String)]) -> Result<i64> {
        let arr = Value::Array(
            moves
                .iter()
                .map(|(a, b)| Value::Array(vec![Value::String(a.clone()), Value::String(b.clone())]))
                .collect(),
        );
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.execute("INSERT INTO ops (at, label, moves) VALUES (?,?,?)", params![pyos::now(), label, py_json(&arr)])?;
        let id = conn.last_insert_rowid();
        commit(&conn)?;
        Ok(id)
    }

    /// 아직 되돌리지 않은 작업 (최근 것부터): (id, label, [(src, dst)]).
    pub fn open_ops(&self, label_prefix: &str) -> Result<Vec<(i64, String, Vec<(String, String)>)>> {
        let conn = self.conn.lock();
        let mut st =
            conn.prepare("SELECT id, label, moves FROM ops WHERE undone=0 AND substr(label, 1, ?)=? ORDER BY id DESC")?;
        let n = label_prefix.chars().count() as i64;
        let mut out = Vec::new();
        let mut rows = st.query(params![n, label_prefix])?;
        while let Some(r) = rows.next()? {
            let moves: Vec<(String, String)> = serde_json::from_str(&r.get::<_, String>(2)?)?;
            out.push((r.get(0)?, r.get(1)?, moves));
        }
        Ok(out)
    }

    pub fn mark_undone(&self, op_id: i64) -> Result<()> {
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.execute("UPDATE ops SET undone=1 WHERE id=?", [op_id])?;
        commit(&conn)?;
        Ok(())
    }

    pub fn kv_get(&self, k: &str) -> Result<Option<Value>> {
        let conn = self.conn.lock();
        let v: Option<Option<String>> = conn.query_row("SELECT v FROM kv WHERE k=?", [k], |r| r.get(0)).optional()?;
        match v.flatten() {
            Some(s) => Ok(Some(serde_json::from_str(&s)?)),
            None => Ok(None),
        }
    }

    pub fn kv_set(&self, k: &str, v: &Value) -> Result<()> {
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.execute(
            "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            params![k, py_json(v)],
        )?;
        commit(&conn)?;
        Ok(())
    }

    pub fn set_res_ids(&self, fp: &str, ids: &[u64]) -> Result<()> {
        let unique: HashSet<u64> = ids.iter().copied().collect();
        let conn = self.conn.lock();
        begin(&conn)?;
        conn.prepare_cached("DELETE FROM res_ids WHERE fp=?")?.execute([fp])?;
        let mut st = conn.prepare_cached("INSERT INTO res_ids (fp, inst) VALUES (?, ?)")?;
        for i in unique {
            st.execute(params![fp, i as i64])?;
        }
        Ok(())
    }

    /// 리소스 인스턴스 ID → 그 리소스를 가진 파일 지문들.
    pub fn res_id_map(&self) -> Result<HashMap<u64, HashSet<String>>> {
        let conn = self.conn.lock();
        let mut st = conn.prepare("SELECT fp, inst FROM res_ids")?;
        let mut out: HashMap<u64, HashSet<String>> = HashMap::new();
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            out.entry(r.get::<_, i64>(1)? as u64).or_default().insert(r.get(0)?);
        }
        Ok(out)
    }

    /// 썸네일 경로 일괄 교체 (PNG → JPG 이전용).
    pub fn replace_thumb_paths(&self, pairs: &[(String, String)]) -> Result<()> {
        let conn = self.conn.lock();
        if !pairs.is_empty() {
            begin(&conn)?;
        }
        for (src, dst) in pairs {
            conn.execute("UPDATE items SET thumb=? WHERE thumb=?", params![dst, src])?;
            conn.execute("UPDATE notes SET user_thumb=? WHERE user_thumb=?", params![dst, src])?;
        }
        commit(&conn)?;
        Ok(())
    }
}
