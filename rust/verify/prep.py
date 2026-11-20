"""샌드박스 심즈 폴더 두 벌(py/rs)과 가져오기용 소스 폴더를 만든다."""
import os
import shutil
import zipfile

from PIL import Image

ROOT = r"C:\Users\user\Documents\S4CCMtoRUST"
HERE = os.path.dirname(os.path.abspath(__file__))
base = os.path.join(HERE, "sb")
shutil.rmtree(base, ignore_errors=True)
mods = os.path.join(ROOT, "Mods")
WANT = ("Body", "Makeup", "Clothing", "Hair", "Slider", "Mod", "Overrides", "Cars")
pk = sorted((os.path.getsize(os.path.join(dp, n)), os.path.join(dp, n))
            for dp, _d, fn in os.walk(mods) for n in fn
            if n.lower().endswith(".package") and os.path.dirname(dp) == mods and os.path.basename(dp) in WANT)
small = [p for s, p in pk if s < 4_000_000]
inside, extra = small[::3][:45], small[1::3][:8]
sims = os.path.join(base, "sims")
for p in inside:
    dst = os.path.join(sims, os.path.relpath(p, ROOT))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(p, dst)
for name in os.listdir(mods):
    p = os.path.join(mods, name)
    if os.path.isfile(p) and os.path.getsize(p) < 30_000_000:
        shutil.copy2(p, os.path.join(sims, "Mods", name))


def tree_size(d):
    return sum(os.path.getsize(os.path.join(dp, n)) for dp, _d, fn in os.walk(d) for n in fn)


for d in ("MC", "sims4aichat", "Tmex-TOOL"):
    full = os.path.join(mods, d)
    if os.path.isdir(full) and tree_size(full) < 60_000_000:
        shutil.copytree(full, os.path.join(sims, "Mods", d))
os.makedirs(os.path.join(sims, "Mods", "deep", "er"), exist_ok=True)
shutil.copy2(inside[0], os.path.join(sims, "Mods", "deep", "er", "nested copy.package"))
os.makedirs(os.path.join(sims, "S4CCM_Disabled", "Makeup"), exist_ok=True)
shutil.copy2(extra[0], os.path.join(sims, "S4CCM_Disabled", "Makeup", os.path.basename(extra[0])))

tray = sorted(os.listdir(os.path.join(ROOT, "Tray")))
keys = sorted({n.split("!0x")[1][:15] for n in tray})
os.makedirs(os.path.join(sims, "Tray"))
src = os.path.join(base, "src")
os.makedirs(src)
for n in tray:
    to = os.path.join(sims, "Tray") if n.split("!0x")[1][:15] == keys[0] else src
    shutil.copy2(os.path.join(ROOT, "Tray", n), os.path.join(to, n))
shutil.copy2(extra[1], src)
shutil.copy2(inside[1], os.path.join(src, "dup of installed.package"))
Image.new("RGB", (600, 300), (200, 80, 40)).save(os.path.join(base, "p.png"))
with zipfile.ZipFile(os.path.join(src, "묶음.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    z.write(extra[2], "한글폴더/" + os.path.basename(extra[2]))
    z.write(extra[3], "한글폴더/sub/" + os.path.basename(extra[3]))
    z.write(os.path.join(base, "p.png"), "한글폴더/preview.png")
with zipfile.ZipFile(os.path.join(src, "cp949.zip"), "w") as z:
    z.write(extra[4], "XXXXXX/item.package")
raw = open(os.path.join(src, "cp949.zip"), "rb").read().replace(b"XXXXXX", "가나다".encode("cp949"))
open(os.path.join(src, "cp949.zip"), "wb").write(raw)
fold = os.path.join(src, "sandboxmod folder")
os.makedirs(fold)
open(os.path.join(fold, "sandboxmod_v1.2.ts4script"), "wb").write(b"PK\x05\x06" + b"\0" * 18)
shutil.copy2(extra[5], os.path.join(fold, "sandboxmod_tuning.package"))
open(os.path.join(fold, "readme.txt"), "w").write("hi")
open(os.path.join(src, "loose_script.ts4script"), "wb").write(b"PK\x05\x06" + b"\0" * 17 + b"\1")
shutil.copy2(extra[6], os.path.join(src, "loose_extra.package"))
open(os.path.join(src, "unsupported.doc"), "w").write("x")
os.makedirs(os.path.join(src, "emptydir"))
open(os.path.join(src, "emptydir", "note.txt"), "w").write("x")
os.makedirs(os.path.join(sims, "Saves"), exist_ok=True)
for who in ("py", "rs"):
    shutil.copytree(sims, os.path.join(base, "sims_" + who))
    shutil.copytree(src, os.path.join(base, "src_" + who))
    os.makedirs(os.path.join(base, "home_" + who))
print("sandbox ready:", len(inside), "packages,", sum(len(f) for _a, _b, f in os.walk(sims)), "files")
