"""계약을 그대로 구현한 Mock 추천 서비스.

규칙 4절: 이 파일은 전처리 → 호출 → 후처리 **조립**을 맡는다.
HTTP 는 `router.py` 가, 계약 모델은 `schema.py` · `stage.py` 가 담당한다.

응답은 고정 시드로 생성되어 재현 가능하다. 실제 추천 로직은 없다 —
각 스테이지 담당자가 자기 mock 을 실제 구현으로 갈아끼우면 된다.

🔴 404 같은 **HTTP 결정은 여기서 하지 않는다.** 없으면 None 을 돌려주고
   상태코드는 라우터가 정한다 (02 의 3.2: router 는 HTTP 만).
"""

from __future__ import annotations

import random
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from features.recommend.engine.explore import exploration_slots
from features.recommend.engine.rank import feature_stats, top_reasons
from features.recommend.engine.reason import build_reason
from features.recommend.engine.serendipity import ClusterStats, mixed_exploration
from features.recommend.enums import (
    DEFAULT_WEIGHTS,
    FEATURE_KEYS,
    PROPENSITY_SEMANTICS,
    UNAVAILABLE_FEATURES,
    Stage,
    UserMode,
)
from features.recommend.schema import (
    EventAck,
    EventBatchIn,
    HealthOut,
    IngredientHit,
    IngredientSearchOut,
    OnboardingIn,
    OnboardingOut,
    PantryIn,
    PantryItemOut,
    PantryOut,
    RecipeHit,
    RecipeSearchOut,
    RecommendationLogOut,
    RecommendRequest,
    RecommendResponse,
)
from features.recommend.stage import RankedItem, StageInfo, StageTrace, TraceTotals

SEED = 20260827  # 재현성. Date.now() 류를 응답 생성에 쓰지 않는다

#: (제목, 부족재료수, 두드러진 피처, 클러스터) — 이유 문구는 하드코딩하지 않는다.
#: v1.9 부터 z-salience 로 실제 계산해서 만든다. 그래야 mock 이 실제와 같은 코드를 탄다.
_RECIPES = [
    ("김치찌개", 0, "f_coverage", 1),  # 1 = 국물류
    ("두부조림", 0, "f_expiring", 2),  # 2 = 조림
    ("애호박볶음", 1, "f_missing", 3),  # 3 = 볶음
    ("제육볶음", 1, "f_taste", 3),
    ("계란말이", 0, "f_ing_pref", 4),  # 4 = 계란·부침
    ("된장찌개", 2, "f_popularity", 1),
    ("콩나물무침", 0, "f_season", 5),  # 5 = 무침
    ("어묵볶음", 1, "f_cooccur", 3),
]

#: 유저별 클러스터 관측. 실제 구현은 `user_cluster_stat` 테이블에서 읽는다 (설계 5-3-5).
#: 배치가 채우는 값이므로 서빙 중에는 변하지 않는다. mock 도 그렇게 흉내 낸다.
_CLUSTER_STAT: dict[int, ClusterStats] = {}


def _seed_cluster_stat(user_id: int) -> ClusterStats:
    """유저별로 결정적인 관측을 만든다 — 배치가 채워둔 상태를 흉내 낸다.

    같은 user_id 면 프로세스를 재시작해도 같은 값이 나온다. 그래야 대시보드가
    같은 요청을 두 번 눌렀을 때 propensity 가 흔들리지 않는다.
    """
    r = random.Random(SEED + user_id * 7919)  # noqa: S311  # 목업 재현용 시드 RNG. 암호 용도가 아닙니다
    st = ClusterStats()
    for c in range(1, 6):
        n = r.randint(0, 20)
        st.n[c] = n
        st.hits[c] = r.randint(0, n) if n else 0
    return st


#: 이유 템플릿이 필요로 하는 값. 실제 구현은 DB 에서 가져온다.
_CTX = {
    "expiring_name": "두부",
    "expiring_days": 2,
    "missing_name": "애호박",
    "taste_axis": "매운맛",
    "pref_ing": "달걀",
    "cuisine": "한식",
    "similar_title": "김치볶음밥",
    "pantry_used": 3,
    "dish_type": "볶음",
}
_INGREDIENTS = [
    (1042, "대파", "농산물.채소.경채류"),
    (1050, "양파", "농산물.채소.근채류"),
    (1101, "애호박", "농산물.채소.과채류"),
    (1200, "돼지고기", "축산물.돼지고기"),
    (1300, "두부", "가공식품.두부묵"),
    (1400, "배추김치", "가공식품.김치절임"),
    (1500, "달걀", "축산물.난류"),
    (1600, "청양고추", "농산물.채소.고추류"),
]
_LOGS: dict[UUID, RecommendationLogOut] = {}
_PANTRY: dict[int, list[PantryItemOut]] = {}


def _config_hash(weights: dict[str, float]) -> str:
    """가중치 집합의 지문. `scoring_config` 레지스트리의 키가 된다 (07 E-3 ④).

    해시는 단방향이라 이것만으로는 못 되돌린다 — 레지스트리에 행이 있어야 한다.
    """
    import hashlib

    body = ",".join(f"{k}={weights[k]:.6f}" for k in sorted(weights))
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def _trace(
    n_items: int,
    ms: int,
    mode: UserMode,
    degraded: bool,
    explore: list[int] | None = None,
    *,
    top_k: int = 20,
    n_explore: int = 2,
    serving_mode: str = "sim",
    rng_seed: int = 0,
    max_missing_final: int = 2,
) -> StageTrace:
    """🔴 ③ 의 `params` 는 REQUIRED_TRACE_PARAMS 10종을 **전부** 실어야 한다 (S0 ①).

    값이 아니라 정의가 소급 불가다 — 로그가 있어도 이 키가 없으면
    propensity 를 재구성할 수 없다. `check_trace_params()` 가 계약으로 강제한다.
    """
    return StageTrace(
        stages=[
            StageInfo(
                name=Stage.RETRIEVAL,
                in_count=231042,
                out_count=487,
                latency_ms=int(ms * 0.55),
                strategy="pantry_coverage",
                filters={
                    "no_overlap": 227999,
                    "allergy_cut": 12,
                    "missing_gt_k": 1502,
                    "cooktime_cut": 1042,
                },
                params={"max_missing": 2, "staple_added": 28},
            ),
            StageInfo(
                name=Stage.RANKING,
                in_count=487,
                out_count=487,
                latency_ms=int(ms * 0.32),
                model="mock-linear-v0",
                score_stats={"min": 0.02, "p25": 0.19, "p50": 0.41, "p75": 0.63, "max": 0.93},
            ),
            StageInfo(
                name=Stage.RERANK,
                in_count=487,
                out_count=n_items,
                latency_ms=int(ms * 0.13),
                dropped={"diversity": 31, "recent_seen": 8, "category_cap": 5},
                params={
                    "mmr_lambda": 0.7,
                    "explore_slots_random": 1,
                    "explore_pool_size": 200,
                    "uniform_share": 0.5,
                    "propensity_semantics": PROPENSITY_SEMANTICS,
                    "top_k": top_k,
                    "n_explore": n_explore,
                    "serving_mode": serving_mode,
                    "propensity_mc": 200,
                    "policy_id": "mock-linear-v0+explore2-randpos",
                    "rng_seed": rng_seed,
                    "max_missing_final": max_missing_final,
                },
                exploration_items=explore or [],
            ),
        ],
        totals=TraceTotals(latency_ms=ms, user_mode=mode, degraded=degraded),
    )


def health_payload() -> HealthOut:
    return HealthOut(model_version="mock-linear-v0")


def build_recommendation(req: RecommendRequest) -> RecommendResponse:
    t0 = time.perf_counter()
    rng = random.Random(SEED + req.user_id)  # noqa: S311  # 목업 재현용 시드 RNG. 암호 용도가 아닙니다
    weights = {**DEFAULT_WEIGHTS, **(req.weight_override or {})}
    # 탐색 풀 = 상위 200 (설계 5-3-3). trace params 와 일치해야 한다
    pool_size = 200

    # ── ② Ranking — 피처 원값을 만든다 (contrib 가 아니다) ────────
    scored: list[RankedItem] = []
    for i in range(req.top_k):
        _title, missing, spike, cluster = _RECIPES[i % len(_RECIPES)]
        if missing > req.max_missing:
            continue
        base = max(0.05, 0.95 - i * 0.03 - rng.random() * 0.05)

        # 주의: w 와 무관하게 FEATURE_KEYS 전부를 채운다. None 은 "계산 불가".
        feats: dict[str, float | None] = {}
        for k in FEATURE_KEYS:
            if k in UNAVAILABLE_FEATURES:
                feats[k] = None  # 0.0 이 아니다 — 의미가 다르다
            elif k == "f_coverage":
                feats[k] = round(1.0 - missing * 0.2, 4)
            elif k == "f_missing":
                feats[k] = round(1.0 / (1 + missing), 4)
            else:
                v = rng.random()
                feats[k] = round(min(1.0, v * 1.6) if k == spike else v * 0.7, 4)

        scored.append(
            RankedItem(
                recipe_id=10000 + i,
                missing_count=missing,
                missing_ids=[1101] if missing else [],
                coverage=1.0 - missing * 0.2,
                score=round(base, 4),
                cluster_id=cluster,
                features=feats,
                final_rank=i + 1,
            )
        )

    # 주의: 점수 내림차순으로 정렬한다. 두 가지 이유다.
    #    ① mixed_exploration 이 "정렬돼 있어야 한다"를 전제한다 (serendipity.py:148)
    #    ② final_rank 는 순위지 열거 순서가 아니다. 지터 폭(0.05)이 스텝(0.03)보다
    #       커서 정렬 없이는 역전이 난다 — 실측 top_k=20 에서 53/59명.
    scored.sort(key=lambda c: c.score, reverse=True)

    # ── ③ Re-ranking — 이유는 후보 집합 대비 z-salience 로 고른다 ──
    stats = feature_stats(scored)

    # 🔑 우연성 (설계 5-3-5) — 균등 절반(support 보장) + Thompson 절반(우연성)
    # 주의: 요청마다 누적하지 않는다. 실제 구현은 `user_cluster_stat` 을 배치가
    #    갱신하므로 같은 날 같은 요청은 같은 propensity 를 낸다. mock 이 인라인으로
    #    누적하면 현실보다 더 변덕스러울 뿐 아니라, 모듈 docstring 의
    #    "재현 가능하다" 가 propensity 에 대해 거짓이 된다 — 실측으로 같은 요청
    #    2회에 0.325 → 0.265 로 변했다. 유저별 결정적 관측을 흉내 낸다.
    cstat = _CLUSTER_STAT.setdefault(req.user_id, _seed_cluster_stat(req.user_id))
    k_ex = min(2, len(scored))
    pool = [
        {"recipe_id": c.recipe_id, "cluster_id": c.cluster_id, "score": c.score} for c in scored
    ]
    picked, prop = mixed_exploration(pool, cstat, rng, k=k_ex, pool_size=pool_size)
    n_uniform = max(1, round(k_ex * 0.5))
    src = {
        p["recipe_id"]: ("uniform" if j < n_uniform else "thompson") for j, p in enumerate(picked)
    }
    # 주의: 탐색 아이템을 무작위 위치에 실제로 꽂는다 (09-02 수정).
    #    이전에는 위치만 뽑고 `explore` 를 쓰지 않아, 탐색 아이템이 점수 순서
    #    그대로 남았다 — 즉 항상 비슷한 위치에 왔다.
    #    그러면 위치별 CTR 이 검사확률 곡선이 되지 않아 IPS 보정이 성립하지 않는다.
    #    explore.py 의 docstring 이 정확히 그것을 설명한다.
    slots = exploration_slots(len(scored), len(picked), rng)
    ex_items = [c for c in scored if c.recipe_id in src]
    rest = [c for c in scored if c.recipe_id not in src]
    arranged: list[RankedItem] = []
    ei = ri = 0
    for pos in range(len(scored)):
        if ei < len(ex_items) and pos in slots:
            arranged.append(ex_items[ei])
            ei += 1
        elif ri < len(rest):
            arranged.append(rest[ri])
            ri += 1
        else:
            arranged.append(ex_items[ei])
            ei += 1
    scored = arranged

    for rank, it in enumerate(scored, start=1):
        it.final_rank = rank
        it.is_exploration = it.recipe_id in src
        it.explore_source = src.get(it.recipe_id)
        it.propensity = prop.get(it.recipe_id, 1.0) if it.is_exploration else 1.0
        keys = top_reasons(it, weights, stats, n=2)
        it.reason, it.reason_features = build_reason(keys, _CTX, it.is_exploration)
        if req.interleave_with:
            it.team = "A" if rng.random() < 0.5 else "B"

    items = scored
    ms = max(1, int((time.perf_counter() - t0) * 1000) + 28)  # 실측 p95 근사
    mode = UserMode.COLD if req.user_id % 3 else UserMode.BLENDED
    rid = uuid4()
    trace = _trace(
        len(items),
        ms,
        mode,
        degraded=len(items) < req.top_k,  # 상수 20 이 아니다
        explore=[it.recipe_id for it in items if it.is_exploration],
        top_k=req.top_k,
        n_explore=k_ex,
        serving_mode="sim",
        rng_seed=SEED + req.user_id,
        max_missing_final=req.max_missing,
    )

    _LOGS[rid] = RecommendationLogOut(
        request_id=rid,
        user_id=req.user_id,
        model_version=req.model_version or "mock-linear-v0",
        # 주의: `weights` 를 싣지 않는다 (v2.9). 5-2-2-1 이 재분배를 나눗셈으로 바꾸면서
        #    실효 가중치가 config_hash + warm_alpha + features 의 None 패턴으로 유도된다.
        config_hash=_config_hash(weights),
        warm_alpha=0.0,  # mock 은 콜드 유저만 흉내낸다
        session_id=req.session_id or f"g-{req.user_id}-000000000000",
        pantry_snapshot=[i[0] for i in _INGREDIENTS[:5]],
        allergy_snapshot=[],
        stage_trace=trace,
        served=[it.recipe_id for it in items],
        total_latency_ms=ms,
        created_at=datetime.now(UTC),
    )
    return RecommendResponse(
        request_id=rid,
        user_id=req.user_id,
        model_version=req.model_version or "mock-linear-v0",
        weights=weights,
        items=items,
        trace=trace if req.include_trace else None,
        served_at=datetime.now(UTC),
    )


def ack_events(batch: EventBatchIn) -> EventAck:
    # 실제 구현에서는 여기서 event_log 에 INSERT 한다.
    # impression 은 클라이언트가 아니라 /v1/recommend 가 서버측에서 기록한다 (설계 3-2).
    bad = [
        f"{e.event_type}: request_id 없음 — 학습 라벨과 이을 수 없다"
        for e in batch.events
        if e.request_id is None and e.recipe_id is not None
    ]
    return EventAck(accepted=len(batch.events) - len(bad), rejected=len(bad), errors=bad)


def search_ingredients(q: str, limit: int = 5) -> IngredientSearchOut:
    hits = [
        IngredientHit(
            ingredient_id=i,
            name=n,
            score=1.0 if n == q else 0.45,
            method="exact" if n == q else "jamo_trgm",
            category=c,
        )
        for i, n, c in _INGREDIENTS
        if q and (q in n or n in q)
    ][:limit]
    return IngredientSearchOut(
        query=q,
        hits=hits,
        not_found_message=None if hits else f"'{q}'을(를) 찾지 못했어요. 직접 등록을 요청할까요?",
    )


def search_recipes(
    q: str, limit: int = 20, user_id: int | None = None, max_missing: int | None = None
) -> RecipeSearchOut:
    """자연어 레시피 검색. 임베딩 코사인 유사도 기반 (설계 6-4-3).

    Mock 은 제목 부분일치로 흉내낸다. 실제 구현은 pgvector HNSW 조회다.
    """
    t0 = time.perf_counter()
    rng = random.Random(SEED + len(q))  # noqa: S311  # 목업 재현용 시드 RNG. 암호 용도가 아닙니다
    hits = []
    for i, (title, missing, _, _cl) in enumerate(_RECIPES):
        if q and not any(c in title for c in q):
            continue
        hits.append(
            RecipeHit(
                recipe_id=10000 + i,
                title=title,
                score=round(max(0.35, 0.92 - i * 0.06 - rng.random() * 0.04), 3),
                cuisine="korean",
                cook_minutes=[10, 20, 30, 45][i % 4],
                missing_count=missing if user_id else None,
                missing_names=["애호박"] if (user_id and missing) else [],
            )
        )
    if max_missing is not None:
        hits = [h for h in hits if (h.missing_count or 0) <= max_missing]
    hits = sorted(hits, key=lambda h: -h.score)[:limit]
    return RecipeSearchOut(
        query=q,
        hits=hits,
        model_version="ko-sbert-mock",
        latency_ms=max(1, int((time.perf_counter() - t0) * 1000) + 8),
        degraded=not hits,
    )


#: 소진·폐기 기록 (mock). 실제 구현은 pantry_item 의 removed_at·removed_reason.
_REMOVED: dict[int, list[tuple[int, str]]] = {}

#: 온보딩 응답 저장소 (mock). 실제 구현은 user_vector · user_preference · user_allergy.
_ONBOARDING: dict[int, OnboardingIn] = {}


def save_onboarding(user_id: int, body: OnboardingIn) -> OnboardingOut:
    """온보딩 5문항을 저장한다.

    🔴 **원본을 그대로 남긴다.** `taste_vec` 은 고른 레시피들의 평균이라
       결과만 저장하면 시드가 바뀔 때 다시 계산할 수 없다.
       실제 구현은 `user_vector.onboarding_picks` · `onboarding_scales` 에 넣는다.

    🔴 **알러지는 `severity='allergy'` 로 저장한다.** DB 기본값 'avoid' 에 맡기면
       그룹 확산이 조용히 꺼져 본인이 적은 재료만 막힌다 —
       아몬드만 등록한 사람에게 호두·잣이 그대로 추천된다.
    """
    _ONBOARDING[user_id] = body
    # mock 은 척도 3축을 앞 3칸에 넣고 나머지는 코퍼스 평균(여기선 0.5)으로 둔다.
    # 실제 구현은 고른 레시피들의 flavor_vec 평균을 쓴다.
    tv = [round(x / 4.0, 4) for x in body.scales] + [0.5, 0.5, 0.5]
    n_blocked = len(body.allergy_ingredient_ids) + len(body.allergy_groups) * 12
    return OnboardingOut(user_id=user_id, taste_vec=tv, n_blocked_ingredients=n_blocked)


def read_pantry(user_id: int) -> PantryOut:
    items = _PANTRY.get(user_id) or [
        PantryItemOut(
            ingredient_id=i,
            name=n,
            quantity=1,
            unit="개",
            expires_at=date(2026, 9, 1) + timedelta(days=k),
            days_left=5 + k,
        )
        for k, (i, n, _) in enumerate(_INGREDIENTS[:4])
    ]
    return PantryOut(user_id=user_id, items=items, staple_count=28)


def replace_pantry(user_id: int, body: PantryIn) -> PantryOut:
    name = {i: n for i, n, _ in _INGREDIENTS}
    # 주의: `removed` 를 버리지 않는다. 소진/폐기 사유는 안 물어보면 나중에
    #    물을 대상이 없다 (S0 ⑤). 실제 구현은 tombstone 으로 남긴다 —
    #    행을 지우면 "무엇을 얼마나 버렸나" 를 영원히 못 센다.
    for rm in body.removed:
        _REMOVED.setdefault(user_id, []).append((rm.ingredient_id, rm.reason))
    _PANTRY[user_id] = [
        PantryItemOut(**it.model_dump(), name=name.get(it.ingredient_id, f"재료{it.ingredient_id}"))
        for it in body.items
    ]
    return read_pantry(user_id)


def read_log(request_id: UUID) -> RecommendationLogOut | None:
    return _LOGS.get(request_id)
