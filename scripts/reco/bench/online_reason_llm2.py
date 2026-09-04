"""온라인 사유 생성 2차 — 배치 실패 원인 · 진짜 TTFT · 동시성 한계.

1차에서 배치가 0건을 반환했고 TTFT 가 총시간과 같았다(버퍼링 의심).
동시성은 아예 안 쟀는데, 그것이 GPU 1장당 QPS 를 정하고 곧 인프라 비용이 된다.
"""
from __future__ import annotations

import http.client
import json
import statistics
import threading
import time
import urllib.request

MODEL = "gemma4:12b-it-qat"
HOST, PORT = "localhost", 11434
SYSTEM = ("주어진 사실만으로 추천 이유를 한국어 한 문장으로 쓴다. "
          "주어진 사실 외 정보를 추가하지 않는다. 40자 이내, 존댓말.")

CASES = [
    ("김치찌개", ["보유 재료로 바로 조리 가능", "김치 소비기한 D-3"]),
    ("애호박볶음", ["애호박 소비기한 D-2", "조리 10분"]),
    ("된장찌개", ["보유 재료로 바로 조리 가능", "두부 소비기한 D-1"]),
    ("제육볶음", ["선호 맛: 매움", "돼지고기 보유"]),
    ("계란말이", ["조리 8분", "부족한 재료: 없음"]),
    ("미역국", ["보유 재료로 바로 조리 가능", "많이 만드는 레시피"]),
    ("오이무침", ["오이 소비기한 D-2", "조리 5분"]),
    ("잡채", ["부족한 재료: 당면 1개", "선호 맛: 단맛"]),
    ("콩나물국", ["콩나물 소비기한 D-1", "조리 12분"]),
    ("감자조림", ["보유 재료로 바로 조리 가능", "감자 소비기한 D-5"]),
]


def mk(t, f):
    return f"메뉴: {t}\n사실:\n" + "\n".join(f"- {x}" for x in f)


def post(payload: dict, timeout=300) -> dict:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


print("=" * 64)
print("B'. 배치 실패 원인 규명")
print("=" * 64)
schema = {"type": "object",
          "properties": {"reasons": {"type": "array", "items": {"type": "string"},
                                     "minItems": 10, "maxItems": 10}},
          "required": ["reasons"]}
p = "각 메뉴의 추천 이유를 순서대로 쓴다.\n\n" + "\n\n".join(
    f"{i+1}. {t}\n" + "\n".join(f"   - {x}" for x in f) for i, (t, f) in enumerate(CASES))
for npred in (512, 1024):
    t0 = time.perf_counter()
    d = post({"model": MODEL, "system": SYSTEM, "prompt": p, "format": schema,
              "stream": False, "options": {"temperature": 0.3, "num_predict": npred}})
    w = time.perf_counter() - t0
    raw = d.get("response", "")
    try:
        n = len(json.loads(raw)["reasons"])
        ok = f"{n}건 OK"
    except Exception as e:
        ok = f"파싱실패({type(e).__name__}) 길이{len(raw)} 끝='{raw[-40:]}'"
    print(f"  num_predict={npred:<5} {w:6.2f}s  생성{d.get('eval_count',0):4d}tok  "
          f"done_reason={d.get('done_reason','?'):<8} {ok}")

print("\n" + "=" * 64)
print("C'. 진짜 TTFT (원시 소켓, 버퍼링 배제)")
print("=" * 64)
ttfts = []
for t, f in CASES[:5]:
    body = json.dumps({"model": MODEL, "system": SYSTEM, "prompt": mk(t, f),
                       "stream": True, "options": {"temperature": 0.3, "num_predict": 128}})
    c = http.client.HTTPConnection(HOST, PORT, timeout=300)
    t0 = time.perf_counter()
    c.request("POST", "/api/generate", body=body, headers={"Content-Type": "application/json"})
    r = c.getresponse()
    first = None
    ntok = 0
    while True:
        chunk = r.readline()
        if not chunk:
            break
        if chunk.strip():
            if first is None:
                first = time.perf_counter() - t0
            ntok += 1
            if json.loads(chunk).get("done"):
                break
    tot = time.perf_counter() - t0
    c.close()
    ttfts.append((first, tot))
    print(f"  {t:<7} TTFT {first:.2f}s   완료 {tot:.2f}s   {ntok}청크")
print(f"\n  TTFT 중앙값 {statistics.median([a for a, _ in ttfts]):.2f}s")

print("\n" + "=" * 64)
print("D. 동시성 — GPU 1장이 감당하는 QPS")
print("=" * 64)
print(f"  {'동시':<5}{'p50(s)':>9}{'p95(s)':>9}{'처리량(req/s)':>14}{'단건대비':>10}")
base = None
for conc in (1, 2, 4, 8):
    lats, lock = [], threading.Lock()

    def work(i):
        t, f = CASES[i % len(CASES)]
        s = time.perf_counter()
        try:
            post({"model": MODEL, "system": SYSTEM, "prompt": mk(t, f),
                  "format": {"type": "object", "properties": {"reason": {"type": "string"}},
                             "required": ["reason"]},
                  "stream": False, "options": {"temperature": 0.3, "num_predict": 128}})
        except Exception:
            return
        with lock:
            lats.append(time.perf_counter() - s)

    n = conc * 2
    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    w0 = time.perf_counter()
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    wall = time.perf_counter() - w0
    if not lats:
        print(f"  {conc:<5}{'실패':>9}")
        continue
    ls = sorted(lats)
    p50, p95 = statistics.median(ls), ls[max(0, int(len(ls) * 0.95) - 1)]
    qps = len(lats) / wall
    if base is None:
        base = p50
    print(f"  {conc:<5}{p50:9.2f}{p95:9.2f}{qps:14.2f}{p50/base:9.1f}x")
