"""이 도메인 테이블에 대한 SQL. 쿼리와 매핑만 둡니다.

**스테이지는 DB 를 직접 만지지 않습니다.** 여기를 거칩니다 (`stage.py` 규율 3).

    from features.recommend.repository import retrieve, write_recommendation

    cands = retrieve(user_id=1, max_missing=2)     # list[Candidate]

## 요청당 왕복 1회

`retrieve_for_user()` 가 SQL 함수 하나로 끝나도록 설계돼 있습니다 (01 1-7).
스테이지마다 따로 조회하면 **왕복 횟수만큼 RTT 가 곱해지고**, 로컬에서는 티가
안 나다가 원격 DB 로 옮기는 순간 드러납니다 (06 8-D).

## 로그는 소급이 안 됩니다

`recipe_ingredient_raw` 는 원문이 남아 사전을 고치면 재정규화됩니다. **로그에는
그런 장치가 없습니다.** 서빙 순간에만 존재하는 값(propensity·features·서빙 시점의
pantry)은 그 요청이 지나가면 어떤 백필로도 복원되지 않습니다.

## 피처 로딩은 아직 비어 있습니다

S3 에서 채웁니다. 채울 때 **후보 N개를 N번 조회하면 안 됩니다** —
`WHERE recipe_id = ANY(%s)` 한 번으로 끝내야 요청당 왕복 1회가 유지됩니다.
"""

from __future__ import annotations

from features.recommend.stage import Candidate, RetrievalRequest

from infra.db import cursor

#: 🔴 ① 은 넉넉히 뽑고 ②③ 에서 좁힌다. 500 은 p95 30.2ms 로 실측된 값
#:    (06 8-A). 늘리면 ② 의 파이썬 점수 계산이 선형으로 늘어난다.
DEFAULT_LIMIT = 500

_SQL = "SELECT * FROM retrieve_for_user(%s, %s, %s, %s, %s)"


def retrieve_raw(
    user_id: int,
    max_missing: int = 2,
    max_minutes: int | None = None,
    limit: int = DEFAULT_LIMIT,
    include_test: bool = False,
) -> list[tuple]:
    """SQL 원본 행. 스모크·벤치가 계약 변환 없이 쓰려고 남겨둔다.

    🔴 `include_test` 는 **테스트 전용**이다. `feature_version='test-*'` 인 합성
       피처까지 본다. 기본이 False 라 서빙 경로는 켤 수 없다 — B·C 가 넣은
       합성 5만 건이 실추천에 섞이는 것을 구조로 막는다.

    인자는 계약(`RetrievalRequest`)으로 검증한다 — 상한을 여기 다시 적으면
    두 곳이 어긋난다.
    """
    q = RetrievalRequest(user_id=user_id, max_missing=max_missing,
                         max_minutes=max_minutes, limit=limit)
    with cursor() as cur:
        cur.execute(_SQL, (q.user_id, q.max_missing, q.max_minutes, q.limit, include_test))
        return cur.fetchall()


def retrieve(
    user_id: int,
    max_missing: int = 2,
    max_minutes: int | None = None,
    limit: int = DEFAULT_LIMIT,
    include_test: bool = False,
) -> list[Candidate]:
    """① 산출. **점수는 아직 없다** — ② Ranking 이 매긴다.

    `max_missing` 은 요청마다 바뀔 수 있다 (사용자가 "재료 더 사도 됨" 을 켜는 경우).
    그래서 기본값을 상수로 박지 않고 인자로 받는다. 실제로 쓰인 값은
    `recommendation_log.policies` 에 실려야 재현이 된다 (S0 ① REQUIRED_TRACE_PARAMS).
    """
    return [
        Candidate(
            recipe_id=r[0],
            missing_count=r[1],
            # 🔴 SQL 이 NULL 을 줄 수 있다 (부족 재료가 없는 경우 빈 배열이 아니라 NULL).
            missing_ids=list(r[2] or []),
            coverage=float(r[3]),
            # SMALLINT → int. NULL 이면 클러스터링 배치 전이므로 균등 탐색 폴백.
            cluster_id=None if r[4] is None else int(r[4]),
        )
        for r in retrieve_raw(user_id, max_missing, max_minutes, limit, include_test)
    ]


import json
from typing import Any, Sequence

from features.recommend.schema import RecommendRequest, RecommendResponse
from features.recommend.enums import CANDIDATE_KEEP, SESSION_PREFIXES
from features.recommend.engine.rank import (
    check_trace_params,
    keep_candidates,
    merge_served_detail,
)
from features.recommend.enums import Stage
from features.recommend.stage import ScoredCandidate

from features.recommend.service import bump

#: 🔴 로그 쓰기가 요청을 오래 붙들지 않게 한다. 여기 걸리면 실패로 세고 넘어간다.
STATEMENT_TIMEOUT_MS = 300

#: 묘비를 표시하는 키. 정본과 구분하는 유일한 근거다.
TOMBSTONE_KEY = "tombstone"

# 🔴 `DO NOTHING` 이면 안 된다. 묘비(_tombstone)가 먼저 들어간 뒤 재시도하면
#    정본이 통째로 **조용히 버려지고** 함수는 True 를 돌려준다 — 실측으로
#    candidates=NULL·config_hash=NULL·latency=0 인 행이 written 으로 집계됐다.
#    재시도 시점에는 propensity 가 아직 메모리에 살아 있으므로, 이건
#    **복구 가능한 문제를 복구 불가능한 문제로 바꾸는 거래**다 (07:918 이 같은
#    안티패턴을 명시적으로 반려한다).
#
#    그래서 **묘비 위에서만 승격**한다. 정본 위에는 절대 덮지 않는다 —
#    로그는 append-only 이고, 나중 호출이 앞선 정본을 훼손하면 안 된다.
_RL_SQL = """
INSERT INTO recommendation_log (
    request_id, user_id, session_id, model_version, mlflow_run_id, config_hash,
    warm_alpha, stats_version, pantry_snapshot, pantry_detail, allergy_snapshot,
    request_params, policies, stage_trace, candidates, served, total_latency_ms)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (request_id) DO UPDATE SET
    session_id       = EXCLUDED.session_id,
    model_version    = EXCLUDED.model_version,
    mlflow_run_id    = EXCLUDED.mlflow_run_id,
    config_hash      = EXCLUDED.config_hash,
    warm_alpha       = EXCLUDED.warm_alpha,
    stats_version    = EXCLUDED.stats_version,
    pantry_snapshot  = EXCLUDED.pantry_snapshot,
    pantry_detail    = EXCLUDED.pantry_detail,
    allergy_snapshot = EXCLUDED.allergy_snapshot,
    request_params   = EXCLUDED.request_params,
    policies         = EXCLUDED.policies,
    stage_trace      = EXCLUDED.stage_trace,
    candidates       = EXCLUDED.candidates,
    served           = EXCLUDED.served,
    total_latency_ms = EXCLUDED.total_latency_ms
WHERE recommendation_log.request_params -> 'log_degraded' ? %s
RETURNING (xmax = 0)
"""

_EV_SQL = """
INSERT INTO event_log
    (user_id, recipe_id, event_type, request_id, position, session_id, source)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (request_id, recipe_id, source)
    WHERE event_type = 'impression' AND request_id IS NOT NULL
DO NOTHING
"""


def _rerank_params(tr: Any) -> dict[str, Any]:
    """③ Rerank 의 params. 동결 키 10종이 여기 실린다.

    🔴 **모든 스테이지를 검사하면 안 된다.** ①② 는 이 키들을 갖지 않는 것이 정상이라
       (mock 실측: retrieval·ranking 은 10종 전부 누락) 전수 검사는 정상 출력을 반려한다.
    """
    if tr is None:
        return {}
    for st in tr.stages:
        if st.name == Stage.RERANK:
            return dict(st.params or {})
    return {}


def _serving_mode(tr: Any) -> str:
    """real | sim | load_test. 절단 폭(CANDIDATE_KEEP)을 가른다.

    추적에 이미 실려 있으므로 라이터가 따로 유도하지 않는다 — 유도하면 로그에
    적힌 값과 실제 적용값이 갈라질 수 있다.
    """
    m = _rerank_params(tr).get("serving_mode", "real")
    return m if m in CANDIDATE_KEEP else "real"


def _session_id(req: RecommendRequest, user_id: int) -> str:
    """요청당 1회만 해결해서 impression N행에 복사한다.

    행마다 조회하면 노출 20건에 20배 조회가 된다. 그리고 CHECK 가 `^[cgd]-` 를
    강제하므로(02_schema.sql) 폴백도 규약을 지켜야 한다.
    """
    s = req.session_id
    # 🔴 허용 접두어를 여기 다시 적지 않는다 — 그렇게 했다가 'd-' 를 빠뜨려
    #    디버거 트래픽이 'g-' 로 바뀌어 저장됐다 (09-03).
    if not s or not s.startswith(SESSION_PREFIXES):
        return f"g-{user_id}-000000000000"
    # 🔴 DDL 이 VARCHAR(64) 다. 넘치면 INSERT 가 통째로 실패해 **요청 로그를 잃는다** —
    #    세션 묶음이 조금 뭉치는 것보다 행 유실이 훨씬 비싸다. 잘라서라도 남긴다.
    return s[:64]


def _dump(objs: Sequence[Any]) -> str:
    """JSONB 직렬화. 🔴 features 의 None 을 0 으로 바꾸지 않는다 — 뜻이 다르다."""
    return json.dumps([o.model_dump(mode="json") for o in objs], ensure_ascii=False)


def write_recommendation(
    req: RecommendRequest,
    resp: RecommendResponse,
    *,
    scored: Sequence[ScoredCandidate] | None = None,
    pantry_ids: Sequence[int] = (),
    allergy_ids: Sequence[int] | None = None,
    pantry_detail: Any = None,
    trace: Any = None,
    config_hash: str | None = None,
    warm_alpha: float | None = None,
    stats_version: int | None = None,
    mlflow_run_id: str | None = None,
    policies: Any = None,
) -> bool:
    """1행 + N행을 쓴다. **예외를 올리지 않는다** — 성공 여부만 돌려준다.

    `scored` 는 ② 산출(후보 풀)이다. 안 주면 노출분만 저장되어
    `candidates` 에 미노출 후보 정보가 0 이 된다 — mock 처럼 후보 풀이 없는
    경우에 해당한다. `served ⊆ candidates` 는 그래도 성립한다.
    """
    # 🔴 `include_trace=false` 는 **응답 페이로드**를 줄이라는 뜻이지 로그를 비우라는
    #    뜻이 아니다. 여기서 실효 추적을 먼저 정해야 한다 — 나중에 정하면
    #    `serving_mode` 가 real 로 잘못 떨어져 절단 폭이 10 이 아니라 50 이 된다.
    tr = trace if trace is not None else resp.trace

    served = [it.recipe_id for it in resp.items]
    mode = _serving_mode(tr)
    sid = _session_id(req, resp.user_id)

    # ── 계약 검증. 위반이면 쓰기 전에 표시해 둔다 (행은 쓴다) ──────
    flags: dict[str, Any] = {}
    if tr is None:
        flags["no_trace"] = True
    missing = check_trace_params(_rerank_params(tr))
    if missing:
        flags["missing_trace_params"] = missing

    # 🔴 merge 를 거쳐야 노출분에 propensity 가 실린다. 안 거치면 저장되는 후보가
    #    전부 ② 투영이라 IPS 분모가 로그에 한 번도 안 남는다.
    pool = merge_served_detail(list(scored) if scored else [], resp.items)
    if not pool:                      # 후보 풀이 없으면 노출분이 곧 후보다
        pool = list(resp.items)
    kept = keep_candidates(pool, served, mode)
    if not set(served) <= {c.recipe_id for c in kept} and CANDIDATE_KEEP.get(mode):
        flags["served_not_subset"] = True

    # 🔴 이 셋이 없으면 그 행의 점수는 **영원히 재현되지 않는다.** 호출자가 안 넘겼다고
    #    조용히 NULL 을 넣으면, 나중에 "왜 이 추천이 나왔나" 를 물었을 때 답이 없다.
    #    강제할 수는 없으니 **행에 표시**해서 분석이 걸러낼 수 있게 한다.
    no_repro = [k for k, v in (("config_hash", config_hash),
                               ("warm_alpha", warm_alpha),
                               ("stats_version", stats_version)) if v is None]
    if no_repro:
        flags["not_reproducible"] = no_repro

    params: dict[str, Any] = req.model_dump(mode="json")
    if flags:
        bump("contract_violation")
        params["log_degraded"] = flags

    # 🔴 `include_trace=false` 는 **응답 페이로드**를 줄이라는 뜻이지 로그를 비우라는
    #    뜻이 아니다. resp.trace 만 보면 그 요청의 stage_trace 를 통째로 잃는다.
    #    호출자가 내부에 들고 있는 것을 `trace=` 로 넘길 수 있다.
    total_ms = tr.totals.latency_ms if tr else 0
    rid = str(resp.request_id)

    try:
        from infra.db import cursor

        with cursor(commit=True) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
            cur.execute(_RL_SQL, (
                rid, resp.user_id, sid, resp.model_version, mlflow_run_id, config_hash,
                warm_alpha, stats_version, list(pantry_ids),
                json.dumps(pantry_detail, ensure_ascii=False) if pantry_detail else None,
                list(allergy_ids) if allergy_ids is not None else None,
                json.dumps(params, ensure_ascii=False),
                json.dumps(policies, ensure_ascii=False) if policies else None,
                json.dumps(tr.model_dump(mode="json"), ensure_ascii=False) if tr else "{}",
                _dump(kept), served, total_ms, TOMBSTONE_KEY,
            ))
            # 없음 = 정본이 이미 있어 가드가 막았다. 버려도 잃는 것이 없다.
            # True  = 새로 넣었다.  False = 묘비를 정본으로 승격했다.
            got = cur.fetchone()
            outcome = "duplicate" if got is None else ("written" if got[0] else "promoted")
            # 🔴 position 은 final_rank 다. 비면 그 시대 데이터는 통째로 못 쓴다 —
            #    '상위 k 만 잘라 보기' 조차 안 되고 position bias 보정이 불가능하다.
            rows = [(resp.user_id, it.recipe_id, "impression", rid,
                     it.final_rank, sid, "served") for it in resp.items]
            n_ins = 0
            if rows:
                # 🔴 **넣은 건수를 반드시 센다.** psycopg2 시절에는
                #    execute_values(fetch=True) 에 page_size 를 안 주면 RETURNING 이
                #    마지막 청크만 돌려줘 후기 60만 건이 조용히 유실된 전례가 있다.
                #    psycopg3 의 executemany 는 rowcount 에 **전체 합**을 담으므로
                #    청크 문제가 없다. ON CONFLICT DO NOTHING 이라 실제로 들어간
                #    행만 세어진다 — 중복은 0 으로 잡힌다.
                cur.executemany(_EV_SQL, rows)
                n_ins = max(cur.rowcount, 0)
        bump(outcome)
        bump("impressions", n_ins)
        return True
    except Exception as e:                    # 🔴 추천 응답은 절대 실패시키지 않는다
        bump("failed")
        bump(f"failed:{type(e).__name__}")
        _tombstone(rid, resp, sid, e)
        return False


def _tombstone(rid: str, resp: RecommendResponse, sid: str, exc: Exception) -> None:
    """최소 행만이라도 남긴다 — "그 요청이 존재했다" 는 사실은 복원이 안 된다.

    ⚠️ DB 가 통째로 죽은 경우엔 이것도 실패한다. 그때는 카운터만 남는다.
       그래서 카운터가 선택이 아니라 필수다.
    """
    # 🔴 실패를 유발한 값을 그대로 재사용하지 않는다 — 직렬화 불가한 context 나
    #    너무 긴 문자열이 원인이었다면 마지막 보루까지 같이 죽는다.
    #    묘비는 **반드시 직렬화되는 최소 페이로드**만 싣는다.
    marker = json.dumps(
        {"log_degraded": {TOMBSTONE_KEY: True, "err": type(exc).__name__}},
        ensure_ascii=False)
    try:
        from infra.db import cursor
        with cursor(commit=True) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
            cur.execute(
                "INSERT INTO recommendation_log (request_id, user_id, session_id, "
                "model_version, pantry_snapshot, request_params, stage_trace, served, "
                "total_latency_ms) VALUES (%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s,0) "
                "ON CONFLICT (request_id) DO NOTHING",
                (rid, resp.user_id, sid[:64], resp.model_version[:32], [], marker,
                 [it.recipe_id for it in resp.items]))
        bump("tombstoned")
    except Exception:
        bump("tombstone_failed")
