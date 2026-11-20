//! 검증용 덤프 도구: Python 판과 같은 입력을 넣고 결과를 JSON 으로 비교한다.
//!
//!   s4ccm_dump packages <dir>...        패키지별 분류·텍스처·리소스 ID·지문
//!   s4ccm_dump tray <tray_dir>          트레이 항목
//!   s4ccm_dump rescfg <file>            Resource.cfg 해석/재생성
//!   s4ccm_dump scan <sims_dir>          전체 스캔 뒤 DB·중복·트레이 CC (S4CCM_HOME 사용)
//!   s4ccm_dump saves <saves_dir>        세이브별 이름·관계
//!   s4ccm_dump savecopy <tray_dir> <key> <name> <look_only 0|1> [desc]
//!   s4ccm_dump thumb <src> <out>        썸네일 한 장 저장

use std::time::Instant;

use s4ccm_core::config::Config;
use s4ccm_core::library::Library;
use s4ccm_core::{bisect, importer, classify, db, duplicates, library, pyos, resourcecfg, saves, scriptmods, thumbs, tray};
use serde_json::{json, Value};

fn walk_packages(dir: &str) -> Vec<String> {
    let mut out = Vec::new();
    for w in pyos::walk(dir) {
        for n in &w.filenames {
            if n.to_lowercase().ends_with(".package") {
                out.push(pyos::join(&w.dirpath, n));
            }
        }
    }
    out
}

fn tray_json(e: &tray::TrayEntry) -> Value {
    json!({
        "key": e.key,
        "kind": e.kind,
        "kind_label": e.kind_label(),
        "name": e.name,
        "description": e.description,
        "creator": e.creator,
        "saved_at": e.saved_at.map(|t| t.format("%Y-%m-%d %H:%M:%S").to_string()),
        "export_name": e.export_name(),
        "files": e.files.iter().map(|f| pyos::basename(f)).collect::<Vec<_>>(),
        "sims": e.sims.iter().map(|s| json!({
            "first_name": s.first_name, "last_name": s.last_name, "age": s.age, "gender": s.gender,
            "traits": s.traits, "aspiration": s.aspiration, "likes": s.likes, "dislikes": s.dislikes,
            "sim_id": s.sim_id.to_string(), "full_name": s.full_name(), "summary": s.summary(),
            "skills": s.skills.iter().map(|k| json!({
                "skill_id": k.skill_id.to_string(), "points": k.points, "name": k.name,
                "level": k.level, "max_level": k.max_level,
            })).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cmd = args.first().map(|s| s.as_str()).unwrap_or("");
    let out: Value = match cmd {
        "packages" => {
            let mut paths: Vec<String> = args[1..].iter().flat_map(|d| walk_packages(d)).collect();
            paths.sort();
            let t = Instant::now();
            let list: Vec<Value> = paths
                .iter()
                .map(|p| {
                    let c = classify::classify_package(p);
                    let tex = classify::texture_size(p);
                    let mut ids = classify::resource_ids(p);
                    ids.sort_unstable();
                    let libs: Vec<&str> = scriptmods::required_libraries(&[p]).into_iter().collect();
                    json!({
                        "path": p,
                        "fingerprint": library::fingerprint(p).ok(),
                        "category": c.category, "confidence": c.confidence, "reason": c.reason,
                        "casp_names": c.casp_names,
                        "casp_instances": c.casp_instances.iter().map(|x| x.to_string()).collect::<Vec<_>>(),
                        "object_instances": c.object_instances.iter().map(|x| x.to_string()).collect::<Vec<_>>(),
                        "resource_count": c.resource_count,
                        "error": c.error.is_some(),
                        "tex": tex.map(|(w, h)| vec![w, h]),
                        "hq": classify::is_hq(tex),
                        "ids": ids.iter().map(|x| x.to_string()).collect::<Vec<_>>(),
                        "libs": libs,
                    })
                })
                .collect();
            eprintln!("packages: {} files in {:.2}s", list.len(), t.elapsed().as_secs_f64());
            Value::Array(list)
        }
        "tray" => Value::Array(tray::load_tray(&args[1]).iter().map(tray_json).collect()),
        "rescfg" => {
            let bytes = std::fs::read(&args[1])?;
            let bytes = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF][..]).unwrap_or(&bytes);
            let text = pyos::universal_newlines(&String::from_utf8_lossy(bytes));
            let cfg = resourcecfg::ResourceCfg::parse(&text);
            json!({
                "rules": cfg.rules.iter().map(|r| json!([r.priority, r.folder, r.depth])).collect::<Vec<_>>(),
                "extra": cfg.extra.iter().map(|e| json!([e.0, e.1])).collect::<Vec<_>>(),
                "render": cfg.render(),
                "default": resourcecfg::ResourceCfg::default_cfg().render(),
            })
        }
        "scan" => {
            let mut config = Config::default();
            config.sims_dir = args[1].clone();
            let lib = Library::new(config)?;
            let t = Instant::now();
            let mut calls = 0usize;
            let stats = lib.scan(Some(&mut |_d, _t, _w| calls += 1))?;
            let scan_secs = t.elapsed().as_secs_f64();
            let t = Instant::now();
            let stats2 = lib.scan(None)?;
            eprintln!("scan: {:.2}s, rescan: {:.2}s", scan_secs, t.elapsed().as_secs_f64());
            let items = lib.items()?;
            let item_json = |it: &db::Item| {
                json!({
                    "fingerprint": it.fingerprint, "rel_path": it.rel_path, "kind": it.kind, "name": it.name,
                    "category": it.category, "auto_category": it.auto_category, "user_set": it.user_set,
                    "confidence": it.confidence, "reason": it.reason, "size": it.size, "mtime": it.mtime,
                    "status": it.status, "members": it.members,
                    "thumb": it.thumb.as_deref().map(pyos::basename), "meta": it.meta,
                })
            };
            let mut res: Vec<(String, String)> = lib
                .db
                .res_id_map()?
                .into_iter()
                .flat_map(|(i, fps)| fps.into_iter().map(move |fp| (fp, i.to_string())))
                .collect();
            res.sort();
            let id_map = lib.db.res_id_map()?;
            let trays = tray::load_tray(&pyos::join(&args[1], "Tray"));
            let mut used = Vec::new();
            for e in &trays {
                let cc = tray::used_cc(&lib, e, Some(&id_map))?;
                used.push(json!({"key": e.key, "cc": cc.iter().map(|i| i.rel_path.clone()).collect::<Vec<_>>()}));
            }
            let t = Instant::now();
            let dups = duplicates::find_duplicates(&lib, None)?;
            eprintln!("duplicates: {:.2}s", t.elapsed().as_secs_f64());
            let plans = lib.organize_plan(None)?;
            let history = { let h = lib.organize_history()?; vec![h.0, h.1] };
            json!({
                "stats": [stats.new, stats.updated, stats.removed, stats.total],
                "stats2": [stats2.new, stats2.updated, stats2.removed, stats2.total],
                "progress_calls": calls,
                "items": items.iter().map(item_json).collect::<Vec<_>>(),
                "res_ids": res,
                "used_cc": used,
                "dups": dups.iter().map(|g| json!({
                    "kind": g.kind, "title": g.title(), "shared": g.shared,
                    "items": g.items.iter().map(|i| i.rel_path.clone()).collect::<Vec<_>>(),
                    "keep": g.keep.and_then(|k| g.items.iter().find(|i| i.id == k)).map(|i| i.rel_path.clone()),
                    "overlap": g.items.iter().map(|i| g.overlap.get(&i.id).map(|o| vec![o.0, o.1])).collect::<Vec<_>>(),
                })).collect::<Vec<_>>(),
                "plan": plans.iter().map(|p| json!([p.item.rel_path, p.target_rel])).collect::<Vec<_>>(),
                "history": history,
            })
        }
        "saves" => {
            let mut out = Vec::new();
            for p in saves::save_files(&args[1]) {
                let t = Instant::now();
                match saves::load_save(&p) {
                    Ok(Some(idx)) => {
                        let mut names: Vec<(String, String)> =
                            idx.names.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
                        names.sort();
                        let mut rels: Vec<Value> = idx
                            .relations
                            .iter()
                            .map(|(k, v)| json!([k.to_string(), v.iter().map(|r| json!([r.0.to_string(), r.1, r.2])).collect::<Vec<_>>()]))
                            .collect();
                        rels.sort_by_key(|v| v[0].as_str().unwrap_or("").to_string());
                        out.push(json!({"file": pyos::basename(&p), "names": names, "relations": rels}));
                    }
                    Ok(None) => out.push(json!({"file": pyos::basename(&p), "none": true})),
                    Err(e) => out.push(json!({"file": pyos::basename(&p), "error": e.to_string()})),
                }
                eprintln!("save {}: {:.2}s", pyos::basename(&p), t.elapsed().as_secs_f64());
            }
            Value::Array(out)
        }
        "savecopy" => {
            let entries = tray::load_tray(&args[1]);
            let entry = entries.iter().find(|e| e.key == args[2]).ok_or("no such tray entry")?;
            let r = tray::save_copy(entry, &args[1], &args[3], None, args[4] == "1", args.get(5).map(|s| s.as_str()))?;
            json!({"key": r.key, "cleared": r.cleared, "thumbs": r.thumbs,
                   "files": r.files.iter().map(|f| pyos::basename(f)).collect::<Vec<_>>()})
        }
        "ops" => {
            // 파일을 실제로 옮기는 기능들을 정해진 순서로 실행하고 단계별 결과를 남긴다.
            let sims = args[1].clone();
            let src = args[2].clone();
            let mut config = Config::default();
            config.sims_dir = sims.clone();
            let lib = Library::new(config)?;
            let mut log: Vec<Value> = Vec::new();
            let tree = |root: &str| -> Vec<Value> {
                let mut v: Vec<(String, u64)> = Vec::new();
                for w in pyos::walk(root) {
                    for n in &w.filenames {
                        let p = pyos::join(&w.dirpath, n);
                        v.push((pyos::relpath(&p, root).replace('\\', "/"), pyos::getsize(&p).unwrap_or(0)));
                    }
                }
                v.sort();
                v.into_iter().map(|x| json!([x.0, x.1])).collect()
            };
            let items_json = |lib: &Library| -> Result<Value, Box<dyn std::error::Error>> {
                Ok(Value::Array(lib.items()?.iter().map(|it| json!({
                    "rel_path": it.rel_path, "kind": it.kind, "name": it.name, "category": it.category,
                    "user_set": it.user_set, "status": it.status, "members": it.members, "fingerprint": it.fingerprint,
                    "thumb": it.thumb.as_deref().map(pyos::basename), "user_thumb": it.user_thumb.as_deref().map(pyos::basename),
                    "memo": it.memo, "reason": it.reason,
                })).collect()))
            };
            let s = lib.scan(None)?;
            log.push(json!({"scan": [s.new, s.updated, s.removed, s.total]}));
            let mut plans = lib.organize_plan(None)?;
            let n = lib.apply_moves(&mut plans, library::ORGANIZE_LABEL, None)?;
            log.push(json!({"organize": n, "tree": tree(&sims), "items": items_json(&lib)?}));
            let s = lib.scan(None)?;
            log.push(json!({"rescan": [s.new, s.updated, s.removed, s.total], "plan_after": lib.organize_plan(None)?.len()}));
            let mut all = lib.items()?;
            all.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
            let mut some: Vec<db::Item> = all.iter().step_by(4).take(8).cloned().collect();
            let n = lib.set_status(&mut some, "disabled", "비활성화", None)?;
            log.push(json!({"disable": n, "rels": some.iter().map(|i| i.rel_path.clone()).collect::<Vec<_>>()}));
            let mut pk: Vec<db::Item> = all.iter().filter(|i| i.kind == "package" && i.status == "active" && !some.iter().any(|x| x.id == i.id)).skip(1).step_by(4).take(3).cloned().collect();
            for it in pk.iter_mut() {
                if let Some(mut cur) = lib.db.get(it.id)? {
                    if cur.status == "active" {
                        lib.set_category(&mut cur, "cas.hats", true)?;
                        lib.save_note(&cur, "메모 테스트")?;
                        *it = cur;
                    }
                }
            }
            let hist = { let h = lib.organize_history()?; vec![h.0, h.1] };
            log.push(json!({"recat": pk.iter().map(|i| i.rel_path.clone()).collect::<Vec<_>>(), "history": hist}));
            let n = lib.undo_organize(None)?;
            log.push(json!({"undo": n, "tree": tree(&sims)}));
            let s = lib.scan(None)?;
            log.push(json!({"scan3": [s.new, s.updated, s.removed, s.total], "items": items_json(&lib)?}));
            let mut paths: Vec<String> = pyos::listdir(&src)?.into_iter().map(|n| pyos::join(&src, &n)).collect();
            paths.sort();
            let mut plan = importer::analyze(&lib, &paths);
            let plan_json: Vec<Value> = plan.groups.iter().map(|g| json!({
                "name": g.name, "mode": g.mode, "reason": g.mode_reason, "folder": g.folder,
                "known": g.known.map(|k| k.id), "replaces": g.replaces.as_ref().map(|r| r.rel_path.clone()),
                "missing": g.missing_libs.iter().map(|k| k.id).collect::<Vec<_>>(),
                "entries": g.entries.iter().map(|e| json!([e.rel, e.kind, e.category, e.reason, e.include, e.duplicate])).collect::<Vec<_>>(),
                "trays": g.tray_items.iter().map(|t| json!([t.key, t.name, t.kind_label, t.entries.len(), t.include, t.exists])).collect::<Vec<_>>(),
            })).collect();
            let mut errors = plan.errors.clone();
            errors.sort();
            let (n, mut added) = importer::execute(&lib, &mut plan)?;
            added.sort();
            log.push(json!({"import_plan": plan_json, "errors": errors, "added_n": n, "added": added,
                            "trays_added": plan.trays_added, "mods_added": plan.mods_added,
                            "tree": tree(&sims), "items": items_json(&lib)?}));
            let tray_dir = pyos::join(&sims, "Tray");
            let entries = tray::load_tray(&tray_dir);
            let mut keys: Vec<String> = entries.iter().map(|e| e.key.clone()).collect();
            keys.sort();
            let out_dir = pyos::join(&sims, "_export");
            pyos::makedirs(&out_dir)?;
            let cache = pyos::join(&sims, "_cache");
            pyos::makedirs(&cache)?;
            let mut trays = Vec::new();
            for k in &keys {
                let e = entries.iter().find(|e| &e.key == k).unwrap();
                let cc = tray::used_cc(&lib, e, None)?;
                let z = tray::export_zip(e, &out_dir, Some(&cc), &lib)?;
                let th = tray::entry_thumb(e, &cache)?;
                let st = tray::sim_thumbs(e, &cache)?;
                let r = tray::save_copy(e, &tray_dir, "복사본 이름", None, e.kind == tray::TYPE_HOUSEHOLD, Some("설명 테스트"))?;
                trays.push(json!({"key": k, "cc": cc.iter().map(|i| i.rel_path.clone()).collect::<Vec<_>>(),
                    "zip": pyos::basename(&z), "thumb": th.map(|t| pyos::basename(&t)), "sim_thumbs": st.len(),
                    "copy_key": r.key, "cleared": r.cleared, "copy_files": r.files.len()}));
            }
            log.push(json!({"trays": trays, "tray_after": tray::load_tray(&tray_dir).iter().map(tray_json).map(|mut v| { v["key"] = Value::Null; v["files"] = Value::Null; v["saved_at"] = Value::Null; v }).collect::<Vec<_>>()}));
            let ids: Vec<i64> = lib.items()?.iter().filter(|i| i.status == "active").map(|i| i.id).collect();
            let mut b = bisect::Bisect::new(&lib)?;
            let out = b.start(&ids, None)?;
            let q1 = lib.items()?.iter().filter(|i| i.status == "quarantine").count();
            let moved = b.report(false, None)?;
            let q2 = lib.items()?.iter().filter(|i| i.status == "quarantine").count();
            let (steps, susp) = (b.state.remaining_steps(), b.state.suspects.len());
            b.end(None)?;
            let q3 = lib.items()?.iter().filter(|i| i.status == "quarantine").count();
            log.push(json!({"bisect": [ids.len(), out, q1, moved, q2, steps, susp, q3], "items": items_json(&lib)?}));
            Value::Array(log)
        }
        "misc" => {
            // 설정·Resource.cfg·Options.ini·세이브 관계·트레이 이미지 인코딩.
            let sims = args[1].clone();
            let mut c = Config::load();
            let loaded = c.to_json();
            c.thumb_size = 160;
            c.folder_overrides.insert("cas.hair".into(), "내 헤어".into());
            c.save()?;
            let mods = pyos::join(&sims, "Mods");
            let (mut cfg, text) = resourcecfg::load(&mods)?;
            cfg.rules.push(resourcecfg::Rule { priority: 700, folder: "새 폴더/하위".into(), depth: 9 });
            let backup = resourcecfg::save(&mods, &cfg)?;
            let opts = resourcecfg::read_options(&sims)?;
            let set1 = resourcecfg::set_option(&sims, "scriptmodsenabled", "1")?;
            let set2 = resourcecfg::set_option(&sims, "newkey", "7")?;
            let rel = |f: &str, l: &str| saves::find_relations(&pyos::join(&sims, "Saves"), f, l).map(|(p, r)| {
                json!([pyos::basename(&p), r.iter().map(|x| json!([x.name, x.friendship, x.romance])).collect::<Vec<_>>()])
            });
            let img = thumbs::open_path(&args[2])?;
            std::fs::write(&args[3], tray::encode_image(&img, (591, 394))?)?;
            let back = tray::decode_image(&args[4])?.map(|i| vec![i.width(), i.height()]);
            json!({
                "loaded": loaded, "text_len": text.chars().count(), "backup": backup.is_some(),
                "opts": opts.iter().map(|(k, v)| json!([k, v])).collect::<Vec<_>>(), "set": [set1, set2],
                "rel1": rel("길동", "홍"), "rel2": rel("Bella", "Goth"), "rel3": rel("없음", "누구"),
                "decoded_other": back,
            })
        }
        "thumb" => json!(thumbs::save_user_image_path(&args[1], &args[2])),
        // poseall <패키지> <출력 폴더>
        "poseall" => json!(s4ccm_core::poseicon::extract_all(&args[1], &args[2])),
        // poseicon <패키지> <출력.jpg>
        "poseicon" => {
            let mut pk = s4ccm_core::dbpf::Package::open(&args[1]).map_err(|e| e.to_string())?;
            json!(s4ccm_core::poseicon::extract(&mut pk, &args[2]))
        }
        // relocate <심즈 폴더> <옮길 곳> / restore <심즈 폴더> / linkstatus <심즈 폴더>
        "relocate" | "restore" | "linkstatus" => {
            use s4ccm_core::relocate;
            let mut config = Config::default();
            config.sims_dir = args[1].clone();
            let lib = Library::new(config)?;
            let mut phases: Vec<String> = Vec::new();
            let mut steps = |phase: &str, _i: usize, _n: usize, _t: &str| {
                if phases.last().map(|p| p.as_str()) != Some(phase) {
                    phases.push(phase.to_string());
                }
            };
            let result = match cmd {
                "relocate" => {
                    let m = relocate::measure(&args[1], &args[2]).map(|m| json!([m.files, m.bytes, m.same_volume]));
                    relocate::relocate(&lib, &args[2], Some(&mut steps)).map(|_| m.ok())
                }
                "restore" => relocate::restore(&lib, Some(&mut steps)).map(|_| None),
                _ => Ok(None),
            };
            let st: Vec<Value> = relocate::status(&args[1])
                .iter()
                .map(|s| json!({"name": s.name, "exists": s.exists, "link": s.is_link, "target": s.target}))
                .collect();
            json!({
                "ok": result.as_ref().is_ok(),
                "error": result.as_ref().err().map(|e| e.to_string()),
                "measure": result.ok().flatten(),
                "phases": phases,
                "status": st,
                "can_symlink": relocate::can_symlink(),
            })
        }
        _ => {
            eprintln!("usage: s4ccm_dump <packages|tray|rescfg|scan|saves|savecopy|thumb> ...");
            std::process::exit(2);
        }
    };
    println!("{}", serde_json::to_string(&out)?);
    Ok(())
}
