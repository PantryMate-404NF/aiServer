"""Gemma 4 한국어 실무 적합성 측정.

우리 파이프라인에서 LLM이 실제로 맡을 후보 작업 3종을 그대로 던진다.
대화 품질이 아니라 '배치에 쓸 수 있는가'를 본다: JSON 준수율 + 처리량.
"""
from __future__ import annotations

import json
import time
import urllib.request

MODEL = "gemma4:12b-it-qat"
URL = "http://localhost:11434/api/generate"

# I-15 미해결. 지금 seeds/ingredient_flavor.yaml 값은 전부 내가 임의로 매긴 것이라
# 출처가 없다. LLM이 이 자리를 메울 수 있는지가 이 태스크의 핵심.
FLAVOR_SCHEMA = {
    "type": "object",
    "properties": {
        "sweet": {"type": "number", "minimum": 0, "maximum": 1},
        "salty": {"type": "number", "minimum": 0, "maximum": 1},
        "sour": {"type": "number", "minimum": 0, "maximum": 1},
        "spicy": {"type": "number", "minimum": 0, "maximum": 1},
        "umami": {"type": "number", "minimum": 0, "maximum": 1},
        "bitter": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["sweet", "salty", "sour", "spicy", "umami", "bitter"],
}

# P3 매칭 캐스케이드가 L2(규칙)에서도 못 잡는 표기를 LLM이 받아낼 수 있는지.
NORM_SCHEMA = {
    "type": "object",
    "properties": {
        "core": {"type": "string"},
        "modifiers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["core", "modifiers"],
}

REASON_SCHEMA = {
    "type": "object",
    "properties": {"reason": {"type": "string", "maxLength": 60}},
    "required": ["reason"],
}

TASKS: list[tuple[str, str, dict]] = [
    (
        "flavor_vec",
        "재료 '고추장'의 맛 프로파일을 0~1 사이 실수로 매겨라. "
        "0=전혀 없음, 1=매우 강함. 설명 없이 JSON만.",
        FLAVOR_SCHEMA,
    ),
    (
        "flavor_vec",
        "재료 '멸치액젓'의 맛 프로파일을 0~1 사이 실수로 매겨라. "
        "0=전혀 없음, 1=매우 강함. 설명 없이 JSON만.",
        FLAVOR_SCHEMA,
    ),
    (
        "normalize",
        "한국어 식재료명 '국거리용 한우 양지'를 핵심어(core)와 수식어(modifiers)로 "
        "분해하라. core는 재료의 본체 한 단어. 설명 없이 JSON만.",
        NORM_SCHEMA,
    ),
    (
        "normalize",
        "한국어 식재료명 '다진마늘(냉동)'을 핵심어(core)와 수식어(modifiers)로 "
        "분해하라. core는 재료의 본체 한 단어. 설명 없이 JSON만.",
        NORM_SCHEMA,
    ),
    (
        "reason",
        "사용자 냉장고에 [돼지고기, 김치, 두부, 대파]가 있고 김치가 3일 뒤 "
        "소비기한이 끝난다. 추천 메뉴는 '김치찌개'. 왜 추천하는지 한국어 한 문장, "
        "40자 이내, 존댓말로. 설명 없이 JSON만.",
        REASON_SCHEMA,
    ),
    (
        "reason",
        "사용자는 매운맛을 좋아하고 조리시간 20분 이내를 선호한다. 냉장고에 "
        "[계란, 대파, 밥]이 있다. 추천 메뉴는 '김치볶음밥'. 왜 추천하는지 한국어 "
        "한 문장, 40자 이내, 존댓말로. 설명 없이 JSON만.",
        REASON_SCHEMA,
    ),
]


def run(prompt: str, schema: dict) -> tuple[dict | None, dict]:
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": prompt,
            "format": schema,  # ollama 구조화 출력 = llama.cpp GBNF 강제
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 256},
        }
    ).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    try:
        parsed = json.loads(d["response"])
    except json.JSONDecodeError:
        parsed = None
    return parsed, d


def main() -> None:
    print(f"모델: {MODEL}\n")
    ok = 0
    gen_tok = gen_ns = prompt_tok = prompt_ns = 0

    for kind, prompt, schema in TASKS:
        t0 = time.perf_counter()
        parsed, d = run(prompt, schema)
        wall = time.perf_counter() - t0

        gen_tok += d.get("eval_count", 0)
        gen_ns += d.get("eval_duration", 0)
        prompt_tok += d.get("prompt_eval_count", 0)
        prompt_ns += d.get("prompt_eval_duration", 0)

        valid = parsed is not None
        ok += valid
        mark = "OK  " if valid else "FAIL"
        print(f"[{mark}] {kind:<11} {wall:5.1f}s  {json.dumps(parsed, ensure_ascii=False)}")

    print(f"\nJSON 준수: {ok}/{len(TASKS)}")
    if gen_ns:
        print(f"생성 처리량 : {gen_tok / (gen_ns / 1e9):6.1f} tok/s  ({gen_tok} tok)")
    if prompt_ns:
        print(f"프리필 처리량: {prompt_tok / (prompt_ns / 1e9):6.1f} tok/s  ({prompt_tok} tok)")


if __name__ == "__main__":
    main()
