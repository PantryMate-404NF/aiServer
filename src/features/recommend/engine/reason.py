"""추천 이유 생성 — LLM 없이 (설계 5-5).

🔴 **`contrib = w·f` 의 argmax 로 이유를 고르면 안 된다.** *(v1.9 수정)*

측정 — 후보 500건 시뮬레이션, Top-20 의 이유 분포:

    ① contrib    = w·f              →  이유 1종   f_coverage 100%
    ② salience   = w·(f−μ)          →  이유 2종   f_expiring  95%
    ③ z-salience = w·(f−μ)/σ, 상위 2개 조합  →  실사용 가능

①이 붕괴하는 이유는 구조적이다. Stage ① 이 `max_missing` 으로 이미 걸러냈으므로
상위 후보의 `f_coverage` 는 거의 항상 1.0 이고, 그 가중치 0.24 가 최대다.
곱의 argmax 가 사실상 상수가 된다.

**이유는 "점수가 높은 이유"가 아니라 "다른 후보와 달라서 뽑힌 이유"다.**
따라서 같은 요청의 후보 집합으로 표준화하고, 상위 2개를 이어 붙인다.

---

## 한국어 처리 두 가지

**1. 연결어미는 계산하지 않는다.** `"맞아요"` → `"맞고"`, `"들어가요"` → `"들어가고"` 는
규칙이 서로 다르다(`아요` 제거 vs `요` 제거). 형태소 분석 없이 맞히려는 시도는 반드시
깨진다. **템플릿을 종결형·연결형 두 벌로 적어둔다.** 사람이 검수할 수 있고 틀릴 수 없다.

**2. 조사는 받침으로 자동 선택한다.** `"달걀가"` 같은 오류는 재료명이 데이터에서
오므로 손으로 못 막는다. `{이/가}` 마커를 앞 글자의 종성으로 해석한다.
"""
from __future__ import annotations

import re
from typing import Any

#: 피처 → (종결형, 연결형). `{}` 는 `ctx` 로, `{{이/가}}` 는 받침으로 채운다.
#: 🔴 두 형태를 반드시 같이 적는다. 하나만 적고 나머지를 규칙으로 만들면 깨진다.
REASON_TEMPLATES: dict[str, tuple[str, str]] = {
    "f_expiring":   ("{expiring_name}(D-{expiring_days}){{을/를}} 소진할 수 있어요",
                     "{expiring_name}(D-{expiring_days}){{을/를}} 소진할 수 있고"),
    "f_coverage":   ("가진 재료로 바로 만들 수 있어요",
                     "가진 재료로 바로 만들 수 있고"),
    "f_missing":    ("{missing_name} 하나만 사면 돼요",
                     "{missing_name} 하나만 사면 되고"),
    "f_pantry_use": ("냉장고 재료를 {pantry_used}가지나 써요",
                     "냉장고 재료를 {pantry_used}가지나 쓰고"),
    "f_taste":      ("선호하시는 {taste_axis}에 맞아요",
                     "선호하시는 {taste_axis}에 맞고"),
    "f_ing_pref":   ("좋아하시는 {pref_ing}{{이/가}} 들어가요",
                     "좋아하시는 {pref_ing}{{이/가}} 들어가고"),
    "f_cuisine":    ("즐겨 드시는 {cuisine}{{이에요/예요}}",
                     "즐겨 드시는 {cuisine}{{이고/고}}"),
    "f_dish_type":  ("{dish_type} 종류예요", "{dish_type} 종류이고"),
    "f_cooccur":    ("지난번 만드신 {similar_title}{{과/와}} 비슷해요",
                     "지난번 만드신 {similar_title}{{과/와}} 비슷하고"),
    "f_season":     ("지금이 제철이에요", "지금이 제철이고"),
    "f_popularity": ("많이 만드는 레시피예요", "많이 만드는 레시피이고"),
    "f_time_fit":   ("{cook_minutes}분이면 완성돼요", "{cook_minutes}분이면 완성되고"),
    "f_quality":    ("후기가 좋은 레시피예요", "후기가 좋은 레시피이고"),
    "f_skill_fit":  ("지금 실력에 알맞은 난이도예요", "지금 실력에 알맞은 난이도이고"),
    "f_content":    ("평소 보시던 레시피와 결이 비슷해요", "평소 보시던 레시피와 결이 비슷하고"),
}

_EXPLORATION = "새로운 시도는 어떠세요"
_FALLBACK = "추천 목록에 포함됐어요"

_JOSA = re.compile(r"\{([가-힣]+)/([가-힣]+)\}")


def _jong(ch: str) -> int:
    """한글 음절의 종성 코드. 0 이면 받침 없음. 한글이 아니면 -1."""
    if not ("가" <= ch <= "힣"):
        return -1
    return (ord(ch) - 0xAC00) % 28


def _head_char(text: str, at: int) -> str:
    """조사 앞의 **실질 명사 마지막 글자**. 괄호주석은 건너뛴다.

    `"두부(D-2){을/를}"` 에서 조사는 `)` 가 아니라 `두부` 에 붙어야 한다.
    괄호를 안 건너뛰면 받침 없는 재료가 전부 `"두부(D-2)을"` 이 된다.
    """
    i = at - 1
    if i >= 0 and text[i] == ")":
        depth = 0
        while i >= 0:
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    i -= 1
                    break
            i -= 1
    return text[i] if i >= 0 else ""


def _apply_josa(text: str) -> str:
    """`{이/가}` 마커를 앞 글자의 받침으로 해석한다.

    한글이 아닌 글자(숫자·영문·괄호)로 끝나면 받침 있음으로 본다 — 한국어에서
    숫자는 대개 받침 있는 것으로 읽힌다(`3분이`). 완벽하지 않지만 데이터에서
    오는 재료명은 대부분 한글이라 실패 비용이 낮다.
    """
    def sub(m: re.Match) -> str:
        with_jong, without_jong = m.group(1), m.group(2)
        j = _jong(_head_char(text, m.start()))
        if j == -1:                       # 한글이 아님 → 받침 있는 쪽
            return with_jong
        if j == 0:
            return without_jong
        if j == 8 and with_jong == "으로":  # ㄹ 받침은 '로' (서울로)
            return without_jong
        return with_jong
    prev_text = None
    out = text
    while prev_text != out:               # 마커가 연속으로 붙어도 왼쪽부터 해소
        prev_text, out = out, _JOSA.sub(sub, out, count=1)
    return out


def _fill(key: str, ctx: dict[str, Any], connective: bool) -> str | None:
    """템플릿을 채운다. 필요한 값이 하나라도 없으면 None (그 이유는 쓸 수 없다)."""
    tpl = REASON_TEMPLATES.get(key)
    if tpl is None:
        return None
    try:
        # 🔴 조사 마커는 템플릿에 `{{이/가}}` 로 적혀 있다. format 이 `{이/가}` 로
        #    풀어준 뒤에야 받침을 볼 수 있다 — 값이 채워져야 앞 글자를 알기 때문이다.
        return _apply_josa(tpl[1 if connective else 0].format(**ctx))
    except (KeyError, IndexError):
        return None


def build_reason(feature_keys: list[str], ctx: dict[str, Any],
                 is_exploration: bool = False, max_parts: int = 2) -> tuple[str, list[str]]:
    """z-salience 상위 피처들로 문장을 만든다.

    Returns:
        (문장, 실제로 사용된 피처 키 목록)

    `feature_keys` 는 salience 내림차순이어야 한다 (`schemas.top_reasons`).
    채울 값이 없는 피처는 조용히 건너뛴다 — `"{}(D-{})를 소진할 수 있어요"` 같은
    깨진 문장을 내보내는 것이 아무 이유도 안 다는 것보다 나쁘다.
    """
    if is_exploration:
        return _EXPLORATION, []

    usable = [k for k in feature_keys if _fill(k, ctx, False)][:max_parts]
    if not usable:
        return _FALLBACK, []
    if len(usable) == 1:
        return _fill(usable[0], ctx, False), usable

    head = _fill(usable[0], ctx, connective=True)
    tail = _fill(usable[1], ctx, connective=False)
    return f"{head}, {tail}", usable


def check_templates() -> list[str]:
    """종결형·연결형이 짝을 이루는지, 마커가 유효한지 검사한다 (계약 테스트용)."""
    errs = []
    for k, v in REASON_TEMPLATES.items():
        if not isinstance(v, tuple) or len(v) != 2:
            errs.append(f"{k}: (종결형, 연결형) 튜플이 아니다")
            continue
        fin, con = (_JOSA.sub(lambda m: m.group(1), x.replace("{{", "{").replace("}}", "}"))
                    for x in v)
        if not fin.endswith(("요", "다")):
            errs.append(f"{k}: 종결형이 종결어미로 끝나지 않는다 — {fin!r}")
        if not con.endswith(("고", "며", "여")):
            errs.append(f"{k}: 연결형이 연결어미로 끝나지 않는다 — {con!r}")
        for t in v:
            for a, b in _JOSA.findall(t):
                if (a, b) not in {("이", "가"), ("을", "를"), ("은", "는"),
                                  ("과", "와"), ("이에요", "예요"), ("이고", "고"),
                                  ("으로", "로"), ("이", "")}:
                    errs.append(f"{k}: 알 수 없는 조사 마커 {{{a}/{b}}}")
    return errs
