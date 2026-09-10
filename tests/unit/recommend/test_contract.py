"""계약 검증. 스키마를 바꿀 때 이것이 깨지면 DB·문서도 함께 바꿔야 한다.

.venv/bin/python -m tests.unit.recommend.test_contract
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from features.recommend.engine.explore import exploration_slots, interleave
from features.recommend.engine.rank import (
    feature_stats,
    merge_served_detail,
    top_reasons,
)
from features.recommend.engine.reason import build_reason, check_templates
from features.recommend.engine.serendipity import (
    ClusterStats,
    mixed_exploration,
    thompson_propensity,
)
from features.recommend.enums import (
    DEFAULT_WEIGHTS,
    FEATURE_KEYS,
    LABEL_WEIGHT,
    UNAVAILABLE_FEATURES,
    EventType,
    Stage,
    UserMode,
)
from features.recommend.schema import EventIn, RecommendRequest, RecommendResponse
from features.recommend.stage import (
    Candidate,
    RankedItem,
    RetrievalInput,
    RetrievalRequest,
    ScoredCandidate,
    StageInfo,
    StageTrace,
    TraceTotals,
)

FULL = {k: 0.5 for k in FEATURE_KEYS}  # 전 피처를 채운 최소 예시

ok, fail = 0, []


def check(label: str, cond: bool):
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        fail.append(label)
        print(f"  ✗ {label}")


print("[계약 검증]")

# ── 상수 정합성 ──────────────────────────────────────────────────
check("가중치 합 = 1.00", abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9)
check("가중치 키 ⊆ FEATURE_KEYS", set(DEFAULT_WEIGHTS) <= set(FEATURE_KEYS))
check("이벤트 8종", len(EventType) == 8)
check("impression 라벨 = 0 (negative 후보)", LABEL_WEIGHT[EventType.IMPRESSION] == 0.0)
check("cook 라벨이 최대", LABEL_WEIGHT[EventType.COOK] == max(LABEL_WEIGHT.values()))

# ── 🔑 w>0 인 피처는 전부 계산 가능해야 한다 (v1.9) ──────────────
#    04_실행계획에서 잘린 수단에 의존하는 피처에 가중치를 주면 Σw=1.00 을
#    통과하면서도 서빙은 0.84 짜리 랭커가 된다. 그것을 여기서 막는다.
check(
    "w>0 인 피처는 전부 계산 가능하다",
    not [k for k in UNAVAILABLE_FEATURES if DEFAULT_WEIGHTS.get(k, 0) > 0],
)

# 주의: 위 검사만으로는 부족했다. 크롤이 도착하며 f_cuisine·f_season 이 계산 불가가
#    됐는데 UNAVAILABLE 집합을 갱신하지 않아 0.06 이 초록으로 통과했다 (09-02 발견).
#    "수단이 없다"와 "데이터가 없다"는 다르므로 집합을 갈랐다.
from features.recommend.enums import ACTIVE_WEIGHT_TODAY, PENDING_DATA_FEATURES

check(
    "두 집합이 겹치지 않는다 (수단 없음 ≠ 데이터 없음)",
    not (UNAVAILABLE_FEATURES & PENDING_DATA_FEATURES),
)
check("데이터 대기 피처는 FEATURE_KEYS 안에 있다", PENDING_DATA_FEATURES <= set(FEATURE_KEYS))
# 나눗셈 정규화가 None 을 분자·분모에서 함께 빼므로 w 를 0 으로 내릴 필요가 없다.
# 대신 오늘 실효 가중치를 문서에 쓰는 숫자로 노출한다.
check(
    f"오늘 실효 가중치 = {ACTIVE_WEIGHT_TODAY} (설계 의도 1.00)",
    abs(ACTIVE_WEIGHT_TODAY - (1.0 - sum(DEFAULT_WEIGHTS[k] for k in PENDING_DATA_FEATURES)))
    < 1e-9,
)

# ── features 검증 (v1.9 — contrib 대체) ─────────────────────────
try:
    ScoredCandidate(
        recipe_id=1, missing_count=0, coverage=1.0, score=0.5, features={**FULL, "f_oops": 1.0}
    )
    check("모르는 피처 키를 거부한다", False)
except ValidationError:
    check("모르는 피처 키를 거부한다", True)

try:
    ScoredCandidate(
        recipe_id=1, missing_count=0, coverage=1.0, score=0.5, features={"f_coverage": 1.0}
    )
    check("🔑 피처를 빠뜨리면 거부한다 (로그 소실 방지)", False)
except ValidationError:
    check("🔑 피처를 빠뜨리면 거부한다 (로그 소실 방지)", True)

sc = ScoredCandidate(
    recipe_id=1,
    missing_count=0,
    coverage=1.0,
    score=0.87,
    features={**FULL, "f_expiring": 0.9, "f_ing_cf": None},
)
check(
    "None(계산불가) 과 0.0(계산결과) 을 구분한다",
    sc.features["f_ing_cf"] is None and sc.features["f_coverage"] == 0.5,
)
check(
    "contrib 은 저장이 아니라 계산이다",
    "contrib" not in sc.model_dump() and sc.contrib(DEFAULT_WEIGHTS)["f_expiring"] > 0,
)
check(
    "w=0 피처도 features 에 남는다 (소급 학습 가능)",
    all(k in sc.features for k in FEATURE_KEYS if DEFAULT_WEIGHTS[k] == 0),
)

# ── 🔑 이유 선택 — contrib argmax 붕괴를 막는다 (설계 5-5) ────────
#    ① 통과 후에는 f_coverage 가 거의 항상 1.0 이라 w·f 의 argmax 가 고정된다.
#    후보 집합 대비 z-salience 를 쓰면 그 레시피만의 특징이 뽑힌다.
pool = [
    ScoredCandidate(
        recipe_id=i,
        missing_count=0,
        coverage=1.0,
        score=0.5,
        features={**FULL, "f_coverage": 0.98, "f_expiring": 0.0},
    )
    for i in range(9)
]
odd = ScoredCandidate(
    recipe_id=99,
    missing_count=0,
    coverage=1.0,
    score=0.5,
    features={**FULL, "f_coverage": 0.99, "f_expiring": 1.0},
)
pool.append(odd)
stats = feature_stats(pool)
check(
    "z-salience 가 '남들과 다른' 피처를 고른다",
    top_reasons(odd, DEFAULT_WEIGHTS, stats, n=1) == ["f_expiring"],
)
check("σ 하한이 무의미한 차이의 증폭을 막는다", all(sd >= 0.05 for _, sd in stats.values()))
check("이유 템플릿이 종결형·연결형 짝을 이룬다", not check_templates())
_txt, _used = build_reason(["f_ing_pref", "f_time_fit"], {"pref_ing": "달걀", "cook_minutes": 20})
check("조사가 받침에 맞는다 (달걀'이')", "달걀이" in _txt)
_txt2, _ = build_reason(["f_ing_pref"], {"pref_ing": "두부"})
check("조사가 받침에 맞는다 (두부'가')", "두부가" in _txt2)
check("값이 없는 템플릿은 건너뛴다", build_reason(["f_cooccur"], {})[1] == [])

# ── 🔑 exploration 슬롯 위치는 무작위여야 한다 (설계 5-3-3) ───────
#    고정 위치(6·14)로는 위치별 검사확률 곡선을 만들 수 없어 IPS 가 불가능하다.
import random as _r

_pos = {tuple(exploration_slots(20, 2, _r.Random(s))) for s in range(60)}
check("exploration 위치가 요청마다 달라진다 (IPS 전제)", len(_pos) > 20)
check("exploration 개수는 그대로", all(len(p) == 2 for p in _pos))

# ── 🔑 우연성 — 혼합 탐색 정책 (설계 5-3-5) ─────────────────────
#    Thompson 단독은 유망하지 않은 클러스터를 아예 뽑지 않아 propensity=0 이 생긴다.
#    (실측: 클러스터 50개 중 32개). 그 영역은 IPS 분모가 0 이라 영원히 평가 불가능하다.
#    균등 절반을 섞어 모든 후보에 최소 노출확률을 보장한다.
_cand = [{"recipe_id": i, "cluster_id": i % 12, "score": 1.0 - i * 0.004} for i in range(200)]
_st = ClusterStats(n={c: 30 for c in range(12)}, hits={c: (8 if c < 3 else 0) for c in range(12)})
_sel, _prop = mixed_exploration(_cand, _st, _r.Random(5), k=2)
check(
    "🔴 혼합 탐색은 모든 후보에 propensity > 0 을 보장한다 (support)",
    len(_prop) == len(_cand) and all(v > 0 for v in _prop.values()),
)
check("탐색 슬롯 수가 요청한 만큼 나온다", len(_sel) == 2)
check("Σpropensity = 슬롯 수", abs(sum(_prop.values()) - 2.0) < 1e-6)

_tp = thompson_propensity(list(range(12)), _st, 2, mc=200)
check(
    "🔴 Thompson 단독은 support 가 무너진다 (0 인 클러스터가 생긴다)",
    any(v == 0.0 for v in _tp.values()),
)
check(
    "Thompson 은 관측이 많고 반응 좋은 클러스터를 더 자주 뽑는다",
    sum(_tp[c] for c in range(3)) > sum(_tp[c] for c in range(3, 12)),
)

# 클러스터가 없으면(배치 미실행) 균등으로 폴백해야 한다 — 죽으면 안 된다
_nc = [{"recipe_id": i, "cluster_id": None, "score": 1.0 - i * 0.01} for i in range(50)]
_s2, _p2 = mixed_exploration(_nc, ClusterStats(), _r.Random(1), k=2)
check(
    "cluster_id 가 없어도 균등 탐색으로 동작한다 (배치 미실행 폴백)",
    len(_s2) >= 1 and all(v > 0 for v in _p2.values()),
)

# ── Team-Draft Interleaving (설계 5-7-2) ────────────────────────
_il = interleave(list(range(10)), list(range(100, 110)), _r.Random(3), 10)
check("interleave 결과에 중복이 없다", len({x for x, _ in _il}) == len(_il))
check("interleave 가 두 팀을 모두 쓴다", len({t for _, t in _il}) == 2)

# ── 상속 체인 ────────────────────────────────────────────────────
check("ScoredCandidate ⊃ Candidate", issubclass(ScoredCandidate, Candidate))
check("RankedItem ⊃ ScoredCandidate", issubclass(RankedItem, ScoredCandidate))

# ── 범위 제약 ────────────────────────────────────────────────────
for label, kw in [
    ("coverage > 1 거부", dict(recipe_id=1, missing_count=0, coverage=1.5)),
    ("missing_count < 0 거부", dict(recipe_id=1, missing_count=-1, coverage=1.0)),
]:
    try:
        Candidate(**kw)
        check(label, False)
    except ValidationError:
        check(label, True)

# ── 오타 방지 (extra=forbid) ─────────────────────────────────────
try:
    RecommendRequest(user_id=1, topk=20)  # top_k 오타
    check("알 수 없는 필드를 거부한다", False)
except ValidationError:
    check("알 수 없는 필드를 거부한다", True)

# ── stage_trace 직렬화 ───────────────────────────────────────────
trace = StageTrace(
    stages=[
        StageInfo(
            name=Stage.RETRIEVAL,
            in_count=231042,
            out_count=487,
            latency_ms=34,
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
            latency_ms=18,
            model="ranker-v0-linear",
            score_stats={"min": 0.02, "p50": 0.41, "max": 0.93},
        ),
        StageInfo(
            name=Stage.RERANK,
            in_count=487,
            out_count=20,
            latency_ms=6,
            dropped={"diversity": 31, "recent_seen": 8, "category_cap": 5},
            exploration_items=[88213, 91002],
        ),
    ],
    totals=TraceTotals(latency_ms=58, user_mode=UserMode.COLD),
)
d = trace.model_dump(mode="json")
check("stage_trace 직렬화", d["trace_version"] == "v1" and len(d["stages"]) == 3)
check("filters 가 보존된다", d["stages"][0]["filters"]["missing_gt_k"] == 1502)
check("round-trip 동일", StageTrace.model_validate(d) == trace)

# ── 응답 전체 ────────────────────────────────────────────────────
resp = RecommendResponse(
    request_id=uuid4(),
    user_id=1,
    model_version="ranker-v0-linear",
    weights=DEFAULT_WEIGHTS,
    items=[
        RankedItem(
            recipe_id=10432,
            missing_count=0,
            coverage=1.0,
            score=0.87,
            features={**FULL, "f_expiring": 0.9},
            final_rank=1,
            reason_features=["f_expiring"],
            propensity=1.0,
            reason="애호박(D-2)을 소진할 수 있어요",
        )
    ],
    trace=trace,
    served_at=datetime.now(UTC),
)
check("RecommendResponse 직렬화", "items" in resp.model_dump(mode="json"))
check(
    "🔑 응답이 실효 가중치를 싣는다 (점수 재현 가능)", abs(sum(resp.weights.values()) - 1.0) < 1e-9
)
check("🔴 propensity 가 담긴다 (off-policy 평가의 분모)", resp.items[0].propensity is not None)
check(
    "탐색 경로(uniform/thompson)를 구분해 기록할 수 있다",
    "explore_source" in RankedItem.model_fields,
)
check("Candidate 가 cluster_id 를 나른다 (① → ③ 전달)", "cluster_id" in Candidate.model_fields)

# ── 로그 필수 필드 (설계 3-7 동결 대상) ──────────────────────────
ev = EventIn(
    user_id=1,
    event_type=EventType.CLICK,
    recipe_id=10432,
    request_id=uuid4(),
    position=3,
    session_id="c-1-abc",
)
check("event 에 request_id·position 이 담긴다", ev.request_id is not None and ev.position == 3)
check("🔴 event 에 session_id 가 담긴다 (시퀀스 모델의 전제)", ev.session_id == "c-1-abc")
check(
    "🔴 recommend 요청도 session_id 를 받는다 (impression 95% 의 세션)",
    "session_id" in RecommendRequest.model_fields,
)
try:
    EventIn(user_id=1, event_type=EventType.CLICK, recipe_id=1, position=0)
    check("position 은 1-base — 0 을 거부한다", False)
except ValidationError:
    check("position 은 1-base — 0 을 거부한다", True)

# ── S0 ① 로그 규약 동결 (2026-09-01) — 소급 불가 ──────────────
from features.recommend.engine.rank import check_trace_params, keep_candidates
from features.recommend.enums import CANDIDATE_KEEP, PROPENSITY_SEMANTICS, REQUIRED_TRACE_PARAMS

check(
    "🔴 propensity 의미론이 'item' 으로 동결됐다 (아이템 주변확률)", PROPENSITY_SEMANTICS == "item"
)
check(
    "🔴 동결 키 10종이 정의돼 있다 (07 E-3 ①)",
    len(REQUIRED_TRACE_PARAMS) == 10
    and {"top_k", "n_explore", "serving_mode"} <= set(REQUIRED_TRACE_PARAMS),
)

_full = {k: 1 for k in REQUIRED_TRACE_PARAMS} | {"propensity_semantics": "item"}
check("완전한 params 는 통과한다", check_trace_params(_full) == [])
check("키가 빠지면 잡아낸다", len(check_trace_params({"policy_id": "x"})) >= 9)
check(
    "🔴 의미론이 다르면 잡아낸다 (정의가 섞이면 사후 구분 불가)",
    any(
        "propensity_semantics" in m
        for m in check_trace_params(_full | {"propensity_semantics": "item_position"})
    ),
)

_f = {k: (None if k in UNAVAILABLE_FEATURES else 0.5) for k in FEATURE_KEYS}
_cands = [
    ScoredCandidate(recipe_id=i, missing_count=0, coverage=1.0, features=_f, score=1.0 - i * 0.001)
    for i in range(500)
]
_served = [3, 7, 180, 400]  # 180·400 은 상위 50 밖 — 탐색 슬롯이 뽑을 수 있다
_kept = keep_candidates(_cands, _served, "real")
check(
    "🔴 served ⊆ candidates — 상위 50 밖 탐색분도 저장된다",
    set(_served) <= {c.recipe_id for c in _kept},
)
check("합집합 비용은 최대 +2건 (0.5KB)", len(_kept) == 52)
check(
    "serving_mode 별 저장 개수가 정책과 같다",
    len(keep_candidates(_cands, [], "sim")) == CANDIDATE_KEEP["sim"]
    and keep_candidates(_cands, [3], "load_test") == [],
)

# ── S0 ⑤ pantry 소진/폐기 1비트 (2026-09-02) — 소급 불가 ──────
from features.recommend.schema import PantryIn as _PIn

_p = _PIn(items=[], removed=[{"ingredient_id": 1, "reason": "discarded"}])
check("🔴 pantry 교체 시 사라진 재료의 사유를 실을 수 있다", _p.removed[0].reason == "discarded")
check(
    "사유는 3값이다 — unknown(물었는데 스킵)이 NULL(안 물음)과 구분된다",
    _PIn(items=[], removed=[{"ingredient_id": 1, "reason": "unknown"}]).removed,
)
try:
    _PIn(items=[], removed=[{"ingredient_id": 1, "reason": "기타"}])
    check("정의되지 않은 사유는 거부한다", False)
except ValidationError:
    check("정의되지 않은 사유는 거부한다", True)
check("사유를 안 보내도 된다 — 안 물었으면 빈 목록", _PIn(items=[]).removed == [])

# ── 종단 검증 — mock 을 실제로 호출한다 ───────────────────────
#    스키마만 검사하면 "계약은 맞는데 구현이 안 따라온" 상태를 놓친다.
#    실제로 v2.9 에서 계약의 weights 를 뺐는데 mock 이 계속 싣고 있었고,
#    아래 테스트가 없었으면 W4 에 로그를 쓰기 시작한 뒤에야 터졌다.
try:
    from fastapi.testclient import TestClient

    from main import create_app

    _app = create_app()
    # 주의: 09-07 부터 모든 라우터가 내부 API 키를 요구한다 (deps.verify_internal_api_key).
    #    헤더가 없으면 401 이라 응답 본문을 볼 수 없다.
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER

    _c = TestClient(_app, headers={INTERNAL_API_KEY_HEADER: get_settings().internal_api_key})
    _r = _c.post("/v1/recommend", json={"user_id": 1, "top_k": 20})
    check("🔴 mock 실호출이 200 을 돌려준다 (계약과 구현이 일치)", _r.status_code == 200)
    if _r.status_code == 200:
        _d = _r.json()
        _p = next(st["params"] for st in _d["trace"]["stages"] if st["name"] == "rerank")
        check("🔴 실호출 params 가 동결 키 10종을 전부 싣는다", check_trace_params(_p) == [])
        check(
            "실호출 propensity 가 전부 0 보다 크다 (support 보장)",
            all(it["propensity"] > 0 for it in _d["items"]),
        )
        _lr = _c.get(f"/v1/logs/{_d['request_id']}")
        if _lr.status_code == 200:
            check(
                "🔴 저장된 로그가 config_hash 를 남긴다 (점수 재현의 열쇠)",
                _lr.json().get("config_hash"),
            )
except ImportError:
    check("mock 종단 검증 (httpx 미설치 — 건너뜀)", True)

# ── S1. DB 액세스 레이어 (04 3-1) ────────────────────────────────
print("\n[S1 · DB 액세스 계약]")

# 주의: 왕복 1회. pantry 를 파이썬이 조회해 넘기면 왕복이 2회가 된다.
check(
    "서빙 요청은 집합을 받지 않는다",
    "pantry_ids" not in RetrievalRequest.model_fields
    and "allergy_ids" not in RetrievalRequest.model_fields,
)

_extra_blocked = False
try:
    RetrievalRequest(user_id=1, pantry_ids=[1, 2])
except Exception:
    _extra_blocked = True
check("서빙 요청에 집합을 실으면 거부된다 (extra=forbid)", _extra_blocked)

# 시뮬과 서빙이 다른 상한을 쓰면 같은 입력에 다른 후보가 나오는데
# 로그만 봐서는 못 찾는다.
check(
    "시뮬·서빙 경계값 일치 (max_missing · limit)",
    all(
        repr(RetrievalInput.model_fields[k].metadata)
        == repr(RetrievalRequest.model_fields[k].metadata)
        for k in ("max_missing", "limit")
    ),
)

_rejected = 0
for _bad in (
    {"max_missing": -1},
    {"max_missing": 11},
    {"limit": 0},
    {"limit": 2001},
    {"max_minutes": 0},
):
    try:
        RetrievalRequest(user_id=1, **_bad)
    except Exception:
        _rejected += 1
check("요청 경계값 5종을 거부한다", _rejected == 5)

# ① 은 순서를 만들지 않는다. score 가 여기 있으면 ②가 건너뛰어질 수 있다.
check("후보는 점수를 갖지 않는다", "score" not in Candidate.model_fields)
check(
    "cluster_id 기본값 None (클러스터링 배치 전에도 안전)",
    Candidate(recipe_id=1, missing_count=0, coverage=1.0).cluster_id is None,
)


# ── S2. propensity 저장 경로 (DB 불필요) ─────────────────────────
print("\n[S2 · 로그 계약]")

_F = {k: 0.5 for k in FEATURE_KEYS}
_sc = [
    ScoredCandidate(
        recipe_id=i, missing_count=0, coverage=1.0, score=1.0 - i * 0.01, penalty=1.0, features=_F
    )
    for i in range(60)
]
_ri = [
    RankedItem(
        recipe_id=i,
        missing_count=0,
        coverage=1.0,
        score=1.0 - i * 0.01,
        penalty=1.0,
        features=_F,
        final_rank=n + 1,
        reason="x",
        reason_features=["f_coverage"],
        propensity=0.3 if i == 55 else 1.0,
        is_exploration=(i == 55),
    )
    for n, i in enumerate((0, 1, 2, 55))
]
_served = [0, 1, 2, 55]  # 55 는 탐색 슬롯 — 상위 50 밖이다

# 주의: 병합을 건너뛰면 저장되는 후보가 전부 ② 투영이라 propensity 가 로그에
#    단 한 번도 남지 않는다. off-policy 평가의 IPS 분모가 통째로 사라진다.
_bare = keep_candidates(_sc, _served, "real")
check(
    "병합 없이는 propensity 가 없다 (이 결함의 재현)",
    not any(hasattr(c, "propensity") for c in _bare if c.recipe_id in set(_served)),
)

_kept = keep_candidates(merge_served_detail(_sc, _ri), _served, "real")
check(
    "🔴 병합하면 노출분 전량이 propensity 를 갖는다",
    all(hasattr(c, "propensity") for c in _kept if c.recipe_id in set(_served)),
)
check("절단 밖 탐색 아이템이 살아남는다 (IPS 분모)", 55 in {c.recipe_id for c in _kept})
check(
    "탐색 아이템의 propensity 가 보존된다",
    next(c for c in _kept if c.recipe_id == 55).propensity == 0.3,
)
check("served ⊆ candidates 는 병합 후에도 성립", set(_served) <= {c.recipe_id for c in _kept})
check(
    "미노출 후보는 8키 그대로 (부풀리지 않는다)",
    len(next(c for c in _kept if c.recipe_id == 10).model_dump()) == 8,
)
check(
    "노출분은 16키 (propensity·team·mmr_penalty 포함)",
    len(next(c for c in _kept if c.recipe_id == 0).model_dump()) == 16,
)

# 동결 키는 ③ 에만 있다 — 전수 검사하면 정상 출력이 반려된다
check("동결 키는 10종이다 (04 문서의 7종은 낡았다)", len(REQUIRED_TRACE_PARAMS) == 10)


# ── 09-02 신설: 계획에 빠져 있던 셋 ──────────────────────────────
print("\n[신설 · 온보딩 · 팬트리 · 탐색]")

from features.recommend.schema import OnboardingIn, PantryIn

# 주의: 온보딩 저장 경로가 없어서 가중치 0.27 을 담을 곳이 없었다.
check(
    "온보딩 계약이 원본을 받는다 (picks · scales)",
    {"picks", "scales"} <= set(OnboardingIn.model_fields),
)
check(
    "알러지를 그룹·재료 둘 다 받는다 (안전 이중화)",
    {"allergy_groups", "allergy_ingredient_ids"} <= set(OnboardingIn.model_fields),
)
_ok = False
try:
    OnboardingIn(picks=[1], scales=[9, 0, 0])
except ValidationError:
    _ok = True
check("척도 0~4 범위 밖을 거부한다", _ok)
_ok2 = False
try:
    OnboardingIn(picks=[1], scales=[1, 2])
except ValidationError:
    _ok2 = True
check("척도는 정확히 3축이어야 한다", _ok2)

# 주의: 소진/폐기 사유를 버리면 안 물어본 것과 구분이 안 된다
check("팬트리가 removed 를 받는다", "removed" in PantryIn.model_fields)

# 주의: 탐색 아이템이 무작위 위치에 꽂혀야 position bias 곡선이 나온다
try:
    from fastapi.testclient import TestClient

    from main import create_app

    _app2 = create_app()
    # 주의: 09-07 부터 모든 라우터가 내부 API 키를 요구한다 (deps.verify_internal_api_key).
    #    헤더가 없으면 401 이라 응답 본문을 볼 수 없다.
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER

    _c2 = TestClient(_app2, headers={INTERNAL_API_KEY_HEADER: get_settings().internal_api_key})
    _pos = set()
    for _u in range(1, 60):
        _d2 = _c2.post("/v1/recommend", json={"user_id": _u, "top_k": 8}).json()
        _pos |= {i["final_rank"] for i in _d2["items"] if i["is_exploration"]}
    check(f"🔴 탐색 아이템이 여러 위치에 퍼진다 ({len(_pos)}종)", len(_pos) >= 5)

    _r3 = _c2.post("/v1/onboarding/1", json={"picks": [1, 2], "scales": [2, 2, 2]})
    check("온보딩 실호출이 200 을 돌려준다", _r3.status_code == 200)
    check("온보딩 응답이 6축 taste_vec 을 준다", len(_r3.json()["taste_vec"]) == 6)
except ImportError:
    check("탐색·온보딩 종단 (httpx 미설치 — 건너뜀)", True)


# ── session_id 접두어 — DDL 과 계약이 같아야 한다 (09-03) ─────────
print("\n[session_id 접두어]")
from features.recommend.schema import EventIn as _EvIn
from features.recommend.schema import RecommendationLogOut as _LogOut

_pat = lambda m, f: str(getattr(m.model_fields[f], "metadata", ""))
check(
    "요청·이벤트·로그 셋 다 ^[cgd]- 를 쓴다",
    all("cgd" in _pat(m, "session_id") for m in (RecommendRequest, _EvIn, _LogOut)),
)
# 주의: 입력에 패턴이 없으면 잘못된 값이 통과해 응답 조립 중 로그 계약에서 터진다.
#    422 여야 할 것이 500 이 된다 — 09-03 에 실제로 그랬다.
_ok = False
try:
    RecommendRequest(user_id=1, session_id="x-1-bad")
except ValidationError:
    _ok = True
check("요청 계약이 잘못된 접두어를 **입력에서** 거부한다", _ok)
_ok_ev = False
try:
    _EvIn(user_id=1, event_type=EventType.CLICK, recipe_id=1, session_id="x-1-bad")
except ValidationError:
    _ok_ev = True
check("이벤트 계약이 잘못된 접두어를 **입력에서** 거부한다", _ok_ev)
for _sid in ("c-1-a", "g-1-b", "d-1-c"):
    _ok2 = True
    try:
        RecommendRequest(user_id=1, session_id=_sid)
    except ValidationError:
        _ok2 = False
    check(f"{_sid[:2]} 접두어 허용", _ok2)


# ── JSONB 칸의 속 형식 (09-03 신설) ──────────────────────────────
# 주의: 표가 있다고 양식이 정해진 게 아니다. jsonb 는 무엇이든 받는다 —
#    실제로 A 와 C 가 suggested 를 배열과 객체로 각각 적어 놓았었다.
#    그대로 짰으면 에러가 아니라 빈 화면으로 나타났을 것이다.
print("\n[JSONB 속 형식]")
from features.recommend.schema import (
    PantrySnapshotItem,
    PolicyArm,
    QualityExtra,
    QueueCandidate,
    QueueSuggestion,
)

_q = QueueSuggestion(candidates=[QueueCandidate(name="매실청", score=0.62, method="jamo_trgm")])
check("검수 후보는 객체 형식 (배열 아님)", "candidates" in _q.model_dump())
check("나중에 칸을 늘릴 수 있다 (blocked_by)", "blocked_by" in _q.model_dump())

_bad = 0
for _kw in (
    {"name": "x", "score": 1.5, "method": "jamo_trgm"},
    {"name": "x", "score": 0.5, "method": "guess"},
    {"name": "", "score": 0.5, "method": "exact"},
):
    try:
        QueueCandidate(**_kw)
    except ValidationError:
        _bad += 1
check("잘못된 후보 3종을 거부한다 (점수 범위·방법·빈 이름)", _bad == 3)

# 주의: 소비기한이 유저 입력인지 추정인지 구분해야 f_expiring 을 검증할 수 있다
_pd = PantrySnapshotItem(ingredient_id=1, expires_at_source="user")
check("냉장고 스냅샷이 소비기한 출처를 구분한다", _pd.expires_at_source == "user")
_ok_src = False
try:
    PantrySnapshotItem(ingredient_id=1, expires_at_source="guess")
except ValidationError:
    _ok_src = True
check("미정의 출처를 거부한다", _ok_src)

# 주의: recipe_ids 가 없으면 어느 정책이 이겼는지 셀 수 없다
_arm = PolicyArm(team="A", model_version="v1", recipe_ids=[10, 11])
check("정책 배정이 recipe_ids 를 담는다 (승패 귀속)", _arm.recipe_ids == [10, 11])

# 주의: 표본 수가 없으면 배치 개선 없이도 추이선이 움직인다
_qe = QualityExtra(_sample_n=8000, _source="file", _log_counters={"failed": 2})
check("품질 기록이 표본 수와 출처를 담는다", (_qe.sample_n, _qe.source) == (8000, "file"))
check("로그 실패 카운터가 시계열로 남는다", _qe.log_counters["failed"] == 2)


print(f"\n{'✅ 전부 통과' if not fail else f'❌ {len(fail)}건 실패'} ({ok}건 확인)")
sys.exit(1 if fail else 0)
