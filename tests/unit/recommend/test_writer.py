#!/usr/bin/env python3
"""S2 라이터 검증 — mock 출력을 실제 DB 로 흘린다.

    make log-test

DB 가 필요하다. 합성 유저는 username LIKE 's2test_%' 로 격리되며 끝에 지운다.
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from features.recommend.repository import write_recommendation
from features.recommend.service import counters, reset_counters
from infra.db import cursor
from main import create_app

app = create_app()
from features.recommend.engine.rank import keep_candidates
from features.recommend.schema import RecommendRequest, RecommendResponse

ok, fail = 0, []


def check(label: str, cond: bool, note: str = "") -> None:
    global ok
    if cond:
        ok += 1
        print(f"  ✓ {label}")
    else:
        fail.append(label)
        print(f"  ✗ {label}  {note}")


def mk_user(name: str) -> int:
    with cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO app_user (username, is_simulated) VALUES (%s, TRUE) "
            "ON CONFLICT (username) DO UPDATE SET username=EXCLUDED.username "
            "RETURNING id",
            (f"s2test_{name}",),
        )
        return cur.fetchone()[0]


def serve(client, uid: int, **kw):
    body = {"user_id": uid, "top_k": kw.pop("top_k", 8), **kw}
    raw = client.post("/v1/recommend", json=body).json()
    return RecommendRequest(**body), RecommendResponse(**raw)


def main() -> int:
    # 주의: 09-07 부터 모든 라우터가 내부 API 키를 요구한다 (deps.verify_internal_api_key).
    #    헤더가 없으면 401 이라 응답 본문을 볼 수 없다.
    from config import get_settings
    from deps import INTERNAL_API_KEY_HEADER

    client = TestClient(app, headers={INTERNAL_API_KEY_HEADER: get_settings().internal_api_key})
    reset_counters()

    print("[1] 정상 경로")
    uid = mk_user("normal")
    req, resp = serve(client, uid)
    wrote = write_recommendation(
        req, resp, pantry_ids=[1, 2], config_hash="h1", warm_alpha=0.4, stats_version=1
    )
    check("쓰기 성공", wrote)
    rid = str(resp.request_id)
    with cursor() as cur:
        cur.execute(
            "SELECT served, candidates, request_params, session_id, "
            "total_latency_ms, config_hash FROM recommendation_log "
            "WHERE request_id=%s",
            (rid,),
        )
        row = cur.fetchone()
    check("recommendation_log 1행", row is not None)
    served, cands, params, sid, ms, chash = row
    check("served 가 응답 순서 그대로", served == [it.recipe_id for it in resp.items], str(served))
    check("config_hash 보존", chash == "h1")
    check("정상 행에는 log_degraded 가 없다", "log_degraded" not in params)
    check("session_id 규약 준수 (^[cgd]-)", sid[:2] in ("c-", "g-", "d-"), sid)

    print("\n[2] 🔴 propensity — 이게 없으면 off-policy 평가가 통째로 불가능하다")
    by_id = {c["recipe_id"]: c for c in cands}
    check("candidates 에 served 전량", set(served) <= set(by_id), str(set(served) - set(by_id)))
    check(
        "served 전량이 propensity 를 갖는다",
        all("propensity" in by_id[r] for r in served),
        str([r for r in served if "propensity" not in by_id[r]]),
    )
    check(
        "propensity 가 전부 0 보다 크다 (support 보장)",
        all(by_id[r]["propensity"] > 0 for r in served),
    )
    check(
        "탐색 아이템은 propensity < 1 (IPS 분모가 실렸다)",
        any(by_id[r].get("is_exploration") and by_id[r]["propensity"] < 1.0 for r in served),
        "탐색 아이템이 없거나 propensity=1",
    )
    check(
        "features 의 None 이 0 으로 바뀌지 않았다",
        any(v is None for v in by_id[served[0]]["features"].values()),
    )

    print("\n[3] impression N행")
    with cursor() as cur:
        cur.execute(
            "SELECT count(*), count(position), count(DISTINCT source), "
            "min(position), max(position), count(DISTINCT session_id) "
            "FROM event_log WHERE request_id=%s",
            (rid,),
        )
        n, npos, nsrc, pmin, pmax, nsid = cur.fetchone()
    check(f"노출 {len(resp.items)}행", n == len(resp.items), str(n))
    check("🔴 position 이 전 행에 있다", npos == n, f"{npos}/{n}")
    check("position 이 1..N 연속", (pmin, pmax) == (1, n), f"{pmin}..{pmax}")
    check("source 는 전부 'served'", nsrc == 1)
    check("session_id 는 요청당 1개 (행마다 조회하지 않는다)", nsid == 1)

    print("\n[4] 멱등 — 재시도가 중복을 만들지 않는다")
    before = counters().get("written", 0)
    write_recommendation(req, resp, pantry_ids=[1, 2], config_hash="h1")
    with cursor() as cur:
        cur.execute("SELECT count(*) FROM recommendation_log WHERE request_id=%s", (rid,))
        n_rl = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM event_log WHERE request_id=%s", (rid,))
        n_ev = cur.fetchone()[0]
    check("recommendation_log 여전히 1행", n_rl == 1, str(n_rl))
    check("event_log 중복 없음", n_ev == len(resp.items), str(n_ev))
    # 주의: 정본 위 재시도는 written 이 아니라 duplicate 다 — 실제로 쓴 것이 아니므로
    #    written 으로 세면 대시보드가 유실을 성공으로 읽는다.
    check(
        "정본 위 재시도는 written 을 올리지 않는다",
        counters().get("written", 0) == before,
        str(counters()),
    )

    print("\n[5] 계약 위반 — 행은 쓰되 표시한다")
    uid2 = mk_user("degraded")
    req2, resp2 = serve(client, uid2)
    for st in resp2.trace.stages:  # rerank 의 동결 키를 부순다
        if st.name == "rerank":
            st.params = {"top_k": 8}
    wrote2 = write_recommendation(req2, resp2, pantry_ids=[1])
    check("위반이 있어도 행은 써진다", wrote2)
    with cursor() as cur:
        cur.execute(
            "SELECT request_params->'log_degraded' FROM recommendation_log WHERE request_id=%s",
            (str(resp2.request_id),),
        )
        deg = cur.fetchone()[0]
    check(
        "log_degraded 에 누락 키가 기록된다",
        deg is not None and "missing_trace_params" in deg,
        str(deg),
    )
    check("원문 요청도 함께 보존된다 (마커는 가산이지 대체가 아니다)", True)
    with cursor() as cur:
        cur.execute(
            "SELECT request_params->>'top_k' FROM recommendation_log WHERE request_id=%s",
            (str(resp2.request_id),),
        )
        check("request_params 에 요청 원문", cur.fetchone()[0] == "8")
    check("contract_violation 카운터", counters().get("contract_violation", 0) >= 1)

    print("\n[6] 🔴 쓰기 실패가 추천을 죽이지 않는다")
    req3, resp3 = serve(client, uid)
    object.__setattr__(resp3, "user_id", 2**40)  # 존재하지 않는 유저 → FK 위반
    n_fail_before = counters().get("failed", 0)
    try:
        wrote3 = write_recommendation(req3, resp3, pantry_ids=[1])
        raised = False
    except Exception as e:
        wrote3, raised = None, True
        print(f"      예외: {e}")
    check("예외를 올리지 않는다", not raised)
    check("실패를 False 로 알린다", wrote3 is False, str(wrote3))
    check(
        "🔴 실패를 센다 (안 세면 비어가는 걸 영원히 모른다)",
        counters().get("failed", 0) == n_fail_before + 1,
    )
    check("실패 종류별로 센다", any(k.startswith("failed:") for k in counters()), str(counters()))

    print("\n[6b] 🔴 묘비가 정본을 막지 않는다 — 적대적 검증 확정 결함")
    uid4 = mk_user("tomb")
    req4, resp4 = serve(client, uid4)
    explore = {i.recipe_id: i.propensity for i in resp4.items if i.is_exploration}
    saved = [i.final_rank for i in resp4.items]
    for i in resp4.items:  # position NULL → event_log CHECK 위반
        i.final_rank = None
    r1 = write_recommendation(req4, resp4, pantry_ids=[1], config_hash="h9")
    check("1차 실패", r1 is False)
    with cursor() as cur:
        cur.execute(
            "SELECT request_params->'log_degraded' ? 'tombstone' "
            "FROM recommendation_log WHERE request_id=%s",
            (str(resp4.request_id),),
        )
        check("묘비가 표식을 남긴다", cur.fetchone()[0] is True)

    for i, v in zip(resp4.items, saved):  # 라이터 복구 후 재시도
        i.final_rank = v
    r2 = write_recommendation(
        req4, resp4, pantry_ids=[1, 2], config_hash="h9", warm_alpha=0.5, stats_version=3
    )
    check("2차 성공", r2 is True)
    with cursor() as cur:
        cur.execute(
            "SELECT candidates, config_hash, warm_alpha, total_latency_ms, "
            "request_params->'log_degraded' ? 'tombstone' "
            "FROM recommendation_log WHERE request_id=%s",
            (str(resp4.request_id),),
        )
        cds, ch, wa, ms4, still = cur.fetchone()
    check("🔴 묘비가 정본으로 승격된다 (버려지지 않는다)", cds is not None)
    check("재현 파라미터가 복구된다", (ch, wa) == ("h9", 0.5), f"{ch},{wa}")
    check("묘비 표식이 사라진다", not still)
    check("latency 가 복구된다", ms4 > 0, str(ms4))
    if explore:
        pmap = {c["recipe_id"]: c.get("propensity") for c in cds}
        check(
            "🔴 탐색 propensity 가 살아 돌아온다 (IPS 분모)",
            all(pmap.get(r) == v for r, v in explore.items()),
            str(explore),
        )
    check("승격을 written 과 구분해 센다", counters().get("promoted", 0) >= 1, str(counters()))

    # 정본 위에는 절대 덮지 않는다 — 로그는 append-only 다
    write_recommendation(req4, resp4, pantry_ids=[9, 9, 9], config_hash="OVERWRITTEN")
    with cursor() as cur:
        cur.execute(
            "SELECT config_hash, pantry_snapshot FROM recommendation_log WHERE request_id=%s",
            (str(resp4.request_id),),
        )
        ch2, pan2 = cur.fetchone()
    check("정본 위 재시도는 덮지 않는다", (ch2, pan2) == ("h9", [1, 2]), f"{ch2},{pan2}")
    check("그 경우 duplicate 로 센다", counters().get("duplicate", 0) >= 1)

    print("\n[6c] 재현 불가·추적 부재를 행에 표시한다")
    uid5 = mk_user("norepro")
    req5, resp5 = serve(client, uid5)
    write_recommendation(req5, resp5, pantry_ids=[1])  # config_hash 등 미전달
    with cursor() as cur:
        cur.execute(
            "SELECT request_params->'log_degraded'->'not_reproducible' "
            "FROM recommendation_log WHERE request_id=%s",
            (str(resp5.request_id),),
        )
        nr = cur.fetchone()[0]
    check(
        "🔴 점수 재현 불가를 표시한다 (조용한 NULL 금지)",
        nr is not None and "config_hash" in nr,
        str(nr),
    )

    uid6 = mk_user("notrace")
    req6, resp6 = serve(client, uid6)
    kept_trace = resp6.trace
    object.__setattr__(resp6, "trace", None)  # include_trace=false 상황
    write_recommendation(
        req6,
        resp6,
        pantry_ids=[1],
        trace=kept_trace,
        config_hash="h",
        warm_alpha=0.1,
        stats_version=1,
    )
    with cursor() as cur:
        cur.execute(
            "SELECT stage_trace->'stages' IS NOT NULL, "
            "request_params->'log_degraded' FROM recommendation_log "
            "WHERE request_id=%s",
            (str(resp6.request_id),),
        )
        has_tr, deg6 = cur.fetchone()
    check("🔴 include_trace=false 여도 stage_trace 를 잃지 않는다", has_tr is True)
    check(
        "trace= 로 넘기면 동결 키 검사도 통과한다 (resp.trace 가 아니라 실효 추적)",
        deg6 is None,
        str(deg6),
    )
    with cursor() as cur:
        cur.execute(
            "SELECT jsonb_array_length(candidates) FROM recommendation_log WHERE request_id=%s",
            (str(resp6.request_id),),
        )
        # serving_mode='sim' → 10건 절단. resp.trace 를 봤다면 real 로 떨어졌을 것이다.
        check("실효 추적으로 serving_mode 를 읽는다 (절단 폭이 옳다)", cur.fetchone()[0] <= 10)

    print("\n[6d] session_id 가 행을 죽이지 않는다")
    uid7 = mk_user("longsid")
    long_sid = "c-" + "9" * 200
    req7, resp7 = serve(client, uid7, session_id=long_sid)
    wrote7 = write_recommendation(
        req7, resp7, pantry_ids=[1], config_hash="h", warm_alpha=0.1, stats_version=1
    )
    check("🔴 긴 session_id 로 행을 잃지 않는다 (DDL 은 VARCHAR(64))", wrote7)
    with cursor() as cur:
        cur.execute(
            "SELECT length(session_id) FROM recommendation_log WHERE request_id=%s",
            (str(resp7.request_id),),
        )
        check("64자로 잘린다", cur.fetchone()[0] == 64)

    print("\n[7] serving_mode 별 절단 폭")
    from features.recommend.enums import CANDIDATE_KEEP

    check(
        "real=50 · sim=10 · load_test=0",
        (CANDIDATE_KEEP["real"], CANDIDATE_KEEP["sim"], CANDIDATE_KEEP["load_test"]) == (50, 10, 0),
    )
    big = list(resp.items) * 9  # 72건
    kept = keep_candidates(big, [it.recipe_id for it in resp.items], "sim")
    check("sim 이면 10건으로 절단", len(kept) == 10, str(len(kept)))
    check(
        "절단해도 served 는 전량 남는다",
        set(it.recipe_id for it in resp.items) <= {c.recipe_id for c in kept},
    )

    print(f"\n최종 카운터: {counters()}")
    with cursor(commit=True) as cur:
        cur.execute("DELETE FROM app_user WHERE username LIKE 's2test_%%'")

    print("\n" + "─" * 54)
    if fail:
        print(f"❌ {len(fail)}건 실패 / {ok}건 통과")
        for f in fail:
            print(f"   {f}")
        return 1
    print(f"✅ {ok}건 전부 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
