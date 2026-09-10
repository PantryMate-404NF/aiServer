"""정성적 강도(단위·제목·순서)를 넣으면 판별력이 좋아지는가 (설계 2-5-1 ⑤).

    .venv/bin/python bench/flavor_intensity.py

수량 환산(P5)을 보류한 상태에서 "많이 들어감"을 근사한다.
**넣어서 판별력이 안 좋아지면 넣을 이유가 없다** — 복잡도만 는다.
"""
from __future__ import annotations

import glob
import io
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from features.recommend.ingest.parse import normalize                                # noqa: E402
from features.recommend.ingest.match import Dictionary, match               # noqa: E402
from features.recommend.ingest.role import judge                            # noqa: E402
from features.recommend.ingest.flavor import AXES, FlavorTable, ROLE_WEIGHT # noqa: E402
from features.recommend.ingest.flavor import intensity                   # noqa: E402

d = Dictionary.from_seeds(); ft = FlavorTable.from_seeds()


def parse(path):
    r = json.load(io.open(path, encoding="utf-8")); title = r["title"]; out = []
    for g in r.get("ingredient_groups", []):
        for it in g.get("items", []):
            for p in normalize(it.get("raw_text") or it.get("name", "")):
                m = match(p.name, d); role = judge(p, m, d).role
                cat = (d.meta.get(m.ingredient_id, {}) or {}).get("category_path")
                out.append((p, m.ingredient_name, ft.of(m.ingredient_name, cat), role))
    return title, out


recipes = [parse(f) for f in sorted(glob.glob("tests/fixtures/responses/real/*.json"))]


def agg(title, items, use_role, use_int):
    n = len(items); num = np.zeros(6); den = 0.0
    for i, (p, nm, v, role) in enumerate(items):
        w = ROLE_WEIGHT.get(role, 1.0) if use_role else 1.0
        if use_int:
            w *= intensity(p, nm, title, i, n, role)
        num += np.array(v) * w; den += w
    return num / (den or 1.0)


MODES = [("① 단순 평균", False, False), ("② 역할 가중", True, False),
         ("③ 강도만", False, True), ("④ 역할+강도", True, True)]


def stats(V):
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    Vc = V - V.mean(0); Vcn = Vc / (np.linalg.norm(Vc, axis=1, keepdims=True) + 1e-9)
    iu = np.triu_indices(len(V), 1)
    return float((Vn @ Vn.T)[iu].mean()), float((Vcn @ Vcn.T)[iu].mean())


print(f"실제 크롤 {len(recipes)}건 · 집계 4종\n")
print(f"  {'방식':<16}{'원본 코사인 (↓)':>17}{'중심화 후 (↓)':>15}")
print("  " + "-" * 50)
res = {}
for name, ur, ui in MODES:
    V = np.array([agg(t, its, ur, ui) for t, its in recipes])
    res[name] = V
    a, b = stats(V)
    print(f"  {name:<16}{a:>+17.3f}{b:>+15.3f}")

print("\n  🔴 무작위 양수 벡터의 코사인 기준선 ≈ +0.77 — 그보다 낮아야 갈라내는 것이다.")

print("\n\n═══ ④ 역할+강도 의 실제 산출 ═══")
for i, (t, _) in enumerate(recipes):
    v = res["④ 역할+강도"][i]
    top = sorted(zip(AXES, v), key=lambda x: -x[1])[:3]
    print(f"  {t[:24]:<26} " + " · ".join(f"{a} {x:.2f}" for a, x in top))

print("\n═══ ②(역할만) 대비 ④(역할+강도) 가 무엇을 바꿨나 ═══")
for i, (t, _) in enumerate(recipes):
    d2, d4 = res["② 역할 가중"][i], res["④ 역할+강도"][i]
    diff = sorted(zip(AXES, d4 - d2), key=lambda x: -abs(x[1]))[:3]
    print(f"  {t[:20]:<22} " + " · ".join(f"{a} {x:+.2f}" for a, x in diff))
