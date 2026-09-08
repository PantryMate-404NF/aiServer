"""k-means 배치가 없으면 어떻게 되는가 — 8주 현실(유저당 20세션) 기준.

`cluster_id` 가 NULL 이면 세 가지가 동시에 꺼진다.
  ① 우연성 탐색  → 균등 무작위로 폴백
  ② 5-3-2 캡 대체 → 분류축 캡(한식 70~80% 편중)으로 남음
  ③ ③ 폴백 다양성 → 없음 (점수순 그대로)
셋을 따로 재야 "빼도 되는가"에 답할 수 있다.
"""
import math, random
from collections import Counter
exec(open("/Users/kdtmacbook/Desktop/통합_프로젝트/bench/serendipity_sim.py").read().split("# ── 탐색 전략")[0])

def explore(kind, cand, ranked, belief, n, rng, k=2):
    if kind == "none": return []
    if kind == "uniform": return rng.sample(ranked[:200], min(k, len(ranked[:200])))
    if kind == "mixed":                       # 균등 1 + Thompson 1
        out = rng.sample(ranked[:200], 1)
        draw = {c: rng.betavariate(1+belief[c]*n[c]+1, 1+(1-belief[c])*n[c]+1) for c in range(NC)}
        for c in sorted(draw, key=lambda x: -draw[x]):
            b = max((d for d in cand if d["c"] == c and d not in out),
                    key=lambda d: d["q"], default=None)
            if b: out.append(b); break
        return out
    raise ValueError(kind)

def run(kind, seed, sess):
    rng = random.Random(seed); CAT = catalog(rng)
    true, belief, n, good = new_user(rng)
    hidden = good - {c for c in good if n[c] > 0}
    cooks = 0; found = set()
    for t in range(sess):
        cand = rng.sample(CAT, NCAND)
        ranked = sorted(cand, key=lambda d: -(belief[d["c"]] * d["q"]))
        ex = explore(kind, cand, ranked, belief, n, rng)
        for d in [x for x in ranked if x not in ex][:TOPK-len(ex)] + ex:
            hit = rng.random() < true[d["c"]] * d["q"]; n[d["c"]] += 1
            belief[d["c"]] = ((belief[d["c"]]*(n[d["c"]]-1)) + (1 if hit else 0))/n[d["c"]]
            if hit:
                cooks += 1
                if d["c"] in hidden: found.add(d["c"])
    return cooks, len(found)

R = 120
print("① 우연성 — 8주 현실은 유저당 20세션이다\n")
print(f"  {'세션':>5}{'k-means 없음(균등)':>22}{'k-means 있음(혼합)':>22}{'조리 차이':>11}{'숨은취향 차이':>15}")
print("  " + "-" * 76)
for sess in (10, 20, 40, 80):
    a = b = fa = fb = 0
    for s in range(R):
        c1, f1 = run("uniform", 7000+s, sess); c2, f2 = run("mixed", 7000+s, sess)
        a += c1; b += c2; fa += f1; fb += f2
    print(f"  {sess:>5}{f'{a/R:.1f} / {fa/R:.2f}':>22}{f'{b/R:.1f} / {fb/R:.2f}':>22}"
          f"{b/R-a/R:>+11.1f}{fb/R-fa/R:>+15.2f}")
print("  (조리 수 / 숨은취향 발견)")

# ── ②③ 은 세션 수와 무관하다 — 1회 요청에서 바로 나온다 ──────
print("\n\n② 캡 대체 · ③ 폴백 다양성 — 세션 수와 무관하게 즉시 나온다")
POOL_ING = list(range(240)); STAPLE = set(random.Random(0).sample(POOL_ING, 18))
def build2(K, rng):
    core = {c: rng.sample([g for g in POOL_ING if g not in STAPLE], 8) for c in range(K)}
    boost = {c: rng.betavariate(2, 3) for c in range(K)}
    P = []
    for i in range(500):
        c = rng.choices(range(K), weights=[4 if x < 4 else 1 for x in range(K)])[0]
        ing = (set(rng.sample(core[c], 7)) | set(rng.sample(POOL_ING, rng.randint(1, 3)))
               | set(rng.sample(list(STAPLE), 3)))
        P.append({"id": i, "c": c, "g": 0 if (c % 10) < 7 else 1 + c % 6,   # 분류축: 70% 편중
                  "ing": ing, "score": max(.02, min(.99, .35+.45*boost[c]+rng.gauss(0, .12)))})
    return P
def idf(P):
    df = Counter(g for x in P for g in x["ing"]); N = len(P)
    return {g: math.log(N/(1+df[g])) for g in POOL_ING}
def wj(a, b, W):
    A, B = a["ing"]-STAPLE, b["ing"]-STAPLE
    u = sum(W[g] for g in A|B)
    return sum(W[g] for g in A & B)/u if u else 0.
def quota(P, key, cap):
    sel = []; used = {}
    for d in sorted(P, key=lambda x: -x["score"]):
        k = d[key]
        if used.get(k, 0) < cap: sel.append(d); used[k] = used.get(k, 0)+1
        if len(sel) == 20: return sel
    for d in sorted(P, key=lambda x: -x["score"]):
        if d not in sel: sel.append(d)
        if len(sel) == 20: break
    return sel
rows = {k: [0.]*3 for k in ["점수순 (다양화 없음)", "분류축 캡 (k-means 없을 때)", "클러스터 쿼터 (k-means 있을 때)"]}
for s in range(120):
    rng = random.Random(9000+s); P = build2(50, rng); W = idf(P)
    base = sum(x["score"] for x in sorted(P, key=lambda x: -x["score"])[:20])
    for name, sel in [("점수순 (다양화 없음)", sorted(P, key=lambda x: -x["score"])[:20]),
                      ("분류축 캡 (k-means 없을 때)", quota(P, "g", 3)),
                      ("클러스터 쿼터 (k-means 있을 때)", quota(P, "c", 2))]:
        ps = [wj(a, b, W) for i, a in enumerate(sel) for b in sel[i+1:]]
        r = rows[name]
        r[0] += 1-sum(ps)/len(ps); r[1] += sum(1 for x in ps if x >= .5)
        r[2] += sum(d["score"] for d in sel)/base
print(f"  {'':<34}{'ILD':>8}{'근중복 쌍':>10}{'점수 유지':>10}")
for k, r in rows.items():
    print(f"  {k:<34}{r[0]/120:>8.3f}{r[1]/120:>10.1f}{r[2]/120:>10.1%}")
