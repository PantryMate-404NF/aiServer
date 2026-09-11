"""P1 — 전처리 (설계 4-2).

문자열을 **매칭 가능한 상태**로 만든다. 아직 분해하지 않는다.

    preprocess("대파 1대(흰 부분만)")
    → Preprocessed(text="대파 1대", notes=["흰 부분만"])

🔴 괄호를 통째로 지우지 않는다. `(생략가능)` 은 P4 optional 판정의 결정적 근거이고,
   `(400ml)` 은 수량 정보다. 지우면 모든 재료가 essential 이 되어 후보가 과도하게 좁아진다.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from features.recommend.stage import Preprocessed

SEEDS = Path(__file__).resolve().parents[4] / "seeds"

# ── 유니코드 분수 글리프 (NFKC 이전에 처리해 제어권을 유지한다) ──
_FRACTION_GLYPH = {
    "½": "1/2",
    "⅓": "1/3",
    "⅔": "2/3",
    "¼": "1/4",
    "¾": "3/4",
    "⅕": "1/5",
    "⅙": "1/6",
    "⅛": "1/8",
}

#: 제거 대상 특수문자. `·` 는 복합 재료 구분자이므로 여기 없다 (P2 가 처리).
_JUNK = re.compile(r"[※▶◆●■□▷☆★◇→←｜|]+")
_LEAD_DASH = re.compile(r"^\s*[-–—*•]\s*")

#: 괄호 안 내용 분류 (설계 4-2)
_RE_CONVERSION = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*[a-zA-Z가-힣]+\s*$")
_RE_OPTIONAL = re.compile(r"생략|취향|기호|선택|옵션|없으면|패스|안넣어도|빼도")
_RE_SUBSTITUTE = re.compile(r"또는|혹은|\bor\b", re.I)

_BRACKETS = re.compile(r"[（(\[]([^)）\]]*)[)）\]]")


def _load_brands() -> set[str]:
    """수식어 화이트리스트의 brand 절을 재사용한다 (seeds/modifier_whitelist.yaml)."""
    p = SEEDS / "modifier_whitelist.yaml"
    if not p.exists():
        return set()
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return set(d.get("brand") or [])


BRANDS = _load_brands()


def _load_non_ingredient() -> dict[str, str]:
    """재료가 아닌 표현 → 종류 (seeds/non_ingredient.yaml).

    🔴 이것을 `ingredient` 사전에 넣으면 안 된다. 그러면 레시피가
    "도마를 재료로 쓴다" 가 되어 `essential_ids` 에 들어가고 Retrieval 이 망가진다.
    **매칭 대상에서 빼는 것**이지 매칭시키는 것이 아니다.
    """
    p = SEEDS / "non_ingredient.yaml"
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for kind in ("tool", "vessel", "consumable", "action", "reviewed"):
        for name in d.get(kind) or []:
            out[str(name).strip()] = kind
    return out


NON_INGREDIENT = _load_non_ingredient()


def non_ingredient_kind(name: str) -> str | None:
    """이 이름이 재료가 아니면 그 종류를, 재료면 None 을 돌려준다."""
    return NON_INGREDIENT.get((name or "").strip())


def classify_bracket(inner: str) -> tuple[str, str]:
    """괄호 안 내용을 5종으로 분류한다.

    반환 (종류, 정리된 값) — 종류는 conversion·optional·substitute·brand·note.
    """
    s = inner.strip()
    if not s:
        return "note", ""
    if _RE_CONVERSION.match(s):
        return "conversion", s
    if _RE_OPTIONAL.search(s):
        return "optional", s
    if _RE_SUBSTITUTE.search(s):
        # '또는 미림' → '미림'
        return "substitute", _RE_SUBSTITUTE.sub("", s).strip()
    if s in BRANDS:
        return "brand", s
    return "note", s


def preprocess(raw: str) -> Preprocessed:
    original = raw
    s = raw or ""

    # ① 유니코드 분수 → ASCII (NFKC 가 U+2044 로 바꾸기 전에)
    for g, r in _FRACTION_GLYPH.items():
        s = s.replace(g, r)

    # ② NFKC — 전각→반각, 호환 문자 정리. 한글 음절은 그대로 유지된다.
    #    ⚠️ P1 은 NFC 계열로 합성한다. name_jamo 용 NFD 분해는 정반대 방향이며
    #       배치에서 따로 만든다 (설계 6-4-1).
    s = unicodedata.normalize("NFKC", s)

    # ③ 괄호 추출 + 분류
    pre = Preprocessed(original=original, text=s)
    for m in _BRACKETS.finditer(s):
        kind, val = classify_bracket(m.group(1))
        if not val:
            continue
        if kind == "conversion":
            pre.conversions.append(val)
        elif kind == "optional":
            pre.optional_hints.append(val)
        elif kind == "substitute":
            pre.substitutes.append(val)
        elif kind == "note":
            pre.notes.append(val)
        # brand 는 버린다
    s = _BRACKETS.sub(" ", s)

    # ④ 특수문자 · 선행 불릿 제거
    s = _LEAD_DASH.sub("", s)
    s = _JUNK.sub(" ", s)

    # ⑤ 공백 정규화
    pre.text = re.sub(r"\s+", " ", s).strip()
    return pre
