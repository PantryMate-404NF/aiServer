"""맛 축(flavor_vec / taste_vec)을 몇 개로 할 것인가 (설계 2-5 · 5-2-1).

    .venv/bin/python bench/taste_axes.py

## 왜 이 질문이 5-2-6 과 다른가

`f_taste = cos(taste_vec, flavor_vec)` 은 **축이 6이든 20이든 스칼라 하나**를 낸다.
랭킹 모델의 파라미터가 늘지 않으므로 5-2-6 의 "파라미터를 늘리면 작은 N 에서 진다"가
직접 적용되지 않는다. **다른 것이 걸린다:**

    ① 관측 노이즈  — 설문 응답과 레시피 라벨이 정확하지 않다
    ② 차원의 저주  — 축이 늘수록 코사인 값이 뭉쳐 판별력이 떨어진다
    ③ 축 간 상관   — 맛 축은 독립이 아니다 (매움↔짠맛, 기름짐↔감칠맛)

셋 다 **축을 늘리면 나빠지는 방향**이다. 표현력만 보면 늘리는 게 맞지만
관측이 부정확하면 역전된다. 그 교차점을 잰다.

## 측정 방법

진짜 취향은 K_true 축에 있고, 우리는 K_obs 축으로 관측한다.
K_obs < K_true 면 정보를 잃고, K_obs 가 커지면 노이즈를 더 받는다.
`f_taste` 로 정렬했을 때 진짜 선호 상위 K 를 얼마나 맞히는가(NDCG@10)로 잰다.
"""
from __future__ import annotations

import numpy as np

rng = np.random.default_rng(20260828)
N_RECIPE, N_QUERY, K = 2000, 400, 10


def corr_axes(k: int, rho: float) -> np.ndarray:
    """축 간 상관 행렬. 맛 축은 독립이 아니다 — 매운 음식은 대체로 짜다."""
    C = np.full((k, k), rho)
    np.fill_diagonal(C, 1.0)
    return np.linalg.cholesky(C + 1e-6 * np.eye(k))


def run(k_true: int, k_obs: int, noise_u: float, noise_r: float, rho: float) -> float:
    L = corr_axes(k_true, rho)
    # 진짜 맛 공간
    R = (rng.standard_normal((N_RECIPE, k_true)) @ L.T)
    U = (rng.standard_normal((N_QUERY, k_true)) @ L.T)
    # 관측: k_obs 축만 본다 (자르거나 = 정보 손실) + 노이즈
    ko = min(k_obs, k_true)
    Ro = R[:, :ko] + rng.standard_normal((N_RECIPE, ko)) * noise_r
    Uo = U[:, :ko] + rng.standard_normal((N_QUERY, ko)) * noise_u
    if k_obs > k_true:                       # 근거 없는 축을 더 만든 경우 = 순수 노이즈
        pad = k_obs - k_true
        Ro = np.hstack([Ro, rng.standard_normal((N_RECIPE, pad))])
        Uo = np.hstack([Uo, rng.standard_normal((N_QUERY, pad))])

    def cos(A, B):
        A = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)
        B = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-9)
        return A @ B.T

    true_s, obs_s = cos(U, R), cos(Uo, Ro)
    disc = 1 / np.log2(np.arange(2, K + 2)); idcg = disc.sum()
    tot = 0.0
    for q in range(N_QUERY):
        rel = set(np.argsort(-true_s[q])[:K].tolist())
        pred = np.argsort(-obs_s[q])[:K]
        tot += sum(d for d, o in zip(disc, pred) if o in rel) / idcg
    return tot / N_QUERY


print("진짜 취향은 8축에 있다고 두고, 우리가 몇 축으로 관측할지를 바꾼다")
print("(축 간 상관 rho=0.3 — 맛 축은 독립이 아니다)\n")
print(f"  {'관측 축':>7}" + "".join(f"{f'노이즈 {n}':>13}" for n in (0.0, 0.3, 0.6, 1.0)))
print("  " + "-" * 59)
best = {}
for ko in (3, 6, 8, 10, 12, 16, 24):
    row = []
    for nz in (0.0, 0.3, 0.6, 1.0):
        v = run(8, ko, nz, nz, 0.3)
        row.append(v)
        best[nz] = max(best.get(nz, 0), v)
    mark = "  ← 현재" if ko == 6 else ("  ← 진짜 축 수" if ko == 8 else "")
    print(f"  {ko:>7}" + "".join(f"{v:>13.3f}" for v in row) + mark)
print("  " + "-" * 59)
print("  최적 축수" + "".join(
    f"{max((k for k in (3,6,8,10,12,16,24) if abs(run(8,k,nz,nz,0.3)-best[nz])<1e-9), default=0):>13}"
    for nz in (0.0, 0.3, 0.6, 1.0)))

print("\n\n진짜 축이 6개뿐인데 12축으로 늘리면 (= 근거 없는 축 6개 추가)")
print(f"  {'관측 축':>7}{'노이즈 0.0':>13}{'노이즈 0.6':>13}")
for ko in (6, 9, 12, 18):
    print(f"  {ko:>7}{run(6,ko,0.0,0.0,0.3):>13.3f}{run(6,ko,0.6,0.6,0.3):>13.3f}"
          + ("  ← 딱 맞음" if ko == 6 else "  🔴 노이즈 축"))

print("\n\n축 간 상관이 세지면 (같은 것을 두 번 묻는 축을 추가한 경우)")
print(f"  {'rho':>7}{'6축':>10}{'12축':>10}{'차이':>10}")
for rho in (0.0, 0.3, 0.6, 0.85):
    a, b = run(8, 6, 0.4, 0.4, rho), run(8, 12, 0.4, 0.4, rho)
    print(f"  {rho:>7.2f}{a:>10.3f}{b:>10.3f}{b-a:>+10.3f}")
