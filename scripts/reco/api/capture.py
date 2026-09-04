#!/usr/bin/env python3
"""Mock 서버를 실제로 호출해 요청/응답 예시를 캡처한다.

    .venv/bin/python scripts/reco/api/capture.py

손으로 쓴 예시는 코드와 어긋나지만, 이렇게 만든 것은 어긋날 수 없다.
계약(src/features/recommend/schema.py · stage.py)이 바뀌면 이 스크립트를 다시 돌리고 render.py 로 문서를 재생성한다.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from main import create_app  # noqa: E402

app = create_app()
from features.recommend.enums import (  # noqa: E402
    ACTIVE_WEIGHT_TODAY, DEFAULT_WEIGHTS, FEATURE_KEYS, PENDING_DATA_FEATURES,
    PROPENSITY_SEMANTICS, REQUIRED_TRACE_PARAMS, SESSION_PREFIXES,
    UNAVAILABLE_FEATURES,
)

OUT = ROOT / "docs" / "reco" / "design" / "api"
#: 🔴 산출물은 저장소 루트 기준으로 쓴다. 예전에는 이 스크립트가 docs/api/ 에
#:    있어서 자기 폴더가 곧 출력지였다. 지금은 scripts/reco/api/ 라
#:    그대로 두면 스크립트 옆에 json 을 흘린다 (09-04 실제로 그랬다).


def main() -> None:
    c = TestClient(app)
    cap: dict[str, dict] = {}
    wrong: list[str] = []

    def grab(key: str, method: str, path: str, *, expect: int = 200, **kw):
        """실호출 캡처. 🔴 `expect` 와 다르면 실패한다.

        이 가드가 없어서 09-03 까지 `/v1/events` 의 **정상 예시가 실제로는 422**
        였고, 문서는 그 에러 본문을 "## 응답" 으로 렌더했다. 프론트가 그대로
        따라 하면 즉시 막히는데, 문서만 봐서는 알 수 없었다.
        의도한 에러 예시는 `expect=422` 처럼 명시한다.
        """
        r = getattr(c, method)(path, **kw)
        if r.status_code != expect:
            # 🔴 그 자리에서 멈춘다. 계속 가면 뒤 호출이 KeyError 로 죽으면서
            #    **진짜 원인이 묻힌다** — 무엇이 왜 틀렸는지 여기서 말해야 한다.
            wrong.append(key)
            print(f"\n🔴 {key}: {method.upper()} {path} → {r.status_code} "
                  f"(기대 {expect})\n   {r.text[:400]}\n\n"
                  f"   이대로 두면 문서에 잘못된 예시가 실린다. "
                  f"의도한 에러 예시라면 expect={r.status_code} 을 명시하세요.",
                  file=sys.stderr)
            sys.exit(1)
        cap[key] = {
            "method": method.upper(), "path": path,
            "request": kw.get("json") or kw.get("params"),
            "status": r.status_code, "expect": expect, "response": r.json(),
        }
        return r.json()

    # 🔴 session_id 를 예시에 **반드시** 넣는다. impression 은 이 요청에서 서버가
    #    자동 기록하므로, 프론트가 안 보내면 이벤트의 95% 에 세션이 빈 채로 쌓인다.
    #    예시에 없으면 아무도 안 보낸다 — 그리고 그건 소급해서 못 채운다.
    # 🔴 top_k 를 8 로 둔다. 3 이면 items 가 전부 한 종류라 문서에
    #    **탐색 슬롯의 실제 모양이 안 나온다** — propensity 가 1.0 으로만 보여
    #    "IPS 분모" 라는 설명과 예시가 어긋난다.
    resp = grab("recommend", "post", "/v1/recommend",
                json={"user_id": 7, "session_id": "c-7-a1b2c3d4e5f6",
                      "top_k": 8, "max_missing": 2})
    rid = resp["request_id"]

    # 디버거 경로라 d- 세션을 쓴다 (D-15). 접두어 실물이 문서에 남는다.
    grab("recommend_ablation", "post", "/v1/recommend",
         json={"user_id": 7, "session_id": "d-7-debug00000001",
               "top_k": 2, "weight_override": {"f_expiring": 0.0}})
    grab("recommend_degraded", "post", "/v1/recommend",
         json={"user_id": 7, "top_k": 20, "max_missing": 0})
    grab("recommend_interleave", "post", "/v1/recommend",
         json={"user_id": 7, "top_k": 6, "interleave_with": "ranker-lgbm-v1"})
    grab("events", "post", "/v1/events",
         json={"events": [{"user_id": 7, "event_type": "click", "recipe_id": 10001,
                           "request_id": rid, "position": 1, "session_id": "c-7-a1b2c3d4e5f6",
                           "context": {"hour": 19}}]})
    grab("events_rating", "post", "/v1/events",
         json={"events": [{"user_id": 7, "event_type": "rating", "recipe_id": 10001,
                           "value": 5, "request_id": rid, "position": 2,
                           "session_id": "c-7-a1b2c3d4e5f6"}]})
    grab("events_reject", "post", "/v1/events",
         json={"events": [{"user_id": 7, "event_type": "cook", "recipe_id": 10001}]})
    # 🔴 프론트가 가장 흔히 맞을 422 — 세션 접두어를 안 지킨 경우.
    #    c- 실사용자 · g- 게스트 · d- 개발/디버거 이외는 입력에서 거부된다.
    grab("events_bad_session", "post", "/v1/events", expect=422,
         json={"events": [{"user_id": 7, "event_type": "click", "recipe_id": 10001,
                           "request_id": rid, "position": 1,
                           "session_id": "s-7-a1b2"}]})
    grab("recipe_search", "get", "/v1/recipes/search",
         params={"q": "김치", "limit": 5, "user_id": 7})
    # 🔴 '라따뚜이' 는 mock 제목과 글자가 겹쳐 **결과가 나왔다** — 결과 없음 예시가
    #    결과 있음이었다. 한글 제목과 문자 교집합이 0 인 값을 쓴다.
    miss = grab("recipe_search_miss", "get", "/v1/recipes/search",
                params={"q": "ratatouille"})
    assert not miss["hits"], "결과 없음 예시에 hits 가 있다"
    grab("search", "get", "/v1/ingredients/search", params={"q": "대파", "limit": 5})
    smiss = grab("search_miss", "get", "/v1/ingredients/search", params={"q": "zzzz"})
    assert not smiss["hits"], "결과 없음 예시에 hits 가 있다"
    grab("pantry_get", "get", "/v1/users/7/pantry")
    # 🔴 purchased_at 을 담는 예시 — 소비기한은 구매일 기준으로 추정한다 (09-03).
    #    유저가 expires_at 을 직접 주면 그것이 추정을 이긴다.
    grab("pantry_put", "put", "/v1/users/7/pantry",
         json={"items": [{"ingredient_id": 1042, "quantity": 1, "unit": "대",
                          "purchased_at": "2026-09-01"},
                         {"ingredient_id": 1300, "quantity": 1, "unit": "모",
                          "expires_at": "2026-09-06"}],
               "removed": [{"ingredient_id": 1101, "reason": "consumed"}]})
    # 온보딩 (09-03 신설). 이 계약이 없어서 가중치 0.27 을 저장할 곳이 없었다.
    grab("onboarding", "post", "/v1/onboarding/7",
         json={"picks": [3, 7, 12], "scales": [2, 3, 1],
               "allergy_groups": ["nut", "shellfish"], "allergy_ingredient_ids": [170],
               "avoid_ingredient_ids": [55], "household_size": 2})
    grab("onboarding_reject", "post", "/v1/onboarding/7", expect=422,
         json={"picks": [1], "scales": [9, 0, 0]})
    grab("log", "get", f"/v1/recommendations/{rid}")
    # 에러 규약 표가 404 를 말하는데 예시가 없었다.
    grab("log_404", "get", "/v1/recommendations/00000000-0000-4000-8000-000000000000",
         expect=404)
    grab("error_422", "post", "/v1/recommend", expect=422,
         json={"user_id": 7, "topk": 20})
    grab("health", "get", "/health")

    # ─────────────────────────────────────────────────────────────
    # 계약 상수 — render.py 가 **손으로 못 적게** 여기서 실어 보낸다.
    # 🔴 render.py 는 `.venv/bin/python scripts/reco/api/render.py` 로 도는데
    #    그때 sys.path[0] 이 scripts/reco/api 라 `import main` 이 안 된다 (Makefile).
    #    그래서 SoT 를 읽을 수 있는 쪽(여기)이 읽어서 넘긴다.
    #    문서에 숫자를 손으로 적으면 다음 변경에서 조용히 낡는다.
    # ─────────────────────────────────────────────────────────────
    oas = app.openapi()
    schema_sql = (ROOT / "deploy/init/02_schema.sql").read_text(encoding="utf-8")

    def _check_values(table: str, col: str) -> list[str]:
        """`table` 의 CHECK (col IN ('a','b',...)) 에서 값 목록을 뽑는다.

        🔴 테이블을 반드시 지정한다. 같은 컬럼명이 여러 테이블에 있어서
           (`source` 는 user_ingredient_pref 에도 event_log 에도 있다)
           전역 검색은 **첫 매치를 집어 엉뚱한 값 목록을 문서에 싣는다.**
        """
        blk = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", schema_sql, re.S)
        if not blk:
            raise SystemExit(f"🔴 02_schema.sql 에서 {table} 을 못 찾았다")
        m = re.search(rf"\b{col}\s+IN\s*\(([^)]*)\)", blk.group(1))
        if not m:
            raise SystemExit(f"🔴 {table}.{col} 의 CHECK 를 못 찾았다")
        return re.findall(r"'([^']+)'", m.group(1))

    axes: list[str] = []
    ay = (ROOT / "seeds/onboarding_recipes.yaml")
    if ay.exists():
        m = re.search(r"^axes:\s*\[([^\]]*)\]", ay.read_text(encoding="utf-8"), re.M)
        if m:
            axes = [a.strip() for a in m.group(1).split(",")]

    n_staple = 0
    ic = ROOT / "seeds/ingredient.csv"
    if ic.exists():
        with ic.open(encoding="utf-8") as f:
            n_staple = sum(1 for r in csv.DictReader(f)
                           if str(r.get("is_staple", "")).strip().lower()
                           in ("true", "t", "1", "y"))

    METHODS = ("get", "post", "put", "patch", "delete")
    cap["_const"] = {
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_paths": len(oas["paths"]),
        "n_ops": sum(1 for v in oas["paths"].values() for m in v if m in METHODS),
        "session_prefixes": list(SESSION_PREFIXES),
        "feature_keys": list(FEATURE_KEYS),
        "default_weights": {k: DEFAULT_WEIGHTS.get(k, 0.0) for k in FEATURE_KEYS},
        "unavailable_features": sorted(UNAVAILABLE_FEATURES),
        "pending_data_features": sorted(PENDING_DATA_FEATURES),
        "active_weight_today": ACTIVE_WEIGHT_TODAY,
        "required_trace_params": list(REQUIRED_TRACE_PARAMS),
        "propensity_semantics": PROPENSITY_SEMANTICS,
        "taste_axes": axes,
        "allergen_groups": _check_values("user_allergy", "allergen_group"),
        "event_sources": _check_values("event_log", "source"),
        "removal_reasons": _check_values("pantry_item", "removed_reason"),
        "expires_at_sources": _check_values("pantry_item", "expires_at_source"),
        "n_staple_seed": n_staple,
        "event_in": oas["components"]["schemas"]["EventIn"]["properties"],
    }

    (OUT / "examples.json").write_text(
        json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")

    n_schema = len(oas["components"]["schemas"])
    n_cap = sum(1 for k in cap if not k.startswith("_"))
    print(f"✓ 캡처 {n_cap}건        → docs/reco/design/api/examples.json")
    print(f"✓ OpenAPI 스키마 {n_schema}종 → docs/reco/design/api/openapi.json")


if __name__ == "__main__":
    main()
