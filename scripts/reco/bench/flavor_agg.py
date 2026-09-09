"""flavor_vec 집계 방식 4종 비교 — 판별력으로 고른다 (설계 2-5-1 ⑤).

    .venv/bin/python bench/flavor_agg.py

## 무엇으로 고르는가

정답 라벨이 없으므로 **정확도를 잴 수 없다.** 대신 잴 수 있는 것:

    판별력   레시피 간 flavor_vec 거리가 벌어지는가 — 뭉치면 f_taste 가 무용지물
    포화     축이 0 또는 1 에 몰리지 않는가
    커버리지 flavor 값을 받은 재료 비율

**뭉치는 것이 가장 나쁜 실패다.** 2-5-1 ①의 노이즈 실험이 보인 것처럼,
모든 레시피가 비슷한 값을 받으면 축을 몇 개로 하든 0.1 근처에서 논다.
"""
from __future__ import annotations

import glob
import io
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from features.recommend.ingest.parse import normalize                      # noqa: E402
from features.recommend.ingest.match import Dictionary, match     # noqa: E402
from features.recommend.ingest.role import judge                  # noqa: E402
from features.recommend.ingest.flavor import AXES, FlavorTable, aggregate  # noqa: E402

d = Dictionary.from_seeds()
ft = FlavorTable.from_seeds()


def recipe_items(path: str):
    r = json.load(io.open(path, encoding="utf-8"))
    out = []
    for g in r.get("ingredient_groups", []):
        for it in g.get("items", []):
            for p in normalize(it.get("raw_text") or it.get("name", "")):
                m = match(p.name, d)
                role = judge(p, m, d).role
                cat = (d.meta.get(m.ingredient_id, {}) or {}).get("category_path")
                out.append((ft.of(m.ingredient_name, cat), role, m.ingredient_name))
    return r.get("title", path), out


files = sorted(glob.glob("tests/fixtures/responses/real/*.json"))
recipes = [recipe_items(f) for f in files]

print(f"실제 크롤 {len(recipes)}건 · 집계 방식 4종\n")
MODES = ["mean", "max", "top3", "role_w"]
vecs = {m: [] for m in MODES}
for title, items in recipes:
    for m in MODES:
        vecs[m].append(aggregate([(v, r) for v, r, _ in items], m))

print(f"  {'축':<8}" + "".join(f"{m:>10}" for m in MODES) + "   (레시피 3건의 값)")
for k, ax in enumerate(AXES):
    print(f"  {ax:<8}" + "".join(
        "  " + "/".join(f"{vecs[m][i][k]:.2f}" for i in range(len(recipes))) for m in MODES))

print(f"\n  {'지표':<22}" + "".join(f"{m:>10}" for m in MODES))
print("  " + "-" * 62)
rows = {}
for m in MODES:
    V = np.array(vecs[m])
    # 판별력: 레시피 쌍 간 L2 거리 평균 (클수록 좋다)
    dist = [np.linalg.norm(V[i] - V[j]) for i in range(len(V)) for j in range(i + 1, len(V))]
    # 코사인 유사도 (작을수록 잘 갈린다)
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    cs = [float(Vn[i] @ Vn[j]) for i in range(len(V)) for j in range(i + 1, len(V))]
    rows[m] = (float(np.mean(dist)), float(np.mean(cs)), float(V.mean()), float(V.max()))
for i, label in enumerate(["쌍간 L2 거리 (↑)", "쌍간 코사인 (↓)", "평균값", "최댓값"]):
    print(f"  {label:<22}" + "".join(f"{rows[m][i]:>10.3f}" for m in MODES))

print("\n═══ 커버리지 ═══")
allv = [(v, nm) for _, items in recipes for v, _, nm in items]
nz = sum(1 for v, _ in allv if any(x > 0 for x in v))
print(f"  flavor 값을 받은 재료: {nz}/{len(allv)} = {nz/len(allv):.0%}")
zero = sorted({nm or '(미매칭)' for v, nm in allv if not any(x > 0 for x in v)})
print(f"  0 벡터인 재료: {zero}")

print("\n═══ 레시피별 role_w 결과 ═══")
for i, (title, _) in enumerate(recipes):
    v = vecs["role_w"][i]
    top = sorted(zip(AXES, v), key=lambda t: -t[1])[:3]
    print(f"  {title[:24]:<26} " + " · ".join(f"{a} {x:.2f}" for a, x in top))


# ══════════════════════════════════════════════════════════════
# 주의: 코사인 기준선 — 비음수 벡터는 원래 코사인이 높다
# ══════════════════════════════════════════════════════════════
print("\n\n═══ 🔴 코사인이 정말 갈라내고 있는가 — 무작위 기준선 대조 ═══\n")
rng = np.random.default_rng(0)


def mean_cos(V):
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    S = Vn @ Vn.T
    iu = np.triu_indices(len(V), 1)
    return float(S[iu].mean())


# 우리 값과 같은 분포의 무작위 벡터
V_role = np.array(vecs["role_w"])
scale = V_role.mean(0)
BASE = {
    "무작위 [0,1]^6 (양수만)": rng.random((300, 6)),
    "무작위 · 우리 값 스케일": rng.random((300, 6)) * (scale * 2),
    "무작위 · 중심화 후": None,
}
for name, V in BASE.items():
    if V is None:
        V = rng.random((300, 6)) * (scale * 2)
        V = V - V.mean(0)
    print(f"  {name:<26} 평균 코사인 {mean_cos(V):+.3f}")

print(f"\n  {'우리 role_w (3건)':<26} 평균 코사인 {mean_cos(V_role):+.3f}")
print("  🔴 무작위 양수 벡터와 비슷하면 f_taste 는 아무것도 구분하지 못한다는 뜻이다.")

print("\n═══ 중심화하면 달라지는가 ═══")
print(f"  {'방식':<12}{'원본 코사인':>14}{'중심화 후':>12}")
print("  " + "-" * 40)
for m in MODES:
    V = np.array(vecs[m])
    Vc = V - V.mean(0)
    print(f"  {m:<12}{mean_cos(V):>+14.3f}{mean_cos(Vc):>+12.3f}")

print("""
  중심화 = 코퍼스 평균을 뺀다. 비음수 제약이 풀려 벡터가 전 방향으로 퍼진다.
  🔴 단, taste_vec 도 **같은 평균으로** 중심화해야 한다 — 한쪽만 하면 좌표계가 어긋난다.""")
