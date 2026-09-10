"""점수 계산과 추천 이유 선택. 순수 함수만 둡니다.

여기에 있는 것은 전부 입력만 보고 결과를 내는 함수입니다 — DB 도, 시각도,
난수도 보지 않습니다. 03 의 4절이 판단과 순위를 외부 서비스에 맡기지 말라고
하는 이유가 이것입니다: 재현되지 않으면 디버깅할 수 없습니다.

주고받는 모델은 `features/recommend/stage.py` 에 있습니다. 이 파일은 그 모델을
읽기만 하고 정의하지 않습니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from features.recommend.enums import (
    CANDIDATE_KEEP,
    FEATURE_KEYS,
    PROPENSITY_SEMANTICS,
    REQUIRED_TRACE_PARAMS,
)
from features.recommend.stage import RankedItem, ScoredCandidate

# ─────────────────────────────────────────────────────────────────
# 추천 이유 선택 — z-salience (설계 5-5) *(v1.9)*
#
# 주의: `contrib = w·f` 의 최댓값으로 이유를 고르면 이유가 한 종류로 붕괴한다.
#    측정: 후보 500건 시뮬레이션에서 Top-20 의 이유가 100% `f_coverage` 였다.
#    ① 이 곧 max_missing 으로 걸러낸 뒤라 상위 후보의 f_coverage 는 항상 1.0 근처이고,
#    ② w(f_coverage)=0.24 가 최대 가중치이므로 w·f 의 argmax 가 사실상 고정된다.
#
#    이유는 "점수가 높은 이유"가 아니라 "다른 후보와 달라서 뽑힌 이유" 여야 한다.
#    따라서 같은 요청의 후보 집합을 기준으로 표준화한다.
# ─────────────────────────────────────────────────────────────────
#: sigma 하한. 없으면 z-salience 가 무의미한 차이를 증폭한다.
#:    후보 500건의 `f_coverage` 가 전부 0.98~0.99 라면 sigma≈0.003 이고,
#:    0.01 차이가 z=3 으로 튀어 그것이 추천 이유가 된다. 유저가 지각할 수 없는 차이다.
#:    모든 피처가 0~1 로 정규화돼 있으므로(설계 5-2-1) 5% 를 지각 하한으로 둔다.
SIGMA_FLOOR = 0.05


def feature_stats(cands: Sequence[ScoredCandidate]) -> dict[str, tuple[float, float]]:
    """후보 집합의 피처별 (평균, 표준편차). None 은 제외하고 계산한다."""
    out: dict[str, tuple[float, float]] = {}
    for k in FEATURE_KEYS:
        raw = [c.features.get(k) for c in cands]
        vals = [v for v in raw if v is not None]
        if not vals:
            out[k] = (0.0, SIGMA_FLOOR)
            continue
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        out[k] = (mu, max(var**0.5, SIGMA_FLOOR))
    return out


def salience(
    cand: ScoredCandidate, weights: dict[str, float], stats: dict[str, tuple[float, float]]
) -> dict[str, float]:
    """w·(f-μ)/sigma — 후보 집합 대비 이 레시피가 두드러진 정도."""
    out = {}
    for k in FEATURE_KEYS:
        w = weights.get(k, 0.0)
        f = cand.features.get(k)
        if w <= 0 or f is None:
            continue
        mu, sd = stats.get(k, (0.0, 1.0))
        out[k] = w * (f - mu) / sd
    return out


def top_reasons(
    cand: ScoredCandidate,
    weights: dict[str, float],
    stats: dict[str, tuple[float, float]],
    n: int = 2,
) -> list[str]:
    """이유 템플릿에 쓸 상위 n개 피처.

    **2개를 쓰는 것이 기본이다.** 1개만 쓰면 sigma 로 표준화해도 분포가 뾰족한 피처
    (`f_expiring` — 대부분 0, 가끔 1) 가 목록을 다시 지배한다. 측정에서 85% 였다.
    """
    sal = salience(cand, weights, stats)
    return [k for k, _ in sorted(sal.items(), key=lambda kv: -kv[1])[:n]]


def merge_served_detail(
    scored: Sequence[ScoredCandidate], items: Sequence[RankedItem]
) -> list[ScoredCandidate]:
    """③ 산출(RankedItem)을 ② 산출(ScoredCandidate) 위에 덮어쓴다.

    🔴 **propensity 는 `RankedItem` 에만 있다.** `ScoredCandidate` 에는 없고,
       `keep_candidates()` 는 `ScoredCandidate` 를 돌려준다. 그래서 이 병합을
       건너뛰면 저장되는 후보가 전부 ② 투영이라 **propensity 가 로그에 단 한 번도
       남지 않는다** — off-policy 평가의 IPS 분모가 통째로 사라진다.
       같은 이유로 `is_exploration`·`team`·`mmr_penalty`·`explore_source` 도 잃는다.

    propensity 는 서빙 순간의 MC 값이 유일본이다. `user_cluster_stat` 이 갱신되면
    사후 재계산이 불가능하므로 **이 자리에서 안 실으면 영원히 없다.**

    점수 내림차순 순서는 `scored` 것을 그대로 쓴다 — 절단 기준이 순서이기 때문이다.

        >>> merged = merge_served_detail(scored, ranked_items)
        >>> kept   = keep_candidates(merged, served, serving_mode)
        >>> all(hasattr(c, "propensity") for c in kept if c.recipe_id in set(served))
        True
    """
    by_id = {it.recipe_id: it for it in items}
    return [by_id.get(c.recipe_id, c) for c in scored]


def keep_candidates(
    candidates: Sequence[ScoredCandidate], served: Sequence[int], serving_mode: str = "real"
) -> list[ScoredCandidate]:
    """🔴 저장할 candidates 를 고른다 — **`served ⊆ candidates` 를 보장한다** (S0 ① 확정).

    후보 500건을 다 저장하면 1행이 100KB 를 넘는다. 그래서 상위 N 만 남기는데,
    **exploration 아이템은 상위 200 풀에서 뽑히므로 그 N 밖으로 떨어질 수 있다.**
    하필 그것이 **propensity ≠ 1.0 인 유일한 행**이라, 잘리면 off-policy 평가에
    필요한 것만 정확히 사라진다. 실험 기록에서 대조군만 빼먹는 것과 같다.

    그래서 **절단한 뒤 실제 노출분을 합집합한다.** 최대 +2건, 한 행에 약 0.5KB 다.

    🔴 **`merge_served_detail()` 을 먼저 통과시켜라.** 이 함수는 `recipe_id` 만 읽으므로
       `RankedItem` 이 섞여 있어도 그대로 보존한다 — 그래야 노출분에 propensity 가 실린다.

        >>> kept = keep_candidates(scored, served=[c.recipe_id for c in ranked])
        >>> set(served) <= {c.recipe_id for c in kept}
        True
    """
    n = CANDIDATE_KEEP.get(serving_mode, 50)
    if n is None or n <= 0:
        return []
    head = list(candidates[:n])
    have = {c.recipe_id for c in head}
    rest = {c.recipe_id: c for c in candidates if c.recipe_id not in have}
    for rid in served:  # 순서를 보존해 재현성을 지킨다
        if rid not in have and rid in rest:
            head.append(rest[rid])
            have.add(rid)
    return head


def check_trace_params(params: dict[str, Any]) -> list[str]:
    """`StageInfo.params` 에 동결 키가 다 있는지. 없는 키 목록을 돌려준다.

    값이 아니라 **정의**가 소급 불가다 — 로그가 있어도 이 키들이 없으면
    propensity 를 재구성할 수 없다 (07 E-3 ①).
    """
    missing = [k for k in REQUIRED_TRACE_PARAMS if k not in params]
    if params.get("propensity_semantics") not in (None, PROPENSITY_SEMANTICS):
        missing.append(f"propensity_semantics!={PROPENSITY_SEMANTICS}")
    return missing
