"""두 덤프(JSON)를 비교한다. float 은 허용 오차 안에서 같으면 통과."""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
diffs = []


def walk(x, y, path):
    if len(diffs) > 40:
        return
    if isinstance(x, float) or isinstance(y, float):
        if not (isinstance(x, (int, float)) and isinstance(y, (int, float)) and abs(x - y) <= 1e-6 * max(1, abs(x))):
            diffs.append((path, x, y))
    elif type(x) is not type(y):
        diffs.append((path, x, y))
    elif isinstance(x, dict):
        for k in sorted(set(x) | set(y)):
            if k not in x or k not in y:
                diffs.append((f"{path}.{k}", x.get(k, "<없음>"), y.get(k, "<없음>")))
            else:
                walk(x[k], y[k], f"{path}.{k}")
    elif isinstance(x, list):
        if len(x) != len(y):
            diffs.append((path + ".len", len(x), len(y)))
        for i, (p, q) in enumerate(zip(x, y)):
            walk(p, q, f"{path}[{i}]")
    elif x != y:
        diffs.append((path, x, y))


walk(a, b, "")
n = len(a) if isinstance(a, (list, dict)) else 1
if diffs:
    print(f"불일치 {len(diffs)}건 (항목 {n}개 중)")
    for p, x, y in diffs[:25]:
        print(" ", p, "\n    py  :", str(x)[:300], "\n    rust:", str(y)[:300])
    sys.exit(1)
print(f"일치 (최상위 항목 {n}개)")
