"""Mock 픽스처로 랭킹과 재정렬을 끝까지 돌려 동작을 눈으로 확인하고 요약 지표를 냅니다.

실행: uv run python scripts/eval_recommend_mock.py [--user 1001] [--only-latency]

DB 없이 tests/fixtures/recommend 만 씁니다. 후보 조회는 A 트랙 SQL 함수
`retrieve_candidates` 의 조건을 파이썬으로 흉내 낸 것이며, 조건이 어긋나면 여기 결과와
서빙 결과가 조용히 갈라집니다.

정답 라벨이 없으므로 정확도가 아니라 "엔진이 계약대로 움직이는가"를 봅니다.
결과는 docs/recommend 의 검증 기록에 옮겨 적습니다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Any

from features.recommend import service
from features.recommend.engine import candidate as plan_module
from features.recommend.engine import taste
from features.recommend.engine.context import (
    CorpusStats,
    RecipeFeature,
    UserContext,
    UserHistory,
    build_context,
)
from features.recommend.enums import DEFAULT_WEIGHTS, FEATURE_KEYS, UNAVAILABLE_FEATURES
from features.recommend.policy import RankingPolicy
from features.recommend.stage import Candidate, RankedItem

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "recommend"
GENERATOR = ROOT / "scripts" / "generate_mock_fixtures.py"
TOP_N_FOR_TASTE = 5
LATENCY_POOL = 3000
LATENCY_RUNS = 30
SPICY_THRESHOLD = 0.75
DEFAULT_TOP_K = 20


def load_catalog() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "catalog.json").read_text(encoding="utf-8"))


def load_profiles() -> list[dict[str, Any]]:
    files = sorted((FIXTURE_DIR / "personas").glob("persona_*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def to_recipe(row: Mapping[str, Any]) -> RecipeFeature:
    return RecipeFeature(
        recipe_id=int(row["recipe_id"]),
        title=str(row["title"]),
        essential_ids=frozenset(row["essential_ids"]),
        all_ids=frozenset(row["all_ids"]),
        flavor_vec=taste.as_vector(row["flavor_vec"]),
        popularity_score=row["popularity_score"],
        quality_score=row["quality_score"],
        cook_minutes=row["cook_minutes"],
        cuisine=row["cuisine"],
        product_ids=tuple(row["product_ids"]),
    )


def build_corpus(catalog: Mapping[str, Any], recipes: Mapping[int, RecipeFeature]) -> CorpusStats:
    pool = list(recipes.values())
    mean = [statistics.fmean([axis_of(r, i) for r in pool]) for i in range(taste.AXIS_COUNT)]
    frequency: dict[int, int] = {}
    for recipe in pool:
        for ingredient in recipe.all_ids:
            frequency[ingredient] = frequency.get(ingredient, 0) + 1
    return CorpusStats(
        flavor_mean=taste.as_vector(mean),
        ingredient_idf={i: math.log(len(pool) / c) for i, c in frequency.items()},
        ingredient_names={int(k): str(v) for k, v in catalog["ingredients"].items()},
    )


def axis_of(recipe: RecipeFeature, index: int) -> float:
    value = recipe.flavor_vec[index]
    return 0.0 if value is None else value


def retrieve(
    recipes: Mapping[int, RecipeFeature],
    clusters: Mapping[int, int],
    pantry: Iterable[int],
    *,
    allergy: Iterable[int] = (),
    max_missing: int = 2,
    max_minutes: int | None = None,
    limit: int = 500,
    ignore_missing: bool = False,
) -> list[Candidate]:
    """A 트랙 `retrieve_candidates` 의 WHERE 와 ORDER BY 를 같은 순서로 흉내 냅니다."""
    pantry_set, allergy_set = frozenset(pantry), frozenset(allergy)
    rows: list[tuple[int, float, Candidate]] = []
    for recipe in recipes.values():
        if recipe.all_ids & allergy_set:
            continue
        if max_minutes is not None and (recipe.cook_minutes or 0) > max_minutes:
            continue
        missing = sorted(recipe.essential_ids - pantry_set)
        if not ignore_missing:
            if recipe.essential_ids and not (recipe.essential_ids & pantry_set):
                continue
            if len(missing) > max_missing:
                continue
        total = len(recipe.essential_ids)
        rows.append(
            (
                len(missing),
                -(recipe.popularity_score or 0.0),
                Candidate(
                    recipe_id=recipe.recipe_id,
                    missing_count=len(missing),
                    missing_ids=missing,
                    coverage=1.0 if total == 0 else (total - len(missing)) / total,
                    cluster_id=clusters.get(recipe.recipe_id),
                ),
            )
        )
    rows.sort(key=lambda row: (row[0], row[1], row[2].recipe_id))
    return [row[2] for row in rows[:limit]]


def retrieve_with_fallback(
    recipes: Mapping[int, RecipeFeature],
    clusters: Mapping[int, int],
    ctx: UserContext,
    policy: RankingPolicy,
    top_k: int,
    allergy: frozenset[int] = frozenset(),
) -> tuple[list[Candidate], str, int]:
    """`engine/candidate.py` 의 계획대로 다시 조회합니다. 운영에서는 repository 가 합니다."""
    plan = plan_module.first_plan(policy)
    rows = retrieve(
        recipes,
        clusters,
        ctx.pantry_ids,
        allergy=allergy,
        max_missing=plan.max_missing,
        max_minutes=ctx.max_cook_minutes,
    )
    while True:
        nxt = plan_module.next_plan(plan, len(rows), policy, top_k)
        if nxt is None:
            return plan_module.dedupe(rows), plan.stage, plan.max_missing
        plan = nxt
        wider = retrieve(
            recipes,
            clusters,
            ctx.pantry_ids,
            allergy=allergy,
            max_missing=plan.max_missing,
            max_minutes=ctx.max_cook_minutes,
            ignore_missing=plan.stage == plan_module.FALLBACK_POPULARITY,
        )
        rows = plan_module.dedupe([*rows, *wider])


def context_of(profile: Mapping[str, Any], policy: RankingPolicy) -> UserContext:
    """온보딩은 앞 3축만 채웁니다. 뒤 3축은 None 이라 계산에서 빠집니다."""
    preference = profile.get("taste_preference", {})
    onboarding = [
        preference.get("spicy_level"),
        preference.get("salty_level"),
        preference.get("sweet_level"),
    ]
    return build_context(
        user_id=int(profile["user_id"]),
        pantry_ids=profile.get("pantry_ingredient_ids", []),
        expiring_ids=profile.get("expiring_ingredient_ids", []),
        onboarding_taste=[None if v is None else v / 4 for v in onboarding],
        warm_event_count=policy.warm_event_count,
        max_cook_minutes=profile.get("max_cook_minutes"),
        preferred_cuisines=cuisines_of(profile.get("preferred_cuisines", [])),
    )


def cuisines_of(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [p.strip() for p in raw.replace("/", ",").split(",") if p.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if x]
    return []


def jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def intra_list_distance(items: Sequence[frozenset[int]]) -> float:
    pairs = [1.0 - jaccard(a, b) for i, a in enumerate(items) for b in items[i + 1 :]]
    return statistics.fmean(pairs) if pairs else 0.0


def measured_features(items: Sequence[RankedItem]) -> list[str]:
    """한 번이라도 값이 나온 피처. 나머지는 수단이 없거나 데이터가 없는 것입니다."""
    return sorted(
        key for key in FEATURE_KEYS if any(i.features.get(key) is not None for i in items)
    )


def dominant_axis_index(user: tuple[float | None, ...], mean: tuple[float | None, ...]) -> int:
    axes = taste.shared_axes(user, mean)
    if not axes:
        return 0
    return max(axes, key=lambda i: abs(value_at(user, i) - value_at(mean, i)))


def value_at(vector: tuple[float | None, ...], index: int) -> float:
    value = vector[index]
    return 0.0 if value is None else value


def fmt(vector: tuple[float | None, ...]) -> str:
    return "(" + ", ".join("-" if v is None else f"{v:.2f}" for v in vector) + ")"


def evaluate(
    profile: Mapping[str, Any],
    recipes: Mapping[int, RecipeFeature],
    clusters: Mapping[int, int],
    corpus: CorpusStats,
    allergen_groups: Mapping[str, list[int]],
    policy: RankingPolicy,
    rng: random.Random,
) -> dict[str, Any]:
    user_id = int(profile["user_id"])
    top_k = int(profile.get("top_k", DEFAULT_TOP_K))
    allergy = frozenset(
        i for code in profile.get("allergy_group_codes", []) for i in allergen_groups.get(code, [])
    )
    ctx = context_of(profile, policy)
    candidates, stage, max_missing = retrieve_with_fallback(
        recipes, clusters, ctx, policy, top_k, allergy
    )
    result = service.rank_candidates(
        candidates,
        recipes,
        ctx,
        corpus,
        policy,
        rng,
        top_k=top_k,
        rng_seed=user_id,
        max_missing_final=max_missing,
    )
    items = result.items
    personal = [i for i in items if not i.is_exploration]
    explored = [i for i in items if i.is_exploration]

    print(f"\n=== user {user_id} | pantry {len(ctx.pantry_ids)}", end="")
    print(f" | expiring {sorted(ctx.expiring_ids)} | top_k {top_k}")
    print(f"    taste {fmt(ctx.taste_vec)} | cuisines {sorted(ctx.preferred_cuisines)}", end="")
    print(f" | max_min {ctx.max_cook_minutes} | allergy {profile.get('allergy_group_codes', [])}")
    print(f"    stage={stage} k={max_missing} candidates={len(candidates)}", end="")
    print(f" latency={result.latency_ms}ms served={len(items)} exploration={len(explored)}")
    for item in items:
        recipe = recipes[item.recipe_id]
        active = " ".join(
            f"{key[2:6]}={item.features[key]:.2f}"
            for key in FEATURE_KEYS
            if item.features.get(key) is not None and DEFAULT_WEIGHTS.get(key, 0.0) > 0
        )
        flag = "X" if item.is_exploration else " "
        propensity = item.propensity or 0.0
        print(f"    {item.final_rank:>2} {flag} {item.score:.3f} p={propensity:.3f}", end="")
        print(f" [{active}] miss={item.missing_count} {recipe.cuisine}", end="")
        print(f" {recipe.cook_minutes:>3}m {recipe.title} | {item.reason}")

    mean = corpus.flavor_mean or taste.as_vector(None)
    axis = dominant_axis_index(ctx.taste_vec, mean)
    top = [recipes[i.recipe_id] for i in personal[:TOP_N_FOR_TASTE]]
    served_axis = statistics.fmean([axis_of(r, axis) for r in top]) if top else 0.0
    pool_axis = statistics.fmean([axis_of(recipes[c.recipe_id], axis) for c in candidates])
    sign = 1.0 if value_at(ctx.taste_vec, axis) >= value_at(mean, axis) else -1.0
    uses_expiring = [bool(recipes[i.recipe_id].essential_ids & ctx.expiring_ids) for i in personal]
    pool_expiring = [
        bool(recipes[c.recipe_id].essential_ids & ctx.expiring_ids) for c in candidates
    ]
    checks: dict[str, Any] = {
        "user_id": user_id,
        "stage": stage,
        # 알레르기가 없는 사람은 이 판정의 대상이 아닙니다. 대상 수를 함께 내지 않으면
        # "12명 전원 통과" 가 실은 4명만 검사한 결과라는 것이 숨습니다.
        "allergy_clean": all(not (recipes[i.recipe_id].all_ids & allergy) for i in items),
        "allergy_tested": bool(allergy),
        "cook_cap": ctx.max_cook_minutes is None
        or all((recipes[i.recipe_id].cook_minutes or 0) <= ctx.max_cook_minutes for i in personal),
        "cook_cap_tested": ctx.max_cook_minutes is not None,
        "propensity_ok": all(i.propensity is not None and 0 < i.propensity <= 1 for i in items),
        "exploration": len(explored),
        "explore_sources": sorted({str(i.explore_source) for i in explored}),
        "taste_axis": taste.FLAVOR_AXES[axis],
        "taste_lift": (served_axis - pool_axis) * sign,
        "expiring_served": statistics.fmean(uses_expiring) if uses_expiring else 0.0,
        "expiring_pool": statistics.fmean(pool_expiring) if pool_expiring else 0.0,
        "ild": intra_list_distance([recipes[i.recipe_id].all_ids for i in items]),
        "score_min": min(i.score for i in items),
        "score_max": max(i.score for i in items),
        "reason_ok": all(bool(i.reason) and "{" not in i.reason for i in items),
        "measured": measured_features(items),
        "features": [dict(i.features) for i in items],
        "served_ids": [i.recipe_id for i in items],
        "latency_ms": result.latency_ms,
    }
    print(f"    checks: allergy={checks['allergy_clean']} cook_cap={checks['cook_cap']}", end="")
    print(f" propensity={checks['propensity_ok']} reason={checks['reason_ok']}", end="")
    print(f" expl={checks['exploration']}{checks['explore_sources']}", end="")
    print(f" taste[{checks['taste_axis']}]lift={checks['taste_lift']:+.3f}")
    print(f"            expiring served={checks['expiring_served']:.2f}", end="")
    print(f" pool={checks['expiring_pool']:.2f} | ILD {checks['ild']:.3f}", end="")
    print(f" | score {checks['score_min']:.3f}~{checks['score_max']:.3f}", end="")
    print(f" | measured {len(checks['measured'])}/17")
    return checks


def penalty_scenario(
    profile: Mapping[str, Any],
    recipes: Mapping[int, RecipeFeature],
    clusters: Mapping[int, int],
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """1위 레시피를 최근 조리로 표시하면 점수가 절반이 되는지."""
    ctx = context_of(profile, policy)
    candidates, _, _ = retrieve_with_fallback(recipes, clusters, ctx, policy, DEFAULT_TOP_K)
    before = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)
    top = next(i for i in before.items if not i.is_exploration)
    warm = build_context(
        user_id=ctx.user_id,
        pantry_ids=ctx.pantry_ids,
        expiring_ids=ctx.expiring_ids,
        onboarding_taste=ctx.taste_vec,
        history=UserHistory(cooked_recipe_ids=frozenset({top.recipe_id})),
        warm_event_count=policy.warm_event_count,
        max_cook_minutes=ctx.max_cook_minutes,
        preferred_cuisines=ctx.preferred_cuisines,
    )
    after = service.rank_candidates(candidates, recipes, warm, corpus, policy, rng)
    scored = next(s for s in after.scored if s.recipe_id == top.recipe_id)
    rank_after = next((i.final_rank for i in after.items if i.recipe_id == top.recipe_id), None)
    title = recipes[top.recipe_id].title
    print(f"\n=== penalty (user {ctx.user_id}) recipe {top.recipe_id} {title}")
    print(f"    score {top.score:.3f} -> {scored.score:.3f}", end="")
    print(f" (penalty {scored.penalty:.2f})", end="")
    print(f" rank {top.final_rank} -> {rank_after if rank_after else 'out of top-k'}")


def feedback_scenario(
    profile: Mapping[str, Any],
    recipes: Mapping[int, RecipeFeature],
    clusters: Mapping[int, int],
    corpus: CorpusStats,
    policy: RankingPolicy,
    rng: random.Random,
) -> None:
    """매운 레시피를 조리하면 상위 목록의 매운맛이 오르는지. 뒤 3축도 함께 배웁니다."""
    base = context_of(profile, policy)
    spicy = [r for r in recipes.values() if (r.flavor_vec[0] or 0.0) >= SPICY_THRESHOLD]
    used = spicy[: policy.warm_event_count]
    behavior: tuple[float | None, ...] | None = None
    for recipe in used:
        behavior = taste.update_behavior(behavior, recipe.flavor_vec, policy.ema_gamma)

    def top_spicy(history: UserHistory) -> tuple[float, tuple[float | None, ...]]:
        ctx = build_context(
            user_id=base.user_id,
            pantry_ids=base.pantry_ids,
            expiring_ids=base.expiring_ids,
            onboarding_taste=base.taste_vec,
            history=history,
            warm_event_count=policy.warm_event_count,
            max_cook_minutes=base.max_cook_minutes,
            preferred_cuisines=base.preferred_cuisines,
        )
        candidates, _, _ = retrieve_with_fallback(recipes, clusters, ctx, policy, DEFAULT_TOP_K)
        result = service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng)
        personal = [i for i in result.items if not i.is_exploration][:TOP_N_FOR_TASTE]
        return statistics.fmean([axis_of(recipes[i.recipe_id], 0) for i in personal]), ctx.taste_vec

    cold = top_spicy(UserHistory())
    warm = top_spicy(UserHistory(behavior_taste_vec=behavior, events_count=len(used)))
    print(f"\n=== feedback (user {base.user_id}) after {len(used)} spicy cooks")
    print(f"    effective taste {fmt(cold[1])} -> {fmt(warm[1])}")
    print(f"    top-{TOP_N_FOR_TASTE} spicy mean {cold[0]:.3f} -> {warm[0]:.3f}")


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_mock_fixtures", GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    # dataclass 데코레이터가 sys.modules 에서 모듈을 찾으므로 실행 전에 등록합니다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def latency_benchmark(corpus: CorpusStats, policy: RankingPolicy, rng: random.Random) -> None:
    """후보 500건에서 랭킹과 재정렬의 지연시간. DB 왕복은 포함하지 않습니다."""
    generator = load_generator()
    rows = [generator.build_recipe(i) for i in range(1, LATENCY_POOL + 1)]
    recipes = {int(r["recipe_id"]): to_recipe(r) for r in rows}
    clusters = {int(r["recipe_id"]): int(r["cluster_id"]) for r in rows}
    ctx = build_context(
        user_id=1,
        pantry_ids=range(1, 61),
        warm_event_count=policy.warm_event_count,
        preferred_cuisines=["한식"],
    )
    candidates = retrieve(recipes, clusters, ctx.pantry_ids, limit=policy.candidate_limit)

    timings: list[float] = []
    for _ in range(LATENCY_RUNS):
        started = perf_counter()
        service.rank_candidates(candidates, recipes, ctx, corpus, policy, rng, top_k=DEFAULT_TOP_K)
        timings.append((perf_counter() - started) * 1000)
    timings.sort()
    print(f"\n=== latency: pool {LATENCY_POOL} -> candidates {len(candidates)}", end="")
    print(f", {LATENCY_RUNS} runs")
    print(f"    p50 {percentile(timings, 0.5):.1f}ms p95 {percentile(timings, 0.95):.1f}ms", end="")
    print(f" max {timings[-1]:.1f}ms  (target p95 < 58ms)")


def report_dead_weight(results: Sequence[Mapping[str, Any]]) -> None:
    """순위를 바꾸지 못하는 가중치를 냅니다.

    전건 None 이거나 값이 하나뿐인 피처는 가중치가 아무리 커도 순서를 못 바꿉니다.
    에러가 나지 않으므로 이 줄이 없으면 아무도 알아채지 못합니다 (검증 기록 F-28).
    """
    seen: dict[str, set[float]] = {key: set() for key in FEATURE_KEYS}
    for row in results:
        for item in row["features"]:
            for key, value in item.items():
                if value is not None:
                    seen[key].add(round(float(value), 6))
    dead = {
        key: DEFAULT_WEIGHTS.get(key, 0.0)
        for key in FEATURE_KEYS
        if DEFAULT_WEIGHTS.get(key, 0.0) > 0 and len(seen[key]) <= 1
    }
    live = {key: len(values) for key, values in seen.items() if len(values) > 1}
    print(f"    값이 변하는 피처 {len(live)}/17 = {dict(sorted(live.items()))}")
    print(f"    순위를 못 바꾸는 가중치 {sum(dead.values()):.2f}/1.00 = {dead}")


def percentile(values: Sequence[float], share: float) -> float:
    return values[min(len(values) - 1, math.ceil(len(values) * share) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=int, default=None, help="한 사람만 돌립니다")
    parser.add_argument("--only-latency", action="store_true", help="지연시간만 잽니다")
    args = parser.parse_args()

    catalog = load_catalog()
    recipes = {int(r["recipe_id"]): to_recipe(r) for r in catalog["recipes"]}
    clusters = {int(r["recipe_id"]): int(r["cluster_id"]) for r in catalog["recipes"]}
    corpus = build_corpus(catalog, recipes)
    policy = RankingPolicy()
    rng = random.SystemRandom()

    if args.only_latency:
        latency_benchmark(corpus, policy, rng)
        return

    profiles = load_profiles()
    if args.user is not None:
        profiles = [p for p in profiles if p["user_id"] == args.user]

    print(f"pool={len(recipes)} corpus_mean={fmt(corpus.flavor_mean or ())}")
    print(f"active weights={sorted(k for k, w in DEFAULT_WEIGHTS.items() if w > 0)}")
    print(f"unavailable={sorted(UNAVAILABLE_FEATURES)}")
    results = [
        evaluate(p, recipes, clusters, corpus, catalog["allergen_groups"], policy, rng)
        for p in profiles
    ]

    served = {rid for r in results for rid in r["served_ids"]}
    stages = [r["stage"] for r in results]
    measured = sorted({k for r in results for k in r["measured"]})
    print(f"\n=== aggregate over {len(results)} profiles")
    print(f"    catalog coverage {len(served)}/{len(recipes)} = {len(served) / len(recipes):.2f}")
    print(f"    stages { ({s: stages.count(s) for s in sorted(set(stages))}) }")
    n_allergy = sum(r["allergy_tested"] for r in results)
    n_cap = sum(r["cook_cap_tested"] for r in results)
    print(
        f"    allergy clean = {all(r['allergy_clean'] for r in results)}"
        f"  (실효 {n_allergy}/{len(results)}명 — 나머지는 대상이 아니라 자동 참)"
    )
    print(
        f"    cook cap (personal) = {all(r['cook_cap'] for r in results)}"
        f"  (실효 {n_cap}/{len(results)}명)"
    )
    print(f"    propensity in (0,1] = {all(r['propensity_ok'] for r in results)}")
    print(f"    reason filled = {all(r['reason_ok'] for r in results)}")
    print(f"    exploration counts = {[r['exploration'] for r in results]}")
    print(f"    taste lift = {[round(r['taste_lift'], 3) for r in results]}")
    print(f"    ILD mean {statistics.fmean([r['ild'] for r in results]):.3f}")
    print(f"    features measured anywhere {len(measured)}/17 = {measured}")
    print(f"    latency ms = {[r['latency_ms'] for r in results]}")
    report_dead_weight(results)

    if args.user is None:
        by_id = {p["user_id"]: p for p in profiles}
        penalty_scenario(by_id[1005], recipes, clusters, corpus, policy, rng)
        feedback_scenario(by_id[1002], recipes, clusters, corpus, policy, rng)
        latency_benchmark(corpus, policy, rng)


if __name__ == "__main__":
    main()
