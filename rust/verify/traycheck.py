"""save_copy 결과(무작위 ID)와 내보낸 zip 을 양쪽에서 비교한다."""
import json
import os
import sys
import zipfile

ROOT = r"C:\Users\user\Documents\S4CCMtoRUST"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
from s4ccm import tray

HERE = os.path.dirname(os.path.abspath(__file__))
ok = True


def canon(sims, src_key, copy_key):
    """복사본 파일들을 원본 그룹 ID 로 되돌린 {확장자·타입: 바이트}."""
    tdir = os.path.join(sims, "Tray")
    entry = next(e for e in tray.load_tray(tdir) if e.key == copy_key)
    new_group, group = int(copy_key, 16), int(src_key, 16)
    mapping = {i: (i & ~tray.GROUP_MASK) | group for i in tray._related_ids(entry, new_group)}
    out = {}
    for path in entry.files:
        m = tray._NAME_RX.match(os.path.basename(path))
        data = open(path, "rb").read()
        if not path.lower().endswith(tray.IMAGE_EXT):
            data = tray._swap_ids(data, mapping)
        inst = mapping.get(int(m.group(2), 16), int(m.group(2), 16))
        name = f"0x{m.group(1)}!0x{inst:016x}.{m.group(3)}"
        if path.lower().endswith(".trayitem"):
            fields = [(n, wt, v) for n, wt, v in tray._fields(data[8:]) if n != 11]
            stamp = [v for n, wt, v in tray._fields(data[8:]) if n == 11]
            data = (data[:4], fields, len(stamp))
        out[name] = data
    return out


py = json.load(open(os.path.join(HERE, "py_ops.json"), encoding="utf-8"))[8]["trays"]
rs = json.load(open(os.path.join(HERE, "rs_ops.json"), encoding="utf-8"))[8]["trays"]
for a, b in zip(py, rs):
    assert a["key"] == b["key"]
    ca = canon(os.path.join(HERE, "sb", "sims_py"), a["key"], a["copy_key"])
    cb = canon(os.path.join(HERE, "sb", "sims_rs"), b["key"], b["copy_key"])
    same = ca.keys() == cb.keys() and all(ca[k] == cb[k] for k in ca)
    orig = {os.path.basename(f) for f in os.listdir(os.path.join(HERE, "sb", "sims_py", "Tray"))}
    print(f"복사본 {a['key']}: 파일 {len(ca)}개, 이름이 원본과 대응={set(ca) <= orig}, 내용 일치={same}, cleared {a['cleared']}/{b['cleared']}")
    if not same:
        ok = False
        for k in ca:
            if ca.get(k) != cb.get(k):
                print("   다름:", k, len(ca[k]) if isinstance(ca[k], bytes) else "trayitem",
                      len(cb[k]) if isinstance(cb.get(k), bytes) else "trayitem")
    za = zipfile.ZipFile(os.path.join(HERE, "sb", "sims_py", "_export", a["zip"]))
    zb = zipfile.ZipFile(os.path.join(HERE, "sb", "sims_rs", "_export", b["zip"]))
    la = [(i.filename, i.file_size, i.CRC, i.date_time, i.flag_bits & 0x800) for i in za.infolist()]
    lb = [(i.filename, i.file_size, i.CRC, i.date_time, i.flag_bits & 0x800) for i in zb.infolist()]
    la_cmp = [x if x[0] != "읽어주세요.txt" else x[:3] for x in la]
    lb_cmp = [x if x[0] != "읽어주세요.txt" else x[:3] for x in lb]
    print(f"내보내기 zip {a['zip']}: 항목 {len(la)}개, 일치={la_cmp == lb_cmp}")
    if la_cmp != lb_cmp:
        ok = False
        for x, y in zip(la_cmp, lb_cmp):
            if x != y:
                print("   ", x, y)

# 게임이 읽는 이미지 형식으로 다시 인코딩한 결과도 Python 이 읽을 수 있는지
print("최종:", "일치" if ok else "불일치")
sys.exit(0 if ok else 1)
