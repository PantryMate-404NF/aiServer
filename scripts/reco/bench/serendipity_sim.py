"""우연성(serendipity) — 유저가 '아직 모르는 자기 취향'을 발견하게 하는가.

MMR 이 재는 것과 완전히 다른 축이다.
  MMR       : 이번 목록 20개 안에 비슷한 게 몇 개인가   (목록 내부 · 1회성)
  Serendipity: 유저가 시간에 걸쳐 새 영역을 만나는가      (세션 간 · 누적)

설정: 유저의 진짜 취향은 50개 클러스터에 흩어져 있는데,
      모델은 온보딩에서 본 소수 클러스터만 안다. 나머지는 발견해야 한다.
"""
import math, random

NC, NCAND, TOPK, SESS = 50, 300, 20, 40

def new_user(rng):
    # 진짜 취향: 8개 클러스터가 높고(0.55~0.9) 나머지는 낮다
    true = {c: rng.uniform(0.03, 0.18) for c in range(NC)}
    for c in rng.sample(range(NC), 8): true[c] = rng.uniform(0.55, 0.90)
    # 온보딩에서 드러나는 것은 3개뿐 — 나머지 5개는 '숨은 취향'
    known = set(rng.sample(sorted(true, key=lambda c: -true[c])[:8], 3))
    belief = {c: (true[c] if c in known else 0.12) for c in range(NC)}
    n = {c: (8 if c in known else 0) for c in range(NC)}
    return true, belief, n, {c for c in true if true[c] >= 0.55}

def catalog(rng):
    return [{"id": i, "c": rng.randrange(NC), "q": rng.betavariate(5, 3)} for i in range(4000)]

# ── 탐색 전략 ────────────────────────────────────────────────
def pick_explore(kind, cand, ranked, belief, n, rng, k=2):
    """(선택된 아이템, 각 아이템의 노출확률) — propensity 를 함께 돌려준다."""
    if kind == "none": return [], {}
    if kind == "rand_top200":                      # 현재 설계 5-3-3
        pool = ranked[:200]
        s = rng.sample(pool, min(k, len(pool)))
        return s, {d["id"]: k/len(pool) for d in s}
    if kind == "rand_all":
        s = rng.sample(cand, min(k, len(cand)))
        return s, {d["id"]: k/len(cand) for d in s}
    if kind == "ucb_cluster":                      # 결정적 — propensity 계산 불가
        t = sum(n.values()) + 1
        sc = {c: belief[c] + 1.6*math.sqrt(math.log(t)/(n[c]+1)) for c in range(NC)}
        top = sorted(sc, key=lambda c: -sc[c])
        out = []
        for c in top:
            best = max((d for d in cand if d["c"] == c), key=lambda d: d["q"], default=None)
            if best: out.append(best)
            if len(out) == k: break
        return out, {d["id"]: None for d in out}   # None = 확률 없음
    if kind == "thompson_cluster":                 # 확률적 — propensity 계산 가능
        draw = {c: rng.betavariate(1 + belief[c]*n[c] + 1, 1 + (1-belief[c])*n[c] + 1)
                for c in range(NC)}
        top = sorted(draw, key=lambda c: -draw[c])
        out = []
        for c in top:
            best = max((d for d in cand if d["c"] == c), key=lambda d: d["q"], default=None)
            if best: out.append(best)
            if len(out) == k: break
        return out, {d["id"]: 1.0/NC for d in out}      # 근사. 샘플링으로 정밀화 가능
    if kind == "far_cluster":                      # 유저 중심에서 '먼' 클러스터 가중
        w = {c: math.exp(2.5*(1 - belief[c]/max(belief.values()))) for c in range(NC)}
        tot = sum(w.values()); out = []; pr = {}
        for _ in range(k):
            c = rng.choices(range(NC), weights=[w[x] for x in range(NC)])[0]
            best = max((d for d in cand if d["c"] == c), key=lambda d: d["q"], default=None)
            if best and best not in out:
                out.append(best); pr[best["id"]] = w[c]/tot
        return out, pr
    raise ValueError(kind)

def run(kind, seed):
    rng = random.Random(seed)
    CAT = catalog(rng)
    true, belief, n, good = new_user(rng)
    cooks = 0; discovered = set(); seen_c = set()
    for t in range(SESS):
        cand = rng.sample(CAT, NCAND)
        ranked = sorted(cand, key=lambda d: -(belief[d["c"]] * d["q"]))
        ex, _ = pick_explore(kind, cand, ranked, belief, n, rng)
        base = [d for d in ranked if d not in ex][:TOPK - len(ex)]
        shown = base + ex
        for d in shown:
            seen_c.add(d["c"])
            p = true[d["c"]] * d["q"]
            hit = rng.random() < p
            n[d["c"]] += 1
            belief[d["c"]] += (1.0 if hit else 0.0 - belief[d["c"]]) * 0.0  # placeholder
            # 베이즈 갱신 (관측 평균)
            belief[d["c"]] = ((belief[d["c"]] * (n[d["c"]] - 1)) + (1 if hit else 0)) / n[d["c"]]
            if hit:
                cooks += 1
                if d["c"] in good: discovered.add(d["c"])
    return cooks, len(discovered), len(seen_c)

STRAT = [("탐색 없음 (순수 greedy)","none"),
         ("무작위 · 상위 200 중 (현재 설계)","rand_top200"),
         ("무작위 · 후보 전체 중","rand_all"),
         ("클러스터 UCB","ucb_cluster"),
         ("클러스터 Thompson","thompson_cluster"),
         ("먼 클러스터 가중 샘플링","far_cluster")]
R = 60
print(f"유저의 '좋은 클러스터' 8개 중 온보딩이 아는 것은 3개. 나머지 5개는 발견해야 한다.")
print(f"{SESS}세션 × Top-20 · 탐색 슬롯 2칸 · {R}회 평균\n")
print(f"  {'탐색 전략':<30}{'조리 수':>9}{'숨은 취향 발견':>14}{'만난 클러스터':>13}{'propensity':>12}")
print("  "+"-"*80)
for name,k in STRAT:
    a=b=c=0
    for s in range(R):
        x,y,z = run(k, 1000+s); a+=x; b+=y; c+=z
    prop = {"none":"—","ucb_cluster":"🔴 불가(결정적)"}.get(k,"✅ 계산 가능")
    print(f"  {name:<30}{a/R:>9.1f}{b/R:>13.2f}/8{c/R:>13.1f}{prop:>15}")
