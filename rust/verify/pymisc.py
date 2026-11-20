"""Rust `misc` 와 같은 순서로 Python 판을 실행한다."""
import json
import os
import sys
from dataclasses import asdict

ROOT = r"C:\Users\user\Documents\S4CCMtoRUST"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
from PIL import Image

from s4ccm import resourcecfg, saves, tray
from s4ccm.config import Config

sims, src_img, out_img, other_img = sys.argv[1:5]
c = Config.load()
loaded = asdict(c)
c.thumb_size = 160
c.folder_overrides["cas.hair"] = "내 헤어"
c.save()
mods = os.path.join(sims, "Mods")
cfg, text = resourcecfg.load(mods)
cfg.rules.append(resourcecfg.Rule(700, "새 폴더/하위", 9))
backup = resourcecfg.save(mods, cfg)
opts = resourcecfg.read_options(sims)
set1 = resourcecfg.set_option(sims, "scriptmodsenabled", "1")
set2 = resourcecfg.set_option(sims, "newkey", "7")


def rel(f, l):
    r = saves.find_relations(os.path.join(sims, "Saves"), f, l)
    return None if r is None else [os.path.basename(r[0]), [[x.name, x.friendship, x.romance] for x in r[1]]]


img = Image.open(src_img)
img.load()
open(out_img, "wb").write(tray.encode_image(img, (591, 394)))
back = tray.decode_image(other_img) if os.path.exists(other_img) else None
print(json.dumps({
    "loaded": loaded, "text_len": len(text), "backup": backup is not None,
    "opts": [[k, v] for k, v in opts.items()], "set": [set1, set2],
    "rel1": rel("길동", "홍"), "rel2": rel("Bella", "Goth"), "rel3": rel("없음", "누구"),
    "decoded_other": list(back.size) if back is not None else None,
}, ensure_ascii=False))
