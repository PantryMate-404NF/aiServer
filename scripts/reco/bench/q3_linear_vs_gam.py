"""Q3 — 선형 가중합을 언제 버리는가. (설계 01 5-2-6 · numpy 필요)

    .venv/bin/python bench/q3_linear_vs_gam.py          # 전체 (약 40초)
    .venv/bin/python bench/q3_linear_vs_gam.py --quick  # 축약 (약 8초)

## 무엇을 재는가

'진짜 선호'에 선형이 표현할 수 없는 3가지를 넣고, 쌍대비교 라벨로 학습해 NDCG@10 을 잰다.
    ① 비단조  조리시간 25분 최적
    ② 포화    coverage 는 sqrt
    ③ 교차    taste × sqrt(coverage)

## 🔴 이 파일에는 자기정정의 기록이 들어 있다

처음 결론은 `cheat`(= 정답 함수를 알고 만든 변환)로 냈고 "도메인 변환이 GAM 을 압도한다"였다.
그건 부정행위에 가깝다 — 실무에서는 정답을 모른다.
`blind`(모양만 알고 위치는 모름)로 다시 재야 정직한 비교가 된다.

그리고 두 번째 의심: **내 GAM 이 부당하게 약한 것 아닌가?**
초판 GAM 은 등간격 원핫 + IRLS 였는데, 실제 LightGBM 은 적응적 분할 + 그래디언트 부스팅이다.
그래서 `gam_boost` (pairwise 로지스틱 손실 위의 depth-1 부스팅 스텀프)를 함께 돌린다.
이것이 num_leaves=2 LightGBM 에 가장 가까운 재현이다.
"""
from __future__ import annotations
import sys, numpy as np

QUICK = "--quick" in sys.argv
#: 라벨 노이즈. 🔴 저장소 어디에도 근거가 없는 자유 상수다 — 결론이 여기 크게 좌우된다.
#:   temp 가 작을수록 사람의 쌍대비교가 일관적이라는 뜻.
TEMP = float(__import__("os").environ.get("Q3TEMP", "0.10"))
#: MODE=ablate 면 블라인드 변환의 항을 하나씩 빼서 **무엇이 이득의 원천인지** 가른다.
MODE = __import__("os").environ.get("Q3MODE", "base")
assert MODE in ("base", "ablate"), (
    f"Q3MODE={MODE!r} 미지원 — 'corr'(상관 세계) 등은 폭주 정리 때 소실됐다 (W3 복원 예정). "
    "지원: base | ablate. 잘못된 MODE 가 조용히 base 로 도는 것을 막는다")
rng = np.random.default_rng(20260828)
F = ["coverage","expiring","taste","ing_pref","popularity","time","season","cuisine"]

def sample(n):
    return np.column_stack([
        rng.beta(5,2,n),                                          # coverage
        np.where(rng.random(n) < .25, rng.random(n), 0.0),        # expiring (75% 가 0)
        rng.beta(4,3,n), rng.beta(4,4,n), rng.beta(2,5,n),
        rng.random(n), rng.beta(2,2,n), (rng.random(n) < .75).astype(float)])

def utility(X):
    cov,exp,tas,ing,pop,tim,sea,cui = X.T
    m = 5 + tim*85
    return (0.24*np.sqrt(cov) + 0.15*exp + 0.16*tas*(0.4+0.6*np.sqrt(cov))
            + 0.11*ing + 0.10*pop + 0.12*np.exp(-((m-25)**2)/(2*20**2))
            + 0.02*sea + 0.04*cui)

POOL = sample(6000); U = utility(POOL)

# ── 설계행렬 ─────────────────────────────────────────────────
def phi_lin(X):   return X
def phi_cheat(X):                       # ❌ 정답을 알고 만든 것
    cov,exp,tas,ing,pop,tim,sea,cui = X.T; m = 5+tim*85
    return np.column_stack([np.sqrt(cov),exp,tas,ing,pop,
                            np.exp(-((m-25)**2)/(2*20**2)),sea,cui,np.sqrt(cov)*tas])
def phi_blind(X):                       # ✅ 모양만 알고 위치는 모름
    cov,exp,tas,ing,pop,tim,sea,cui = X.T
    return np.column_stack([cov,np.sqrt(cov),exp,tas,ing,pop,
                            (tim<.25).astype(float),((tim>=.25)&(tim<.55)).astype(float),
                            (tim>=.55).astype(float),sea,cui,cov*tas])
def make_bins(nb):
    cut = [np.quantile(POOL[:,j], np.linspace(0,1,nb+1)[1:-1]) for j in range(len(F))]
    def phi(X):
        return np.concatenate([np.eye(nb)[np.searchsorted(cut[j],X[:,j])] for j in range(len(F))],1)
    return phi

# ── Bradley-Terry (절편 없는 로지스틱, IRLS) ──────────────────
def fit_irls(D, y, lam, iters=25):
    k = D.shape[1]; w = np.zeros(k)
    for _ in range(iters):
        z = np.clip(D@w, -30, 30); p = 1/(1+np.exp(-z)); s = p*(1-p)+1e-6
        g = D.T@(p-y)/len(y) + lam*w
        H = (D*s[:,None]).T@D/len(y) + lam*np.eye(k)
        try: w -= np.linalg.solve(H, g)
        except np.linalg.LinAlgError: break
    return w

# ── 🔑 부스팅 스텀프 GAM (LightGBM num_leaves=2 재현) ─────────
def _prebin(X, nsplit):
    """피처별 분위수 구간 인덱스를 **한 번만** 계산한다.
    매 라운드 후보 분할점을 다시 훑으면 O(rounds x nsplit x n) 이라 폭주한다."""
    cuts = [np.quantile(POOL[:, j], np.linspace(0.02, 0.98, nsplit)) for j in range(X.shape[1])]
    idx = np.column_stack([np.searchsorted(cuts[j], X[:, j]) for j in range(X.shape[1])])
    return cuts, idx


def fit_boost(Xa, Xb, y, rounds=300, lr=0.15, nsplit=24, min_leaf=20):
    """pairwise 로지스틱 손실 위의 depth-1 그래디언트 부스팅.

    깊이 1 이라 트리마다 피처 하나만 쓴다 -> 예측이 피처별 함수의 합으로 분해된다(=GAM).
    분할점은 분위수 후보에서 SSE 최소로 **적응적으로** 고른다.
    LightGBM(num_leaves=2) 에 가장 가까운 재현이며, 순수 파이썬판보다 100배 빠르다.

    벡터화: 구간을 미리 계산하고 매 라운드 bincount + cumsum 으로
    모든 분할점의 SSE 를 한 번에 평가한다.
    """
    nf = Xa.shape[1]
    cuts, ia = _prebin(Xa, nsplit)
    _,    ib = _prebin(Xb, nsplit)
    nb = nsplit + 1
    Fa = np.zeros(len(y)); Fb = np.zeros(len(y)); trees = []
    for _ in range(rounds):
        p = 1 / (1 + np.exp(-np.clip(Fa - Fb, -30, 30)))
        r = np.concatenate([(y - p), -(y - p)])          # 음의 그래디언트
        tot = r.sum(); n_tot = len(r); best = None
        for j in range(nf):
            b = np.concatenate([ia[:, j], ib[:, j]])
            sr = np.bincount(b, weights=r, minlength=nb)
            cn = np.bincount(b, minlength=nb)
            cs = np.cumsum(sr)[:-1]; cc = np.cumsum(cn)[:-1]     # 왼쪽 누적
            ok = (cc >= min_leaf) & (n_tot - cc >= min_leaf)
            if not ok.any(): continue
            # SSE 감소량 = cs^2/cc + (tot-cs)^2/(n-cc)  (클수록 좋다)
            gain = np.where(ok, cs**2 / np.maximum(cc, 1)
                            + (tot - cs)**2 / np.maximum(n_tot - cc, 1), -np.inf)
            k = int(np.argmax(gain))
            if best is None or gain[k] > best[0]:
                best = (gain[k], j, k, cs[k] / cc[k], (tot - cs[k]) / (n_tot - cc[k]))
        if best is None: break
        _, j, k, ml, mr = best
        t = cuts[j][k] if k < len(cuts[j]) else cuts[j][-1]
        trees.append((j, t, lr * ml, lr * mr))
        Fa += np.where(ia[:, j] <= k, lr * ml, lr * mr)
        Fb += np.where(ib[:, j] <= k, lr * ml, lr * mr)
    return trees


def boost_score(trees, X):
    s = np.zeros(len(X))
    for j,t,vl,vr in trees: s += np.where(X[:,j] <= t, vl, vr)
    return s

# ── 데이터 · 평가 ────────────────────────────────────────────
def make(n):
    i = rng.integers(0,len(POOL),n); j = rng.integers(0,len(POOL),n)
    p = 1/(1+np.exp(-np.clip((U[i]-U[j])/TEMP,-30,30)))
    return i, j, (rng.random(n) < p).astype(float)

def ndcg(score_fn, nq=400, K=10):
    disc = 1/np.log2(np.arange(2,K+2)); idcg = disc.sum(); tot = 0.0
    for _ in range(nq):
        idx = rng.integers(0,len(POOL),200)
        rel = set(idx[np.argsort(-U[idx])[:K]].tolist())
        pred = idx[np.argsort(-score_fn(POOL[idx]))[:K]]
        tot += sum(d for d,o in zip(disc,pred) if o in rel)/idcg
    return tot/nq

LAM = [1e-5,1e-4,1e-3,1e-2] if QUICK else [1e-5,1e-4,1e-3,1e-2,1e-1]
LAM_G = [1e-4,1e-3,1e-2,1e-1] if QUICK else [1e-5,1e-4,1e-3,1e-2,1e-1,3e-1]
NS = [600,2000,6000] if QUICK else [600,2000,6000,15000]
REP = 3 if QUICK else 5
NQ  = 250 if QUICK else 400

# 🔑 GAM 에 최대한 유리하게: 구간 수도 N 별로 최적화한다.
#    초판은 구간 6 으로 고정했는데, 스윕해보니 그것이 불리했다 (6:0.796 vs 16:0.847).
#    "내 GAM 이 약해서 진 것"이라는 반론을 없애기 위해 λ 와 구간 수를 동시에 고른다.
NBINS = [4, 6, 8, 12, 16]

def phi_time_only(X):        # 시간 구간더미만 (sqrt·곱항 제거)
    cov,exp,tas,ing,pop,tim,sea,cui = X.T
    return np.column_stack([cov,exp,tas,ing,pop,
                            (tim<.25).astype(float),((tim>=.25)&(tim<.55)).astype(float),
                            (tim>=.55).astype(float),sea,cui])
def phi_no_prod(X):          # 곱항만 제거
    return phi_blind(X)[:, :-1]

MODELS = [("선형 raw", phi_lin, LAM), ("블라인드 변환", phi_blind, LAM),
          ("정답을 알고 만든 것", phi_cheat, LAM),
          ("GAM-IRLS(구간·λ 최적)", "GAM", LAM_G),
          ("GAM-부스팅(LGBM 근사)", None, None)]
if MODE == "ablate":
    MODELS = [("선형 raw", phi_lin, LAM), ("블라인드 12모수", phi_blind, LAM),
              ("− 곱항 (11모수)", phi_no_prod, LAM),
              ("− sqrt·곱항 = 시간더미만 (10모수)", phi_time_only, LAM)]

print(f"진짜 선호가 비선형인 세계 · 라벨 {REP}회 재추출 · λ 각각 최적화 · NDCG@10")
print(f"{'(--quick)' if QUICK else '(full)'}  평가쿼리 {NQ} · TEMP={TEMP} · MODE={MODE}\n")
print(f"  {'라벨 쌍':>7}" + "".join(f"{m[0]:>24}" for m in MODELS))
print("  " + "-"*(7+24*len(MODELS)))
for n in NS:
    acc = {m[0]: [] for m in MODELS}
    for _ in range(REP):
        i,j,y = make(n); Xa, Xb = POOL[i], POOL[j]
        for name, phi, lams in MODELS:
            if phi is None:
                tr = fit_boost(Xa, Xb, y, rounds=150 if QUICK else 300)
                acc[name].append(ndcg(lambda X: boost_score(tr,X), NQ))
            elif phi == "GAM":
                best = 0.0
                for nb_ in NBINS:
                    pg = make_bins(nb_); Dg = pg(Xa)-pg(Xb)
                    best = max(best, max(ndcg(lambda X,w=fit_irls(Dg,y,l),p=pg: p(X)@w, NQ)
                                         for l in lams))
                acc[name].append(best)
            else:
                D = phi(Xa)-phi(Xb)
                acc[name].append(max(ndcg(lambda X,w=fit_irls(D,y,l): phi(X)@w, NQ) for l in lams))
    mu = {k: float(np.mean(v)) for k,v in acc.items()}; b = max(mu.values())
    print(f"  {n:>7}" + "".join(
        f"{mu[m[0]]:>17.3f}±{(max(acc[m[0]])-min(acc[m[0]]))/2:.3f}{'★' if abs(mu[m[0]]-b)<1e-9 else ' '}"
        for m in MODELS))
print(f"  {'파라미터':>7}" + "".join(
    f"{('32~128' if m[1]=='GAM' else ('트리150~300' if m[1] is None else m[1](POOL[:1]).shape[1])):>24}"
    for m in MODELS))

if not QUICK:
    print("\n\n── GAM 구간 수 스윕 (6,000쌍) — 내 GAM 이 불리하게 설정됐는가 ──")
    i,j,y = make(6000); Xa,Xb = POOL[i],POOL[j]
    for nb in (4,6,8,12,16):
        phi = make_bins(nb); D = phi(Xa)-phi(Xb)
        v = max(ndcg(lambda X,w=fit_irls(D,y,l): phi(X)@w, 300) for l in LAM_G)
        print(f"     구간 {nb:>2}  파라미터 {nb*8:>3}  NDCG {v:.3f}")
