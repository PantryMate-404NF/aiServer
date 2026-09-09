"""탐색 품질을 올릴 수 있는가 — 현재 혼합 정책의 구조적 낭비를 찾는다.

현재 `mixed_exploration` 은 클러스터를 **확률적으로** 고른 뒤
그 안에서 아이템을 **결정적으로**(최고 품질 1건) 고른다. 그래서:

  - 같은 클러스터의 2위 이하는 Thompson 경로로 영원히 안 뽑힌다
  - propensity 가 클러스터 top 아이템에 전부 몰린다 (IPS 분산 ↑)
  - 탐색 다양성이 클러스터 수(50)로 상한이 걸린다

세 가지를 잰다:
  A. uniform_share 를 낮추면? (0.5 → 0.25) — support 를 유지하면서 성능
  B. 클러스터 **안에서도** 확률적으로 고르면? (품질 softmax)
  C. 둘 다

지표는 셋이다. 발견·조리는 유저 가치, propensity 최솟값은 **IPS 분산의 대리**다 —
작을수록 1/p 가중이 폭발한다.
"""
import math
import random
import statistics

exec(open("bench/serendipity_sim.py").read().split("# ── 탐색 전략")[0])

TOPK, POOL = 20, 200


def pick(kind, cand, ranked, belief, n, rng, k=2, ushare=0.5, temp=0.0):
    """탐색 k칸을 고르고 (선택, 아이템별 노출확률) 을 돌려준다."""
    pool = ranked[:POOL]
    if not pool:
        return [], {}
    n_u = max(1, round(k * ushare)) if ushare > 0 else 0
    n_t = k - n_u

    chosen, taken = [], set()
    for d in rng.sample(pool, min(n_u, len(pool))):
        chosen.append(d); taken.add(id(d))

    draw = {c: rng.betavariate(1 + belief[c] * n[c] + 1, 1 + (1 - belief[c]) * n[c] + 1)
            for c in range(NC)}
    if kind == "floor":
        # 🔑 슬롯을 나누지 않는다. **두 칸 다 Thompson** 으로 뽑되
        #    클러스터 선택 확률에 바닥 ε 를 깔아 support 를 보장한다.
        #      p(c) = (1-ε)·Thompson(c) + ε/|C|
        cs2 = sorted({d["c"] for d in pool})
        chosen, taken = [], set()
        for _ in range(k):
            if rng.random() < ushare:          # ushare 를 ε 로 재해석
                c = rng.choice(cs2)
            else:
                c = max((x for x in cs2), key=lambda x: draw[x])
                draw[c] = -1                    # 뽑힌 클러스터는 다음 턴에서 제외
            inc = [d for d in pool if d["c"] == c and id(d) not in taken]
            if not inc:
                continue
            b = (rng.choices(inc, weights=[math.exp(d["q"] / temp) for d in inc])[0]
                 if temp > 0 else max(inc, key=lambda d: d["q"]))
            chosen.append(b); taken.add(id(b))
        p_floor = ushare / max(1, len(cs2))
        prop = {}
        for c in cs2:
            inc = [d for d in pool if d["c"] == c]
            pc = p_floor * k
            if temp > 0:
                w = [math.exp(d["q"] / temp) for d in inc]; tot = sum(w)
                for d, wi in zip(inc, w):
                    prop[id(d)] = min(1.0, pc * wi / tot)
            else:
                top = max(inc, key=lambda d: d["q"])
                for d in inc:
                    prop[id(d)] = min(1.0, pc if d is top else pc * 0.05)
        for d in pool:
            prop.setdefault(id(d), p_floor)
        return chosen[:k], prop

    for c in sorted(draw, key=lambda x: -draw[x]):
        if len(chosen) >= k:
            break
        inc = [d for d in pool if d["c"] == c and id(d) not in taken]
        if not inc:
            continue
        if temp > 0:                      # B·C — 클러스터 안에서도 확률적으로
            w = [math.exp(d["q"] / temp) for d in inc]
            b = rng.choices(inc, weights=w)[0]
        else:                             # 현재 — 결정적으로 최고 품질
            b = max(inc, key=lambda d: d["q"])
        chosen.append(b); taken.add(id(b))

    # ── propensity (닫힌 해로 근사) ─────────────────────────
    p_u = n_u / len(pool) if n_u else 0.0
    # 클러스터가 뽑힐 확률은 MC 대신 균등 근사 — 상대 비교가 목적이라 충분하다
    cs = sorted({d["c"] for d in pool})
    p_c = (n_t / len(cs)) if (n_t and cs) else 0.0
    prop = {}
    for c in cs:
        inc = [d for d in pool if d["c"] == c]
        if temp > 0:
            w = [math.exp(d["q"] / temp) for d in inc]
            tot = sum(w)
            for d, wi in zip(inc, w):
                prop[id(d)] = min(1.0, p_u + p_c * wi / tot)
        else:
            top = max(inc, key=lambda d: d["q"])
            for d in inc:
                prop[id(d)] = min(1.0, p_u + (p_c if d is top else 0.0))
    for d in pool:
        prop.setdefault(id(d), p_u)
    return chosen[:k], prop


def run(kind, seed, ushare, temp, sess=40):
    rng = random.Random(seed)
    CAT = catalog(rng)
    true, belief, n, good = new_user(rng)
    hidden = good - {c for c in good if n[c] > 0}
    cooks, found, cov, pmins = 0, set(), set(), []
    for _ in range(sess):
        cand = rng.sample(CAT, NCAND)
        ranked = sorted(cand, key=lambda d: -(belief[d["c"]] * d["q"]))
        ex, prop = pick(kind, cand, ranked, belief, n, rng, 2, ushare, temp)
        if prop:
            pmins.append(min(prop.values()))
        shown = [d for d in ranked if d not in ex][:TOPK - len(ex)] + ex
        for d in ex:
            cov.add(d["c"])
        for d in shown:
            hit = rng.random() < true[d["c"]] * d["q"]
            n[d["c"]] += 1
            belief[d["c"]] = ((belief[d["c"]] * (n[d["c"]] - 1)) + (1 if hit else 0)) / n[d["c"]]
            if hit:
                cooks += 1
                if d["c"] in hidden:
                    found.add(d["c"])
    return cooks, len(found), len(cov), (statistics.mean(pmins) if pmins else 0.0)


R = 90
VARIANTS = [
    ("현재 — 슬롯분할 균등1 + Thompson1",              "mix",   0.5,  0.0),
    ("B. 클러스터 안도 확률적 (softmax τ=0.15)",       "mix",   0.5,  0.15),
    ("🔑 D. 확률 바닥 섞기 ε=0.25 — 2칸 다 Thompson",  "floor", 0.25, 0.0),
    ("🔑 E. D + 클러스터내 softmax",                   "floor", 0.25, 0.15),
    ("   F. 바닥을 더 낮게 ε=0.10",                    "floor", 0.10, 0.15),
    ("(참고) Thompson 2칸 — support 붕괴",             "mix",   0.0,  0.0),
]

print(f"세션 40 × 유저 {R}명\n")
print(f"  {'구성':<44}{'숨은취향':>9}{'조리':>8}{'닿은클러스터':>13}{'p 최솟값':>11}")
print("  " + "-" * 87)
base = None
for name, kd, us, tp in VARIANTS:
    c = f = v = 0.0
    pm = []
    for i in range(R):
        a, b, cv, p = run(kd, 5000 + i, us, tp)
        c += a; f += b; v += cv; pm.append(p)
    mp = statistics.mean(pm)
    if base is None:
        base = (f / R, c / R, mp)
    d_f = (f / R) - base[0]
    print(f"  {name:<44}{f/R:>7.2f}/5{c/R:>8.1f}{v/R:>13.1f}{mp:>11.5f}"
          f"{'' if base is None else f'   {d_f:+.2f}'}")

print("\n  p 최솟값 = 그 요청에서 가장 낮은 노출확률. **IPS 분산의 대리**다 —")
print("  작을수록 1/p 가중이 폭발해 추정이 불안정해진다.")
