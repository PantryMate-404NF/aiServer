"""선택 개수 × 척도 축 수 교차 — 사용자 안(3개+3축)이 어디에 있나.

기존 onboarding_size.py 는 **척도 실험을 선택 8개에 고정**해서 쟀다.
그래서 "적게 고르고 척도를 늘리면?" 이라는 조합이 측정된 적이 없다.

시간 비용이 다르다는 것이 핵심이다:
    레시피 1개 고르기 ≈ 9초   (8개 = 75초)
    척도 1축         ≈ 5초   (3축 = 15초)

**척도가 싸다.** 만약 척도가 선택 개수를 보완할 수 있다면
'3개 + 6축'(약 60초) 이 '8개 + 3축'(약 90초) 보다 짧으면서 나을 수 있다.
"""
from __future__ import annotations

import numpy as np

exec(open("bench/onboarding_size.py").read().split("if __name__")[0].split("rng = np.random")[0])

rng = np.random.default_rng(20260831)
D, N_RECIPE, N_USER = 6, 2000, 600


def trial(n_pick, noise_rand, noise_bias, scale_mix=0.0, n_scale_axes=3):
    R_true = rng.random((N_RECIPE, D))
    U_true = rng.random((N_USER, D))
    bias = rng.standard_normal(D) * noise_bias
    R_obs = R_true + bias + rng.standard_normal((N_RECIPE, D)) * noise_rand
    sim = U_true @ R_true.T
    pick = np.argsort(-sim, axis=1)[:, :n_pick]
    U_hat = R_obs[pick].mean(axis=1)
    if scale_mix > 0:
        # 척도 문항 — 독립 관측이라 체계 편향이 안 실린다. 다만 응답 자체에 잡음이 있다
        scale = U_true + rng.standard_normal((N_USER, D)) * 0.25
        m = np.zeros(D); m[:n_scale_axes] = 1.0
        U_hat = U_hat * (1 - scale_mix * m) + scale * (scale_mix * m)
    # 지표: 추정 취향과 진짜 취향의 순위 일치 (코사인 상관)
    a = U_hat - U_hat.mean(0); b = U_true - U_true.mean(0)
    return float(np.mean(np.sum(a * b, 1) /
                 (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9)))


SEC_PICK, SEC_AXIS = 9, 5
print("체계 편향 0.4 · 랜덤오차 0.3 · 척도 비중 0.5\n")
print(f"  {'구성':<26}{'점수':>8}{'소요':>8}{'초당 효율':>11}")
print("  " + "-" * 55)

rows = []
for n_pick in (3, 5, 8, 12):
    for axes in (0, 3, 6):
        s = np.mean([trial(n_pick, 0.3, 0.4, 0.5 if axes else 0.0, axes or 3)
                     for _ in range(12)])
        sec = n_pick * SEC_PICK + axes * SEC_AXIS
        rows.append((n_pick, axes, s, sec))

base = next(r for r in rows if r[0] == 8 and r[1] == 3)
for n_pick, axes, s, sec in rows:
    tag = ""
    if (n_pick, axes) == (3, 3):
        tag = "  ← 사용자 안"
    elif (n_pick, axes) == (8, 3):
        tag = "  ← 기존 확정안"
    elif (n_pick, axes) == (3, 6):
        tag = "  🔑"
    name = f"레시피 {n_pick}개 + 척도 {axes}축" if axes else f"레시피 {n_pick}개만"
    print(f"  {name:<26}{s:>8.3f}{sec:>7}초{s/sec*100:>10.2f}{tag}")

print(f"\n  기준(8개+3축) = {base[2]:.3f} · {base[3]}초")
print("  초당 효율 = 점수 ÷ 소요초 × 100. 온보딩 시간이 모집률을 좌우하므로 이게 실질 지표다.")
