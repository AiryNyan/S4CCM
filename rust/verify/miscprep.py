"""misc 검증용 폴더 두 벌: 설정, Resource.cfg, Options.ini, 합성 세이브."""
import json
import os
import shutil
import struct
import zlib

from PIL import Image

ROOT = r"C:\Users\user\Documents\S4CCMtoRUST"
HERE = os.path.dirname(os.path.abspath(__file__))
base = os.path.join(HERE, "misc")
shutil.rmtree(base, ignore_errors=True)


def vi(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def f_var(n, v):
    return vi(n << 3) + vi(v)


def f_len(n, b):
    return vi((n << 3) | 2) + vi(len(b)) + b


def f_32(n, f):
    return vi((n << 3) | 5) + struct.pack("<f", f)


def f_64(n, v):
    return vi((n << 3) | 1) + struct.pack("<Q", v)


def sim(sid, first, last):
    return f_len(6, f_var(1, sid) + f_var(3, 5) + f_len(5, first.encode()) + f_len(6, last.encode()) + f_var(9, 1))


def track(tid, val):
    return f_len(3, f_var(1, tid) + f_32(2, val))


def rel(a, b, tracks, fixed=False):
    ids = (f_64(1, a) + f_64(2, b)) if fixed else (f_var(1, a) + f_var(2, b))
    return f_len(1, ids + f_len(3, b"".join(tracks)) + f_var(9, 3))


A, B, C, D = 0x0109177800000001, 0x0109177800000002, 0xF109177800000003, 44
sims = sim(A, "길동", "홍") + sim(B, "Bella", "Goth") + sim(C, "", "") + sim(D, "철수", "김")
rels = (rel(A, B, [track(16650, 55.349998), track(16651, -12.25)]) +
        rel(A, C, [track(16651, 80.05)], fixed=True) +
        rel(A, D, [track(99, 1.0)]) +
        rel(B, D, [track(16650, 100.0), track(16650, 3.14159)]))
world = f_var(1, 7) + f_len(8, f_len(2, b"junk") + f_len(13, rels))
save = f_var(1, 3) + f_len(2, world) + sims + f_len(7, b"tail")


def package(resources):
    """(type, data, compressed) 목록으로 DBPF 2.1 을 만든다."""
    body, index = b"", b""
    off = 96
    for t, data, comp in resources:
        raw = zlib.compress(data) if comp else data
        index += struct.pack("<IIIIIII", t, 0, 0, 1, off, len(raw) | 0x80000000, len(data))
        index += struct.pack("<HH", 0x5A42 if comp else 0, 1)
        body += raw
        off += len(raw)
    head = bytearray(96)
    head[:4] = b"DBPF"
    struct.pack_into("<II", head, 4, 2, 1)
    struct.pack_into("<I", head, 36, len(resources))
    struct.pack_into("<I", head, 44, len(index) + 4)
    struct.pack_into("<I", head, 64, off)
    return bytes(head) + body + struct.pack("<I", 0) + index


for who in ("py", "rs"):
    home = os.path.join(base, "home_" + who)
    sd = os.path.join(base, "sims_" + who)
    os.makedirs(home)
    os.makedirs(os.path.join(sd, "Mods"))
    os.makedirs(os.path.join(sd, "Saves"))
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"sims_dir": "C:\\가상\\The Sims 4", "theme": "light", "unknown_key": 1,
                   "clear_thumbcache_on_launch": True, "folder_overrides": {"cas.tops": "상의 모음"}}, f,
                  ensure_ascii=False)
    shutil.copy2(os.path.join(ROOT, "Mods", "Resource.cfg"), os.path.join(sd, "Mods"))
    with open(os.path.join(sd, "Options.ini"), "wb") as f:
        f.write("[options]\r\nfullscreen = 1\r\nScriptModsEnabled = 0\r\nmodsdisabled=0\r\n\r\n[other]\r\nk = v = w\r\n".encode())
    open(os.path.join(sd, "Saves", "Slot_00000002.save"), "wb").write(package([(0x0D, save, True), (0x99, b"x" * 10, False)]))
    open(os.path.join(sd, "Saves", "Slot_00000001.save.ver0"), "wb").write(b"not a save")
    open(os.path.join(sd, "Saves", "Slot_00000003.save"), "wb").write(b"broken")
    os.utime(os.path.join(sd, "Saves", "Slot_00000002.save"), (1_700_000_000, 1_700_000_000))
Image.radial_gradient("L").resize((900, 500)).convert("RGB").save(os.path.join(base, "src.png"))
print("misc ready")
