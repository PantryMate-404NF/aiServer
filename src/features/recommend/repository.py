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

#: 주의: ① 은 넉넉히 뽑고 ②③ 에서 좁힌다. 500 은 p95 30.2ms 로 실측된 값
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
    q = RetrievalRequest(
        user_id=user_id, max_missing=max_missing, max_minutes=max_minutes, limit=limit
    )
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
            # 주의: SQL 이 NULL 을 줄 수 있다 (부족 재료가 없는 경우 빈 배열이 아니라 NULL).
            missing_ids=list(r[2] or []),
            coverage=float(r[3]),
            # SMALLINT → int. NULL 이면 클러스터링 배치 전이므로 균등 탐색 폴백.
            cluster_id=None if r[4] is None else int(r[4]),
        )
        for r in retrieve_raw(user_id, max_missing, max_minutes, limit, include_test)
    ]


import json
from collections.abc import Mapping, Sequence
from typing import Any

from features.recommend.engine.rank import (
    check_trace_params,
    keep_candidates,
    merge_served_detail,
)
from features.recommend.enums import CANDIDATE_KEEP, SESSION_PREFIXES, Stage
from features.recommend.schema import RecommendRequest, RecommendResponse
from features.recommend.service import bump
from features.recommend.stage import ScoredCandidate

#: 주의: 로그 쓰기가 요청을 오래 붙들지 않게 한다. 여기 걸리면 실패로 세고 넘어간다.
STATEMENT_TIMEOUT_MS = 300

#: 묘비를 표시하는 키. 정본과 구분하는 유일한 근거다.
TOMBSTONE_KEY = "tombstone"

# 주의: `DO NOTHING` 이면 안 된다. 묘비(_tombstone)가 먼저 들어간 뒤 재시도하면
#    정본이 통째로 조용히 버려지고 함수는 True 를 돌려준다 — 실측으로
#    candidates=NULL·config_hash=NULL·latency=0 인 행이 written 으로 집계됐다.
#    재시도 시점에는 propensity 가 아직 메모리에 살아 있으므로, 이건
#    복구 가능한 문제를 복구 불가능한 문제로 바꾸는 거래다 (07:918 이 같은
#    안티패턴을 명시적으로 반려한다).
#
#    그래서 묘비 위에서만 승격한다. 정본 위에는 절대 덮지 않는다 —
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
    # 주의: 허용 접두어를 여기 다시 적지 않는다 — 그렇게 했다가 'd-' 를 빠뜨려
    #    디버거 트래픽이 'g-' 로 바뀌어 저장됐다 (09-03).
    if not s or not s.startswith(SESSION_PREFIXES):
        return f"g-{user_id}-000000000000"
    # 주의: DDL 이 VARCHAR(64) 다. 넘치면 INSERT 가 통째로 실패해 요청 로그를 잃는다 —
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
    # 주의: `include_trace=false` 는 응답 페이로드를 줄이라는 뜻이지 로그를 비우라는
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

    # 주의: merge 를 거쳐야 노출분에 propensity 가 실린다. 안 거치면 저장되는 후보가
    #    전부 ② 투영이라 IPS 분모가 로그에 한 번도 안 남는다.
    pool = merge_served_detail(list(scored) if scored else [], resp.items)
    if not pool:  # 후보 풀이 없으면 노출분이 곧 후보다
        pool = list(resp.items)
    kept = keep_candidates(pool, served, mode)
    if not set(served) <= {c.recipe_id for c in kept} and CANDIDATE_KEEP.get(mode):
        flags["served_not_subset"] = True

    # 주의: 이 셋이 없으면 그 행의 점수는 영원히 재현되지 않는다. 호출자가 안 넘겼다고
    #    조용히 NULL 을 넣으면, 나중에 "왜 이 추천이 나왔나" 를 물었을 때 답이 없다.
    #    강제할 수는 없으니 행에 표시해서 분석이 걸러낼 수 있게 한다.
    no_repro = [
        k
        for k, v in (
            ("config_hash", config_hash),
            ("warm_alpha", warm_alpha),
            ("stats_version", stats_version),
        )
        if v is None
    ]
    if no_repro:
        flags["not_reproducible"] = no_repro

    params: dict[str, Any] = req.model_dump(mode="json")
    if flags:
        bump("contract_violation")
        params["log_degraded"] = flags

    # 주의: `include_trace=false` 는 응답 페이로드를 줄이라는 뜻이지 로그를 비우라는
    #    뜻이 아니다. resp.trace 만 보면 그 요청의 stage_trace 를 통째로 잃는다.
    #    호출자가 내부에 들고 있는 것을 `trace=` 로 넘길 수 있다.
    total_ms = tr.totals.latency_ms if tr else 0
    rid = str(resp.request_id)

    try:
        from infra.db import cursor

        with cursor(commit=True) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
            cur.execute(
                _RL_SQL,
                (
                    rid,
                    resp.user_id,
                    sid,
                    resp.model_version,
                    mlflow_run_id,
                    config_hash,
                    warm_alpha,
                    stats_version,
                    list(pantry_ids),
                    json.dumps(pantry_detail, ensure_ascii=False) if pantry_detail else None,
                    list(allergy_ids) if allergy_ids is not None else None,
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(policies, ensure_ascii=False) if policies else None,
                    json.dumps(tr.model_dump(mode="json"), ensure_ascii=False) if tr else "{}",
                    _dump(kept),
                    served,
                    total_ms,
                    TOMBSTONE_KEY,
                ),
            )
            # 없음 = 정본이 이미 있어 가드가 막았다. 버려도 잃는 것이 없다.
            # True  = 새로 넣었다.  False = 묘비를 정본으로 승격했다.
            got = cur.fetchone()
            outcome = "duplicate" if got is None else ("written" if got[0] else "promoted")
            # 주의: position 은 final_rank 다. 비면 그 시대 데이터는 통째로 못 쓴다 —
            #    '상위 k 만 잘라 보기' 조차 안 되고 position bias 보정이 불가능하다.
            rows = [
                (resp.user_id, it.recipe_id, "impression", rid, it.final_rank, sid, "served")
                for it in resp.items
            ]
            n_ins = 0
            if rows:
                # 주의: 넣은 건수를 반드시 센다. psycopg2 시절에는
                #    execute_values(fetch=True) 에 page_size 를 안 주면 RETURNING 이
                #    마지막 청크만 돌려줘 후기 60만 건이 조용히 유실된 전례가 있다.
                #    psycopg3 의 executemany 는 rowcount 에 전체 합을 담으므로
                #    청크 문제가 없다. ON CONFLICT DO NOTHING 이라 실제로 들어간
                #    행만 세어진다 — 중복은 0 으로 잡힌다.
                cur.executemany(_EV_SQL, rows)
                n_ins = max(cur.rowcount, 0)
        bump(outcome)
        bump("impressions", n_ins)
        return True
    except Exception as e:  # 주의: 추천 응답은 절대 실패시키지 않는다
        bump("failed")
        bump(f"failed:{type(e).__name__}")
        _tombstone(rid, resp, sid, e)
        return False


def _tombstone(rid: str, resp: RecommendResponse, sid: str, exc: Exception) -> None:
    """최소 행만이라도 남긴다 — "그 요청이 존재했다" 는 사실은 복원이 안 된다.

    ⚠️ DB 가 통째로 죽은 경우엔 이것도 실패한다. 그때는 카운터만 남는다.
       그래서 카운터가 선택이 아니라 필수다.
    """
    # 주의: 실패를 유발한 값을 그대로 재사용하지 않는다 — 직렬화 불가한 context 나
    #    너무 긴 문자열이 원인이었다면 마지막 보루까지 같이 죽는다.
    #    묘비는 반드시 직렬화되는 최소 페이로드만 싣는다.
    marker = json.dumps(
        {"log_degraded": {TOMBSTONE_KEY: True, "err": type(exc).__name__}}, ensure_ascii=False
    )
    try:
        from infra.db import cursor

        with cursor(commit=True) as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
            cur.execute(
                "INSERT INTO recommendation_log (request_id, user_id, session_id, "
                "model_version, pantry_snapshot, request_params, stage_trace, served, "
                "total_latency_ms) VALUES (%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s,0) "
                "ON CONFLICT (request_id) DO NOTHING",
                (
                    rid,
                    resp.user_id,
                    sid[:64],
                    resp.model_version[:32],
                    [],
                    marker,
                    [it.recipe_id for it in resp.items],
                ),
            )
        bump("tombstoned")
    except Exception:
        bump("tombstone_failed")


# ─────────────────────────────────────────────────────────────────
# 재료 사전 — ingest 의 P3 매칭이 운영 경로에서 쓴다
#
# 주의: SQL 이 여기 있는 이유. 03 의 5절이 "SQL 은 repository.py 밖으로 나가지
#    않는다" 고 정한다. 09-05 까지는 이 두 쿼리가 ingest/match.py 안에 있었다.
#    ingest 는 스테이지이고, 스테이지는 DB 접근을 여기에 위임한다.
# ─────────────────────────────────────────────────────────────────
#: 주의: 컬럼을 `i.` 로 한정한다. ingredient 와 ingredient_category 양쪽에
#:    id·name 이 있어 한정하지 않으면 AmbiguousColumn 으로 죽는다.
_DICT_SQL = """
SELECT i.id, i.name, i.is_staple, i.is_seasoning, c.path::text
FROM ingredient i LEFT JOIN ingredient_category c ON c.id = i.category_id
"""

_ALIAS_SQL = "SELECT alias, ingredient_id FROM ingredient_alias"


def load_dictionary_rows() -> tuple[list[tuple], list[tuple]]:
    """(재료 행, 별칭 행). 검수로 쌓인 `source='manual'` alias 까지 포함된다.

    한 커서에서 두 번 조회한다 — 사전 로드는 배치 시작에 1회뿐이라
    왕복 2회가 문제되지 않고, 조인하면 재료 하나가 별칭 수만큼 중복된다.
    """
    with cursor() as cur:
        cur.execute(_DICT_SQL)
        ingredients = cur.fetchall()
        cur.execute(_ALIAS_SQL)
        aliases = cur.fetchall()
    return ingredients, aliases


# ─────────────────────────────────────────────────────────────────
# 정규화 배치 — ingest/batch.py 가 읽고 쓴다 (A-2)
# ─────────────────────────────────────────────────────────────────
_RECIPE_IDS_SQL = "SELECT id FROM recipe ORDER BY id"

#: 주의: recipe_id 로 좁힌다. 배치가 레시피 단위로 끊어 돌기 때문이다.
#:    한 레시피의 원문이 두 청크로 갈리면 역할 우선순위 병합이 반쪽만 보고
#:    돌아, 같은 재료가 두 역할로 나뉜 경우를 파이썬이 못 접고 DB 의
#:    ON CONFLICT 에 떠넘기게 된다 — 그러면 결과가 실행마다 달라진다.
_RAW_SQL = """
SELECT recipe_id, id, position, raw_text
FROM recipe_ingredient_raw
WHERE recipe_id = ANY(%s)
ORDER BY recipe_id, position
"""

#: 주의: DELETE 가 아니라 TRUNCATE 다. 45만 행을 DELETE 하면 死행이 남아
#:    VACUUM 전까지 순차 스캔이 계속 그것을 읽는다. 이 테이블은 DDL 주석대로
#:    "재생성 가능" 이라 통째로 버려도 잃는 것이 없다.
_TRUNCATE_SQL = "TRUNCATE recipe_ingredient"

_INS_SQL = """
INSERT INTO recipe_ingredient
    (recipe_id, ingredient_id, raw_id, quantity, unit, quantity_g,
     role, match_method, match_score)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (recipe_id, ingredient_id) DO NOTHING
"""


def load_recipe_ids(limit: int | None = None) -> list[int]:
    """정규화 대상 레시피 id. id 순으로 준다 — 부분 실행을 재현 가능하게 한다."""
    sql = _RECIPE_IDS_SQL + (" LIMIT %s" if limit else "")
    with cursor() as cur:
        cur.execute(sql, (limit,) if limit else None)
        return [r[0] for r in cur.fetchall()]


def load_raw_ingredients(recipe_ids: Sequence[int]) -> list[tuple[int, int, int, str]]:
    """(recipe_id, raw_id, position, raw_text). position 순으로 준다.

    청크 하나를 한 번의 왕복으로 읽는다. 레시피마다 조회하면 2,000 왕복이 된다.
    """
    with cursor() as cur:
        cur.execute(_RAW_SQL, (list(recipe_ids),))
        return cur.fetchall()


def truncate_recipe_ingredient() -> None:
    """정규화 결과를 비운다. 원문(`recipe_ingredient_raw`)은 건드리지 않는다."""
    with cursor(commit=True) as cur:
        cur.execute(_TRUNCATE_SQL)


def insert_recipe_ingredients(rows: Sequence[tuple]) -> int:
    """넣은 행 수를 돌려준다.

    주의: 반환값을 반드시 쓴다. psycopg3 의 executemany 는 rowcount 에 전체
       합을 담으므로 청크가 나뉘어도 수가 맞다. psycopg2 시절 `execute_values` 는
       page_size 를 안 주면 마지막 청크만 보고해, 로더가 62만 건을 넣고도
       29,035 만 넣은 줄 알았던 전례가 있다.
    """
    if not rows:
        return 0
    with cursor(commit=True) as cur:
        cur.executemany(_INS_SQL, rows)
        return max(cur.rowcount, 0)


# ─────────────────────────────────────────────────────────────────
# recipe_feature 빌더 — ingest/feature_build.py 가 쓴다 (A-4)
# ─────────────────────────────────────────────────────────────────
#: 집계와 삽입을 한 문장으로 끝낸다. 46,353행을 파이썬으로 왕복시키면 배열까지
#: 실어 나르게 되는데, 계산이 전부 집합 연산이라 DB 안에서 끝내는 편이 싸다.
#:
#: 주의: is_staple 을 SQL 에서 한 번 더 건다. P4 가 이미 staple 을 seasoning 으로
#:    보내지만, 39종 중 15종(쌀·밀가루·물·얼음 등)은 is_staple 로만 걸린다.
#:    검수 alias 나 manual override 가 role 을 바꾸면 물·쌀이 essential_ids 에
#:    들어가고, 그러면 "물이 없어서 못 만드는 레시피" 가 된다. 두 겹으로 건다.
#:
#: 주의: LEFT JOIN 이다. 재료가 하나도 안 붙은 레시피도 행을 만든다 —
#:    n_total=0 으로 남겨야 조회의 ⓪ 관문이 그것을 걸러낸다. 빼 버리면
#:    "왜 이 레시피가 없지" 를 추적할 근거가 사라진다.
#:
#: 주의: n_unmatched 는 여기서 안 건드린다. 재삽입할 때마다 0 으로 덮으면
#:    배치가 넣어 둔 값이 조용히 사라진다. 갱신은 _UNMATCHED_SQL 이 따로 한다.
_FEATURE_SQL = """
INSERT INTO recipe_feature
    (recipe_id, essential_ids, all_ids, category_ids,
     n_essential, n_total, flavor_vec, cook_minutes, difficulty, feature_version)
SELECT r.id,
       ess, alls, cats,
       cardinality(ess), cardinality(alls),
       ARRAY[0,0,0,0,0,0]::REAL[],
       r.cook_minutes, r.difficulty, %s
FROM recipe r
CROSS JOIN LATERAL (
    SELECT COALESCE(array_agg(DISTINCT ri.ingredient_id)
                    FILTER (WHERE ri.role = 'essential' AND NOT i.is_staple),
                    '{}')::INTEGER[] AS ess,
           COALESCE(array_agg(DISTINCT ri.ingredient_id), '{}')::INTEGER[] AS alls,
           COALESCE(array_agg(DISTINCT i.category_id)
                    FILTER (WHERE i.category_id IS NOT NULL),
                    '{}')::INTEGER[] AS cats
    FROM recipe_ingredient ri
    JOIN ingredient i ON i.id = ri.ingredient_id
    WHERE ri.recipe_id = r.id
) agg
ON CONFLICT (recipe_id) DO UPDATE SET
    essential_ids  = EXCLUDED.essential_ids,
    all_ids        = EXCLUDED.all_ids,
    category_ids   = EXCLUDED.category_ids,
    n_essential    = EXCLUDED.n_essential,
    n_total        = EXCLUDED.n_total,
    cook_minutes   = EXCLUDED.cook_minutes,
    difficulty     = EXCLUDED.difficulty,
    feature_version = EXCLUDED.feature_version,
    updated_at     = now()
"""

#: 배치가 센 레시피별 미매칭 수를 한 문장으로 반영한다.
#:
#: 주의: 이번에 처리한 레시피만 0 으로 되돌린다. 범위를 안 주고 전 행을 비우면
#:    `--limit 2000` 부분 실행 한 번이 나머지 44,353건의 n_unmatched 를 지운다.
#:    그 값은 배치 메모리에만 있었으므로 전량을 다시 돌리기 전에는 복구되지
#:    않고, D-10 은 미매칭 0 을 정상으로 읽어 조용히 꺼진다.
#:
#: 0 으로 되돌리는 것 자체는 필요하다. 사전이 좋아져 미매칭이 사라진 레시피가
#: 옛 값을 이고 있으면 고친 것이 안 고쳐진 것처럼 보인다.
_RESET_UNMATCHED_SQL = """
UPDATE recipe_feature SET n_unmatched = 0
WHERE  recipe_id = ANY(%s::BIGINT[]) AND n_unmatched <> 0
"""

_UNMATCHED_SQL = """
UPDATE recipe_feature rf
SET    n_unmatched = u.n
FROM   (SELECT unnest(%s::BIGINT[]) AS rid, unnest(%s::INT[]) AS n) u
WHERE  rf.recipe_id = u.rid AND rf.n_unmatched IS DISTINCT FROM u.n
"""

#: 정규화가 끝났음을 표시한다.
#:
#: 주의: 'raw' 만 건드린다. 출발 상태를 안 보면 사람이 검수해 'published' 로
#:    올린 레시피가 배치를 다시 돌릴 때마다 'normalized' 로 강등되고, 수동으로
#:    'rejected' 를 찍어 둔 것도 되살아난다. 이 빌더는 자기가 만든 상태만 쓴다.
#:
#: 주의: D-10 실패를 여기에 'rejected' 로 쓰지 않는다. D-14 가 거르는 곳을
#:    조회 함수로 정했다 — recipe_feature 46,353행 계약을 살리고, C 가 "몇 건을
#:    왜 버렸나" 를 셀 수 있게 하기 위해서다. status 로 거르면 조회가 recipe 를
#:    조인하지 않으므로 아무것도 걸리지 않는다.
_STATUS_SQL = """
UPDATE recipe r
SET    status = 'normalized'
WHERE  r.status = 'raw'
   AND EXISTS (SELECT 1 FROM recipe_feature rf
               WHERE rf.recipe_id = r.id AND rf.n_total > 0)
"""


def rebuild_recipe_features(version: str) -> int:
    """recipe_feature 를 recipe_ingredient 로부터 다시 만든다. 멱등이다."""
    with cursor(commit=True) as cur:
        cur.execute(_FEATURE_SQL, (version,))
        return max(cur.rowcount, 0)


def set_unmatched_counts(counts: Mapping[int, int], scope: Sequence[int]) -> int:
    """레시피별 P3 미매칭 수를 반영한다.

    Args:
        counts: 미매칭이 있는 레시피만. 없는 레시피는 키가 없다.
        scope: 이번 배치가 실제로 처리한 레시피 전체. 이 범위 안에서만 0 으로
            되돌린다 — 범위 밖은 손대지 않아야 부분 실행이 남의 값을 지우지 않는다.

    한 트랜잭션에서 되돌리고 다시 채운다. 둘로 나뉘면 그 사이에 조회가 들어와
    미매칭 0 을 보고 D-10 이 통과시킨다.
    """
    ids = list(scope)
    with cursor(commit=True) as cur:
        cur.execute(_RESET_UNMATCHED_SQL, (ids,))
        n = max(cur.rowcount, 0)
        if counts:
            rids = list(counts.keys())
            cur.execute(_UNMATCHED_SQL, (rids, [counts[r] for r in rids]))
            n += max(cur.rowcount, 0)
        return n


def mark_recipe_status() -> int:
    """피처가 붙은 레시피를 'raw' 에서 'normalized' 로 올린다."""
    with cursor(commit=True) as cur:
        cur.execute(_STATUS_SQL)
        return max(cur.rowcount, 0)


_QUALITY_SQL = """
SELECT rf.recipe_id, rf.n_essential, rf.n_total, rf.n_unmatched
FROM recipe_feature rf
"""


def load_feature_quality() -> list[tuple[int, int, int, int]]:
    """(recipe_id, n_essential, n_total, n_unmatched). D-10 판정과 품질 보고용."""
    with cursor() as cur:
        cur.execute(_QUALITY_SQL)
        return cur.fetchall()


# ─────────────────────────────────────────────────────────────────
# flavor_vec 빌더 — ingest/flavor_build.py 가 쓴다 (A-5)
# ─────────────────────────────────────────────────────────────────
#: 강도 계산에 필요한 것을 한 번에 가져온다.
#:
#: 주의: n_total 은 *원문* 행 수다. 매칭된 행 수가 아니다. intensity 의 위치
#:    보정이 "재료 목록의 앞 1/3" 을 보는데, 못 붙은 재료도 목록에는 있었다.
#:    매칭분으로 세면 미매칭이 많은 레시피일수록 앞쪽 판정이 헐거워진다.
_FLAVOR_SRC_SQL = """
SELECT ri.recipe_id, r.title, i.name, c.path::text, ri.role, ri.unit,
       COALESCE(rr.position, 0), nt.n
FROM recipe_ingredient ri
JOIN recipe r      ON r.id = ri.recipe_id
JOIN ingredient i  ON i.id = ri.ingredient_id
LEFT JOIN ingredient_category c ON c.id = i.category_id
LEFT JOIN recipe_ingredient_raw rr ON rr.id = ri.raw_id
JOIN LATERAL (SELECT count(*) AS n FROM recipe_ingredient_raw x
              WHERE x.recipe_id = ri.recipe_id) nt ON TRUE
WHERE ri.recipe_id = ANY(%s)
ORDER BY ri.recipe_id, rr.position
"""

_FLAVOR_UPD_SQL = """
UPDATE recipe_feature rf
SET    flavor_vec = u.v
FROM   (SELECT unnest(%s::BIGINT[]) AS rid, unnest(%s::REAL[][]) AS v) u
WHERE  rf.recipe_id = u.rid
"""

_FLAVOR_ONE_SQL = "UPDATE recipe_feature SET flavor_vec = %s WHERE recipe_id = %s"

_MU_SQL = """
INSERT INTO feature_stats (flavor_mu, n_recipes, note)
VALUES (%s, %s, %s)
RETURNING stats_version
"""

_FLAVOR_ALL_SQL = "SELECT recipe_id, flavor_vec FROM recipe_feature"


def load_flavor_source(recipe_ids: Sequence[int]) -> list[tuple]:
    """(recipe_id, title, 재료명, 분류경로, role, unit, position, 원문행수)."""
    with cursor() as cur:
        cur.execute(_FLAVOR_SRC_SQL, (list(recipe_ids),))
        return cur.fetchall()


def set_flavor_vectors(vectors: Mapping[int, Sequence[float]]) -> int:
    """레시피별 6축을 쓴다.

    주의: 원값을 그대로 넣는다. 코퍼스 평균을 미리 빼면 CHECK(길이 6)는 통과하고
       값도 그럴듯하지만, 유저 taste_vec 은 원좌표계라 f_taste 가 좌표계가
       어긋난 채 조용히 돈다. 게다가 스코어러가 규약대로 한 번 더 빼면 두 번
       빠진다. 빼기는 읽는 쪽이 양쪽에 같은 μ 로 한다 (A-5 규약).
    """
    if not vectors:
        return 0
    with cursor(commit=True) as cur:
        n = 0
        for rid, vec in vectors.items():
            cur.execute(_FLAVOR_ONE_SQL, (list(vec), rid))
            n += max(cur.rowcount, 0)
        return n


def load_all_flavor_vectors() -> list[tuple[int, list[float]]]:
    """μ 계산과 검증기가 읽는다. 46,353행이라 한 번에 올려도 된다."""
    with cursor() as cur:
        cur.execute(_FLAVOR_ALL_SQL)
        return cur.fetchall()


def insert_feature_stats(mu: Sequence[float], n_recipes: int, note: str) -> int:
    """새 stats_version 을 만든다. 기존 행은 지우지 않는다.

    주의: 덮어쓰지 않는다. recommendation_log 가 stats_version 을 싣기 때문에,
       옛 μ 를 지우면 과거 요청의 점수를 재현할 수 없다 — 소급이 안 된다.
    """
    with cursor(commit=True) as cur:
        cur.execute(_MU_SQL, (list(mu), n_recipes, note))
        row = cur.fetchone()
        return int(row[0])


_FLAVOR_LABEL_SQL = """
SELECT rf.recipe_id, r.title, rf.flavor_vec
FROM recipe_feature rf JOIN recipe r ON r.id = rf.recipe_id
WHERE rf.n_total > 0
"""


def load_flavor_with_titles() -> list[tuple[int, str, list[float]]]:
    """검증기(A-5)가 제목 라벨로 판별력을 잴 때 읽는다."""
    with cursor() as cur:
        cur.execute(_FLAVOR_LABEL_SQL)
        return cur.fetchall()


_LATEST_MU_SQL = """
SELECT stats_version, flavor_mu, n_recipes
FROM feature_stats ORDER BY stats_version DESC LIMIT 1
"""


def load_latest_mu() -> tuple[int, list[float], int] | None:
    """가장 최근 stats_version 의 μ. 없으면 None."""
    with cursor() as cur:
        cur.execute(_LATEST_MU_SQL)
        return cur.fetchone()
