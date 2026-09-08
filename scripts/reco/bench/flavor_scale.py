"""맛 점수를 0~1 로 둘 것인가 −1~1 로 둘 것인가 (설계 2-5-1 ⑤).

    .venv/bin/python bench/flavor_scale.py

## 문제

비음수 벡터끼리는 코사인이 [0,1] 에 갇히고 무작위여도 ~0.77 로 뭉친다.
실측: 우리 role_w 가 0.747 — **무작위와 구분되지 않는다.**

## 후보 넷

    ① 0~1 원본                그대로
    ② 2v−1 (단순 재척도)       0~1 을 −1~1 로 선형 변환
    ③ 중심화 v − corpus_mean   코퍼스 평균을 뺀다
    ④ 의미 있는 음수           완화 재료에 음수 부여 (우유가 매움을 낮춘다)

②가 직관적으로 보이지만 **값이 대부분 낮으면 전부 음수가 되어 3사분면으로 옮겨갈 뿐**이다.
사분면이 바뀌었을 뿐 뭉치는 것은 같다. 그것을 확인한다.
"""
from __future__ import annotations

import numpy as np

rng = np.random.default_rng(20260831)
N, D = 400, 6


def mean_cos(V):
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    S = Vn @ Vn.T
    iu = np.triu_indices(len(V), 1)
    return float(S[iu].mean()), float(S[iu].std())


def spread(V):
    """방향의 퍼짐 — 단위벡터로 만든 뒤 합의 크기. 1에 가까우면 한 방향에 뭉친 것."""
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    return float(np.linalg.norm(Vn.mean(0)))


# 실제 flavor 값의 분포를 흉내낸다 — 대부분 낮고 일부만 높다 (실측 평균 0.13)
V0 = rng.beta(1.2, 7.0, (N, D))
print(f"모의 flavor 값 — 평균 {V0.mean():.3f} · 최댓값 {V0.max():.3f}  (실측 role_w 평균 0.130)\n")

cands = {
    "① 0~1 원본": V0,
    "② 2v−1 단순 재척도": 2 * V0 - 1,
    "③ 중심화 (v − 평균)": V0 - V0.mean(0),
    "④ 축별 표준화 (z-score)": (V0 - V0.mean(0)) / (V0.std(0) + 1e-9),
}
print(f"  {'방식':<24}{'평균 코사인':>12}{'표준편차':>10}{'방향 뭉침':>11}")
print("  " + "-" * 58)
for k, V in cands.items():
    m, sd = mean_cos(V)
    print(f"  {k:<24}{m:>+12.3f}{sd:>10.3f}{spread(V):>11.3f}")

print("\n  🔴 ②가 ①보다 **더 나쁘다** — 값이 대부분 낮아 2v−1 이 전부 음수가 되고,")
print("     1사분면에서 3사분면으로 옮겨갔을 뿐 뭉치는 것은 같다.")

# ── ④ 의미 있는 음수: 완화 재료 ────────────────────────────
print("\n\n═══ ④ 의미 있는 음수 — 완화 재료를 넣으면 ═══")
print("  (우유가 매움을 낮추고, 설탕이 신맛을 눌러주는 것 같은 실제 현상)\n")
print(f"  {'음수 재료 비율':>14}{'평균 코사인':>13}{'방향 뭉침':>11}")
for frac in (0.0, 0.05, 0.15, 0.30, 0.50):
    V = rng.beta(1.2, 7.0, (N, D)).copy()
    mask = rng.random((N, D)) < frac
    V[mask] = -rng.beta(1.2, 7.0, mask.sum())      # 일부 축을 음수로
    m, _ = mean_cos(V)
    print(f"  {frac:>13.0%}{m:>+13.3f}{spread(V):>11.3f}")
print("\n  음수 비율이 늘수록 코사인이 0 으로 간다 — 하지만 **완화 재료는 실제로 소수**다.")
print("  15% 를 넘기려면 없는 음수를 지어내야 하고, 그것은 근거 없는 축과 같은 문제다.")

# ── 유사도 함수를 바꾸면? ──────────────────────────────────
print("\n\n═══ 코사인 말고 다른 유사도를 쓰면 ═══\n")
V = rng.beta(1.2, 7.0, (N, D))
Vc = V - V.mean(0)


def pair_stats(S):
    iu = np.triu_indices(len(V), 1)
    x = S[iu]
    return float(x.mean()), float(x.std()), float(x.std() / (abs(x.mean()) + 1e-9))


Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
Vcn = Vc / (np.linalg.norm(Vc, axis=1, keepdims=True) + 1e-9)
D2 = -np.linalg.norm(V[:, None] - V[None], axis=2)
sims = {
    "코사인 (0~1 원본)": Vn @ Vn.T,
    "코사인 (중심화 후)": Vcn @ Vcn.T,
    "음의 L2 거리": D2,
    "내적 (정규화 없음)": V @ V.T,
}
print(f"  {'유사도':<22}{'평균':>10}{'표준편차':>10}{'변동계수 (↑좋음)':>16}")
print("  " + "-" * 60)
for k, S in sims.items():
    m, sd, cv = pair_stats(S)
    print(f"  {k:<22}{m:>+10.3f}{sd:>10.3f}{cv:>16.2f}")
print("\n  변동계수 = 표준편차/|평균|. **클수록 값이 잘 벌어져 순위를 만든다.**")
