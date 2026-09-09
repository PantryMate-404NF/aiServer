"""대안 (f) 의 급소: 슬롯 없는 템플릿은 기계 검증이 통하지 않는다.

reason_polish.py 에서 "많이 만드는 레시피예요" → "양이 많은 레시피예요" 가 나왔다.
인기도(f_popularity)가 분량으로 뒤바뀌었는데 슬롯값 검증은 통과한다.

슬롯 없는 템플릿 6종을 반복 시행해 **문구가 바뀌는 비율**을 잰다.
슬롯이 없으면 검증할 것이 없으므로, 바뀌면 그 자체가 위험이다.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter

sys.path.insert(0, ".")
from features.recommend.engine.reason import REASON_TEMPLATES  # noqa: E402

MODEL = "gemma4:12b-it-qat"
SYSTEM = ("주어진 한국어 문장을 더 자연스럽게 다듬는다. "
          "재료명·숫자·단위를 바꾸거나 빼지 않는다. 새 사실을 추가하지 않는다. "
          "40자 이내, 존댓말, '~요' 로 끝낸다. 설명 없이 JSON만 출력한다.")
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

# 슬롯이 없는 템플릿 = 기계 검증 불가 구간
SLOTLESS = ["f_coverage", "f_popularity", "f_season", "f_quality", "f_skill_fit", "f_content"]
N = 5


def polish(text: str) -> str:
    body = json.dumps({"model": MODEL, "system": SYSTEM, "prompt": text, "format": SCHEMA,
                       "stream": False, "options": {"temperature": 0.3, "num_predict": 128}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    try:
        return json.loads(d["response"])["text"]
    except Exception:
        return ""


total = changed = 0
print(f"슬롯 없는 템플릿 {len(SLOTLESS)}종 × {N}회\n")
for k in SLOTLESS:
    src = REASON_TEMPLATES[k][0]
    outs = [polish(src) for _ in range(N)]
    c = Counter(outs)
    same = sum(v for o, v in c.items() if o.rstrip(".") == src.rstrip("."))
    total += N
    changed += N - same
    print(f"  {k}")
    print(f"    원문: {src}")
    for o, v in c.most_common():
        mark = "  " if o.rstrip(".") == src.rstrip(".") else "🔴"
        print(f"    {mark} ×{v}  {o}")

print(f"\n{'='*60}")
print(f"문구 변경 {changed}/{total} = {changed/total:.0%}")
print("슬롯이 없으므로 이 변경들은 **기계 검증으로 잡을 수 없다.**")
