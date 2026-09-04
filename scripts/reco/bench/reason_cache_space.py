"""사유 캐시가 성립하는가 — 키 공간을 실측한다.

온라인 LLM 사유(0.7 req/s 상한)를 캐시로 구제할 수 있는지는 **캐시 키가 몇 개나
생기는가**에 달렸다. 키가 폭발하면 히트가 안 나고 캐시는 무의미하다.

파이프라인이 실제로 쓰는 top_reasons() 를 그대로 돌려서 센다.
"""
from __future__ import annotations

import random
import sys
from collections import Counter

sys.path.insert(0, ".")
from features.recommend.enums import DEFAULT_WEIGHTS, FEATURE_KEYS, UNAVAILABLE_FEATURES  # noqa: E402
from features.recommend.engine.rank import feature_stats
from features.recommend.stage import ScoredCandidate, top_reasons  # noqa: E402

rng = random.Random(20260831)
N_REQ = 400          # 시뮬 요청 수
N_CAND = 500         # 요청당 후보 (설계 기준)
TOP_N = 10           # 노출 수
N_RECIPES = 230_000

# 🔴 레시피를 4.4만에서 균등추출하면 겹칠 수가 없어 히트율이 0 으로 나온다 — 시뮬 결함이다.
#    실제 Retrieval 은 냉장고 재료로 거르고, 한국 가정의 재료는 서로 크게 겹친다.
#    따라서 노출 레시피는 심하게 편중된다. 멱법칙(Zipf)으로 모델링한다.
#    ZIPF_A 가 클수록 집중이 심하다. 1.0 = 전형적 Zipf.
ZIPF_A = 1.0
POOL = 20_000        # 냉장고 필터를 통과하는 현실적 후보 풀

_zipf_w = [1.0 / (i + 1) ** ZIPF_A for i in range(POOL)]
_zipf_cum = []
_acc = 0.0
for w in _zipf_w:
    _acc += w
    _zipf_cum.append(_acc)
_TOT = _acc


def pick_recipe() -> int:
    import bisect
    return bisect.bisect(_zipf_cum, rng.random() * _TOT)


def mk_features() -> dict[str, float | None]:
    f: dict[str, float | None] = {}
    for k in FEATURE_KEYS:
        if k in UNAVAILABLE_FEATURES:
            f[k] = None
        elif k == "f_coverage":
            f[k] = rng.uniform(0.85, 1.0)          # Stage① 이 걸러서 높게 몰린다
        elif k == "f_expiring":
            f[k] = 1.0 if rng.random() < 0.12 else 0.0   # 뾰족하다
        elif k == "f_missing":
            f[k] = rng.choice([0.0, 0.0, 0.5, 1.0])
        else:
            f[k] = rng.random()
    return f


combo_only = Counter()       # (피처조합) 만
combo_recipe = Counter()     # (레시피, 피처조합)
served = Counter()

for _ in range(N_REQ):
    cands = [
        ScoredCandidate(recipe_id=pick_recipe(), features=mk_features(),
                        score=rng.random(), missing_count=rng.randrange(3),
                        coverage=rng.uniform(0.85, 1.0))
        for _ in range(N_CAND)
    ]
    st = feature_stats(cands)
    ranked = sorted(cands, key=lambda c: c.score, reverse=True)[:TOP_N]
    for c in ranked:
        combo = tuple(top_reasons(c, DEFAULT_WEIGHTS, st, n=2))
        combo_only[combo] += 1
        combo_recipe[(c.recipe_id, combo)] += 1
        served[c.recipe_id] += 1

exp = N_REQ * TOP_N
print(f"시뮬: 요청 {N_REQ} × 후보 {N_CAND} → 노출 {exp}건\n")

print("── 키 A: 피처조합만 ──")
print(f"  고유 키 {len(combo_only)}개   상위 5:")
for k, v in combo_only.most_common(5):
    print(f"    {v:5d} ({v/exp:5.1%})  {'+'.join(k) or '(없음)'}")

print(f"\n── 키 B: (레시피 × 피처조합) — 실제로 필요한 키 ──")
print(f"  고유 키 {len(combo_recipe)}개 / 노출 {exp}건")
reuse = sum(v - 1 for v in combo_recipe.values())
print(f"  재사용 가능 {reuse}건 → **히트율 상한 {reuse/exp:.1%}**")

print(f"\n── 노출 레시피 집중도 ──")
print(f"  고유 레시피 {len(served)}개 (풀 {POOL:,} 중 {len(served)/POOL:.1%})")
top100 = sum(v for _, v in served.most_common(100))
print(f"  상위 100개가 노출의 {top100/exp:.1%}")

print(f"\n{'='*62}")
print("해석: 키 B 의 히트율이 캐시의 실효 상한이다.")
print("소비기한 D-N 은 매일 바뀌므로 실제 TTL 은 24시간을 못 넘는다.")
