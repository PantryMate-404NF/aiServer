"""시뮬 유저 시나리오 — 추천 결과가 냉장고·행동에 따라 바뀌는지 API 로 확인합니다.

실행: uv run python scripts/sim/scenario_run.py [--base http://127.0.0.1:8000]
                                             [--user 1000184] [--cold 1000001] [--api-key ...]

순서
  1. GET  /health
  2. POST /v1/recommend  (warm 유저)                       → 기준 목록
  3. PUT  /v1/users/{id}/pantry  (임박 재료 3종 추가)       → f_expiring 자극
  4. POST /v1/events     (cook 5건, 세션 'd-')             → 행동 신호 추가
  5. POST /v1/recommend  (같은 유저)                       → 변경 목록, 상위 10 비교
  6. POST /v1/recommend  (cold 유저)                       → 대조군

모든 라우터가 내부 API 키를 요구합니다 (deps.verify_internal_api_key). `--api-key` 를 주지
않으면 서버와 같은 설정(`config.get_settings`)에서 읽으므로 서버를 띄운 환경에서 실행합니다.

종료 코드로 판정합니다. 0 = 계약대로 응답했고 위 단계가 전부 통과.
🔴 M-01 이 대기 상태면 라우터가 mock 을 부르므로 2 와 5 의 목록이 같을 수 있습니다.
   그것은 이 스크립트의 실패가 아니라 전환 전 상태이고, 출력에 그렇게 적습니다.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

EXPIRING_INGREDIENT_NAMES = ["두부", "콩나물", "달걀"]


@dataclass(frozen=True)
class Api:
    base: str
    api_key: str

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "X-Internal-Api-Key": self.api_key}
        req = urllib.request.Request(  # noqa: S310  # 로컬 개발 서버 http 호출 전용
            self.base + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                out: dict[str, Any] = json.loads(resp.read().decode())
                return out
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} → HTTP {e.code}: {e.read().decode()[:300]}") from e


def api_key_of(given: str | None) -> str:
    """인자가 없으면 서버와 같은 설정에서 읽습니다. 환경변수를 직접 보지 않습니다 (03 의 2절)."""
    if given:
        return given
    from config import get_settings

    return get_settings().internal_api_key


def ingredient_ids(api: Api, names: list[str]) -> list[int]:
    ids = []
    for n in names:
        query = f"/v1/ingredients/search?q={urllib.parse.quote(n)}&limit=1"
        hits = api.call("GET", query).get("hits", [])
        if hits:
            ids.append(int(hits[0]["ingredient_id"]))
    return ids


def top(items: list[dict[str, Any]], k: int = 10) -> list[tuple[int, str]]:
    return [(int(i["recipe_id"]), str(i.get("title", ""))) for i in items[:k]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", type=int, default=1000184, help="warm 유저 id (99_verify 상위 10 중)")
    ap.add_argument("--cold", type=int, default=1000001, help="cold 대조 유저 id")
    ap.add_argument("--api-key", default=None, help="내부 API 키. 없으면 서버 설정에서 읽음")
    ap.add_argument(
        "--skip-health",
        action="store_true",
        help="DB 없는 PC 에서 목업 라우터만 볼 때. /health 는 DB 접속을 기다리다 멈춥니다",
    )
    a = ap.parse_args()
    api = Api(base=a.base, api_key=api_key_of(a.api_key))
    ok = True

    if a.skip_health:
        print("[1] health: 건너뜀 (--skip-health)")
    else:
        health = api.call("GET", "/health")
        print(f"[1] health: {health}")

    sid = f"d-{a.user}-{datetime.now(tz=UTC):%Y%m%d%H%M}"
    req = {"user_id": a.user, "session_id": sid, "top_k": 20, "include_trace": True}
    before = api.call("POST", "/v1/recommend", req)
    print(f"[2] recommend(warm={a.user}) model={before['model_version']} n={len(before['items'])}")
    for rank, (rid, title) in enumerate(top(before["items"]), 1):
        print(f"     {rank:2d}. {rid} {title}")

    ids = ingredient_ids(api, EXPIRING_INGREDIENT_NAMES)
    if ids:
        soon = (datetime.now(tz=UTC) + timedelta(days=2)).date().isoformat()
        current = api.call("GET", f"/v1/users/{a.user}/pantry").get("items", [])
        items = [{"ingredient_id": int(i["ingredient_id"])} for i in current]
        have = {x["ingredient_id"] for x in items}
        items += [{"ingredient_id": i, "expires_at": soon} for i in ids if i not in have]
        pantry = api.call("PUT", f"/v1/users/{a.user}/pantry", {"items": items})
        print(
            f"[3] pantry: {len(current)} → {len(pantry.get('items', []))} (임박 {len(ids)}종 추가)"
        )
    else:
        print("[3] pantry: 재료 검색이 비어 건너뜀 (ingredient 시드 확인)")

    cooked = [int(i["recipe_id"]) for i in before["items"][:5]]
    events = [
        {
            "user_id": a.user,
            "event_type": "cook",
            "recipe_id": rid,
            "request_id": before["request_id"],
            "position": pos,
            "session_id": sid,
        }
        for pos, rid in enumerate(cooked, 1)
    ]
    ack = api.call("POST", "/v1/events", {"events": events})
    print(f"[4] events: accepted={ack['accepted']} rejected={ack['rejected']}")
    ok &= ack["rejected"] == 0

    after = api.call("POST", "/v1/recommend", req)
    b, c = top(before["items"]), top(after["items"])
    moved = sum(1 for x, y in zip(b, c, strict=False) if x != y)
    print(f"[5] recommend(warm) 다시: 상위 10 중 {moved} 자리 변동")
    if moved == 0:
        print("     변동 없음 — M-01(라우터 mock)·M-03(이력 미적재) 전환 전이면 정상입니다")

    cold_req = {"user_id": a.cold, "session_id": f"d-{a.cold}-x", "top_k": 20}
    cold = api.call("POST", "/v1/recommend", cold_req)
    same = len({r for r, _ in top(cold["items"])} & {r for r, _ in c})
    print(f"[6] recommend(cold={a.cold}): n={len(cold['items'])}, warm 상위 10 과 겹침 {same}")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
