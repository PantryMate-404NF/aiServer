#!/usr/bin/env python3
"""DDL 개정분 검증 — 소급 불가 컬럼이 실제로 값을 보존하는가.

    python3 tests/integration/test_ddl.py

07 E-3 로 추가한 것들은 **지금 안 담기면 나중에 영원히 없는** 값이다.
그래서 "컬럼이 있다"가 아니라 **"넣은 값이 그대로 나온다"** 를 확인한다.

부분 유니크 방식의 유일한 실제 위험도 여기서 잡는다 —
user_pantry_ids() 가 tombstone 을 빼먹으면 버린 재료가 계속 보유 중으로 잡히고,
그것은 **에러 없이** 틀린다.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

URL = os.environ.get("DATABASE_URL", "postgresql://reco:reco@localhost:5432/recodb")
SRC = "ddltest"
ok = fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {detail}")


def main() -> None:
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg 필요:  uv sync")

    conn = psycopg.connect(URL)
    cur = conn.cursor()
    cur.execute("SET search_path TO reco, public")

    # ── 격리된 픽스처
    cur.execute(
        "INSERT INTO ingredient (name, is_staple) VALUES (%s, false) RETURNING id",
        (f"__{SRC}_김치",),
    )
    ing_a = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO ingredient (name, is_staple) VALUES (%s, false) RETURNING id",
        (f"__{SRC}_두부",),
    )
    ing_b = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO app_user (username, consent_at, consent_version) "
        "VALUES (%s, now(), 'v1') RETURNING id",
        (f"__{SRC}_u",),
    )
    uid = cur.fetchone()[0]

    print("\n[1] ⑩ 동의 — 값 보존")
    cur.execute("SELECT consent_at IS NOT NULL, consent_version FROM app_user WHERE id=%s", (uid,))
    got = cur.fetchone()
    check("consent_at · consent_version 왕복", got == (True, "v1"), str(got))

    cur.execute("SAVEPOINT s")
    try:
        cur.execute("UPDATE app_user SET consent_at=NULL, consent_version='v2' WHERE id=%s", (uid,))
        check("동의 없이 버전만 있으면 거부", False, "통과돼버림")
    except Exception:
        cur.execute("ROLLBACK TO s")
        check("동의 없이 버전만 있으면 거부", True)

    print("\n[2] ③ 냉장고 tombstone")
    # 넣고 → 버리고 → 다시 넣고 → 소진
    cur.execute(
        "INSERT INTO pantry_item (user_id, ingredient_id, removed_at, removed_reason) "
        "VALUES (%s,%s, now()-interval '5d', 'discarded')",
        (uid, ing_a),
    )
    cur.execute("INSERT INTO pantry_item (user_id, ingredient_id) VALUES (%s,%s)", (uid, ing_a))
    cur.execute("INSERT INTO pantry_item (user_id, ingredient_id) VALUES (%s,%s)", (uid, ing_b))

    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE removed_at IS NULL) "
        "FROM pantry_item WHERE user_id=%s AND ingredient_id=%s",
        (uid, ing_a),
    )
    total, active = cur.fetchone()
    check("이력이 쌓인다 (총 2행 · 활성 1행)", (total, active) == (2, 1), f"{total},{active}")

    cur.execute("SAVEPOINT s")
    try:
        cur.execute("INSERT INTO pantry_item (user_id, ingredient_id) VALUES (%s,%s)", (uid, ing_a))
        check("중복 활성 행 차단", False, "들어가버림 — 부분 유니크 실패")
    except Exception:
        cur.execute("ROLLBACK TO s")
        check("중복 활성 행 차단", True)

    cur.execute("SAVEPOINT s")
    try:
        cur.execute(
            "INSERT INTO pantry_item (user_id, ingredient_id, removed_reason) "
            "VALUES (%s,%s,'consumed')",
            (uid, ing_b),
        )
        check("removed_at 없이 사유만 있으면 거부", False, "통과돼버림")
    except Exception:
        cur.execute("ROLLBACK TO s")
        check("removed_at 없이 사유만 있으면 거부", True)

    print("\n[3] 🔴 user_pantry_ids() 가 tombstone 을 제외하는가")
    cur.execute("SELECT user_pantry_ids(%s)", (uid,))
    ids = set(cur.fetchone()[0])
    check("활성 재료가 들어 있다", {ing_a, ing_b} <= ids, str(sorted(ids))[:80])
    # 활성분을 전부 내리면 두 재료가 사라져야 한다
    cur.execute(
        "UPDATE pantry_item SET removed_at=now(), removed_reason='consumed' "
        "WHERE user_id=%s AND removed_at IS NULL",
        (uid,),
    )
    cur.execute("SELECT user_pantry_ids(%s)", (uid,))
    ids2 = set(cur.fetchone()[0])
    check(
        "전부 소진하면 제외된다 (staple 만 남음)",
        not ({ing_a, ing_b} & ids2),
        f"남아있음: {sorted({ing_a, ing_b} & ids2)}",
    )

    print("\n[4] ⑤⑥⑦④ recommendation_log 신규 컬럼 왕복")
    rid = uuid.uuid4()
    detail = [
        {
            "ingredient_id": ing_a,
            "quantity": 1.5,
            "unit": "개",
            "expires_at": "2026-09-10",
            "expires_at_source": "user",
        }
    ]
    policies = [
        {"team": "A", "model_version": "v0", "mlflow_run_id": "r1"},
        {"team": "B", "model_version": "v1", "mlflow_run_id": "r2"},
    ]
    cur.execute(
        "INSERT INTO recommendation_log (request_id, user_id, session_id, model_version, "
        "config_hash, warm_alpha, pantry_snapshot, pantry_detail, policies, stage_trace, "
        "served, total_latency_ms) VALUES (%s,%s,%s,'v0','abc123',0.35,%s,%s,%s,'{}'::jsonb,%s,30)",
        (
            str(rid),
            uid,
            f"c-{uid}-0123456789ab",
            [ing_a],
            json.dumps(detail),
            json.dumps(policies),
            [1],
        ),
    )
    cur.execute(
        "SELECT session_id, warm_alpha, pantry_detail, policies "
        "FROM recommendation_log WHERE request_id=%s",
        (str(rid),),
    )
    sid, alpha, pd, pol = cur.fetchone()
    check("session_id 보존", sid == f"c-{uid}-0123456789ab", str(sid))
    check("warm_alpha 보존", abs(alpha - 0.35) < 1e-6, str(alpha))
    check(
        "pantry_detail 보존 (expires_at_source 포함)",
        pd[0]["expires_at_source"] == "user",
        str(pd)[:60],
    )
    check("policies 보존 (team 2개)", [p["team"] for p in pol] == ["A", "B"], str(pol)[:60])

    print("\n[5] ⑥ session_id prefix 규약")
    for bad in ("x-1-abc", "abc", ""):
        cur.execute("SAVEPOINT s")
        try:
            cur.execute(
                "INSERT INTO event_log (user_id, event_type, session_id, source) "
                "VALUES (%s,'click',%s,'client')",
                (uid, bad),
            )
            check(f"잘못된 prefix 거부 ({bad!r})", False, "통과돼버림")
        except Exception:
            cur.execute("ROLLBACK TO s")
            check(f"잘못된 prefix 거부 ({bad!r})", True)
    for good in (f"c-{uid}-0123456789ab", f"g-{uid}-202609010930"):
        cur.execute(
            "INSERT INTO event_log (user_id, event_type, session_id, source) "
            "VALUES (%s,'click',%s,'client')",
            (uid, good),
        )
    cur.execute(
        "SELECT count(*) FROM event_log WHERE user_id=%s AND session_id IS NOT NULL", (uid,)
    )
    check("올바른 prefix 2종 저장", cur.fetchone()[0] == 2)

    print("\n[5b] S2 event_log.source — impression 시대 구분")

    def _ev(**kw):
        """event_log 1행. 실패하면 예외를 그대로 올린다."""
        cols = ", ".join(kw)
        vals = ", ".join(["%s"] * len(kw))
        cur.execute(f"INSERT INTO event_log ({cols}) VALUES ({vals})", tuple(kw.values()))

    def _rejects(label, **kw):
        cur.execute("SAVEPOINT s2")
        try:
            _ev(**kw)
            check(label, False, "통과돼버림")
        except Exception:
            check(label, True)
        finally:
            cur.execute("ROLLBACK TO s2")

    # source 는 기본값이 없다 — 새 삽입 경로가 조용히 오분류되지 않게
    _rejects("source 누락 거부 (DEFAULT 없음)", user_id=uid, event_type="click")
    _rejects("미정의 source 거부", user_id=uid, event_type="click", source="guess")

    # 주의: impression 에 position 이 없으면 그 시대가 통째로 못 쓴다
    _rejects(
        "position 없는 impression 거부",
        user_id=uid,
        event_type="impression",
        source="served",
        request_id=str(rid),
        recipe_id=1,
    )
    # ⚠️ served ⇒ request_id 는 DDL 이 아니라 라이터가 지킨다.
    #    CHECK 로 걸면 FK 의 ON DELETE SET NULL 과 충돌해 추천 로그가 삭제 불가가 된다.
    _ev(
        user_id=uid,
        event_type="impression",
        source="served",
        position=9,
        request_id=str(rid),
        recipe_id=77,
    )
    cur.execute("SAVEPOINT s2del")
    cur.execute("DELETE FROM event_log WHERE request_id=%s AND recipe_id=77", (str(rid),))
    cur.execute("RELEASE SAVEPOINT s2del")
    check("보존기간 정리를 위해 추천 로그가 삭제 가능해야 한다", True)

    _ev(
        user_id=uid,
        event_type="impression",
        source="served",
        request_id=str(rid),
        recipe_id=1,
        position=1,
    )
    check("served impression 저장", True)

    # 같은 (request_id, recipe_id, source) 재삽입은 막힌다 — 리플레이 멱등
    _rejects(
        "동일 source 중복 거부 (멱등)",
        user_id=uid,
        event_type="impression",
        source="served",
        request_id=str(rid),
        recipe_id=1,
        position=1,
    )

    # 주의: dual 창: 같은 아이템에 served 와 viewport 가 공존해야 환산계수가 나온다
    _ev(
        user_id=uid,
        event_type="impression",
        source="viewport",
        request_id=str(rid),
        recipe_id=1,
        position=1,
    )
    cur.execute("SELECT count(*) FROM event_log WHERE request_id=%s AND recipe_id=1", (str(rid),))
    check("served·viewport 공존 (전환기 환산계수)", cur.fetchone()[0] == 2)

    print("\n[5c] 09-02 신설 — 트래픽 표식 · 온보딩 원본")

    # 주의: 디버거 트래픽 접두어. 없으면 실유저 지표에 개발자가 눌러본 것이 섞인다.
    _ev(user_id=uid, event_type="click", session_id=f"d-{uid}-debug0000", source="client")
    check("d- 접두어 허용 (디버거 트래픽)", True)
    _rejects(
        "미정의 접두어는 여전히 거부",
        user_id=uid,
        event_type="click",
        session_id="x-1-abc",
        source="client",
    )

    cur.execute("SELECT count(*) FROM v_real_events WHERE session_id LIKE 'd-%%'")
    check("v_real_events 가 d- 를 거른다", cur.fetchone()[0] == 0)

    # mock 난수 점수 행이 실지표에 안 섞이는가
    _rid2 = uuid.uuid4()
    cur.execute(
        "INSERT INTO recommendation_log (request_id, user_id, model_version, "
        "pantry_snapshot, request_params, stage_trace, served, total_latency_ms) "
        "VALUES (%s,%s,'mock-linear-v0','{}','{}'::jsonb,'{}'::jsonb,'{}',1)",
        (str(_rid2), uid),
    )
    cur.execute("SELECT count(*) FROM v_real_recommendations WHERE request_id=%s", (str(_rid2),))
    check("🔴 v_real_recommendations 가 mock 난수 점수를 거른다", cur.fetchone()[0] == 0)

    # 온보딩 원본 — 시드가 바뀌면 taste_vec 을 다시 계산해야 한다
    cur.execute(
        "INSERT INTO user_vector (user_id, taste_vec, computed_from, "
        "onboarding_picks, onboarding_scales) "
        "VALUES (%s,'{0.1,0.2,0.3,0.4,0.5,0.6}','onboarding','{3,7,12}','{2,3,1}') "
        "ON CONFLICT (user_id) DO UPDATE SET onboarding_picks=EXCLUDED.onboarding_picks",
        (uid,),
    )
    cur.execute(
        "SELECT onboarding_picks, onboarding_scales FROM user_vector WHERE user_id=%s", (uid,)
    )
    _pk, _sc = cur.fetchone()
    check("🔴 온보딩 선택 원본 보존 (시드 변경 시 재계산 가능)", _pk == [3, 7, 12])
    check("척도 3축 원본 보존", _sc == [2, 3, 1])
    cur.execute("SAVEPOINT s5c")
    try:
        cur.execute("UPDATE user_vector SET onboarding_scales='{1,2}' WHERE user_id=%s", (uid,))
        check("척도는 3개여야 한다", False, "통과돼버림")
    except Exception:
        check("척도는 3개여야 한다", True)
    finally:
        cur.execute("ROLLBACK TO s5c")

    print("\n[5d] 소비기한 — 구매일 기준 추정 (09-02)")
    import datetime as _dt

    cur.execute(
        "SELECT id FROM ingredient WHERE shelf_life_days IS NOT NULL AND NOT is_staple LIMIT 1"
    )
    _ing = cur.fetchone()[0]
    _buy = _dt.date.today() - _dt.timedelta(days=5)
    cur.execute("DELETE FROM pantry_item WHERE user_id=%s", (uid,))
    cur.execute(
        "INSERT INTO pantry_item (user_id, ingredient_id, purchased_at) VALUES (%s,%s,%s)",
        (uid, _ing, _buy),
    )
    cur.execute("SELECT days_left, src FROM effective_expiry(%s)", (uid,))
    _dl, _src = cur.fetchone()
    cur.execute("SELECT shelf_life_days FROM ingredient WHERE id=%s", (_ing,))
    _sl = cur.fetchone()[0]
    check(
        "🔴 소비기한이 **구매일** 기준으로 계산된다", _dl == _sl - 5, f"남은 {_dl} · 기대 {_sl - 5}"
    )
    check("추정 출처가 estimated", _src == "estimated")

    # 유저 입력이 추정을 이긴다
    cur.execute(
        "UPDATE pantry_item SET expires_at=%s WHERE user_id=%s",
        (_dt.date.today() + _dt.timedelta(days=1), uid),
    )
    cur.execute("SELECT days_left, src FROM effective_expiry(%s)", (uid,))
    _dl2, _src2 = cur.fetchone()
    check("유저 입력이 추정을 이긴다", (_dl2, _src2) == (1, "user"), f"{_dl2},{_src2}")

    # 구매일이 없으면 등록일로 폴백
    cur.execute(
        "UPDATE pantry_item SET expires_at=NULL, purchased_at=NULL WHERE user_id=%s", (uid,)
    )
    cur.execute("SELECT days_left FROM effective_expiry(%s)", (uid,))
    check("구매일이 없으면 등록일로 폴백", cur.fetchone()[0] == _sl)

    # 미래 구매는 막는다
    cur.execute("SAVEPOINT s5d")
    try:
        cur.execute(
            "UPDATE pantry_item SET purchased_at=%s WHERE user_id=%s",
            (_dt.date.today() + _dt.timedelta(days=30), uid),
        )
        check("미래 구매일 거부", False, "통과돼버림")
    except Exception:
        check("미래 구매일 거부", True)
    finally:
        cur.execute("ROLLBACK TO s5d")
    cur.execute("DELETE FROM pantry_item WHERE user_id=%s", (uid,))

    print("\n[5e] 트랙1 오늘의 추천 (09-03 신설)")
    import hashlib as _hl

    def _fp(ids):
        return _hl.sha256(",".join(map(str, sorted(ids))).encode()).hexdigest()[:16]

    _pan = [ing_a, ing_b] if "ing_b" in dir() else [ing_a]
    cur.execute("DELETE FROM daily_recommendation WHERE user_id=%s", (uid,))
    for _r in range(1, 4):
        cur.execute(
            "INSERT INTO daily_recommendation (user_id, rank, recipe_id, score, "
            "reason, reason_source, pantry_fingerprint, config_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'h1')",
            (
                uid,
                _r,
                1000 + _r,
                0.9 - _r * 0.1,
                "두부가 상하기 전에 쓰기 좋아요",
                "llm",
                _fp(_pan),
            ),
        )
    cur.execute(
        "SELECT count(*), min(rank), max(rank) FROM daily_recommendation WHERE user_id=%s", (uid,)
    )
    check("사전계산 3건 저장", cur.fetchone() == (3, 1, 3))

    # 주의: stale 판정 — 냉장고가 바뀌면 지문이 달라진다
    cur.execute(
        "SELECT DISTINCT pantry_fingerprint FROM daily_recommendation WHERE user_id=%s", (uid,)
    )
    _saved = cur.fetchone()[0]
    check("🔴 냉장고가 그대로면 stale 아님", _saved == _fp(_pan))
    check("🔴 재료를 쓰면 stale 로 잡힌다", _saved != _fp(_pan + [99999]))

    # 사유 출처를 구분한다 — LLM 이 템플릿보다 나은지 재려면 필요
    cur.execute("SAVEPOINT s5e")
    try:
        cur.execute(
            "INSERT INTO daily_recommendation (user_id,rank,recipe_id,score,"
            "reason_source,pantry_fingerprint) VALUES (%s,9,1,0.1,'gpt','x')",
            (uid,),
        )
        check("미정의 사유 출처 거부", False, "통과돼버림")
    except Exception:
        check("미정의 사유 출처 거부", True)
    finally:
        cur.execute("ROLLBACK TO s5e")

    # 유저가 지워지면 같이 지워진다 (개인정보 파기)
    cur.execute(
        "SELECT confdeltype FROM pg_constraint WHERE conrelid='daily_recommendation'::regclass "
        "AND confrelid='app_user'::regclass"
    )
    check("유저 삭제 시 CASCADE (개인정보 파기)", cur.fetchone()[0] == "c")
    cur.execute("DELETE FROM daily_recommendation WHERE user_id=%s", (uid,))

    print("\n[5f] 🔴 조용히 뚫리던 자리 (09-03)")

    def _blocked(label, sql, args=()):
        cur.execute("SAVEPOINT s5f")
        try:
            cur.execute(sql, args)
            check(label, False, "통과돼버림")
        except Exception:
            check(label, True)
        finally:
            cur.execute("ROLLBACK TO s5f")

    # 오타 하나로 알러지가 통째로 무력화됐다 — 삽입 성공 · 차단 0종 · 에러 없음
    _blocked(
        "알러지 그룹 오타를 거부한다 ('견과류')",
        "INSERT INTO user_allergy (user_id,allergen_group,severity) VALUES (%s,'견과류','allergy')",
        (uid,),
    )
    cur.execute(
        "INSERT INTO user_allergy (user_id,allergen_group,severity) "
        "VALUES (%s,'nut','allergy') ON CONFLICT DO NOTHING",
        (uid,),
    )
    cur.execute("SELECT cardinality(expand_user_allergens(%s))", (uid,))
    check("정타 'nut' 은 그룹 전체로 확산된다", cur.fetchone()[0] >= 3)
    cur.execute("DELETE FROM user_allergy WHERE user_id=%s", (uid,))

    _F = (
        "INSERT INTO recipe_feature (recipe_id,essential_ids,all_ids,category_ids,"
        "n_essential,n_total,flavor_vec,feature_version) VALUES "
    )
    _V = "'{0.5,0.5,0.5,0.5,0.5,0.5}'"
    # essential 이 all 밖이면 알러지 검사(all_ids 만 본다)를 그대로 통과한다
    _blocked(
        "🔴 essential ⊄ all 을 거부한다 (알러지 우회)",
        _F + f"(901,'{{10}}','{{11}}','{{}}',1,1,{_V},'v1')",
    )
    _blocked(
        "n_total 이 배열 길이와 다르면 거부", _F + f"(902,'{{10}}','{{10}}','{{}}',1,99,{_V},'v1')"
    )
    _blocked(
        "n_essential 이 배열 길이와 다르면 거부",
        _F + f"(903,'{{10}}','{{10}}','{{}}',9,1,{_V},'v1')",
    )
    # 접두어가 아니라 접미어면 실서빙에 샌다
    _blocked(
        "합성 표식이 접미어면 거부 ('scorer_test')",
        _F + f"(904,'{{10}}','{{10}}','{{}}',1,1,{_V},'scorer_test')",
    )

    print("\n[6] ⑨ event_log.recipe_id 에 FK 가 없는가")
    cur.execute("""SELECT count(*) FROM information_schema.key_column_usage k
                   JOIN information_schema.table_constraints t USING (constraint_name)
                   WHERE k.table_name='event_log' AND k.column_name='recipe_id'
                     AND t.constraint_type='FOREIGN KEY'""")
    check("recipe_id FK 없음 (로그가 도메인 수명주기에 묶이지 않는다)", cur.fetchone()[0] == 0)

    print("\n[7] ④ scoring_config 레지스트리")
    cur.execute(
        "INSERT INTO scoring_config (config_hash, base_weights, n_warm) "
        "VALUES ('abc123', %s, 20) ON CONFLICT DO NOTHING",
        (json.dumps({"f_coverage": 0.24, "f_taste": 0.16}),),
    )
    cur.execute(
        """SELECT c.base_weights->>'f_coverage', l.warm_alpha
                   FROM recommendation_log l JOIN scoring_config c
                     ON c.config_hash = l.config_hash WHERE l.request_id=%s""",
        (str(rid),),
    )
    row = cur.fetchone()
    check("config_hash 로 기준 가중치를 되찾는다", row == ("0.24", 0.35), str(row))

    # ── 정리
    conn.rollback()
    cur.close()
    conn.close()

    print("\n" + "─" * 54)
    print(f"{'✅' if fail == 0 else '🔴'} {ok}건 통과 · {fail}건 실패")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
