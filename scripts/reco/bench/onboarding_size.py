"""온보딩에서 레시피를 몇 개 고르게 할 것인가 (설계 2-5-1 · 02 I-8).

    .venv/bin/python bench/onboarding_size.py

## 아이디어

`taste_vec = 고른 레시피들의 flavor_vec 평균` (I-8).
**평균이므로 표본이 늘면 랜덤 오차가 1/√n 으로 준다** — 근거 없는 숫자를 하나도
만들지 않고 노이즈를 줄이는 유일한 방법이다.

조리법 보정처럼 배수를 지어내는 것과 정반대 성격이다.

## 🔴 단, 줄어드는 것은 랜덤 오차뿐이다

    랜덤 오차   flavor_vec 이 이 레시피는 조금 높고 저 레시피는 조금 낮게 매겨짐  → 평균으로 준다 ✅
    체계 편향   내가 '된장 감칠맛'을 일관되게 과대평가함                        → 아무리 평균내도 안 준다 ❌

체계 편향은 **척도 문항(매움·단맛·짠맛)을 독립적으로 받는 것**으로만 깬다.
그래서 둘은 대체재가 아니라 **보완재**다. 그것도 함께 잰다.
"""
from __future__ import annotations

import numpy as np

rng = np.random.default_rng(20260831)
D, N_RECIPE, N_USER = 6, 2000, 600


def trial(n_pick, noise_rand, noise_bias, scale_mix=0.0, n_scale_axes=3):
    """scale_mix: 척도 문항(독립 관측) 을 섞는 비율. 0 이면 레시피 선택만."""
    # 진짜 레시피 맛 · 유저의 진짜 취향
    R_true = rng.random((N_RECIPE, D))
    U_true = rng.random((N_USER, D))

    # 우리가 매긴 flavor_vec = 진짜 + 체계편향 + 랜덤오차
    bias = rng.standard_normal(D) * noise_bias          # 축별 고정 편향 (평균해도 안 준다)
    R_obs = R_true + bias + rng.standard_normal((N_RECIPE, D)) * noise_rand

    # 유저는 자기 취향에 가까운 레시피를 고른다 (진짜 값 기준으로 고른다)
    sim = U_true @ R_true.T
    picks = np.argsort(-sim, axis=1)[:, :max(n_pick * 3, n_pick)]
    U_obs = np.zeros((N_USER, D))
    for u in range(N_USER):
        chosen = rng.choice(picks[u], size=n_pick, replace=False)
        U_obs[u] = R_obs[chosen].mean(0)               # ← taste_vec = flavor_vec 평균

    if scale_mix > 0:                                  # 척도 문항 — 독립 관측
        scale_obs = U_true.copy() + rng.standard_normal((N_USER, D)) * 0.25
        m = np.zeros(D); m[:n_scale_axes] = scale_mix  # 앞 n축만 척도로 받는다
        U_obs = U_obs * (1 - m) + scale_obs * m

    # f_taste 가 진짜 순위를 얼마나 회복하나 (중심화 후 코사인)
    def ndcg(Uo, Ro, Ut, Rt, K=10):
        Uc, Rc = Uo - Uo.mean(0), Ro - Ro.mean(0)
        Un = Uc / (np.linalg.norm(Uc, axis=1, keepdims=True) + 1e-9)
        Rn = Rc / (np.linalg.norm(Rc, axis=1, keepdims=True) + 1e-9)
        Utc, Rtc = Ut - Ut.mean(0), Rt - Rt.mean(0)
        Utn = Utc / (np.linalg.norm(Utc, axis=1, keepdims=True) + 1e-9)
        Rtn = Rtc / (np.linalg.norm(Rtc, axis=1, keepdims=True) + 1e-9)
        disc = 1 / np.log2(np.arange(2, K + 2)); idcg = disc.sum(); tot = 0.0
        for u in range(0, N_USER, 3):
            cand = rng.integers(0, N_RECIPE, 200)
            true = set(cand[np.argsort(-(Rtn[cand] @ Utn[u]))[:K]].tolist())
            pred = cand[np.argsort(-(Rn[cand] @ Un[u]))[:K]]
            tot += sum(d for d, o in zip(disc, pred) if o in true) / idcg
        return tot / len(range(0, N_USER, 3))
    return ndcg(U_obs, R_obs, U_true, R_true)


print("① 몇 개 고르게 할 것인가 — 랜덤 오차만 있을 때 (체계 편향 0)\n")
print(f"  {'선택 개수':>9}" + "".join(f"{f'랜덤오차 {n}':>14}" for n in (0.15, 0.30, 0.50)))
print("  " + "-" * 52)
for n_pick in (1, 3, 5, 8, 12, 20):
    print(f"  {n_pick:>9}" + "".join(
        f"{np.mean([trial(n_pick, nz, 0.0) for _ in range(3)]):>14.3f}" for nz in (0.15, 0.30, 0.50)))

print("\n\n② 🔴 체계 편향이 있으면 — 개수를 늘려도 안 준다\n")
print(f"  {'선택 개수':>9}{'편향 0.0':>12}{'편향 0.2':>12}{'편향 0.4':>12}")
print("  " + "-" * 46)
for n_pick in (3, 8, 20):
    print(f"  {n_pick:>9}" + "".join(
        f"{np.mean([trial(n_pick, 0.30, b) for _ in range(3)]):>12.3f}" for b in (0.0, 0.2, 0.4)))

print("\n\n③ 척도 문항을 섞으면 (체계 편향 0.4 · 선택 8개 고정)\n")
print(f"  {'척도 비중':>9}{'3축만 척도':>14}{'6축 전부 척도':>15}")
print("  " + "-" * 40)
for mix in (0.0, 0.3, 0.5, 0.7, 1.0):
    a = np.mean([trial(8, 0.30, 0.4, mix, 3) for _ in range(3)])
    b = np.mean([trial(8, 0.30, 0.4, mix, 6) for _ in range(3)])
    print(f"  {mix:>9.1f}{a:>14.3f}{b:>15.3f}")
