"""맛·나라·카테고리를 **한 벡터로 합칠 것인가, 따로 둘 것인가** (설계 2-5-1).

    .venv/bin/python bench/merge_vs_separate.py

## 질문

"맛 선호 + 나라별 선호 + 큰 카테고리 선호를 축으로 만들어 taste_vec 을 늘리자"
— 표현력만 보면 맞다. 그런데 **합치면 가중치가 하나로 줄어든다.**

    따로  f_taste·w1 + f_cuisine·w2 + f_ing_pref·w3     ← 가중치 3개
    합침  cos(merged_user, merged_recipe)·w             ← 가중치 1개

합치면 "이 유저는 취향보다 재료를 중시한다"를 표현할 방법이 사라진다.
현재 DEFAULT_WEIGHTS 는 0.16 / 0.04 / 0.11 로 **4배 차이**가 난다.

## 무엇을 재는가

진짜 효용이 세 블록의 **가중합**이라고 두고, 두 방식이 그것을 얼마나 회복하는가.
가중치는 쌍대비교로 학습한다(5-2-5). NDCG@10 으로 비교한다.
"""
from __future__ import annotations

import numpy as np

rng = np.random.default_rng(20260829)
N_ITEM, N_PAIR, N_Q, K = 3000, 2000, 400, 10
D_TASTE, D_CUISINE, D_CAT = 6, 7, 6          # 맛 6 · cuisine_family 7 · 대분류 6

#: 실제 DEFAULT_WEIGHTS 비율 (f_taste 0.16 · f_cuisine 0.04 · f_ing_pref 0.11)
W_TRUE = np.array([0.16, 0.04, 0.11]) / 0.31


def blocks(n):
    return (rng.random((n, D_TASTE)),                       # 맛: 연속 0~1
            (rng.random((n, D_CUISINE)) < 0.25).astype(float),  # 나라: 희소 이진
            rng.random((n, D_CAT)) ** 2)                    # 카테고리: 치우친 분포


def cos(A, B):
    A = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)
    B = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-9)
    return np.einsum("ij,ij->i", A, B)


def feats(u, r):
    """블록별 코사인 3개 = '따로' 방식의 피처."""
    return np.column_stack([cos(u[i], r[i]) for i in range(3)])


def merged_cos(u, r, normalize=False):
    """한 벡터로 이어붙인 뒤 코사인 = '합침' 방식."""
    if normalize:                      # 블록별 L2 정규화 후 결합 (스케일 보정)
        u = [x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9) for x in u]
        r = [x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9) for x in r]
    return cos(np.hstack(u), np.hstack(r))


def fit_bt(X, y, lam=1e-3, iters=25):
    """Bradley-Terry (절편 없는 로지스틱, IRLS) — 5-2-5 와 같은 방식."""
    k = X.shape[1]; w = np.zeros(k)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30))); s = p * (1 - p) + 1e-6
        g = X.T @ (p - y) / len(y) + lam * w
        H = (X * s[:, None]).T @ X / len(y) + lam * np.eye(k)
        try: w -= np.linalg.solve(H, g)
        except np.linalg.LinAlgError: break
    return w


def trial(seed, n_pair=N_PAIR):
    global rng
    rng = np.random.default_rng(seed)
    IU, IR = blocks(N_ITEM), blocks(N_ITEM)          # 유저측·아이템측 (같은 공간)
    # 진짜 효용 = 블록별 코사인의 가중합
    def util(ui, ri):
        u = [b[ui] for b in IU]; r = [b[ri] for b in IR]
        return feats(u, r) @ W_TRUE

    i, j = rng.integers(0, N_ITEM, n_pair), rng.integers(0, N_ITEM, n_pair)
    m = util(i, j - j + i) * 0  # placeholder
    ui, ri1, ri2 = rng.integers(0, N_ITEM, n_pair), rng.integers(0, N_ITEM, n_pair), rng.integers(0, N_ITEM, n_pair)
    u = [b[ui] for b in IU]
    r1, r2 = [b[ri1] for b in IR], [b[ri2] for b in IR]
    margin = feats(u, r1) @ W_TRUE - feats(u, r2) @ W_TRUE
    y = (rng.random(n_pair) < 1 / (1 + np.exp(-margin / 0.10))).astype(float)

    Xa = feats(u, r1) - feats(u, r2)                                   # 따로: 3열
    Xb = (merged_cos(u, r1) - merged_cos(u, r2)).reshape(-1, 1)        # 합침: 1열
    Xc = (merged_cos(u, r1, True) - merged_cos(u, r2, True)).reshape(-1, 1)  # 합침+정규화
    wa, wb, wc = fit_bt(Xa, y), fit_bt(Xb, y), fit_bt(Xc, y)

    disc = 1 / np.log2(np.arange(2, K + 2)); idcg = disc.sum()
    acc = {"따로 (가중치 3개)": 0.0, "합침 (가중치 1개)": 0.0, "합침+블록정규화": 0.0}
    for _ in range(N_Q):
        q = rng.integers(0, N_ITEM); cand = rng.integers(0, N_ITEM, 200)
        u_ = [b[np.full(200, q)] for b in IU]; r_ = [b[cand] for b in IR]
        Fq = feats(u_, r_)
        true = set(np.argsort(-(Fq @ W_TRUE))[:K].tolist())
        for name, sc in (("따로 (가중치 3개)", Fq @ wa),
                         ("합침 (가중치 1개)", merged_cos(u_, r_) * wb[0]),
                         ("합침+블록정규화", merged_cos(u_, r_, True) * wc[0])):
            pred = np.argsort(-sc)[:K]
            acc[name] += sum(d for d, o in zip(disc, pred) if o in true) / idcg
    return {k: v / N_Q for k, v in acc.items()}


print("진짜 효용 = 0.52·맛 + 0.13·나라 + 0.35·카테고리  (DEFAULT_WEIGHTS 비율)")
print(f"쌍대비교 {N_PAIR}건으로 학습 · 5회 평균 · NDCG@10\n")
res = [trial(1000 + s) for s in range(5)]
names = list(res[0])
print(f"  {'방식':<22}{'NDCG@10':>10}{'범위':>10}")
print("  " + "-" * 42)
for n in names:
    v = [r[n] for r in res]
    print(f"  {n:<22}{np.mean(v):>10.3f}{(max(v)-min(v))/2:>10.3f}")

print("\n\n가중치가 서로 비슷하면? (0.33/0.33/0.33 — 합쳐도 손해가 없어야 한다)")
W_TRUE = np.array([1/3, 1/3, 1/3])
res2 = [trial(2000 + s) for s in range(5)]
for n in names:
    v = [r[n] for r in res2]
    print(f"  {n:<22}{np.mean(v):>10.3f}")

print("\n\n가중치 차이가 극단이면? (0.80/0.05/0.15)")
W_TRUE = np.array([0.80, 0.05, 0.15])
res3 = [trial(3000 + s) for s in range(5)]
for n in names:
    v = [r[n] for r in res3]
    print(f"  {n:<22}{np.mean(v):>10.3f}")
