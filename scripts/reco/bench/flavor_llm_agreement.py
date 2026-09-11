"""I-15 판정: LLM이 수기 flavor 시드를 대체·검증할 수 있는가.

seeds/ingredient_flavor.yaml 의 overrides 33종은 전부 내가 요리 상식으로 매긴 값이라
출처가 없다. 같은 축 정의·같은 의미론을 주고 Gemma 4 에게 물어 일치도를 잰다.

핵심: 의미론을 똑같이 줘야 공정하다. "소금=[0,1,0,0,0,0]" 은 "소금이 100% 짜다"가
아니라 "통상량 투입 시 짠맛 축만 강하게 선다" 는 뜻이고, 이걸 프롬프트에 그대로 넣는다.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.request
from pathlib import Path

MODEL = "gemma4:12b-it-qat"
URL = "http://localhost:11434/api/generate"
AXES = ["매움", "짠맛", "단맛", "신맛", "감칠맛", "기름짐"]

SCHEMA = {
    "type": "object",
    "properties": {a: {"type": "number", "minimum": 0, "maximum": 1} for a in AXES},
    "required": AXES,
}

SYSTEM = """한국 요리 재료의 맛 프로파일을 매긴다.

축: [매움, 짠맛, 단맛, 신맛, 감칠맛, 기름짐] 각 0.0~1.0

의미: "이 재료를 통상적인 조리량으로 넣었을 때 그 맛 축이 얼마나 서는가".
재료 자체의 성분비가 아니다. 예를 들어 소금은 [0, 1.0, 0, 0, 0, 0] 이다 —
"소금이 100% 짜다"가 아니라 "넣으면 짠맛 축만 강하게 세운다"는 뜻이다.
참기름은 [0, 0, 0, 0, 0.45, 0.90] 이다 — 기름짐을 강하게, 감칠맛을 중간으로 올린다.

설명 없이 JSON만 출력한다."""


def load_overrides(p: Path) -> list[tuple[str, list[float]]]:
    out = []
    body = p.read_text(encoding="utf-8").split("overrides:", 1)[1]
    for m in re.finditer(r"\{name:\s*(\S+?),\s*v:\s*\[([^\]]+)\]\}", body):
        out.append((m.group(1), [float(x) for x in m.group(2).split(",")]))
    return out


def ask(name: str) -> tuple[list[float] | None, float, int]:
    body = json.dumps({
        "model": MODEL,
        "system": SYSTEM,
        "prompt": f"재료: {name}",
        "format": SCHEMA,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 128},
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    wall = time.perf_counter() - t0
    try:
        o = json.loads(d["response"])
        return [float(o[a]) for a in AXES], wall, d.get("eval_count", 0)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, wall, d.get("eval_count", 0)


def main() -> None:
    items = load_overrides(Path("seeds/ingredient_flavor.yaml"))
    print(f"대조 대상: {len(items)}종  (수기 overrides 전량)\n")

    rows, per_axis, walls, toks = [], [[] for _ in AXES], [], 0
    for name, mine in items:
        got, wall, tk = ask(name)
        walls.append(wall)
        toks += tk
        if got is None:
            print(f"  FAIL {name}")
            continue
        diffs = [abs(a - b) for a, b in zip(mine, got)]
        for i, d in enumerate(diffs):
            per_axis[i].append(d)
        rows.append((max(diffs), name, mine, got))

    print("── 축별 평균절대차 (MAE) ──")
    for a, ds in zip(AXES, per_axis):
        bar = "█" * round(statistics.mean(ds) * 60)
        print(f"  {a:<5} {statistics.mean(ds):.3f}  {bar}")
    alld = [d for ds in per_axis for d in ds]
    print(f"\n  전체 MAE {statistics.mean(alld):.3f}   중앙값 {statistics.median(alld):.3f}")
    agree = sum(d <= 0.15 for d in alld) / len(alld)
    print(f"  |차이|<=0.15 비율 {agree:.1%}")

    # 계통 편향 점검: LLM 이 일관되게 높게/낮게 매기면 값을 그대로 못 쓴다.
    signed = [g - m for _, _, mine, got in rows for m, g in zip(mine, got)]
    print(f"  부호 있는 평균차 (LLM-수기) {statistics.mean(signed):+.3f}")
    hi = sum(d > 0.02 for d in signed)
    lo = sum(d < -0.02 for d in signed)
    print(f"  LLM 이 더 높음 {hi}축 / 더 낮음 {lo}축 / 동일 {len(signed)-hi-lo}축")
    print("  축별 부호차:", "  ".join(
        f"{a} {statistics.mean([g - m for _, _, mi, go in rows for m, g in [(mi[i], go[i])]]):+.3f}"
        for i, a in enumerate(AXES)))

    Path("bench/out").mkdir(exist_ok=True)
    Path("bench/out/flavor_llm_agreement.json").write_text(json.dumps(
        [{"name": n, "hand": mi, "llm": go} for _, n, mi, go in rows],
        ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n── 불일치 최대 8종 (사람 검수 우선순위) ──")
    for mx, name, mine, got in sorted(rows, reverse=True)[:8]:
        print(f"  {name:<8} Δmax={mx:.2f}")
        print(f"      수기 {[round(x,2) for x in mine]}")
        print(f"      LLM  {[round(x,2) for x in got]}")

    print(f"\n── 처리량 ──")
    print(f"  건당 중앙값 {statistics.median(walls):.2f}s   총 {sum(walls):.0f}s / {len(items)}건")
    print(f"  → 재료 1,000종 소요 예상: {statistics.median(walls)*1000/60:.0f}분")
    print(f"  → 재료 5,000종 소요 예상: {statistics.median(walls)*5000/3600:.1f}시간")


if __name__ == "__main__":
    main()
