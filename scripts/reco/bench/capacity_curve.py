"""모델 크기를 늘리면 의미가 있는가 — 라벨 수별 최적 파라미터 수 (설계 5-2-6 · 2-5-1).

    .venv/bin/python bench/capacity_curve.py

앞선 세 측정이 전부 같은 방향을 가리켰다.
  5-2-6      600쌍: 선형(8) 0.809 > GAM(48~128) 0.675  ·  15,000쌍: 역전
  taste_axes 근거 없는 축 추가는 급락
  vector_vs_scalar  600쌍: 코사인(3) > 축별(19)  ·  2,000쌍: 역전

**같은 과제에서 용량만 바꿔** 그 패턴을 한 곡선으로 확인한다.
"""
from __future__ import annotations

import numpy as np

N_ITEM, N_Q, K = 3000, 300, 10
DIMS = (6, 7, 6)


def nrm(X):
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


def make_world(seed):
    r = np.random.default_rng(seed)
    IU = [r.random((N_ITEM, DIMS[0])), (r.random((N_ITEM, DIMS[1])) < .25).astype(float),
          r.random((N_ITEM, DIMS[2])) ** 2]
    IR = [r.random((N_ITEM, DIMS[0])), (r.random((N_ITEM, DIMS[1])) < .25).astype(float),
          r.random((N_ITEM, DIMS[2])) ** 2]
    A = [np.abs(1.0 + 0.5 * r.standard_normal(d)) for d in DIMS]
    BW = np.array([.52, .13, .35])
    A = [a / a.sum() * BW[i] for i, a in enumerate(A)]
    return r, IU, IR, np.concatenate(A)


def prod(u, r):                      # 19차 원소별 곱 (정답 공간)
    return np.hstack([nrm(u[b]) * nrm(r[b]) for b in range(3)])


# ── 용량 사다리: 파라미터 수만 다르고 같은 정보를 본다 ──────────
def phi_1(u, r):   return prod(u, r).sum(1, keepdims=True)                  # 1
def phi_3(u, r):
    P = prod(u, r); s = np.cumsum([0, *DIMS])
    return np.column_stack([P[:, s[b]:s[b+1]].sum(1) for b in range(3)])     # 3
def phi_6(u, r):                                                            # 6
    P = prod(u, r); s = np.cumsum([0, *DIMS]); out = []
    for b in range(3):
        blk = P[:, s[b]:s[b+1]]; h = blk.shape[1] // 2
        out += [blk[:, :h].sum(1), blk[:, h:].sum(1)]
    return np.column_stack(out)
def phi_19(u, r):  return prod(u, r)                                        # 19
def phi_121(u, r):                                                          # 121
    return np.hstack([np.einsum("ij,ik->ijk", nrm(u[b]), nrm(r[b])).reshape(len(u[b]), -1)
                      for b in range(3)])


LADDER = [("1", phi_1), ("3", phi_3), ("6", phi_6), ("19", phi_19), ("121", phi_121)]


def fit(X, y, lam, iters=22):
    k = X.shape[1]; w = np.zeros(k)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30))); s = p * (1 - p) + 1e-6
        g = X.T @ (p - y) / len(y) + lam * w
        H = (X * s[:, None]).T @ X / len(y) + lam * np.eye(k)
        try: w -= np.linalg.solve(H, g)
        except np.linalg.LinAlgError: break
    return w


def run(seed, n_pair):
    r, IU, IR, A = make_world(seed)
    ui = r.integers(0, N_ITEM, n_pair)
    a, b_ = r.integers(0, N_ITEM, n_pair), r.integers(0, N_ITEM, n_pair)
    U = [x[ui] for x in IU]; R1 = [x[a] for x in IR]; R2 = [x[b_] for x in IR]
    marg = prod(U, R1) @ A - prod(U, R2) @ A
    y = (r.random(n_pair) < 1 / (1 + np.exp(-marg / .10))).astype(float)

    disc = 1 / np.log2(np.arange(2, K + 2)); idcg = disc.sum()
    qs = [(r.integers(0, N_ITEM), r.integers(0, N_ITEM, 200)) for _ in range(N_Q)]
    out = {}
    for name, phi in LADDER:
        X = phi(U, R1) - phi(U, R2)
        best = 0.0
        for lam in (1e-4, 1e-3, 1e-2, 3e-2, 1e-1, 3e-1):
            w = fit(X, y, lam); tot = 0.0
            for q, cand in qs:
                u_ = [x[np.full(200, q)] for x in IU]; r_ = [x[cand] for x in IR]
                true = set(np.argsort(-(prod(u_, r_) @ A))[:K].tolist())
                pred = np.argsort(-(phi(u_, r_) @ w))[:K]
                tot += sum(d for d, o in zip(disc, pred) if o in true) / idcg
            best = max(best, tot / N_Q)
        out[name] = best
    return out


print("같은 과제 · 같은 정보 · **파라미터 수만** 다르게 (3회 평균 · NDCG@10)\n")
print(f"  {'라벨 쌍':>8}" + "".join(f"{'p=' + n:>10}" for n, _ in LADDER) + f"{'최적':>8}{'샘플/모수':>11}")
print("  " + "-" * 74)
for n_pair in (300, 600, 2000, 6000, 20000):
    rs = [run(9000 + s, n_pair) for s in range(3)]
    v = {n: float(np.mean([x[n] for x in rs])) for n, _ in LADDER}
    bn = max(v, key=v.get)
    print(f"  {n_pair:>8}" + "".join(
        f"{v[n]:>9.3f}{'★' if n == bn else ' '}" for n, _ in LADDER)
        + f"{bn:>8}{n_pair / int(bn):>11.0f}")
