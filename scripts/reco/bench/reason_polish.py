"""대안 (f) 검증: 템플릿이 사실을 확정하고 LLM 은 문장만 다듬는다.

온라인 LLM 사유의 최대 위험은 환각인데, 템플릿은 피처값으로 슬롯을 채우므로
구조적으로 거짓말을 못 한다. 그 보장을 유지한 채 문장만 자연스럽게 만들 수 있다면
위험 없이 이득만 얻는다. 단, 두 가지가 성립해야 한다:

  1. LLM 이 슬롯값(재료명·일수·분)을 **하나도 잃거나 바꾸지 않는다** — 기계 검증 가능
  2. 지연이 감당 가능하다

둘 다 잰다. 1번이 100% 가 아니면 이 안은 죽는다 (검증 실패분은 템플릿으로 폴백).
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from features.recommend.engine.reason import build_reason  # noqa: E402

MODEL = "gemma4:12b-it-qat"
SYSTEM = """주어진 한국어 문장을 더 자연스럽게 다듬는다.

절대 규칙:
- 재료명·숫자·단위를 **하나도 바꾸거나 빼지 않는다.** 그대로 유지한다.
- 새로운 사실을 추가하지 않는다. 인과관계("~므로", "~때문에")를 만들지 않는다.
- 40자 이내. 존댓말. "~요" 로 끝낸다.

설명 없이 JSON만 출력한다."""

SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

# (피처키 목록, ctx) — 실제 파이프라인이 넘기는 형태
CASES = [
    (["f_expiring", "f_coverage"], {"expiring_name": "김치", "expiring_days": 3}),
    (["f_expiring", "f_time_fit"], {"expiring_name": "애호박", "expiring_days": 2,
                                    "cook_minutes": 10}),
    (["f_missing", "f_taste"], {"missing_name": "당면", "taste_axis": "단맛"}),
    (["f_coverage", "f_popularity"], {}),
    (["f_ing_pref", "f_time_fit"], {"pref_ing": "돼지고기", "cook_minutes": 15}),
    (["f_expiring", "f_pantry_use"], {"expiring_name": "두부", "expiring_days": 1,
                                      "pantry_used": 4}),
    (["f_cooccur", "f_coverage"], {"similar_title": "된장찌개"}),
    (["f_taste", "f_cuisine"], {"taste_axis": "매운맛", "cuisine": "한식"}),
    (["f_missing", "f_time_fit"], {"missing_name": "두부", "cook_minutes": 20}),
    (["f_expiring", "f_ing_pref"], {"expiring_name": "콩나물", "expiring_days": 1,
                                    "pref_ing": "돼지고기"}),
]


def polish(text: str) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL, "system": SYSTEM, "prompt": text, "format": SCHEMA,
        "stream": False, "options": {"temperature": 0.3, "num_predict": 128},
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    w = time.perf_counter() - t0
    try:
        return json.loads(d["response"])["text"], w
    except Exception:
        return "", w


def verify(ctx: dict, out: str) -> list[str]:
    """슬롯값이 출력에 살아있는지 기계 검증. 이게 이 안의 안전장치다."""
    lost = []
    for k, v in ctx.items():
        s = str(v)
        if s not in out:
            lost.append(f"{k}={s}")
    # 원문에 없던 숫자가 생겼는가 (날조)
    orig_nums = {str(v) for v in ctx.values() if str(v).isdigit()}
    for n in re.findall(r"\d+", out):
        if n not in orig_nums:
            lost.append(f"없던숫자:{n}")
    return lost


def main() -> None:
    lats, fails = [], 0
    print(f"{'템플릿 원문':<44} → 다듬은 문장")
    print("─" * 100)
    for keys, ctx in CASES:
        src, used = build_reason(keys, ctx)
        out, w = polish(src)
        lats.append(w)
        lost = verify(ctx, out)
        fails += bool(lost) or not out
        mark = "🔴" if lost else "  "
        print(f"{mark} {src:<42} → {out}")
        if lost:
            print(f"     검증실패: {lost}")

    print("─" * 100)
    ls = sorted(lats)
    print(f"사실 보존 {len(CASES)-fails}/{len(CASES)}   "
          f"p50 {statistics.median(lats):.2f}s   p95 {ls[max(0,int(len(ls)*.95)-1)]:.2f}s")
    print(f"\n판정: {'✅ 기계 검증으로 안전 확보 가능' if fails == 0 else f'⚠️ {fails}건 폴백 필요'}")


if __name__ == "__main__":
    main()
