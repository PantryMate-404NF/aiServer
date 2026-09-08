"""트랙1(사전계산) 배치 용량 — 하루 1회 사유 생성이 몇 명까지 되는가.

사용자 제안: 레시피 추천은 사전 계산하고 사유를 붙인다(1일 1회).
그러면 8.2초 지연이 응답 경로 밖으로 나가므로 무관해진다. 남는 질문은 하나다:
**새벽 배치 창(예: 4시간) 안에 유저 몇 명을 처리하는가.**

지속 부하에서 재는 것이 중요하다 — 단발 측정은 발열 스로틀링을 못 잡는다.
"""
from __future__ import annotations

import json
import statistics
import time
import urllib.request

MODEL = "gemma4:12b-it-qat"
SYS = ("각 메뉴의 추천 이유를 순서대로 쓴다. 주어진 사실만 쓰고 새 사실을 추가하지 않는다. "
       "각 40자 이내, 존댓말. 설명 없이 JSON만 출력한다.")
SCHEMA = {"type": "object",
          "properties": {"reasons": {"type": "array", "items": {"type": "string"},
                                     "minItems": 10, "maxItems": 10}},
          "required": ["reasons"]}

MENUS = ["김치찌개", "애호박볶음", "된장찌개", "제육볶음", "계란말이",
         "미역국", "오이무침", "잡채", "콩나물국", "감자조림"]
FACTS = [["보유 재료로 바로 조리 가능", "김치 소비기한 D-3"], ["애호박 소비기한 D-2", "조리 10분"],
         ["보유 재료로 바로 조리 가능", "두부 소비기한 D-1"], ["선호 맛: 매움", "돼지고기 보유"],
         ["조리 8분", "부족한 재료: 없음"], ["보유 재료로 바로 조리 가능", "많이 만드는 레시피"],
         ["오이 소비기한 D-2", "조리 5분"], ["부족한 재료: 당면 1개", "선호 맛: 단맛"],
         ["콩나물 소비기한 D-1", "조리 12분"], ["보유 재료로 바로 조리 가능", "감자 소비기한 D-5"]]

N_USERS = 10   # 이만큼만 실측하고 선형 외삽한다


def one_user(seed: int) -> tuple[float, int]:
    """유저 1명분 = Top-10 사유를 배치 1회로."""
    p = "각 메뉴의 추천 이유:\n\n" + "\n\n".join(
        f"{i+1}. {MENUS[(i+seed) % 10]}\n" + "\n".join(f"   - {x}" for x in FACTS[(i+seed) % 10])
        for i in range(10))
    body = json.dumps({"model": MODEL, "system": SYS, "prompt": p, "format": SCHEMA,
                       "stream": False, "options": {"temperature": 0.4, "num_predict": 640}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read())
    w = time.perf_counter() - t0
    try:
        n = len(json.loads(d["response"])["reasons"])
    except Exception:
        n = 0
    return w, n


times, okc = [], 0
t_all = time.perf_counter()
for u in range(N_USERS):
    w, n = one_user(u)
    times.append(w)
    okc += (n == 10)
    print(f"  유저 {u+1:2d}  {w:5.2f}s  사유 {n}개")
wall = time.perf_counter() - t_all

per = statistics.median(times)
print(f"\n실측 {N_USERS}명 총 {wall:.1f}s   유저당 중앙값 {per:.2f}s   완전생성 {okc}/{N_USERS}")

# 스로틀링 점검: 전반부 vs 후반부
h = N_USERS // 2
f, b = statistics.mean(times[:h]), statistics.mean(times[h:])
print(f"전반 {f:.2f}s / 후반 {b:.2f}s → 스로틀링 {'없음' if b < f * 1.15 else f'의심 (+{(b/f-1)*100:.0f}%)'}")

print(f"\n{'유저 수':>8}{'소요':>12}{'4시간 창':>10}")
for U in (100, 500, 1_000, 5_000, 10_000):
    s = U * per
    fit = "✅" if s <= 4 * 3600 else "❌"
    hh = f"{s/3600:.1f}h" if s >= 3600 else f"{s/60:.0f}분"
    print(f"{U:>8,}{hh:>12}{fit:>8}")
print(f"\n4시간 배치 창 기준 상한: 약 {int(4*3600/per):,}명")
