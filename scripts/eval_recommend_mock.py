"""Mock 픽스처로 추천 파이프라인을 끝까지 돌려 동작을 눈으로 확인하고 요약 지표를 냅니다.

실행: uv run python scripts/eval_recommend_mock.py [--persona 1001] [--no-latency] [--only-latency]

DB 없이 tests/fixtures/recommend 만 씁니다. 정답 라벨이 없으므로 정확도가 아니라
"엔진이 명세대로 움직이는가"를 봅니다. 결과는 docs/plan 의 검증 기록에 옮겨 적습니다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import statistics
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from time import perf_counter
from types import ModuleType

from features.recommend import service
from features.recommend.engine import candidate, context, explain, feedback, penalty, rank, rerank
from features.recommend.schema import (
    FLAVOR_AXES,
    CorpusStats,
    RankConfig,
    RecipeCandidate,
    RecommendRequest,
    UserHistory,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "recommend"
GENERATOR = ROOT / "scripts" / "generate_mock_fixtures.py"
TOP_N_FOR_TASTE = 5
LATENCY_POOL_SIZE = 3000
LATENCY_RUNS = 30
SPICY_THRESHOLD = 0.75
REASON_KINDS = (
    ("지금 있는 재료만으로", "match_ready"),
    ("만 더 있으면", "match_missing"),
    ("소비기한", "expiring"),
    ("좋아하시는", "taste"),
    ("많은 분들이", "quality"),
    ("완성", "ctx"),
    ("평소와 다른", "exploration"),
)


def load_catalog() -> dict[str, object]:
    return json.loads((FIXTURE_DIR / "catalog.json").read_text(encoding="utf-8"))


def load_personas() -> list[dict[str, object]]:
    files = sorted((FIXTURE_DIR / "personas").glob("persona_*.json"))
    return [json.loads(file.read_text(encoding="utf-8")) for file in files]


def build_corpus(catalog: dict[str, object], pool: Sequence[RecipeCandidate]) -> CorpusStats:
    axes = zip(*(recipe.flavor_vec for recipe in pool), strict=True)
    mean = [sum(axis) / len(pool) for axis in axes]
    frequency: dict[int, int] = {}
    for recipe in pool:
        for ingredient in recipe.all_ids:
            frequency[ingredient] = frequency.get(ingredient, 0) + 1
    names = catalog["ingredients"]
    if not isinstance(names, dict):
        raise TypeError("catalog.ingredients must be a dict")
    return CorpusStats(
        flavor_mean=(mean[0], mean[1], mean[2]),
        ingredient_idf={i: math.log(len(pool) / count) for i, count in frequency.items()},
        ingredient_names={int(key): str(name) for key, name in names.items()},
    )


def resolve_allergy(catalog: dict[str, object], codes: Iterable[str]) -> frozenset[int]:
    groups = catalog["allergen_groups"]
    if not isinstance(groups, dict):
        raise TypeError("catalog.allergen_groups must be a dict")
    return frozenset(int(i) for code in codes for i in groups.get(code, []))


def jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def intra_list_distance(recipes: Sequence[RecipeCandidate]) -> float:
    """서빙 목록의 평균 쌍별 재료 거리(1 - 자카드). 클수록 다양합니다."""
    pairs = [
        1.0 - jaccard(a.all_ids, b.all_ids) for i, a in enumerate(recipes) for b in recipes[i + 1 :]
    ]
    return statistics.fmean(pairs) if pairs else 0.0


def reason_kind(reason: str) -> str:
    return next((kind for needle, kind in REASON_KINDS if needle in reason), "default")


def dominant_axis(taste_vec: tuple[float, float, float], mean: tuple[float, float, float]) -> int:
    return max(range(3), key=lambda i: abs(taste_vec[i] - mean[i]))


def fmt3(values: Iterable[float]) -> str:
    return "(" + ", ".join(f"{v:.3f}" for v in values) + ")"


def prepared(
    persona: dict[str, object], catalog: dict[str, object]
) -> tuple[RecommendRequest, frozenset[int]]:
    request = RecommendRequest.model_validate(persona)
    return request, resolve_allergy(catalog, request.allergy_group_codes)


def evaluate_persona(
    persona: dict[str, object],
    pool: Sequence[RecipeCandidate],
    corpus: CorpusStats,
    catalog: dict[str, object],
    cfg: RankConfig,
    rng: random.Random,
) -> dict[str, object]:
    request, allergy = prepared(persona, catalog)
    history = UserHistory(allergy_ingredient_ids=allergy)
    ctx = context.build_context(request, history, cfg)
    result = service.run_pipeline(ctx, pool, corpus, cfg, rng)
    by_id = {recipe.recipe_id: recipe for recipe in pool}
    served = result.response.recommendations
    personal = [item for item in served if not item.is_exploration]
    explored = [item for item in served if item.is_exploration]
    served_recipes = [by_id[item.recipe_id] for item in served]
    candidates = [scored.candidate for scored in result.log.candidates]
    ranked = sorted(result.log.candidates, key=lambda s: (-s.score, s.candidate.recipe_id))
    baseline = [scored.candidate for scored in ranked][: len(served)]
    blocks_of = {s.candidate.recipe_id: s.blocks for s in result.log.candidates}

    mean = corpus.flavor_mean or (0.0, 0.0, 0.0)
    axis = dominant_axis(ctx.taste_vec, mean)
    top_personal = [by_id[item.recipe_id] for item in personal[:TOP_N_FOR_TASTE]]
    served_axis = (
        statistics.fmean(r.flavor_vec[axis] for r in top_personal) if top_personal else 0.0
    )
    pool_axis = statistics.fmean(r.flavor_vec[axis] for r in candidates) if candidates else 0.0
    expected_sign = 1.0 if ctx.taste_vec[axis] >= mean[axis] else -1.0

    uses_expiring = [bool(by_id[i.recipe_id].essential_ids & ctx.expiring_ids) for i in personal]
    pool_expiring = [bool(r.essential_ids & ctx.expiring_ids) for r in candidates]
    scores = [item.match_score for item in served]
    meta = result.response.meta

    print(f"\n=== user {request.user_id} | pantry {len(ctx.pantry_ids)}", end="")
    print(f" | expiring {sorted(ctx.expiring_ids)} | top_k {ctx.top_k}")
    print(f"    taste {fmt3(request.taste_preference.as_vector())}", end="")
    print(f" | cuisines {sorted(ctx.preferred_cuisines)} | max_min {ctx.max_cook_minutes}", end="")
    print(f" | allergy {request.allergy_group_codes}")
    print(f"    stage={meta.fallback_stage} degraded={meta.degraded}", end="")
    print(f" candidates={meta.candidate_count} latency={meta.latency_ms}ms", end="")
    print(f" served={len(served)} exploration={len(explored)}")
    for item in served:
        recipe = by_id[item.recipe_id]
        compact = " ".join(
            f"{name[0]}={'-' if value is None else f'{value:.2f}'}"
            for name, value in blocks_of[item.recipe_id].items()
        )
        flag = "X" if item.is_exploration else " "
        print(f"    {item.rank:>2} {flag} {item.match_score:.3f} [{compact}]", end="")
        print(f" miss={item.missing_count} {recipe.cuisine} {recipe.cook_minutes:>3}m", end="")
        print(f" {item.recipe_title} | {item.reason}")

    cap = ctx.max_cook_minutes
    checks: dict[str, object] = {
        "allergy_clean": all(not (r.all_ids & allergy) for r in served_recipes),
        "cook_cap_personal": cap is None
        or all((by_id[i.recipe_id].cook_minutes or 0) <= cap for i in personal),
        "missing_le_2_personal": all(i.missing_count <= cfg.max_missing for i in personal),
        "exploration_count": len(explored),
        "exploration_novel": all(
            by_id[i.recipe_id].cuisine not in ctx.preferred_cuisines for i in explored
        ),
        "taste_axis": FLAVOR_AXES[axis],
        "taste_lift": (served_axis - pool_axis) * expected_sign,
        "expiring_share_served": statistics.fmean(uses_expiring) if uses_expiring else 0.0,
        "expiring_share_pool": statistics.fmean(pool_expiring) if pool_expiring else 0.0,
        "ild_served": intra_list_distance(served_recipes),
        "ild_baseline": intra_list_distance(baseline),
        "score_min": min(scores) if scores else 0.0,
        "score_max": max(scores) if scores else 0.0,
        "score_std": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        "reason_placeholder": any("None" in i.reason or "{" in i.reason for i in served),
        "reason_kinds": sorted({reason_kind(i.reason) for i in served}),
        "served_ids": [i.recipe_id for i in served],
        "served_popularity": statistics.fmean(r.popularity_score or 0.0 for r in served_recipes),
        "pool_popularity": statistics.fmean(r.popularity_score or 0.0 for r in candidates)
        if candidates
        else 0.0,
        "stage": meta.fallback_stage,
        "latency_ms": meta.latency_ms,
    }
    print(f"    checks: allergy={checks['allergy_clean']}", end="")
    print(f" cook_cap={checks['cook_cap_personal']}", end="")
    print(f" miss<=2={checks['missing_le_2_personal']}", end="")
    print(f" expl={checks['exploration_count']}/novel={checks['exploration_novel']}", end="")
    print(f" taste[{checks['taste_axis']}]lift={float(str(checks['taste_lift'])):+.3f}")
    print(f"            expiring served={float(str(checks['expiring_share_served'])):.2f}", end="")
    print(f" pool={float(str(checks['expiring_share_pool'])):.2f}", end="")
    print(f" | ILD served={float(str(checks['ild_served'])):.3f}", end="")
    print(f" baseline={float(str(checks['ild_baseline'])):.3f}", end="")
    print(f" | score {float(str(checks['score_min'])):.3f}", end="")
    print(f"~{float(str(checks['score_max'])):.3f}", end="")
    print(f" std={float(str(checks['score_std'])):.3f} | reasons={checks['reason_kinds']}")
    return checks


def penalty_scenario(
    persona: dict[str, object],
    pool: Sequence[RecipeCandidate],
    corpus: CorpusStats,
    catalog: dict[str, object],
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    """상위 1위 레시피를 '최근 조리'로 표시하면 점수가 절반이 되고 순위가 내려가는지."""
    request, allergy = prepared(persona, catalog)
    cold = UserHistory(allergy_ingredient_ids=allergy)
    before = service.run_pipeline(context.build_context(request, cold, cfg), pool, corpus, cfg, rng)
    top = next(i for i in before.response.recommendations if not i.is_exploration)
    cooked = UserHistory(
        allergy_ingredient_ids=allergy, cooked_recipe_ids=frozenset({top.recipe_id})
    )
    after = service.run_pipeline(
        context.build_context(request, cooked, cfg), pool, corpus, cfg, rng
    )
    score_after = next(
        s.score for s in after.log.candidates if s.candidate.recipe_id == top.recipe_id
    )
    rank_after = next(
        (i.rank for i in after.response.recommendations if i.recipe_id == top.recipe_id), None
    )
    print(f"\n=== penalty scenario (user {request.user_id})", end="")
    print(f" recipe {top.recipe_id} '{top.recipe_title}'")
    print(f"    score {top.match_score:.3f} -> {score_after:.3f}", end="")
    print(f" (ratio {score_after / top.match_score:.2f})")
    print(f"    rank {top.rank} -> {rank_after if rank_after is not None else 'out of top-k'}")


def feedback_scenario(
    persona: dict[str, object],
    pool: Sequence[RecipeCandidate],
    corpus: CorpusStats,
    catalog: dict[str, object],
    cfg: RankConfig,
    rng: random.Random,
) -> None:
    """중립 취향 사용자가 매운 레시피를 20번 조리하면 상위 목록의 매운맛 평균이 오르는지."""
    request, allergy = prepared(persona, catalog)
    by_id = {recipe.recipe_id: recipe for recipe in pool}

    def top_spicy(history: UserHistory) -> tuple[float, tuple[float, float, float]]:
        ctx = context.build_context(request, history, cfg)
        result = service.run_pipeline(ctx, pool, corpus, cfg, rng)
        personal = [i for i in result.response.recommendations if not i.is_exploration]
        top = personal[:TOP_N_FOR_TASTE]
        return statistics.fmean(by_id[i.recipe_id].flavor_vec[0] for i in top), ctx.taste_vec

    spicy_recipes = [r for r in pool if r.flavor_vec[0] >= SPICY_THRESHOLD][: cfg.warm_event_count]
    behavior: tuple[float, float, float] | None = None
    for recipe in spicy_recipes:
        behavior = feedback.update_behavior_vector(behavior, recipe.flavor_vec, cfg)
    cold = top_spicy(UserHistory(allergy_ingredient_ids=allergy))
    warm = top_spicy(
        UserHistory(
            allergy_ingredient_ids=allergy,
            behavior_taste_vec=behavior,
            events_count=len(spicy_recipes),
        )
    )
    print(f"\n=== feedback scenario (user {request.user_id})", end="")
    print(f" after {len(spicy_recipes)} spicy cook events")
    print(f"    effective taste {fmt3(cold[1])} -> {fmt3(warm[1])}")
    print(f"    top-{TOP_N_FOR_TASTE} spicy mean {cold[0]:.3f} -> {warm[0]:.3f}")


def determinism_check(
    persona: dict[str, object],
    pool: Sequence[RecipeCandidate],
    corpus: CorpusStats,
    catalog: dict[str, object],
    cfg: RankConfig,
    rng: random.Random,
) -> bool:
    request, allergy = prepared(persona, catalog)
    ctx = context.build_context(request, UserHistory(allergy_ingredient_ids=allergy), cfg)
    runs = [service.run_pipeline(ctx, pool, corpus, cfg, rng) for _ in range(3)]
    score_maps = [{s.candidate.recipe_id: s.score for s in run.log.candidates} for run in runs]
    personal = [
        [i.recipe_id for i in run.response.recommendations if not i.is_exploration] for run in runs
    ]
    same = all(m == score_maps[0] for m in score_maps) and all(p == personal[0] for p in personal)
    print(f"\n=== determinism (user {request.user_id}, 3 runs):", end="")
    print(f" scores and personal slots identical = {same}")
    return same


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_mock_fixtures", GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {GENERATOR}")
    module = importlib.util.module_from_spec(spec)
    # dataclass 데코레이터가 sys.modules 에서 모듈을 찾으므로 실행 전에 등록합니다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def percentile(sorted_values: Sequence[float], share: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, math.ceil(len(sorted_values) * share) - 1)]


def latency_benchmark(corpus: CorpusStats, cfg: RankConfig, rng: random.Random) -> None:
    """후보 500건(candidate_limit)에서 Stage 1~3 전체의 지연시간. DB 조회는 포함하지 않습니다.

    운영에서는 Stage 1 을 SQL 이 맡으므로, 3,000건 풀을 파이썬으로 거르는 첫 단계는
    따로 떼어 보여 줍니다. 엔진 몫은 scoring 이후입니다.
    """
    generator = load_generator()
    big_pool = [
        RecipeCandidate.model_validate(generator.build_recipe(i))
        for i in range(1, LATENCY_POOL_SIZE + 1)
    ]
    request = RecommendRequest(
        user_id=1, pantry_ingredient_ids=list(range(1, 61)), preferred_cuisines=["한식"], top_k=20
    )
    ctx = context.build_context(request, UserHistory(), cfg)
    timings: list[float] = []
    candidates = 0
    for _ in range(LATENCY_RUNS):
        started = perf_counter()
        result = service.run_pipeline(ctx, big_pool, corpus, cfg, rng)
        timings.append((perf_counter() - started) * 1000)
        candidates = result.response.meta.candidate_count
    timings.sort()
    print(
        f"\n=== latency: pool {LATENCY_POOL_SIZE} -> candidates {candidates}, {LATENCY_RUNS} runs"
    )
    print(f"    whole pipeline p50 {percentile(timings, 0.5):.1f}ms", end="")
    print(f" p95 {percentile(timings, 0.95):.1f}ms max {timings[-1]:.1f}ms", end="")
    print("  (target p95 < 58ms)")

    stage_ms: dict[str, list[float]] = {"stage1_py": [], "score": [], "rerank": [], "explain": []}
    for _ in range(LATENCY_RUNS):
        t0 = perf_counter()
        selected = candidate.select_candidates(big_pool, ctx, cfg)
        t1 = perf_counter()
        scored = tuple(
            penalty.apply_penalties(rank.score_candidate(r, ctx, corpus, cfg), ctx, cfg)
            for r in selected.candidates
        )
        t2 = perf_counter()
        served = rerank.rerank(scored, ctx, corpus, cfg, rng)
        t3 = perf_counter()
        stats = explain.block_stats(scored)
        for entry in served:
            explain.explain(
                entry.scored, ctx, corpus, stats, cfg, is_exploration=entry.is_exploration
            )
        t4 = perf_counter()
        parts = ((t1 - t0), (t2 - t1), (t3 - t2), (t4 - t3))
        for name, seconds in zip(stage_ms, parts, strict=True):
            stage_ms[name].append(seconds * 1000)
    print("    stage median ms:", end="")
    for name, values in stage_ms.items():
        print(f" {name}={statistics.median(values):.1f}", end="")
    print()
    engine_only = sorted(
        sum(parts)
        for parts in zip(stage_ms["score"], stage_ms["rerank"], stage_ms["explain"], strict=True)
    )
    print(
        f"    engine only (score+rerank+explain) p50 {percentile(engine_only, 0.5):.1f}ms", end=""
    )
    print(f" p95 {percentile(engine_only, 0.95):.1f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", type=int, default=None, help="run a single user_id")
    parser.add_argument("--no-latency", action="store_true", help="skip the latency benchmark")
    parser.add_argument("--only-latency", action="store_true", help="run only the benchmark")
    args = parser.parse_args()

    catalog = load_catalog()
    recipes = catalog["recipes"]
    if not isinstance(recipes, list):
        raise TypeError("catalog.recipes must be a list")
    pool = [RecipeCandidate.model_validate(row) for row in recipes]
    corpus = build_corpus(catalog, pool)
    cfg = RankConfig()
    rng = random.SystemRandom()
    if args.only_latency:
        latency_benchmark(corpus, cfg, rng)
        return

    personas = load_personas()
    if args.persona is not None:
        personas = [p for p in personas if p["user_id"] == args.persona]

    print(f"pool={len(pool)} corpus_mean={fmt3(corpus.flavor_mean or ())}")
    results = [evaluate_persona(p, pool, corpus, catalog, cfg, rng) for p in personas]

    served_ids: set[int] = set()
    for r in results:
        ids = r["served_ids"]
        if isinstance(ids, list):
            served_ids.update(int(i) for i in ids)
    stages = [str(r["stage"]) for r in results]
    lifts = [round(float(str(r["taste_lift"])), 3) for r in results]
    ild_served = statistics.fmean(float(str(r["ild_served"])) for r in results)
    ild_base = statistics.fmean(float(str(r["ild_baseline"])) for r in results)
    pop_served = statistics.fmean(float(str(r["served_popularity"])) for r in results)
    pop_pool = statistics.fmean(float(str(r["pool_popularity"])) for r in results)

    print(f"\n=== aggregate over {len(results)} personas")
    print(f"    catalog coverage {len(served_ids)}/{len(pool)} = {len(served_ids) / len(pool):.2f}")
    print(f"    fallback stages {dict((s, stages.count(s)) for s in sorted(set(stages)))}")
    print(f"    allergy clean all = {all(bool(r['allergy_clean']) for r in results)}")
    print(f"    cook cap (personal) all = {all(bool(r['cook_cap_personal']) for r in results)}")
    print(
        f"    missing<=2 (personal) all = {all(bool(r['missing_le_2_personal']) for r in results)}"
    )
    print(f"    exploration counts = {[r['exploration_count'] for r in results]}")
    print(f"    taste lift (expected > 0) = {lifts}")
    print(f"    ILD served mean {ild_served:.3f} vs score-only baseline {ild_base:.3f}")
    print(f"    served popularity mean {pop_served:.3f} vs candidate pool {pop_pool:.3f}")
    print(
        f"    reason placeholder anywhere = {any(bool(r['reason_placeholder']) for r in results)}"
    )
    print(f"    latency ms (120-recipe pool) = {[r['latency_ms'] for r in results]}")

    if args.persona is None:
        by_user = {p["user_id"]: p for p in personas}
        determinism_check(by_user[1005], pool, corpus, catalog, cfg, rng)
        penalty_scenario(by_user[1005], pool, corpus, catalog, cfg, rng)
        feedback_scenario(by_user[1002], pool, corpus, catalog, cfg, rng)
        if not args.no_latency:
            latency_benchmark(corpus, cfg, rng)


if __name__ == "__main__":
    main()
