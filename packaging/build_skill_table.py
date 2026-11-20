import json
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from s4ccm.dbpf import Package

GAME = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "S4CCM_GameSample", "Game")
OUT = os.path.join(ROOT, "s4ccm", "data", "skills.json")
T_SIMDATA, T_STBL, T_COMBINED = 0x545AC67A, 0x220557DA, 0x62E94D38
NULL = -0x80000000
LEVEL_TYPES = {0: "MAJOR", 1: "MINOR", 2: "CHILD", 3: "TODDLER"}


def cstr(d: bytes, pos: int) -> str:
    return d[pos:d.index(b"\0", pos)].decode("utf-8", "replace")


def skill_rows(d: bytes):
    if d[:4] != b"DATA":
        return
    th_off, t_count, sc_off, sc_count = struct.unpack_from("<iiii", d, 8)
    schemas = {}
    p = 16 + sc_off
    for _ in range(sc_count):
        name_off, _h, _sh, _size, col_off, col_count = struct.unpack_from("<iIIIii", d, p)
        cols, cp = {}, p + 16 + col_off
        for _c in range(col_count):
            cn_off, _ch, dtype, _fl, offset, _so = struct.unpack_from("<iIHHIi", d, cp)
            cols[cstr(d, cp + cn_off)] = (dtype, offset)
            cp += 20
        schemas[p] = (cstr(d, p + name_off) if name_off != NULL else "", cols)
        p += 24
    p = 8 + th_off
    for _ in range(t_count):
        name_off, _h, sch_off, _dt, row_size, row_off, row_count = struct.unpack_from("<iIiIIii", d, p)
        schema = schemas.get(p + 8 + sch_off) if sch_off != NULL else None
        if schema and schema[0] == "Skill" and row_count:
            cols = schema[1]
            row = p + 20 + row_off
            yield (cstr(d, p + name_off),
                   struct.unpack_from("<I", d, row + cols["stat_name"][1])[0],
                   struct.unpack_from("<q", d, row + cols["skill_level_type"][1])[0])
        p += 28


def stbl(d: bytes) -> dict[int, str]:
    if d[:4] != b"STBL":
        return {}
    n = struct.unpack_from("<Q", d, 7)[0]
    p, out = 21, {}
    for _ in range(n):
        k, _fl, ln = struct.unpack_from("<IBH", d, p)
        p += 7
        out[k] = d[p:p + ln].decode("utf-8", "replace")
        p += ln
    return out


def level_lists(xml: str) -> dict[str, list[int]]:
    defs = dict(re.findall(r'<[TE][^>]*\bx="(\d+)"[^>]*>([^<]*)</[TE]>', xml))
    i = xml.find('<L n="SKILL_LEVEL_LIST">')
    if i < 0:
        return {}
    block = xml[i:xml.index("</L></U></L>", i) + 12]

    def val(tok: str) -> str:
        m = re.match(r'<r (?:n="\w+" )?x="(\d+)" ?/>', tok)
        if m:
            return defs.get(m.group(1), "0")
        m = re.match(r'<[TE](?: n="\w+")?>([^<]*)</[TE]>', tok)
        return m.group(1) if m else "0"

    out = {}
    for u in re.findall(r"<U>(.*?)</L></U>", block):
        key = val(re.match(r'(<r n="key" x="\d+" ?/>|<E n="key">\w+</E>)', u).group(1))
        out[key] = [int(float(val(v))) for v in re.findall(r'<r x="\d+" ?/>|<T>[^<]*</T>', u)]
    return out


def main() -> None:
    skills, strings_ko, strings_en, levels = {}, {}, {}, {}
    for dp, _dn, fn in os.walk(GAME):
        for n in fn:
            path = os.path.join(dp, n)
            if n == "SimulationFullBuild0.package":
                with Package(path) as pk:
                    for e in pk.entries:
                        if e.type == T_SIMDATA and e.mem_size < 4000:
                            for name, key, lvl in skill_rows(pk.read(e)):
                                if "skill" in name.lower():
                                    skills[e.instance] = {"tuning": name, "key": key, "type": LEVEL_TYPES.get(lvl, str(lvl))}
                        elif e.type == T_COMBINED:
                            for k, v in level_lists(pk.read(e).decode("utf-8", "replace")).items():
                                levels.setdefault(k, v)
            elif n in ("Strings_KOR_KR.package", "Strings_ENG_US.package"):
                target = strings_ko if "KOR" in n else strings_en
                with Package(path) as pk:
                    for e in pk.entries:
                        if e.type == T_STBL:
                            target.update(stbl(pk.read(e)))
    table = {}
    for inst, s in sorted(skills.items()):
        table[str(inst)] = {"ko": strings_ko.get(s["key"], ""), "en": strings_en.get(s["key"], ""),
                            "type": s["type"], "tuning": s["tuning"]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"levels": levels, "skills": table}, f, ensure_ascii=False, indent=1)
    print(f"{len(table)} skills, level types {list(levels)} -> {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
