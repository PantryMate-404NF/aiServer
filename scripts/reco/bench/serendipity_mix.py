import math, random, time
exec(open("/Users/kdtmacbook/Desktop/통합_프로젝트/bench/serendipity_strategies.py").read().split("R=90")[0])

def explore2(kind,cand,ranked,belief,n,rng):
    if kind=="hybrid":          # 균등 1칸(OPE용) + Thompson 1칸(우연성용)
        a=explore("rand200",cand,ranked,belief,n,rng,1)
        b=explore("thompson",cand,ranked,belief,n,rng,1)
        return a+[x for x in b if x not in a]
    return explore(kind,cand,ranked,belief,n,rng,2)

def run2(kind,seed,sess=40):
    rng=random.Random(seed); CAT=catalog(rng)
    true,belief,n,good=new_user(rng); hidden=good-{c for c in good if n[c]>0}
    cooks=0; found=set(); cov=set()
    for t in range(sess):
        cand=rng.sample(CAT,NCAND)
        ranked=sorted(cand,key=lambda d:-(belief[d["c"]]*d["q"]))
        ex=explore2(kind,cand,ranked,belief,n,rng)
        shown=[d for d in ranked if d not in ex][:TOPK-len(ex)]+ex
        for d in ex: cov.add(d["c"])
        for d in shown:
            hit=rng.random()<true[d["c"]]*d["q"]; n[d["c"]]+=1
            belief[d["c"]]=((belief[d["c"]]*(n[d["c"]]-1))+(1 if hit else 0))/n[d["c"]]
            if hit:
                cooks+=1
                if d["c"] in hidden: found.add(d["c"])
    return cooks,len(found),len(cov)

R=90
print("탐색 슬롯 2칸을 어떻게 나눌까\n")
print(f"  {'구성':<34}{'숨은취향':>10}{'누적 조리':>11}{'탐색이 닿은 클러스터':>20}")
print("  "+"-"*78)
for name,k in [("무작위 2칸 (현재 설계)","rand200"),("Thompson 2칸","thompson"),
               ("🔑 균등 1칸 + Thompson 1칸","hybrid")]:
    c=f=v=0
    for i in range(R):
        a,b,cv=run2(k,5000+i); c+=a; f+=b; v+=cv
    print(f"  {name:<34}{f'{f/R:.2f}/5':>10}{c/R:>11.1f}{v/R:>20.1f}")

print("\n\n── Thompson 의 propensity 를 실제로 계산할 수 있는가 ────")
NCL=50
def thompson_propensity(belief,n,mc=200,k=2,seed=0):
    """P(클러스터 c 가 탐색 슬롯에 뽑힐 확률) — 몬테카를로. 닫힌 해가 없다."""
    rng=random.Random(seed); cnt=[0]*NCL
    for _ in range(mc):
        draw=[rng.betavariate(1+belief[c]*n[c]+1, 1+(1-belief[c])*n[c]+1) for c in range(NCL)]
        for c in sorted(range(NCL),key=lambda x:-draw[x])[:k]: cnt[c]+=1
    return [x/mc for x in cnt]

rng=random.Random(1)
belief={c: rng.betavariate(2,5) for c in range(NCL)}
n={c: rng.randint(0,60) for c in range(NCL)}
t0=time.perf_counter(); p=thompson_propensity(belief,n,mc=200); ms=(time.perf_counter()-t0)*1000
print(f"  MC 200회 · 클러스터 50개 → {ms:.1f}ms   (설계 목표 p95 200ms 대비 {ms/200:.1%})")
print(f"  Σp = {sum(p):.3f} (= k=2)  ·  최대 {max(p):.3f}  ·  0 인 클러스터 {sum(1 for x in p if x==0)}개")
print(f"  🔴 propensity=0 인 클러스터가 있으면 그 영역은 IPS 로 영원히 평가 불가")
for mc in (50,200,1000):
    t0=time.perf_counter(); pp=thompson_propensity(belief,n,mc=mc); m=(time.perf_counter()-t0)*1000
    zero=sum(1 for x in pp if x==0)
    print(f"     MC {mc:>5} → {m:>6.1f}ms · 0 인 클러스터 {zero:>2}개 · 최소 비영 {min([x for x in pp if x>0]):.4f}")
