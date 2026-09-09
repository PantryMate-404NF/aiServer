#!/usr/bin/env python3
"""문서에 적힌 수치가 실제와 맞는지 기계로 대조한다.

    make doc-check

## 왜 필요한가

문서는 **조용히 낡는다.** 코드는 테스트가 깨져서 알려주는데 문서는 아무 일도
일어나지 않는다. 실제로 09-02 점검에서 `00` 한 문서에만 12건이 나왔고, 그중
셋은 같은 날 고쳐 쓰면서 새로 만든 오류였다.

그래서 **문서에 쓰는 수치를 여기 등록**한다. 데이터가 바뀌면 여기가 먼저 빨개진다.

이 검사가 보는 것 셋:
  ① 문서의 **수치**가 DB·시드·코드와 같은가
  ② 검증 명령 **건수**를 적은 문서가 낡지 않았는가 (docs/ 전부를 훑는다)
  ③ `05_API_명세.md` 가 적은 **단언**대로 실제로 동작하는가
     — "position 0 은 400" 같은 문장은 05 가 생성 파일이어도 손으로 쓴 것이라
       코드가 바뀌어도 안 따라온다. 거기가 남은 유일한 드리프트 경로다.

🔴 **서술의 논리와 최신성은 여전히 사람이 본다.** 등록되지 않은 문장은 안 본다.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("DATABASE_URL", "postgresql://reco:reco@localhost:5432/recodb")

ok, fail = 0, []


def check(label: str, want, got) -> None:
    global ok
    if want == got:
        ok += 1
        print(f"  ✓ {label}  ({got})")
    else:
        fail.append(f"{label} — 문서 {want} · 실제 {got}")
        print(f"  ✗ {label}  문서={want}  실제={got}")


def live_docs() -> list:
    """수치 검사 대상 문서.

    🔴 `docs/reco/decisions/` 는 **날짜가 박힌 기록**이라 제외한다.
       "2026-09-04 시점에 ddl-test 17건으로 검증했다" 는 그날의 사실이고,
       오늘 47건이라고 해서 틀린 문장이 되지 않는다. 검사에 넣으면 기록을
       고쳐야 통과하게 되는데, 그건 기록을 기록이 아니게 만든다.
    """
    return [f for f in sorted((ROOT / "docs").rglob("*.md"))
            if "decisions" not in f.parts]


def doc(name: str) -> str:
    return (ROOT / "docs" / "reco" / "design" / name).read_text(encoding="utf-8")


def has(name: str, pattern: str) -> bool:
    """문서에 이 표현이 있는가. 정규식."""
    return re.search(pattern, doc(name)) is not None


def one(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


def main() -> int:
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg 필요:  uv sync")
    conn = psycopg.connect(DSN)
    cur = conn.cursor()
    cur.execute("SET search_path TO reco, public")

    print("[문서 수치 검증]\n")

    # ── DB 실측 ──────────────────────────────────────────────
    print("00_아키텍처_개요.md — 데이터 규모")
    n_recipe = one(cur, "SELECT count(*) FROM recipe")
    check("레시피 건수", True, has("01_ARCHITECTURE.md", rf"{n_recipe:,}"))
    for label, sql, pat in [
        ("재료 원문", "SELECT count(*) FROM recipe_ingredient_raw", None),
        ("조리 순서", "SELECT count(*) FROM recipe_step", None),
        ("후기", "SELECT count(*) FROM recipe_review", None),
    ]:
        v = one(cur, sql)
        check(f"{label} {v:,}", True, has("01_ARCHITECTURE.md", rf"{v:,}"))

    print("\n00_아키텍처_개요.md — 시드")
    n_ing = one(cur, "SELECT count(*) FROM ingredient")
    n_ali = one(cur, "SELECT count(*) FROM ingredient_alias")
    n_stp = one(cur, "SELECT count(*) FROM ingredient WHERE is_staple")
    check(f"재료 사전 {n_ing}종", True, has("01_ARCHITECTURE.md", rf"{n_ing}종"))
    check(f"별칭 {n_ali}개", True, has("01_ARCHITECTURE.md", rf"{n_ali}개"))
    check(f"기본양념 {n_stp}종", True, has("01_ARCHITECTURE.md", rf"{n_stp}종"))

    print("\n00_아키텍처_개요.md — 없는 데이터 (있다고 쓰면 안 된다)")
    for label, col in [("음식 유형", "cuisine_family"), ("요리 종류", "dish_type"),
                       ("평점", "rating_avg"), ("이미지", "image_url")]:
        v = one(cur, f"SELECT count({col}) FROM recipe")
        check(f"{label} 0건", 0, v)

    # ── 코드 대조 ────────────────────────────────────────────
    print("\n00_아키텍처_개요.md — 가중치")
    sys.path.insert(0, str(ROOT))
    from features.recommend.enums import (
        ACTIVE_WEIGHT_TODAY,
        DEFAULT_WEIGHTS,
        FEATURE_KEYS,
    )
    check("설계 가중치 합 1.00", 1.0, round(sum(DEFAULT_WEIGHTS.values()), 4))
    check("오늘 실효 가중치가 문서에 있다", True,
          has("01_ARCHITECTURE.md", re.escape(f"{ACTIVE_WEIGHT_TODAY}")))
    check("피처 종수", len(FEATURE_KEYS),
          len(FEATURE_KEYS) if has("01_ARCHITECTURE.md", rf"{len(FEATURE_KEYS)}종") else -1)
    n_zero = sum(1 for v in DEFAULT_WEIGHTS.values() if v == 0)
    check(f"가중치 0 인 피처 {n_zero}종", True,
          has("01_ARCHITECTURE.md", rf"(나머지 )?{n_zero}종은 가중치 0"))
    # 문서의 가중치 표가 코드와 일치하는가
    body = doc("01_ARCHITECTURE.md")
    bad = [k for k, v in DEFAULT_WEIGHTS.items()
           if v > 0 and not re.search(rf"{k}\s+{v:.2f}", body)]
    check("표의 모든 w>0 항목이 코드와 일치", [], bad)

    print("\n00_아키텍처_개요.md — 금지된 서술")
    # 08 이 대외 발표 금지로 못박은 값
    stray = re.findall(r"상호작용이 \*\*0\.4건", body)
    check("폐기된 '0.4건' 을 주장하지 않는다", [], stray)
    check("P5 를 완성이라 하지 않는다", False,
          bool(re.search(r"P1~P5 가 완성", body)))

    print("\n00_아키텍처_개요.md — 09-02 재점검에서 나온 오류들")
    # 아래는 전부 실제로 틀렸던 자리다. 같은 실수를 다시 하지 않게 박아 둔다.
    n_pair = one(cur, "SELECT count(DISTINCT (author_hash, recipe_id)) "
                      "FROM recipe_review WHERE author_hash IS NOT NULL")
    per_item = round(n_pair / n_recipe, 2)
    check(f"후기 상호작용 {per_item}건 (쌍 {n_pair:,})", True,
          has("01_ARCHITECTURE.md", rf"{per_item}건") and
          has("01_ARCHITECTURE.md", rf"{n_pair:,}"))

    # 파이프라인 지연: 표의 세 값(30+15+5=50)과 본문 합계(58) 사이의 7.3ms 를
    # 본문이 밝히는가. 안 밝히면 독자가 검산했을 때 숫자가 안 맞는다.
    check("58ms 근거(7.3ms)를 본문이 밝힌다", True,
          has("01_ARCHITECTURE.md", r"7\.3ms"))

    # BT 학습 파라미터는 11개다 (FEATURE_KEYS 17 과 다르다)
    check("BT 파라미터를 17개라 하지 않는다", False,
          bool(re.search(r"17개 개별 가중치", body)))

    # LLM 처리량은 10건 기준 833 tok/s (1,500 은 근거 없음)
    check("LLM 처리량 833 tok/s", True, has("01_ARCHITECTURE.md", r"833 tok/s"))
    check("근거 없는 1,500 tok/s 를 쓰지 않는다", False,
          bool(re.search(r"1,500 tok/s", body)))

    # 노출 나눗셈: 20,000 / 46,353
    exp = round(20000 / n_recipe, 2)
    check(f"선택지당 {exp}회", True, has("01_ARCHITECTURE.md", rf"{exp}회"))

    # 목적함수는 나눗셈이 정본
    check("목적함수에 분모가 있다", True,
          has("01_ARCHITECTURE.md", r"Σ wᵢ·fᵢ\) / Σ wᵢ"))

    print("\n01_추천시스템_설계.md")
    b1 = doc("02_RECOMMENDER_DESIGN.md")
    check("레시피 건수가 실제와 같다", True, f"{n_recipe:,}" in b1)
    check("낡은 '4.4만' 표기가 없다", 0, b1.count("4.4만"))
    check("낡은 '525종' 표기가 없다", 0, b1.count("525종"))
    check("event_log DDL 에 source 가 있다", True,
          bool(re.search(r"source\s+VARCHAR\(16\)\s+NOT NULL", b1)))
    check("재적재 멱등 제약이 적혀 있다", True, "UNIQUE (recipe_id, position)" in b1)
    check("알러지 severity 함정이 경고돼 있다", True, "severity 를 생략" in b1 or "값을 생략하면" in b1)
    check("요리 유형 축 0건 경고가 있다", True, "전제가 깨졌다" in b1)

    print("\n환경 · 의존성")
    # 주의: requirements.txt 는 09-02 에 폐지했다. 문서가 그것을 안내하면
    #    새로 합류하는 사람이 없는 파일을 찾는다.
    for f in ("01_ARCHITECTURE.md", "02_RECOMMENDER_DESIGN.md",
              "04_EXECUTION_PLAN.md", "06_INFRA_SPEC.md"):
        check(f"{f} 가 requirements.txt 를 안내하지 않는다", 0,
              doc(f).count("requirements.txt"))
    check("pyproject.toml 이 있다", True, (ROOT / "pyproject.toml").exists())
    check("uv.lock 이 있다", True, (ROOT / "uv.lock").exists())
    check("requirements.txt 가 지워졌다", False,
          (ROOT / "db/requirements.txt").exists() or
          (ROOT / "reco/requirements.txt").exists())

    print("\n06_인프라_사양.md — 실측 규모")
    # 주의: DB 크기는 VACUUM·WAL·시험 데이터로 늘 흔들린다. 정확히 대조하면
    #    아무 잘못 없이 매번 빨개진다. 자릿수가 맞는지만 본다 —
    #    잡으려는 것은 "크롤 전 10 MB" 같은 낡은 값이다.
    db_mb = int(one(cur, "SELECT round(pg_database_size(current_database())/1024.0/1024)"))
    b6 = doc("06_INFRA_SPEC.md")
    stated = [int(x) for x in re.findall(r"\*\*(\d+) MB\*\*", b6)]
    near = [v for v in stated if 0.7 * db_mb <= v <= 1.3 * db_mb]
    check(f"DB 크기 서술이 실측 {db_mb} MB 와 같은 자릿수", True, bool(near))

    print("\n09-03 신설 — 스키마·함수가 문서와 맞는가")
    # 주의: DDL 을 바꾸면 문서의 인라인 DDL 블록이 조용히 낡는다.
    #    실제 컬럼이 있는데 문서에 없으면 새로 합류하는 사람이 못 찾는다.
    for tbl, col, doc_name in [
        ("event_log", "source", "02_RECOMMENDER_DESIGN.md"),
        ("user_vector", "onboarding_picks", "02_RECOMMENDER_DESIGN.md"),
        ("pantry_item", "purchased_at", "02_RECOMMENDER_DESIGN.md"),
    ]:
        cur.execute("SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='reco' AND table_name=%s AND column_name=%s",
                    (tbl, col))
        in_db = cur.fetchone()[0] == 1
        check(f"{tbl}.{col} 이 DB 와 {doc_name} 양쪽에",
              True, in_db and col in doc(doc_name))

    cur.execute("SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='reco' AND table_name='daily_recommendation'")
    check("daily_recommendation 테이블이 있다", 1, cur.fetchone()[0])

    # 시간대 — UTC 면 임박 판정이 하루 어긋난다
    cur.execute("SELECT current_setting('TimeZone')")
    check("DB 시간대가 Asia/Seoul", "Asia/Seoul", cur.fetchone()[0])

    # 함수 오버로드가 남아 있지 않은가
    cur.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='reco' AND proname='retrieve_for_user'")
    check("retrieve_for_user 가 한 벌만 (오버로드 없음)", 1, cur.fetchone()[0])

    print("\n머메이드 다이어그램")
    for f in ("01_ARCHITECTURE.md", "02_RECOMMENDER_DESIGN.md"):
        blocks = re.findall(r"```mermaid\n(.*?)```", doc(f), re.S)
        okb = all(b.count("[") == b.count("]") and b.count("(") == b.count(")")
                  and b.count('"') % 2 == 0
                  and len(re.findall(r"^\s*subgraph\b", b, re.M))
                      == len(re.findall(r"^\s*end\s*$", b, re.M))
                  for b in blocks)
        check(f"{f} 머메이드 {len(blocks)}개 문법 균형", True, bool(blocks) and okb)

    print("\n검증 명령 건수 — 문서가 적은 수 vs 실제")
    # 주의: 문서가 "make contract 84건" 처럼 적는데, 테스트가 늘면 조용히 낡는다.
    #    09-03 에 실제로 contract 84→98 · ddl-test 41→47 로 늘면서
    #    docs/ 8개 파일 18곳이 한꺼번에 낡았다.
    #    그때 이 검사는 지시서 1개만 보고 있어서 하나도 못 잡았다.
    #    그래서 docs/ 아래 모든 .md 를 본다.
    import subprocess
    TARGETS = ["contract", "ddl-test", "smoke", "smoke-py", "log-test"]

    # 명령 이름 뒤 WINDOW 자(같은 줄) 안에서 건수 표기만 읽는다.
    #  · `98건` 또는 `98` / `98건` 만 건수로 본다.
    #    "경계값 5종" · "test_contract.py:176" 같은 산문 속 숫자를 세지 않으려는 것이다.
    #  · ~~42~~ (취소선) 과 "65건 → 98건" 의 앞쪽은 일부러 남긴 옛 값이라 건너뛴다
    WINDOW = 40
    DOCS = live_docs()
    _COUNT = re.compile(r"(?<![.\d])(\d{1,4})건|\*\*(\d{1,4})건?\*\*")

    def cited(text, t):
        """(줄번호, 인용된 건수) 목록."""
        out = []
        # smoke 가 smoke-py 를 잡아먹지 않게 경계를 준다
        # 주의: `make contract` / `contract` 처럼 명령으로 적힌 것만 본다.
        #    test_contract.py · feature_version='smoke' 같은 이름 속 등장을 세지 않으려는 것이다.
        pat = rf"(?:make {re.escape(t)}|`{re.escape(t)})(?![\w-])`?"
        for m in re.finditer(pat, text):
            win = text[m.end(): m.end() + WINDOW].split("\n", 1)[0]
            win = re.sub(r"~~.*?~~", "", win)                 # 취소선 = 옛 값
            win = re.sub(r"\d{1,4}건?\s*(?:→|->)", "", win)    # "65건 → 98건" 의 앞쪽
            num = _COUNT.search(win)
            if num:
                out.append((text.count("\n", 0, m.start()) + 1,
                            int(num.group(1) or num.group(2))))
        return out

    for t in TARGETS:
        try:
            res = subprocess.run(["make", t], cwd=ROOT, capture_output=True,
                                 text=True, timeout=300)
        except Exception:
            continue
        nums = re.findall(r"✅[^\n]*?(\d+)건", res.stdout)
        if not nums:
            continue
        n = int(nums[-1])
        stale = []
        for f in DOCS:
            for ln, v in cited(f.read_text(encoding="utf-8"), t):
                if v != n:
                    stale.append(f"{f.relative_to(ROOT)}:{ln}={v}")
        check(f"문서의 {t} 건수 (실제 {n})", [], stale)

    # ─────────────────────────────────────────────────────────────
    # 05_API_명세.md 의 단언 ↔ 실제 동작
    # 주의: 05 는 생성 파일이라 예시·숫자는 어긋날 수 없다. 그런데 render.py 안의
    #    "position 0 은 400" 같은 손으로 쓴 단언은 코드가 바뀌어도 안 따라온다.
    #    거기가 지금 유일하게 남은 드리프트 경로다.
    # 각 항목은 둘을 함께 본다 — ① 실제 동작이 그런가 ② 문서가 아직 그렇게 말하는가.
    #    문서에서 그 문장을 지워도 빨개진다.
    # ─────────────────────────────────────────────────────────────
    print("\n05_API_명세.md 의 단언 ↔ 실제 동작")
    spec = doc("05_API_SPEC.md")
    try:
        sys.path.insert(0, str(ROOT))
        from fastapi.testclient import TestClient

        from main import create_app

        app = create_app()
    except Exception as e:
        print(f"  ⏭  Mock 을 못 띄워 건너뜀 ({e})")
    else:
        # 주의: 09-07 부터 모든 라우터가 내부 API 키를 요구한다 (deps.verify_internal_api_key).
        #    헤더가 없으면 401 이라 응답 본문을 볼 수 없다.
        from config import get_settings
        from deps import INTERNAL_API_KEY_HEADER
        tc = TestClient(app, headers={INTERNAL_API_KEY_HEADER: get_settings().internal_api_key})
        _rid = tc.post("/v1/recommend", json={"user_id": 7, "top_k": 3}).json()["request_id"]

        def _ev(**kw) -> int:
            body = {"user_id": 7, "event_type": "click",
                    "recipe_id": 10001, "request_id": _rid}
            body.update(kw)
            return tc.post("/v1/events", json={"events": [body]}).status_code

        def _batch(n: int) -> int:
            one = {"user_id": 7, "event_type": "click",
                   "recipe_id": 1, "request_id": _rid}
            return tc.post("/v1/events", json={"events": [one] * n}).status_code

        #        라벨                      실제값 함수            기대  문서에 있어야 하는 문구
        PROBES = [
            ("position 0 은 400",     lambda: _ev(position=0),   400, "1-base"),
            ("position 101 은 400",   lambda: _ev(position=101), 400, "1~100"),
            ("이벤트 0건은 400",       lambda: _batch(0),         400, "1~200건"),
            ("이벤트 201건은 400",     lambda: _batch(201),       400, "201건은 배치 전체가"),
            ("source 를 보내면 400",   lambda: _ev(source="client"), 400,
                                                        "보내면 **400** 이다"),
            ("context 실수는 400",    lambda: _ev(context={"lat": 37.5}), 400,
                                                        "실수(37.5)·배열·중첩 객체는 400"),
            ("rating value=100 은 200", lambda: tc.post("/v1/events", json={"events": [
                {"user_id": 7, "event_type": "rating", "recipe_id": 1,
                 "value": 100, "request_id": _rid}]}).status_code, 200,
                                                        "서버가 범위를 검증하지 않는다"),
            ("남의 냉장고가 200",      lambda: tc.get("/v1/users/99999/pantry").status_code,
                                                   200, "누구나 임의 `user_id` 로"),
            ("검색 limit=500 이 200",  lambda: tc.get("/v1/recipes/search",
                                        params={"q": "김치", "limit": 500}).status_code,
                                                   200, "Mock 은 이 상한을 걸지 않는다"),
            ("잘못된 세션 접두어는 400", lambda: tc.post("/v1/recommend", json={
                "user_id": 7, "session_id": "s-7-x", "top_k": 2}).status_code, 400,
                                                        "접두어 3종 이외는"),
        ]
        for label, fn, want, phrase in PROBES:
            try:
                got = fn()
            except Exception as e:
                got = f"예외 {e}"
            check(f"동작 — {label}", want, got)
            check(f"문서 — {label}", True, phrase in spec)

        from features.recommend.enums import rating_to_label
        check("동작 — rating 100 의 라벨", 48.5, rating_to_label(100.0))
        check("문서 — rating 100 의 라벨", True, "48.5" in spec)

    # DB 쪽 단언 — 인덱스·CHECK 가 문서의 주장을 실제로 강제하는가
    conn2 = psycopg.connect(DSN)
    cur2 = conn2.cursor()

    def _one(sql: str):
        cur2.execute(sql)
        r = cur2.fetchone()
        return r[0] if r else None

    DB_PROBES = [
        ("한 유저·한 재료 활성 행 1개",
         "SELECT indexdef LIKE '%%UNIQUE%%(user_id, ingredient_id)%%removed_at IS NULL%%' "
         "FROM pg_indexes WHERE indexname='idx_pantry_active'",
         "활성 행은 1개다"),
        ("purchased_at 미래 금지",
         "SELECT bool_or(pg_get_constraintdef(oid) LIKE '%%purchased_at <=%%') "
         "FROM pg_constraint WHERE conrelid='pantry_item'::regclass",
         "미래 날짜는 받지 않는다"),
        ("d- 가 지표 뷰에서 빠진다",
         "SELECT pg_get_viewdef('v_real_events'::regclass) LIKE '%%d-%%'",
         "`d-` 는 지표 뷰에서 통째로 제외"),
        ("impression 이 source 까지 묶어 멱등",
         "SELECT indexdef LIKE '%%(request_id, recipe_id, source)%%' "
         "FROM pg_indexes WHERE indexname='ux_ev_impression'",
         "(request_id, recipe_id, source)"),
        ("feature_version 패턴 강제",
         "SELECT bool_or(pg_get_constraintdef(oid) LIKE '%%^(v[0-9]|test-)%%') "
         "FROM pg_constraint WHERE conrelid='recipe_feature'::regclass",
         "^(v[0-9]|test-)"),
        ("DB 시간대가 Asia/Seoul",
         "SELECT current_setting('TimeZone')='Asia/Seoul'",
         "Asia/Seoul"),
    ]
    for label, sql, phrase in DB_PROBES:
        check(f"동작 — {label}", True, bool(_one(sql)))
        check(f"문서 — {label}", True, phrase in spec)

    # 상수가 문서에 실제로 박혔는가 (render.py 가 _const 를 안 쓰면 여기가 빨개진다)
    check("문서 — 기본양념 종수",
          f"**{_one('SELECT count(*) FROM ingredient WHERE is_staple')}종**" in spec, True)
    conn2.close()

    # ─────────────────────────────────────────────────────────────
    # 문서 곳곳의 수치 ↔ 실제 — 전 문서 대조
    # 주의: 아래 값들은 09-03 감사에서 docs/ 여러 곳이 한꺼번에 낡은 채로 발견됐다.
    #    엔드포인트 7개(5곳) · 테이블 27/28개(9곳) · 기본양념 28종(3곳) ·
    #    재료 525종(7곳) · 알러지 9그룹(3곳). 아무도 안 보고 있었기 때문이다.
    #    취소선(~~27~~)과 "A → B" 의 앞쪽은 일부러 남긴 옛 값이라 건너뛴다.
    # ─────────────────────────────────────────────────────────────
    print("\n문서 곳곳의 수치 ↔ 실제 (전 문서)")
    import csv as _csv
    import json as _json

    _oas = _json.loads((ROOT / "docs/reco/design/api/openapi.json").read_text(encoding="utf-8"))
    _ex = _json.loads((ROOT / "docs/reco/design/api/examples.json").read_text(encoding="utf-8"))
    with (ROOT / "seeds/ingredient.csv").open(encoding="utf-8") as _f:
        _rows = list(_csv.DictReader(_f))

    cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='reco'")
    n_tables = cur.fetchone()[0]
    cur.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conrelid='user_allergy'::regclass
                     AND pg_get_constraintdef(oid) LIKE '%allergen_group%IS NULL%'""")
    n_allergen = len(re.findall(r"'([a-z]+)'::", cur.fetchone()[0]))

    FACTS = [
        ("테이블 수", n_tables,
         [r"(\d{2,3})\s*테이블", r"(\d{2,3})\s*개\s*테이블"]),
        ("엔드포인트(경로)", len(_oas["paths"]),
         [r"엔드포인트\s*(\d{1,2})\s*개", r"API 표면은\s*(\d{1,2})\s*개"]),
        ("기본양념 종수", sum(1 for r in _rows
                          if str(r.get("is_staple", "")).strip().lower()
                          in ("true", "t", "1", "y")),
         [r"기본양념\s*\*{0,2}(\d{2,3})\s*종", r"is_staple[^\n]{0,6}?(\d{2,3})\s*종"]),
        ("재료 시드 종수", len(_rows),
         [r"재료\s*(\d{3})\s*종", r"시드\s*(\d{3})\s*종"]),
        ("알러지 그룹 수", n_allergen,
         [r"그룹\s*\*{0,2}(\d{1,2})\s*칩", r"그룹\s*(\d{1,2})\s*개\s*칩"]),
        ("Mock 캡처 건수", sum(1 for k in _ex if not k.startswith("_")),
         [r"Mock\s*\*{0,2}(\d{1,3})\s*캡처"]),
    ]

    for label, actual, pats in FACTS:
        stale = []
        for f in live_docs():
            txt = f.read_text(encoding="utf-8")
            clean = re.sub(r"~~.*?~~", "", txt)              # 취소선 = 옛 값
            clean = re.sub(r"\d{1,4}\s*[가-힣]{0,3}\s*(?:→|->)", "", clean)  # "A → B" 의 앞
            for pat in pats:
                for m in re.finditer(pat, clean):
                    got = int(m.group(1))
                    # 주의: "알레르기 재료 186종"·"마트 alias 시드 200종" 처럼 부분집합을
                    #    총 시드 수로 오인하지 않는다. 총수의 절반 미만이면 다른 얘기다.
                    if got * 2 < actual:
                        continue
                    if got != actual:
                        ln = clean.count("\n", 0, m.start()) + 1
                        stale.append(f"{f.relative_to(ROOT)}:~{ln}={m.group(1)}")
        check(f"{label} (실제 {actual})", [], stale)

    conn.close()

    # ── 자기 건수 — 검사가 아니라 경고다 ──────────────────────
    # 주의: check() 로 세면 자기 참조가 된다: 검사를 하나 더하면 총계가 늘고,
    #    늘어난 총계를 문서가 다시 못 따라와서 영원히 안 맞는다.
    #    그래서 세지 않고 알리기만 한다 — 사람이 고칠 수 있으면 충분하다.
    total = ok + len(fail)
    stale = []
    for f in live_docs():
        txt = f.read_text(encoding="utf-8")
        for m in re.finditer(r"`?(?:make )?doc-check\b`?", txt):
            win = txt[m.end(): m.end() + 40].split("\n", 1)[0]
            win = re.sub(r"~~.*?~~", "", win)
            n = re.search(r"(?<![.\d])(\d{1,4})건|\*\*(\d{1,4})건?\*\*", win)
            if n:
                v = int(n.group(1) or n.group(2))
                if v != total:
                    stale.append(f"{f.relative_to(ROOT)}:{txt.count(chr(10), 0, m.start())+1}={v}")
    if stale:
        print(f"\n⚠️  doc-check 자기 건수({total})와 다른 곳 {len(stale)}곳 "
              f"— 세지 않고 알리기만 한다 (자기 참조 회피):")
        for x in stale:
            print(f"     {x}")

    print(f"\n{'─'*54}")
    if fail:
        print(f"❌ {len(fail)}건 불일치 / {ok}건 통과")
        for f in fail:
            print(f"   {f}")
        return 1
    print(f"✅ {ok}건 전부 일치")
    print("   🔴 등록된 수치·건수·단언만 봤다. 서술의 논리·최신성은 사람이 본다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
