"""Python 판 s4ccm 으로 Rust 덤프 도구와 같은 JSON 을 만든다."""
import json
import os
import sys
import time

ROOT = r"C:\Users\user\Documents\S4CCMtoRUST"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

cmd, args = sys.argv[1], sys.argv[2:]


def tray_json(e):
    return {
        "key": e.key, "kind": e.kind, "kind_label": e.kind_label, "name": e.name,
        "description": e.description, "creator": e.creator,
        "saved_at": e.saved_at.strftime("%Y-%m-%d %H:%M:%S") if e.saved_at else None,
        "export_name": e.export_name,
        "files": [os.path.basename(f) for f in e.files],
        "sims": [{
            "first_name": s.first_name, "last_name": s.last_name, "age": s.age, "gender": s.gender,
            "traits": s.traits, "aspiration": s.aspiration, "likes": s.likes, "dislikes": s.dislikes,
            "sim_id": str(s.sim_id), "full_name": s.full_name, "summary": s.summary,
            "skills": [{"skill_id": str(k.skill_id), "points": k.points, "name": k.name,
                        "level": k.level, "max_level": k.max_level} for k in s.skills],
        } for s in e.sims],
    }


if cmd == "packages":
    from s4ccm import classify, scriptmods
    from s4ccm.library import fingerprint
    paths = sorted(os.path.join(dp, n) for d in args for dp, _dn, fn in os.walk(d) for n in fn
                   if n.lower().endswith(".package"))
    t = time.time()
    out = []
    for p in paths:
        c = classify.classify_package(p)
        tex = classify.texture_size(p)
        out.append({
            "path": p, "fingerprint": fingerprint(p),
            "category": c.category, "confidence": c.confidence, "reason": c.reason,
            "casp_names": c.casp_names, "casp_instances": [str(x) for x in c.casp_instances],
            "object_instances": [str(x) for x in c.object_instances],
            "resource_count": c.resource_count, "error": c.error is not None,
            "tex": list(tex) if tex else None, "hq": classify.is_hq(tex),
            "ids": [str(x) for x in sorted(classify.resource_ids(p))],
            "libs": sorted(scriptmods.required_libraries([p])),
        })
    print(f"packages: {len(out)} files in {time.time() - t:.2f}s", file=sys.stderr)
elif cmd == "tray":
    from s4ccm import tray
    out = [tray_json(e) for e in tray.load_tray(args[0])]
elif cmd == "rescfg":
    from s4ccm.resourcecfg import ResourceCfg
    with open(args[0], encoding="utf-8-sig", errors="replace") as f:
        cfg = ResourceCfg.parse(f.read())
    out = {"rules": [[r.priority, r.folder, r.depth] for r in cfg.rules],
           "extra": [list(e) for e in cfg.extra], "render": cfg.render(),
           "default": ResourceCfg.default().render()}
elif cmd == "scan":
    from s4ccm import duplicates, tray
    from s4ccm.config import Config
    from s4ccm.library import Library
    lib = Library(Config(sims_dir=args[0]))
    calls = [0]

    def prog(*_a):
        calls[0] += 1
    t = time.time()
    stats = lib.scan(prog)
    scan_secs = time.time() - t
    t = time.time()
    stats2 = lib.scan()
    print(f"scan: {scan_secs:.2f}s, rescan: {time.time() - t:.2f}s", file=sys.stderr)
    items = lib.items()
    res = sorted((fp, str(i)) for i, fps in lib.db.res_id_map().items() for fp in fps)
    id_map = lib.db.res_id_map()
    used = [{"key": e.key, "cc": [i.rel_path for i in tray.used_cc(lib, e, id_map)]}
            for e in tray.load_tray(os.path.join(args[0], "Tray"))]
    t = time.time()
    dups = duplicates.find_duplicates(lib)
    print(f"duplicates: {time.time() - t:.2f}s", file=sys.stderr)
    out = {
        "stats": [stats["new"], stats["updated"], stats["removed"], stats["total"]],
        "stats2": [stats2["new"], stats2["updated"], stats2["removed"], stats2["total"]],
        "progress_calls": calls[0],
        "items": [{
            "fingerprint": it.fingerprint, "rel_path": it.rel_path, "kind": it.kind, "name": it.name,
            "category": it.category, "auto_category": it.auto_category, "user_set": it.user_set,
            "confidence": it.confidence, "reason": it.reason, "size": it.size, "mtime": it.mtime,
            "status": it.status, "members": it.members,
            "thumb": os.path.basename(it.thumb) if it.thumb else None, "meta": it.meta,
        } for it in items],
        "res_ids": [list(x) for x in res],
        "used_cc": used,
        "dups": [{
            "kind": g.kind, "title": g.title, "shared": g.shared,
            "items": [i.rel_path for i in g.items],
            "keep": g.keep.rel_path if g.keep else None,
            "overlap": [list(g.overlap[i.id]) if i.id in g.overlap else None for i in g.items],
        } for g in dups],
        "plan": [[p.item.rel_path, p.target_rel] for p in lib.organize_plan()],
        "history": list(lib.organize_history()),
    }
elif cmd == "saves":
    from s4ccm import saves
    out = []
    for p in saves.save_files(args[0]):
        t = time.time()
        try:
            idx = saves.load_save(p)
        except Exception as ex:
            out.append({"file": os.path.basename(p), "error": str(ex)})
            continue
        if idx is None:
            out.append({"file": os.path.basename(p), "none": True})
        else:
            out.append({
                "file": os.path.basename(p),
                "names": sorted([str(k), v] for k, v in idx.names.items()),
                "relations": sorted(([str(k), [[str(r[0]), r[1], r[2]] for r in v]]
                                     for k, v in idx.relations.items()), key=lambda x: x[0]),
            })
        print(f"save {os.path.basename(p)}: {time.time() - t:.2f}s", file=sys.stderr)
elif cmd == "savecopy":
    from s4ccm import tray
    entry = next(e for e in tray.load_tray(args[0]) if e.key == args[1])
    r = tray.save_copy(entry, args[0], args[2], None, args[3] == "1", args[4] if len(args) > 4 else None)
    out = {"key": r.key, "cleared": r.cleared, "thumbs": r.thumbs,
           "files": [os.path.basename(f) for f in r.files]}
elif cmd == "thumb":
    from s4ccm import thumbs
    out = thumbs.save_user_image(args[0], args[1])

print(json.dumps(out, ensure_ascii=False))
