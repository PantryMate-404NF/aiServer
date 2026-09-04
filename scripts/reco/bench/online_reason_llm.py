"""온라인 LLM 사유 생성 타당성 — 지연·처리량·환각 (설계 5-5 / 5-6-1 재검토).

현재는 템플릿 15종이 z-salience 상위 2개를 이어 붙인다. 템플릿은 피처 값으로
슬롯을 채우므로 **구조적으로 거짓말을 못 한다.** LLM 으로 바꾸면 그 보장이 사라지고
지연이 응답 경로에 들어온다. 셋 다 재야 판단이 된다:

  A. 단건 지연 · TTFT(첫 토큰)  — 스트리밍 가능성
  B. 10건 배치 vs 10회 개별     — 한 응답에 10개 사유가 필요하다
  C. 환각률                     — 주지 않은 사실을 지어내는가
"""
from __future__ import annotations

import json
import statistics
import time
import urllib.request

MODEL = "gemma4:12b-it-qat"
URL = "http://localhost:11434/api/generate"

# 환각을 막는 최소 장치. 템플릿이 공짜로 갖던 보장을 프롬프트로 흉내낸다.
SYSTEM = """주어진 사실만으로 추천 이유를 한국어 한 문장으로 쓴다.

규칙:
- 주어진 사실 외에 어떤 정보도 추가하지 않는다. 영양·건강·계절·맛 평가를 지어내지 않는다.
- 40자 이내. 존댓말. "~예요/~어요" 로 끝낸다.
- 사실을 그대로 나열하지 말고 자연스러운 한 문장으로 잇는다.

설명 없이 JSON만 출력한다."""

SCHEMA = {
    "type": "object",
    "properties": {"reason": {"type": "string"}},
    "required": ["reason"],
}

# 실제 파이프라인이 넘길 구조 — z-salience 상위 2개 피처 + 슬롯값
CASES = [
    {"title": "김치찌개", "facts": ["보유 재료로 바로 조리 가능", "김치 소비기한 D-3"]},
    {"title": "애호박볶음", "facts": ["애호박 소비기한 D-2", "조리 10분"]},
    {"title": "된장찌개", "facts": ["보유 재료로 바로 조리 가능", "두부 소비기한 D-1"]},
    {"title": "제육볶음", "facts": ["선호 맛: 매움", "돼지고기 보유"]},
    {"title": "계란말이", "facts": ["조리 8분", "부족한 재료: 없음"]},
    {"title": "미역국", "facts": ["보유 재료로 바로 조리 가능", "많이 만드는 레시피"]},
    {"title": "오이무침", "facts": ["오이 소비기한 D-2", "조리 5분"]},
    {"title": "잡채", "facts": ["부족한 재료: 당면 1개", "선호 맛: 단맛"]},
    {"title": "콩나물국", "facts": ["콩나물 소비기한 D-1", "조리 12분"]},
    {"title": "감자조림", "facts": ["보유 재료로 바로 조리 가능", "감자 소비기한 D-5"]},
]

# 환각 탐지: 주지 않은 개념이 나오면 지어낸 것이다
BANNED = ["건강", "영양", "다이어트", "단백질", "비타민", "칼로리", "면역",
          "제철", "겨울", "여름", "봄", "가을", "인기", "맛있", "든든", "따뜻"]


def call(prompt: str, stream: bool = False) -> dict:
    body = json.dumps({
        "model": MODEL, "system": SYSTEM, "prompt": prompt,
        "format": SCHEMA, "stream": stream,
        "options": {"temperature": 0.3, "num_predict": 128},
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def mk(c: dict) -> str:
    return f"메뉴: {c['title']}\n사실:\n" + "\n".join(f"- {f}" for f in c["facts"])


def main() -> None:
    print("=" * 62)
    print("A. 단건 지연 (사유 1개)")
    print("=" * 62)
    lat, halluc, outs = [], 0, []
    for c in CASES:
        t0 = time.perf_counter()
        d = call(mk(c))
        w = time.perf_counter() - t0
        lat.append(w)
        try:
            r = json.loads(d["response"])["reason"]
        except Exception:
            r = "<파싱실패>"
        bad = [b for b in BANNED if b in r]
        halluc += bool(bad)
        outs.append((c["title"], r, bad))
        flag = f"  ⚠️{bad}" if bad else ""
        print(f"  {w:5.2f}s  {c['title']:<7} {r}{flag}")

    lat_s = sorted(lat)
    print(f"\n  p50 {statistics.median(lat):.2f}s   p95 {lat_s[int(len(lat_s)*0.95)-1]:.2f}s"
          f"   최대 {max(lat):.2f}s")
    print(f"  환각 의심 {halluc}/{len(CASES)}")

    print("\n" + "=" * 62)
    print("B. 10건을 한 번에 (배치)")
    print("=" * 62)
    batch_schema = {
        "type": "object",
        "properties": {"reasons": {"type": "array", "items": {"type": "string"}}},
        "required": ["reasons"],
    }
    p = "다음 각 메뉴의 추천 이유를 순서대로 쓴다.\n\n" + "\n\n".join(
        f"{i+1}. {c['title']}\n" + "\n".join(f"   - {f}" for f in c["facts"])
        for i, c in enumerate(CASES))
    body = json.dumps({
        "model": MODEL, "system": SYSTEM, "prompt": p, "format": batch_schema,
        "stream": False, "options": {"temperature": 0.3, "num_predict": 512},
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    wb = time.perf_counter() - t0
    try:
        rs = json.loads(d["response"])["reasons"]
    except Exception:
        rs = []
    print(f"  배치 1회: {wb:.2f}s   ({len(rs)}건 반환)")
    for t, r in zip([c["title"] for c in CASES], rs):
        print(f"    {t:<7} {r}")
    print(f"\n  개별 10회 합계 {sum(lat):.2f}s  vs  배치 1회 {wb:.2f}s"
          f"   → {sum(lat)/wb:.1f}배 빠름")

    print("\n" + "=" * 62)
    print("C. 스트리밍 TTFT (첫 토큰까지)")
    print("=" * 62)
    body = json.dumps({
        "model": MODEL, "system": SYSTEM, "prompt": mk(CASES[0]),
        "stream": True, "options": {"temperature": 0.3, "num_predict": 128},
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = None
    with urllib.request.urlopen(req, timeout=300) as r:
        for line in r:
            if not line.strip():
                continue
            if ttft is None:
                ttft = time.perf_counter() - t0
            if json.loads(line).get("done"):
                break
    total = time.perf_counter() - t0
    print(f"  TTFT {ttft:.2f}s   완료 {total:.2f}s")

    json.dump({"latencies": lat, "batch_s": wb, "ttft": ttft,
               "outputs": [{"title": t, "reason": r, "banned": b} for t, r, b in outs]},
              open("bench/out/online_reason_llm.json", "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
