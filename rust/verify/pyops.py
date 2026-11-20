"""Python 판으로 파일 이동/가져오기/트레이 기능을 정해진 순서로 실행한다 (Rust `ops` 와 같은 순서)."""
import json
import os
import sys

ROOT = r"C:\Users\user\Documents\S4CCMtoRUST"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from s4ccm import bisect, importer, tray
from s4ccm.config import Config
from s4ccm.library import ORGANIZE_LABEL, Library

sims, src = sys.argv[1], sys.argv[2]
lib = Library(Config(sims_dir=sims))
out = []


def tree(root):
    return sorted([os.path.relpath(os.path.join(dp, n), root).replace("\\", "/"), os.path.getsize(os.path.join(dp, n))]
                  for dp, _dn, fn in os.walk(root) for n in fn)


def items_json():
    return [{"rel_path": it.rel_path, "kind": it.kind, "name": it.name, "category": it.category,
             "user_set": it.user_set, "status": it.status, "members": it.members, "fingerprint": it.fingerprint,
             "thumb": os.path.basename(it.thumb) if it.thumb else None,
             "user_thumb": os.path.basename(it.user_thumb) if it.user_thumb else None,
             "memo": it.memo, "reason": it.reason} for it in lib.items()]


def st(s):
    return [s["new"], s["updated"], s["removed"], s["total"]]


def tray_json(e):
    return {
        "key": None, "kind": e.kind, "kind_label": e.kind_label, "name": e.name,
        "description": e.description, "creator": e.creator, "saved_at": None,
        "export_name": e.export_name, "files": None,
        "sims": [{
            "first_name": s.first_name, "last_name": s.last_name, "age": s.age, "gender": s.gender,
            "traits": s.traits, "aspiration": s.aspiration, "likes": s.likes, "dislikes": s.dislikes,
            "sim_id": str(s.sim_id), "full_name": s.full_name, "summary": s.summary,
            "skills": [{"skill_id": str(k.skill_id), "points": k.points, "name": k.name,
                        "level": k.level, "max_level": k.max_level} for k in s.skills],
        } for s in e.sims],
    }


out.append({"scan": st(lib.scan())})
n = lib.apply_moves(lib.organize_plan(), ORGANIZE_LABEL)
out.append({"organize": n, "tree": tree(sims), "items": items_json()})
out.append({"rescan": st(lib.scan()), "plan_after": len(lib.organize_plan())})
all_ = sorted(lib.items(), key=lambda i: i.rel_path)
some = all_[::4][:8]
n = lib.set_status(some, "disabled", "비활성화")
out.append({"disable": n, "rels": [i.rel_path for i in some]})
pk = [i for i in all_ if i.kind == "package" and i.status == "active"][1::4][:3]
res = []
for it in pk:
    cur = lib.db.get(it.id)
    if cur is not None and cur.status == "active":
        lib.set_category(cur, "cas.hats", True)
        lib.save_note(cur, "메모 테스트")
        it = cur
    res.append(it.rel_path)
out.append({"recat": res, "history": list(lib.organize_history())})
out.append({"undo": lib.undo_organize(), "tree": tree(sims)})
out.append({"scan3": st(lib.scan()), "items": items_json()})

plan = importer.analyze(lib, sorted(os.path.join(src, n) for n in os.listdir(src)))
plan_json = [{
    "name": g.name, "mode": g.mode, "reason": g.mode_reason, "folder": g.folder,
    "known": g.known.id if g.known else None, "replaces": g.replaces.rel_path if g.replaces else None,
    "missing": [k.id for k in g.missing_libs],
    "entries": [[e.rel, e.kind, e.category, e.reason, e.include, e.duplicate] for e in g.entries],
    "trays": [[t.key, t.name, t.kind_label, len(t.entries), t.include, t.exists] for t in g.tray_items],
} for g in plan.groups]
errors = sorted(plan.errors)
n, added = importer.execute(lib, plan)
out.append({"import_plan": plan_json, "errors": errors, "added_n": n, "added": sorted(added),
            "trays_added": plan.trays_added, "mods_added": plan.mods_added,
            "tree": tree(sims), "items": items_json()})

tray_dir = os.path.join(sims, "Tray")
entries = tray.load_tray(tray_dir)
out_dir, cache = os.path.join(sims, "_export"), os.path.join(sims, "_cache")
os.makedirs(out_dir, exist_ok=True)
os.makedirs(cache, exist_ok=True)
trays = []
for k in sorted(e.key for e in entries):
    e = next(x for x in entries if x.key == k)
    cc = tray.used_cc(lib, e)
    z = tray.export_zip(e, out_dir, cc, lib)
    th = tray.entry_thumb(e, cache)
    sth = tray.sim_thumbs(e, cache)
    r = tray.save_copy(e, tray_dir, "복사본 이름", None, e.kind == tray.TYPE_HOUSEHOLD, "설명 테스트")
    trays.append({"key": k, "cc": [i.rel_path for i in cc], "zip": os.path.basename(z),
                  "thumb": os.path.basename(th) if th else None, "sim_thumbs": len(sth),
                  "copy_key": r.key, "cleared": r.cleared, "copy_files": len(r.files)})
out.append({"trays": trays, "tray_after": [tray_json(e) for e in tray.load_tray(tray_dir)]})

ids = [i.id for i in lib.items() if i.status == "active"]
b = bisect.Bisect(lib)


def q():
    return sum(1 for i in lib.items() if i.status == "quarantine")


o = b.start(ids)
q1 = q()
moved = b.report(False)
q2 = q()
steps, susp = b.state.remaining_steps, len(b.state.suspects)
b.end()
out.append({"bisect": [len(ids), o, q1, moved, q2, steps, susp, q()], "items": items_json()})
print(json.dumps(out, ensure_ascii=False))
