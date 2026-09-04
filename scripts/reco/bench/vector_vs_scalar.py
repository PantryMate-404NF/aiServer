"""블록을 **스칼라로 접을 것인가, 벡터로 쓸 것인가** (설계 2-5-1).

    .venv/bin/python bench/vector_vs_scalar.py

## 세 가지 표현력 단계

    ① 코사인 + 블록 가중치   score = Σ_b w_b · cos(u_b, r_b)          파라미터 3
    ② 축별 가중 (대각 이중선형) score = Σ_b Σ_k a_bk · û_bk · r̂_bk      파라미터 19
    ③ 완전 이중선형          score = Σ_b û_b^T W_b r̂_b                파라미터 121

**①은 ②의 특수한 경우다** — 한 블록 안의 축 가중치를 전부 같게 두면 정확히 코사인이 된다
(정규화 벡터의 원소별 곱 합 = 코사인). 즉 ②는 ①의 **엄격한 일반화**이고,
데이터가 무한하면 ② ≥ ①ㅤ이 보장된다. **질문은 표현력이 아니라 표본 효율이다.**

③은 "유저의 매운맛 선호 × 레시피의 감칠맛" 같은 **축 간 교차**까지 잡는다.

## 5-2-6 과 같은 함정

600쌍에서 GAM(48~128 파라미터)이 선형(8)보다 0.675 vs 0.809 로 크게 졌다.
파라미터가 늘면 작은 N 에서 진다. ②는 19, ③은 121 — 그 사이 어디쯤인지를 잰다.
"""
from __future__ import annotations

import numpy as np

rng = np.random.default_rng(20260829)
N_ITEM, N_Q, K = 3000, 400, 10
DIMS = (6, 7, 6)                       # 맛 · cuisine · 대분류


def blocks(n, r=None):
    r = r or rng
    return [r.random((n, DIMS[0])),
            (r.random((n, DIMS[1])) < 0.25).astype(float),
            r.random((n, DIMS[2])) ** 2]


def nrm(X):
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


def phi_cos(u, r):                     # ① 파라미터 3
    return np.column_stack([np.einsum("ij,ij->i", nrm(u[b]), nrm(r[b])) for b in range(3)])


def phi_diag(u, r):                    # ② 파라미터 19 — 축별 가중
    return np.hstack([nrm(u[b]) * nrm(r[b]) for b in range(3)])


def phi_full(u, r):                    # ③ 파라미터 121 — 축 간 교차까지
    return np.hstack([np.einsum("ij,ik->ijk", nrm(u[b]), nrm(r[b])).reshape(len(u[b]), -1)
                      for b in range(3)])


def fit_bt(X, y, lam, iters=25):
    k = X.shape[1]; w = np.zeros(k)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30))); s = p * (1 - p) + 1e-6
        g = X.T @ (p - y) / len(y) + lam * w
        H = (X * s[:, None]).T @ X / len(y) + lam * np.eye(k)
        try: w -= np.linalg.solve(H, g)
        except np.linalg.LinAlgError: break
    return w


def trial(seed, n_pair, axis_var):
    """axis_var: 축별 중요도의 편차. 0 이면 코사인이 정답(축이 다 똑같이 중요)."""
    r0 = np.random.default_rng(seed)
    IU, IR = blocks(N_ITEM, r0), blocks(N_ITEM, r0)
    # 진짜 효용 — 축별 가중치가 다르다
    A_true = [np.abs(1.0 + axis_var * r0.standard_normal(d)) for d in DIMS]
    BW = np.array([0.52, 0.13, 0.35])
    for b in range(3):
        A_true[b] = A_true[b] / A_true[b].sum() * BW[b]
    A_flat = np.concatenate(A_true)

    def util(uidx, ridx):
        u = [x[uidx] for x in IU]; r = [x[ridx] for x in IR]
        return phi_diag(u, r) @ A_flat

    ui = r0.integers(0, N_ITEM, n_pair)
    a, b_ = r0.integers(0, N_ITEM, n_pair), r0.integers(0, N_ITEM, n_pair)
    y = (r0.random(n_pair) < 1 / (1 + np.exp(-(util(ui, a) - util(ui, b_)) / 0.10))).astype(float)
    U = [x[ui] for x in IU]; R1 = [x[a] for x in IR]; R2 = [x[b_] for x in IR]

    fitted = {}
    for name, phi, lams in (("① 코사인 (3)", phi_cos, [1e-4, 1e-3, 1e-2]),
                            ("② 축별가중 (19)", phi_diag, [1e-3, 1e-2, 3e-2, 1e-1]),
                            ("③ 완전이중선형 (121)", phi_full, [1e-2, 3e-2, 1e-1, 3e-1])):
        X = phi(U, R1) - phi(U, R2)
        fitted[name] = (phi, [fit_bt(X, y, l) for l in lams])

    disc = 1 / np.log2(np.arange(2, K + 2)); idcg = disc.sum()
    best = {n: 0.0 for n in fitted}
    for n, (phi, ws) in fitted.items():
        for w in ws:
            tot = 0.0
            for _ in range(N_Q):
                q = r0.integers(0, N_ITEM); cand = r0.integers(0, N_ITEM, 200)
                u_ = [x[np.full(200, q)] for x in IU]; r_ = [x[cand] for x in IR]
                true = set(np.argsort(-(phi_diag(u_, r_) @ A_flat))[:K].tolist())
                pred = np.argsort(-(phi(u_, r_) @ w))[:K]
                tot += sum(d for d, o in zip(disc, pred) if o in true) / idcg
            best[n] = max(best[n], tot / N_Q)
    return best


for av, label in ((0.0, "축별 중요도가 균등 — 코사인이 정답인 세계"),
                  (0.5, "축별 중요도가 다름 (편차 0.5) — 현실적"),
                  (1.2, "축별 중요도가 크게 다름 (편차 1.2)")):
    print(f"\n{'='*66}\n{label}\n{'='*66}")
    print(f"  {'라벨 쌍':>8}" + "".join(f"{n:>22}" for n in ("① 코사인 (3)", "② 축별가중 (19)", "③ 완전이중선형 (121)")))
    for n_pair in (300, 600, 2000, 6000):
        rs = [trial(7000 + s, n_pair, av) for s in range(3)]
        vals = [np.mean([r[n] for r in rs]) for n in ("① 코사인 (3)", "② 축별가중 (19)", "③ 완전이중선형 (121)")]
        bi = int(np.argmax(vals))
        print(f"  {n_pair:>8}" + "".join(
            f"{v:>21.3f}{'★' if i == bi else ' '}" for i, v in enumerate(vals)))
