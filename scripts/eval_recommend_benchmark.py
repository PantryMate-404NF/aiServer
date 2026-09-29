"""추천 엔진 오프라인 벤치마크 — 실데이터 덤프 위에서 엔진과 기준선을 같은 요청으로 비교합니다.

    PYTHONUTF8=1 uv run python scripts/eval_recommend_benchmark.py --users 200 --seed 1
    (--out 의 기본값은 data/eval/recommend_benchmark 입니다)

네트워크도 DB 도 쓰지 않습니다. `data/Backend_Data_Dump` 의 CSV 를 백엔드 API 의 모양으로 읽어
운영과 같은 경로(`serving.LiveServing` → 사전 → 조회 → 점수 → 재정렬)로 추천을 만들고, 같은 후보
위에서 기준선 넷을 함께 잽니다.

    engine          운영 엔진 그대로 (17 신호 · 판본 묶기 · MMR · 탐색 칸 · 유형 칸)
    score_only      점수만 (재정렬 전부 끔) — 재정렬이 무엇을 바꾸는지 보는 절제 실험
    coverage_only   재료 충족률 순 — 냉장고 앱이 흔히 쓰는 "가진 재료가 많이 겹치는 순"
    random          후보 가운데 무작위
    naive_noallergy 알레르기를 보지 않는 충족률 순 — 하드컷이 막는 양을 재는 대조군

정답 라벨이 없으므로 정확도(NDCG)는 재지 않습니다. 대신 **안전 · 유용성 · 개인화 · 다양성 · 이유
충실도 · 지연 · 재현성**을 잽니다. 가상 사용자는 시드로 고정되어 같은 인자면 같은 결과가 납니다.
사전에 인기 · 후기 · 제철이 없어 그 신호는 꺼진 채(측정 불가) 잰 값입니다 — 지어내지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_recommend_bench_core import (  # noqa: E402
    AT_10,
    NOW,
    SCORE_ONLY,
    SYSTEMS,
    TOP_K,
    DumpClient,
    Prepared,
    Tally,
    coverage_first,
    make_users,
    mean,
    measure,
    prepare,
    reason_checks,
    replaced,
    request_of,
)

from features.recommend import service, serving  # noqa: E402
from features.recommend.engine import allergy, dish, rerank, retrieval  # noqa: E402
from features.recommend.enums import DEFAULT_WEIGHTS, EventType  # noqa: E402
from features.recommend.policy import RankingPolicy  # noqa: E402
from features.recommend.profile_store import (  # noqa: E402
    PRESENTED_PATH,
    JsonProfileStore,
    load_presented_flavors,
)
from features.recommend.schema import EventAck, EventBatchIn, EventIn, OnboardingIn  # noqa: E402


# ── 실행 ──────────────────────────────────────────────────────────
def run(
    users_count: int,
    seed: int,
    out: Path,
    policy_overrides: dict[str, object] | None = None,
    weight_overrides: dict[str, float] | None = None,
) -> dict[str, object]:
    rng = random.Random(seed)  # noqa: S311  # 가상 사용자 · 무작위 기준선용. 암호 용도가 아닙니다
    # 손잡이 · 가중치 덮어쓰기는 실험용입니다. 운영 기본값과의 차이를 같은 사용자 위에서 봅니다.
    policy = replace(RankingPolicy(), **(policy_overrides or {}))  # type: ignore[arg-type]
    # 운영처럼 기본 가중치 위에 덮습니다. 덮어쓴 것만 넘기면 나머지 신호가 전부 0 이 됩니다.
    merged_weights = {**DEFAULT_WEIGHTS, **weight_overrides} if weight_overrides else None
    store_dir = Path(tempfile.mkdtemp(prefix="reco-bench-profiles-"))
    personas = service.PersonaService(
        store=JsonProfileStore(store_dir),
        presented=load_presented_flavors(PRESENTED_PATH),
        policy=policy,
    )
    live = serving.LiveServing(
        DumpClient(),  # type: ignore[arg-type]
        personas,
        policy,
        sync_interval_sec=86_400,
        retry_interval_sec=60,
        clock=lambda: NOW,
        sink=serving.RecommendationSink(out / "logs"),
    )
    started = perf_counter()
    cat = live.sync_once()
    sync_sec = round(perf_counter() - started, 2)
    mains = dish.main_words(tuple(sorted(cat.corpus.ingredient_names.values())))
    ablation = replace(policy, **SCORE_ONLY)
    users = make_users(cat, users_count, rng)

    def score_only(p: Prepared) -> list[int]:
        """재정렬을 끈 점수 순 목록. 난수 시드가 고정이라 같은 문맥이면 같은 목록입니다."""
        ranked = service.rank_candidates(
            p.found.candidates,
            cat.recipes,
            p.ctx,
            cat.corpus,
            ablation,
            top_k=TOP_K,
            rng_seed=seed,
            max_missing_final=p.found.max_missing,
            weights=merged_weights,
        )
        return [it.recipe_id for it in ranked.items]

    tallies = {name: Tally.empty() for name in SYSTEMS}
    segments = {
        seg: Tally.empty() for seg in ("cold", "onboarded", "warm", "allergy", "no_allergy")
    }
    latencies: list[float] = []
    stages: Counter[str] = Counter()
    candidates_n: dict[str, list[int]] = {"allergy": [], "no_allergy": []}
    reasons: Counter[str] = Counter()
    reason_bad: Counter[str] = Counter()
    explore_share: list[float] = []
    slot_share: list[float] = []
    change_warm: list[float] = []
    change_control: list[float] = []
    history_effect: list[float] = []
    next_session_change: list[float] = []
    change_control20: list[float] = []
    signals_on: list[float] = []
    violation_examples: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []
    identical = 0

    for user in users:
        if user.onboarded:
            live.save_onboarding(
                user.user_id,
                OnboardingIn(
                    picks=list(user.picks),
                    scales=list(user.scales or ()),
                    preferred_cuisines=list(user.cuisines),
                    allergy_groups=list(user.allergies),
                ),
            )
        req = request_of(user)
        if weight_overrides:
            req = req.model_copy(update={"weight_override": dict(weight_overrides)})
        first = live.recommend(req)
        before = [it.recipe_id for it in first.items[:AT_10]]
        engine_ids_first = [it.recipe_id for it in first.items]
        # 이력 신호만의 효과를 재려고, 이벤트 전의 문맥으로 점수 순 목록을 미리 뽑아 둡니다.
        # (실서빙의 두 번째 응답은 최근 노출 감점과 탐색 무작위가 섞여 이력 효과를 가립니다.)
        before_scored = score_only(prepare(personas, cat, policy, req)) if user.warm else []
        if user.warm:
            picked = rng.sample(first.items[:AT_10], k=5)
            events = [
                EventIn(
                    user_id=user.user_id,
                    event_type=EventType.COOK if n < 3 else EventType.SAVE,
                    recipe_id=it.recipe_id,
                    request_id=first.request_id,
                    position=it.final_rank,
                    occurred_at=NOW - timedelta(days=rng.randint(1, 10)),
                )
                for n, it in enumerate(picked)
            ]
            live.record_events(EventBatchIn(events=events), EventAck(accepted=len(events)))
        t0 = perf_counter()
        response = live.recommend(req)
        latencies.append((perf_counter() - t0) * 1000)
        after = [it.recipe_id for it in response.items[:AT_10]]
        (change_warm if user.warm else change_control).append(replaced(before, after))
        if not user.warm:
            # 목록 전체(20건) 기준. 유예 안에서는 탐색 칸만 바뀌므로 탐색 비율에 가까워야 합니다.
            whole = frozenset(it.recipe_id for it in response.items)
            change_control20.append(replaced(engine_ids_first, whole))

        prep = prepare(personas, cat, policy, req)
        stages[prep.found.stage] += 1
        candidates_n["allergy" if user.allergies else "no_allergy"].append(
            len(prep.found.candidates)
        )
        engine_ids = [it.recipe_id for it in response.items]
        after_scored = score_only(prep)
        # 다음 세션(유예 30분 뒤)의 재방문 — 방금 보여 준 20건이 감점된 뒤의 점수 순 목록.
        revisit = prepare(personas, cat, policy, req, recent_served=engine_ids_first)
        next_session_change.append(replaced(after_scored[:AT_10], score_only(revisit)[:AT_10]))
        if user.warm:
            history_effect.append(replaced(before_scored[:AT_10], after_scored[:AT_10]))
            signals_on.append(
                sum(
                    1
                    for it in response.items[:AT_10]
                    if it.features.get("f_ing_pref") is not None
                    and it.features.get("f_cooccur") is not None
                )
                / AT_10
            )
        lists = {
            "engine": engine_ids,
            "score_only": after_scored,
            "coverage_only": coverage_first(prep.found.candidates, TOP_K),
            "random": [
                c.recipe_id
                for c in rng.sample(prep.found.candidates, k=min(TOP_K, len(prep.found.candidates)))
            ],
        }
        naive = retrieval.retrieve(
            cat,
            prep.ctx,
            allergy.AllergyResolution(),
            replace(policy, max_missing=req.max_missing),
            TOP_K,
            rerank.exploration_ratio(prep.ctx, policy),
        )
        lists["naive_noallergy"] = coverage_first(naive.candidates, TOP_K)
        for name, ids in lists.items():
            measure(tallies[name], ids, prep, cat, mains)
        measure(
            segments["warm" if user.warm else "onboarded" if user.onboarded else "cold"],
            engine_ids,
            prep,
            cat,
            mains,
        )
        measure(
            segments["allergy" if user.allergies else "no_allergy"], engine_ids, prep, cat, mains
        )

        explore_share.append(
            sum(1 for it in response.items if it.is_exploration) / len(response.items)
        )
        slot_share.append(
            sum(1 for it in response.items if it.is_cuisine_slot) / len(response.items)
        )
        for item in response.items:
            for key, ok in reason_checks(item, prep, cat):
                reasons[key] += 1
                if not ok:
                    reason_bad[key] += 1
                    if len(violation_examples) < 12:
                        violation_examples.append(
                            {
                                "user_id": user.user_id,
                                "feature": key,
                                "title": cat.recipes[item.recipe_id].title,
                                "reason": item.reason,
                                "missing": [
                                    cat.corpus.ingredient_names[i] for i in item.missing_ids
                                ],
                                "preferred_cuisines": sorted(prep.ctx.preferred_cuisines),
                            }
                        )
        if user.user_id % 10 == 0:
            twice = [
                [
                    it.recipe_id
                    for it in service.rank_candidates(
                        prep.found.candidates,
                        cat.recipes,
                        prep.ctx,
                        cat.corpus,
                        policy,
                        top_k=TOP_K,
                        rng_seed=777,
                        max_missing_final=prep.found.max_missing,
                    ).items
                ]
                for _ in range(2)
            ]
            identical += int(twice[0] == twice[1])
        samples.append(
            {
                "user_id": user.user_id,
                "segment": "warm" if user.warm else "onboarded" if user.onboarded else "cold",
                "pantry": [cat.corpus.ingredient_names[i.ingredient_id] for i in user.pantry],
                "expiring": [cat.corpus.ingredient_names[i] for i in prep.ctx.expiring_ids],
                "allergies": list(user.allergies),
                "picks": list(user.picks),
                "cuisines": list(user.cuisines),
                "max_minutes": user.max_minutes,
                "engine_top10": [
                    {
                        "rank": it.final_rank,
                        "title": cat.recipes[it.recipe_id].title,
                        "reason": it.reason,
                        "missing": [cat.corpus.ingredient_names[i] for i in it.missing_ids],
                        "exploration": it.is_exploration,
                    }
                    for it in response.items[:AT_10]
                ],
                "coverage_only_top10": [
                    cat.recipes[i].title for i in lists["coverage_only"][:AT_10]
                ],
            }
        )

    live.stop()
    catalog_size = len(cat.recipes)
    cuisine_catalog = Counter(r.cuisine or "?" for r in cat.recipes.values())
    cuisine_engine: Counter[str] = Counter()
    essential_engine: list[int] = []
    minutes_engine: list[int] = []
    for rid, n in tallies["engine"].exposure.items():
        r = cat.recipes[rid]
        cuisine_engine[r.cuisine or "?"] += n
        essential_engine.extend([len(r.essential_ids)] * n)
        minutes_engine.extend([r.cook_minutes or 0] * n)
    total_exposed = sum(cuisine_engine.values())
    top_share = sum(
        n for _, n in tallies["engine"].exposure.most_common(catalog_size // 100)
    ) / max(1, total_exposed)
    return {
        "meta": {
            "now": NOW.isoformat(),
            "seed": seed,
            "users": users_count,
            "top_k": TOP_K,
            "catalog": dict(cat.stats),
            "catalog_version": cat.version,
            "sync_sec": sync_sec,
            "unmapped_ingredients": list(cat.unmapped_ingredients),
            "policy": policy.fingerprint(merged_weights),
            "policy_overrides": policy_overrides or {},
            "weight_overrides": weight_overrides or {},
            "segments": {k: len(v.leak) for k, v in segments.items()},
        },
        "systems": {name: t.summary(catalog_size) for name, t in tallies.items()},
        "engine_segments": {name: t.summary(catalog_size) for name, t in segments.items()},
        "engine": {
            "latency_p50_ms": round(statistics.median(latencies), 1),
            "latency_p95_ms": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 1),
            "explore_share": mean(explore_share),
            "cuisine_slot_share": mean(slot_share),
            "retrieval_stages": dict(stages),
            "candidates_mean": {k: mean(float(v) for v in vs) for k, vs in candidates_n.items()},
            # 같은 요청을 바로 다시 보냈을 때 상위 10 이 얼마나 바뀌는가(최근 노출 감점 + 탐색).
            "refresh_change@10": mean(change_control),
            "refresh_change@20": mean(change_control20),
            # 이력 신호만의 효과 — 이벤트 전후의 점수 순 목록 차이(감점 · 무작위 제외).
            "history_effect@10": mean(history_effect),
            "history_signals_on@10": mean(signals_on),
            # 유예(30분)를 지나 다시 왔을 때 — 앞 세션의 20건이 감점된 뒤의 점수 순 목록 차이.
            "next_session_change@10": mean(next_session_change),
            "reproducible_pairs": {
                "identical": identical,
                "total": sum(1 for u in users if u.user_id % 10 == 0),
            },
            "reasons": {
                k: {"count": reasons[k], "violations": reason_bad[k]} for k in sorted(reasons)
            },
            "reason_violation_examples": violation_examples,
            "reason_violation_rate": round(
                sum(reason_bad.values()) / max(1, sum(reasons.values())), 4
            ),
        },
        "bias": {
            "cuisine_share": {
                c: {
                    "catalog": round(cuisine_catalog[c] / catalog_size, 4),
                    "engine": round(cuisine_engine[c] / max(1, total_exposed), 4),
                }
                for c in sorted(cuisine_catalog)
            },
            "essential_count_mean": {
                "catalog": mean(float(len(r.essential_ids)) for r in cat.recipes.values()),
                "engine": mean(float(v) for v in essential_engine),
            },
            "cook_minutes_mean": {
                "catalog": mean(float(r.cook_minutes or 0) for r in cat.recipes.values()),
                "engine": mean(float(v) for v in minutes_engine),
            },
            "top1pct_recipes_exposure_share": round(top_share, 4),
        },
        "samples": samples,
    }


def markdown(result: Mapping[str, object]) -> str:
    systems = result["systems"]
    keys = list(next(iter(systems.values())))  # type: ignore[union-attr]
    lines = ["| 지표 | " + " | ".join(SYSTEMS) + " |", "|---|" + "---|" * len(SYSTEMS)]
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(str(systems[s][key]) for s in SYSTEMS) + " |")  # type: ignore[index]
    seg = result["engine_segments"]
    lines += ["", "| 지표 | " + " | ".join(seg) + " |", "|---|" + "---|" * len(seg)]  # type: ignore[arg-type]
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(str(seg[s][key]) for s in seg) + " |")  # type: ignore[index]
    return "\n".join(lines)


def parse_policy_overrides(pairs: list[str]) -> dict[str, object]:
    """`key=value` 를 손잡이의 기본값과 같은 형으로 바꿉니다. 모르는 손잡이는 거부합니다."""
    default = RankingPolicy()
    out: dict[str, object] = {}
    for pair in pairs:
        key, raw = pair.split("=", 1)
        if not hasattr(default, key):
            raise SystemExit(f"모르는 손잡이입니다: {key}")
        out[key] = type(getattr(default, key))(raw)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=ROOT / "data/eval/recommend_benchmark")
    parser.add_argument(
        "--policy",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="RankingPolicy 손잡이 덮어쓰기 (예: --policy penalty_recent=0.9). 실험용",
    )
    parser.add_argument(
        "--weight",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="가중치 덮어쓰기 (예: --weight f_pantry_use=0.15). 실험용",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = run(
        args.users,
        args.seed,
        args.out,
        policy_overrides=parse_policy_overrides(args.policy),
        weight_overrides={k: float(v) for k, v in (pair.split("=", 1) for pair in args.weight)}
        or None,
    )
    samples = result.pop("samples")
    (args.out / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out / "summary.md").write_text(markdown(result), encoding="utf-8")
    with (args.out / "samples.jsonl").open("w", encoding="utf-8") as handle:
        for row in samples:  # type: ignore[union-attr]
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False, indent=2
        )
    )
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
