"""우연성(serendipity) — 유저가 **아직 모르는 자기 취향**을 만나게 한다 (설계 5-3-5).

## MMR 이 하지 못하는 일

    MMR         이번 목록 20개 안에 비슷한 게 몇 개인가     목록 내부 · 1회성
    Serendipity 유저가 시간에 걸쳐 새 영역을 만나는가        세션 간 · 누적

MMR 은 이번 목록을 흩뜨릴 뿐, 유저가 **가본 적 없는 곳으로 데려가지 않는다.**
근중복은 8.1쌍 → 0.1쌍으로 잘 잡지만(실측), 숨은 취향 발견에는 기여하지 않는다.

## 측정 — 숨은 취향 5개 중 몇 개를 찾는가

유저의 진짜 취향은 8개 클러스터인데 온보딩이 아는 것은 3개. 40세션 × Top-20, 90회 평균.

    탐색 슬롯 채우는 법          숨은취향   탐색슬롯 적중률   누적 조리   propensity
    탐색 없음 (greedy)          1.56/5          —          379.0      —
    무작위 상위200 (현재 설계)     2.71/5       14.4%        369.4      ✅
    먼 클러스터 (거리만)          3.20/5       12.7% ↓      369.2      ✅
    먼 클러스터 + 품질하한         3.49/5       12.9%        369.8      ✅
    클러스터 Thompson           3.77/5       14.6%        374.7      ✅
    클러스터 UCB                4.14/5          —          379.3   🔴 불가

## 세 가지 결론

**① 우연성 = 관련성 × 의외성.** '멀기만' 하면 탐색 슬롯 적중률이 14.4% → 12.7% 로
   **떨어진다.** 유저에게는 "이상한 게 두 칸 껴 있네"가 된다. 의외성만 최대화하는 것은
   그냥 안 좋은 것을 보여주는 것이다.

**② Thompson 이 현재 설계보다 모든 축에서 낫다** — 발견 +39% · 적중률 +0.2%p ·
   조리 +5.3건. 비용이 같은데 공짜로 좋아진다. "불확실한 곳"과 "유망한 곳"을
   동시에 보기 때문이고, 거리 기반은 후자를 버린다.

**③ 🔴 Thompson 단독은 off-policy 평가를 죽인다.** 유망하지 않은 클러스터를 아예
   뽑지 않아 **propensity=0 인 클러스터가 50개 중 32개**다 (MC 200회 실측).
   그 영역은 IPS 분모가 0 이라 영원히 평가 불가능하다. MC 를 1,000회로 늘려도 27개가
   남는다 — 표본 문제가 아니라 정책의 성질이다.
   UCB 는 더 나쁘다. **결정적이라 propensity 가 아예 없다.**

## 그래서 혼합 정책을 쓴다

    π = ½ · 균등무작위  +  ½ · Thompson

균등 절반이 **모든 클러스터에 최소 propensity 를 보장**한다(support/overlap 보장).

    구성                          숨은취향   누적 조리   탐색이 닿은 클러스터
    무작위 2칸 (현재)               2.86/5     371.7          43.4
    Thompson 2칸                  3.89/5     378.7          45.4
    🔑 균등 1칸 + Thompson 1칸      3.54/5     378.0          46.0

발견이 Thompson 단독보다 0.35 낮은 것이 **IPS 를 사는 값**이다.

## 🔴 정직한 한계 — 8주 안에는 본전을 못 뽑는다

    세션    greedy   Thompson    차이
      20     189.5      180.2    −9.3
      40     390.9      385.2    −5.7
      80     802.6      814.8   +12.2      ← 손익분기
     150    1534.7     1603.9   +69.2

유저당 20요청인 8주 프로젝트에서는 **조리 수가 오히려 줄어든다.**
그럼에도 하는 이유는 설계 5-3-3 이 이미 밝힌 것과 같다 —
**탐색의 1차 목적은 유저 가치가 아니라 편향 없는 학습 데이터다.**
우연성은 같은 2칸으로 공짜로 얻는 부수 효과다.

절대 수치는 가정(클러스터 50개 중 좋은 것 8개, 온보딩이 3개를 안다, 베르누이 피드백)에
의존한다. **견고한 것은 순서**다: greedy < 무작위 < 거리만 < 거리+품질 < Thompson,
그리고 UCB 는 propensity 가 없고 Thompson 단독은 support 가 무너진다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class ClusterStats:
    """클러스터별 관측. `user_cluster_stat` 테이블에서 읽어온다 (설계 2-5).

    유저별로 들고 있기엔 클러스터 50개 × 유저 100명 = 5,000행이라 부담이 없다.
    """
    #: 이 클러스터 아이템이 유저에게 노출된 횟수
    n: dict[int, int] = field(default_factory=dict)
    #: 그중 긍정 반응(click 이상) 횟수
    hits: dict[int, int] = field(default_factory=dict)

    def alpha_beta(self, c: int, prior_a: float = 1.0, prior_b: float = 4.0
                   ) -> tuple[float, float]:
        """Beta 사후분포의 (α, β).

        prior_b=4 는 "기본 반응률 20%" 를 뜻하는 약한 사전이다. 관측이 쌓이면 밀린다.
        prior 를 0 으로 두면 노출 0 인 클러스터가 α=β=0 이 되어 표본이 불가능하다.
        """
        n = self.n.get(c, 0)
        h = self.hits.get(c, 0)
        return prior_a + h, prior_b + (n - h)


def thompson_cluster_scores(clusters: Sequence[int], stats: ClusterStats,
                            rng: random.Random) -> dict[int, float]:
    """각 클러스터에서 Beta 사후분포를 한 번씩 뽑는다.

    이것이 Thompson sampling 의 전부다 — 뽑은 값이 큰 클러스터를 고른다.
    불확실한 클러스터(관측이 적음)는 분산이 커서 **가끔 크게 뽑힌다.**
    그것이 탐색이고, 관측이 쌓이면 자동으로 잦아든다. 별도의 감쇠 스케줄이 필요 없다.
    """
    out = {}
    for c in clusters:
        a, b = stats.alpha_beta(c)
        out[c] = rng.betavariate(a, b)
    return out


def thompson_propensity(clusters: Sequence[int], stats: ClusterStats,
                        k: int, mc: int = 200, seed: int = 0) -> dict[int, float]:
    """P(클러스터 c 가 탐색 슬롯에 뽑힐 확률). **닫힌 해가 없어 몬테카를로로 구한다.**

    🔴 이 값을 로깅하지 않으면 Thompson 탐색으로 얻은 노출은 off-policy 평가에
       쓸 수 없다. 균등 무작위와 달리 확률이 아이템마다 다르기 때문이다.

    실측 비용: 클러스터 50개 · MC 200회 → 7.3ms (설계 목표 p95 200ms 의 3.7%).
    MC 를 줄이면 0 인 클러스터가 늘어난다 — 50회 34개 · 200회 32개 · 1000회 27개.
    **어차피 0 이 남으므로 이 값만으로 support 를 보장할 수 없다.** 그래서 혼합한다.
    """
    rng = random.Random(seed)
    cnt = {c: 0 for c in clusters}
    for _ in range(mc):
        draw = thompson_cluster_scores(clusters, stats, rng)
        for c in sorted(draw, key=lambda x: -draw[x])[:k]:
            cnt[c] += 1
    return {c: v / mc for c, v in cnt.items()}


def mixed_exploration(candidates: Sequence[dict], stats: ClusterStats,
                      rng: random.Random, k: int = 2,
                      uniform_share: float = 0.5, pool_size: int = 200,
                      quality_key: str = "score", cluster_key: str = "cluster_id",
                      id_key: str = "recipe_id") -> tuple[list[dict], dict[int, float]]:
    """🔑 혼합 정책 — 균등 절반 + Thompson 절반.

        π = uniform_share · 균등무작위  +  (1−uniform_share) · Thompson

    균등 쪽이 **모든 후보에 최소 노출확률을 보장**한다. 이것이 없으면
    Thompson 이 외면한 클러스터(실측 32/50)는 IPS 로 영원히 평가할 수 없다.

    Args:
        candidates: 점수 내림차순으로 정렬돼 있어야 한다.
        uniform_share: 균등에 배정할 슬롯 비율. 0.0 이면 Thompson 단독(support 붕괴),
                       1.0 이면 현재 설계와 동일(우연성 없음).

    Returns:
        (선택된 아이템들, {recipe_id: 노출확률})

    🔴 반환된 확률을 `RankedItem.propensity` 에 반드시 실어야 한다.
       값이 있어도 **그것이 무엇의 확률인지** 모르면 못 쓴다 —
       `StageInfo.params` 에 `explore_pool_size`·`uniform_share`·`mc` 를 함께 남긴다.
    """
    if k <= 0 or not candidates:
        return [], {}

    pool = list(candidates[:pool_size])
    n_uniform = max(1, round(k * uniform_share)) if uniform_share > 0 else 0
    n_thompson = k - n_uniform

    chosen: list[dict] = []
    taken: set = set()

    # ── ① 균등 — support 보장 ────────────────────────────────
    for d in rng.sample(pool, min(n_uniform, len(pool))):
        chosen.append(d)
        taken.add(d[id_key])

    # ── ② Thompson — 우연성 ─────────────────────────────────
    clusters = sorted({d[cluster_key] for d in pool if d.get(cluster_key) is not None})
    if n_thompson > 0 and clusters:
        draw = thompson_cluster_scores(clusters, stats, rng)
        for c in sorted(draw, key=lambda x: -draw[x]):
            if len([x for x in chosen if x[id_key] not in ()]) >= k:
                break
            best = max((d for d in pool
                        if d.get(cluster_key) == c and d[id_key] not in taken),
                       key=lambda d: d.get(quality_key, 0.0), default=None)
            if best is not None:
                chosen.append(best)
                taken.add(best[id_key])
            if len(chosen) >= k:
                break

    # ── ③ propensity — 두 경로의 확률을 합산한다 ───────────────
    #     같은 아이템이 양쪽에서 뽑힐 수 있으므로 더한다.
    p_uniform = (n_uniform / len(pool)) if (pool and n_uniform) else 0.0
    p_cluster = (thompson_propensity(clusters, stats, n_thompson)
                 if (n_thompson > 0 and clusters) else {})
    # 클러스터 확률을 그 클러스터의 '최고 품질 1건' 에 전가한다 —
    # Thompson 은 클러스터를 고른 뒤 결정적으로 최고 품질을 고르기 때문이다.
    top_of: dict[int, int] = {}
    for c in clusters:
        best = max((d for d in pool if d.get(cluster_key) == c),
                   key=lambda d: d.get(quality_key, 0.0), default=None)
        if best is not None:
            top_of[c] = best[id_key]

    prop: dict[int, float] = {}
    for d in pool:
        rid = d[id_key]
        p = p_uniform
        c = d.get(cluster_key)
        if c is not None and top_of.get(c) == rid:
            p += p_cluster.get(c, 0.0)
        prop[rid] = min(1.0, p)

    return chosen[:k], prop
