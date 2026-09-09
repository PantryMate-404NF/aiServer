import math, random
exec(open("/Users/kdtmacbook/Desktop/통합_프로젝트/bench/serendipity_sim.py").read().split("# ── 탐색 전략")[0])

def explore(kind, cand, ranked, belief, n, rng, k=2):
    if kind=="none": return []
    if kind=="rand200":
        return rng.sample(ranked[:200], min(k,len(ranked[:200])))
    if kind=="far":            # 거리만 — 관련성 무시
        w={c: math.exp(2.5*(1-belief[c]/max(belief.values()))) for c in range(NC)}
        out=[]
        for _ in range(k):
            c=rng.choices(range(NC),weights=[w[x] for x in range(NC)])[0]
            b=max((d for d in cand if d["c"]==c),key=lambda d:d["q"],default=None)
            if b and b not in out: out.append(b)
        return out
    if kind=="far_gated":      # 🔑 거리 × 관련성 — 품질 하한을 건다
        qcut=sorted((d["q"] for d in cand),reverse=True)[len(cand)//3]   # 상위 33% 품질만
        pool=[d for d in cand if d["q"]>=qcut]
        if not pool: pool=cand
        w={c: math.exp(2.5*(1-belief[c]/max(belief.values()))) for c in range(NC)}
        out=[]
        for _ in range(k*4):
            if len(out)==k: break
            c=rng.choices(range(NC),weights=[w[x] for x in range(NC)])[0]
            b=max((d for d in pool if d["c"]==c),key=lambda d:d["q"],default=None)
            if b and b not in out: out.append(b)
        return out
    if kind=="thompson":
        draw={c: rng.betavariate(1+belief[c]*n[c]+1, 1+(1-belief[c])*n[c]+1) for c in range(NC)}
        out=[]
        for c in sorted(draw,key=lambda x:-draw[x]):
            b=max((d for d in cand if d["c"]==c),key=lambda d:d["q"],default=None)
            if b: out.append(b)
            if len(out)==k: break
        return out
    raise ValueError(kind)

def run(kind,seed,k=2,sess=40):
    rng=random.Random(seed); CAT=catalog(rng)
    true,belief,n,good=new_user(rng)
    hidden=good-{c for c in good if n[c]>0}
    cooks=0; found=set(); ex_shown=0; ex_hit=0
    for t in range(sess):
        cand=rng.sample(CAT,NCAND)
        ranked=sorted(cand,key=lambda d:-(belief[d["c"]]*d["q"]))
        ex=explore(kind,cand,ranked,belief,n,rng,k)
        shown=[d for d in ranked if d not in ex][:TOPK-len(ex)]+ex
        for d in shown:
            hit=rng.random()<true[d["c"]]*d["q"]; n[d["c"]]+=1
            belief[d["c"]]=((belief[d["c"]]*(n[d["c"]]-1))+(1 if hit else 0))/n[d["c"]]
            if d in ex:
                ex_shown+=1; ex_hit+=hit
            if hit:
                cooks+=1
                if d["c"] in hidden: found.add(d["c"])
    return cooks,len(found),len(hidden),ex_hit,ex_shown

R=90
print("🔑 우연성 = 관련성 × 의외성.  '멀기만' 하면 그냥 안 좋은 걸 보여주는 것이다.\n")
print(f"  {'탐색 슬롯 채우는 법':<26}{'숨은취향':>10}{'탐색슬롯 적중률':>15}{'누적 조리':>10}{'propensity':>12}")
print("  "+"-"*76)
for name,k_ in [("탐색 없음","none"),("무작위 상위200 (현재)","rand200"),
                ("먼 클러스터 (거리만)","far"),("먼 클러스터 + 품질하한","far_gated"),
                ("클러스터 Thompson","thompson")]:
    c=f=h=s=t=0
    for i in range(R):
        a,b,tt,eh,es=run(k_,4000+i); c+=a; f+=b; t+=tt; h+=eh; s+=es
    rate=f"{h/s:.1%}" if s else "—"
    prop={"none":"—","far":"✅","far_gated":"✅","rand200":"✅","thompson":"✅"}[k_]
    print(f"  {name:<26}{f'{f/R:.2f}/{t/R:.0f}':>10}{rate:>15}{c/R:>10.1f}{prop:>12}")

print("\n\n── 탐색 슬롯을 몇 칸 줄까 (Thompson) ─────────────────")
print(f"  {'슬롯':>5}{'숨은취향':>10}{'누적 조리':>11}{'40세션 손익':>13}")
b=None
for k in (0,1,2,3,4,6):
    c=f=t=0
    kind="none" if k==0 else "thompson"
    for i in range(R):
        a,x,tt,_,_=run(kind,4000+i,k=max(k,1)); c+=a; f+=x; t+=tt
    if b is None: b=c/R
    print(f"  {k:>5}{f'{f/R:.2f}/{t/R:.0f}':>10}{c/R:>11.1f}{c/R-b:>+13.1f}")
