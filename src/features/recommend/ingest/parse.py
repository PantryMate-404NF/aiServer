"""P2 — 분해 (설계 4-3).

    parse(preprocess("대파 1대(흰 부분만)"))
    → [ParsedIngredient(name="대파", quantity=1, unit="대", note="흰 부분만")]

🔑 **뒤에서부터 파싱한다.** 문자열 끝에서 `[수량][단위]` 를 먼저 떼어내고 남은 것을
   재료명으로 본다. 앞에서부터 찾으면 `양파 1/2개` 에서 `양파 1` 까지 먹어버린다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from features.recommend.ingest.preprocess import preprocess
from features.recommend.stage import ParsedIngredient, Preprocessed

SEEDS = Path(__file__).resolve().parents[4] / "seeds"

#: 개수 단위. 재료별 무게는 ingredient_unit_weight.csv 가 갖는다 (설계 4-6).
#: 세는 단위. 코드에 하드코딩하지 않고 시드에서 읽는다 —
#: 실데이터에서 `꼬집`(2,810건)·`줄`(1,391)·`봉지`(1,702) 가 여기 없어서
#: `소금 1꼬집` 이 통째로 미매칭이 됐다. 단위가 늘 때 코드를 고치면 안 된다.
COUNT_UNITS = [
    "개",
    "대",
    "쪽",
    "알",
    "마리",
    "줌",
    "톨",
    "장",
    "봉",
    "모",
    "포기",
    "단",
    "송이",
    "캔",
    "팩",
    "인분",
    "뿌리",
    "토막",
    "통",
    "조각",
    "공기",
    "판",
    "덩이",
]

#: 한글 수사. ⚠️ 뒤에 단위가 올 때만 수사로 본다 — `한우` `세발나물` `한천` 오분해 방지.
KO_NUMERAL = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
    "반": 0.5,
}

#: 복합 재료 구분자. `·` 는 P1 이 남겨둔다.
#
# 주의: 숫자 사이의 `,` `/` 는 구분자가 아니다 — 천단위(1,000)와 분수(1/2)를 쪼개면
#    수량 파싱이 통째로 무너진다. lookaround 로 숫자 사이를 제외한다.
_SPLIT = re.compile(r"\s*(?:(?<!\d)\s*,\s*(?!\d)|(?<!\d)\s*/\s*(?!\d)|[、·])\s*")

#: `또는` 대체 표현
_ALT = re.compile(r"\s*(?:또는|혹은|\bor\b)\s*", re.I)

#: 분해 후 남는 조사·부사
_PARTICLE = re.compile(r"\s*(?:각각|각|씩|정도|만큼)\s*$")


@lru_cache(maxsize=1)
def _units() -> tuple[dict[str, str], set[str], list[str]]:
    """(별칭→정규단위, 모호수량 집합, 정규식 대안 목록)."""
    d = yaml.safe_load((SEEDS / "measure_units.yaml").read_text(encoding="utf-8"))
    alias: dict[str, str] = {}
    for u in d["volume_ml"]:
        alias[u] = {
            "T": "큰술",
            "Tbsp": "큰술",
            "스푼": "큰술",
            "큰수저": "큰술",
            "t": "작은술",
            "tsp": "작은술",
            "티스푼": "작은술",
            "작은수저": "작은술",
            "cc": "ml",
            "리터": "L",
        }.get(u, u)
    for u in d["weight_g"]:
        alias[u] = {"그램": "g", "그람": "g", "킬로": "kg"}.get(u, u)
    # 코드 상수 + 시드의 count_unit·length_cm 을 합친다 (시드가 늘어도 코드는 그대로)
    for u in COUNT_UNITS:
        alias[u] = u
    for key in ("count_unit", "length_cm"):
        for u in d.get(key) or []:
            alias[str(u)] = str(u)
    alias["mL"] = "ml"
    alias["ML"] = "ml"
    alias["G"] = "g"
    alias["KG"] = "kg"
    ambiguous = set(d["ambiguous"])
    # 긴 것부터 — `작은술` 이 `술` 보다 먼저 매칭되어야 한다
    alts = sorted(set(alias) | ambiguous, key=len, reverse=True)
    return alias, ambiguous, alts


def _num_pattern() -> str:
    n = r"\d+(?:,\d{3})*(?:\.\d+)?"
    frac = r"\d+\s*/\s*\d+"
    ko = "|".join(sorted(KO_NUMERAL, key=len, reverse=True))
    # 순서가 중요하다. 긴 패턴부터 시도해야 `1과1/2` 에서 `1` 만 잡지 않는다.
    return (
        rf"(?:{n}\s*과\s*{frac}"  # 대분수  1과1/2
        rf"|{n}\s*[~\-–]\s*{n}"  # 범위    2~3
        rf"|{frac}"  # 분수    1/2
        rf"|{n}"  # 정수·소수
        rf"|{ko})"
    )  # 한글 수사


def _to_float(s: str) -> float | None:
    s = s.replace(",", "").replace(" ", "")
    if s in KO_NUMERAL:
        return float(KO_NUMERAL[s])
    if m := re.fullmatch(r"(\d+)과(\d+)/(\d+)", s):  # 1과1/2
        a, b, c = map(float, m.groups())
        return a + b / c
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)[~\-–](\d+(?:\.\d+)?)", s):  # 2~3 → 중앙값
        a, b = map(float, m.groups())
        return (a + b) / 2
    if m := re.fullmatch(r"(\d+)/(\d+)", s):  # 1/2
        a, b = map(float, m.groups())
        return a / b if b else None
    try:
        return float(s)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _regexes() -> dict[str, re.Pattern[str]]:
    _alias, ambiguous, alts = _units()
    u = "|".join(re.escape(a) for a in alts)
    q = _num_pattern()
    # 긴 것부터 잡아야 '약간씩' 이 '약간' 에 먼저 걸려 잘리지 않는다
    amb = "|".join(re.escape(a) for a in sorted(ambiguous, key=len, reverse=True))
    return {
        # 끝에서 [수량][단위]
        "tail": re.compile(rf"\s*({q})\s*({u})\s*$"),
        # 끝에서 모호 수량만 ('약간' '조금' '약간씩')
        "tail_amb": re.compile(rf"\s*({amb})\s*씩?\s*$"),
        # 끝에서 단위 없는 수량 ('사과 2')
        "tail_bare": re.compile(rf"\s*({q})\s*$"),
        # 앞에서 [수량][단위] ('1큰술 참기름')
        "head": re.compile(rf"^\s*({q})\s*({u})\s+"),
    }


def _norm_unit(u: str) -> str:
    alias, ambiguous, _ = _units()
    return u if u in ambiguous else alias.get(u, u)


def _strip_modifiers(name: str) -> tuple[str, list[str]]:
    """수식어를 **기록만 하고 제거하지 않는다.**

    실제 제거는 L2 가 사전을 보고 판단한다 (설계 4-4). P2 는 사전이 없으므로
    여기서 지우면 `다진마늘` → `마늘` 같은 사고를 막을 수 없다.
    """
    from features.recommend.ingest.preprocess import SEEDS as _S

    mods: list[str] = []
    p = _S / "modifier_whitelist.yaml"
    if p.exists():
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for key, vals in d.items():
            if key == "do_not_remove_examples":
                continue
            for v in vals or []:
                if name.startswith(v + " ") or name.startswith(v):
                    if len(name) - len(v) >= 2:
                        mods.append(v)
    return name, mods


def _parse_one(text: str, pre: Preprocessed, pos: int) -> ParsedIngredient | None:
    rx = _regexes()
    qty: float | None = None
    unit: str | None = None
    ambiguous_qty = False

    s = _PARTICLE.sub("", text).strip()
    if not s:
        return None

    # ── 대체 표현 '또는' ─────────────────────────────────────
    subs = list(pre.substitutes)
    if _ALT.search(s):
        parts = _ALT.split(s)
        s = parts[0].strip()
        for p in parts[1:]:
            # '미림 1큰술' → 대체재는 '미림'
            m = rx["tail"].search(p) or rx["tail_amb"].search(p)
            subs.append((p[: m.start()] if m else p).strip())
        # 수량이 대체재 쪽에 붙어 있으면 가져온다
        if not rx["tail"].search(s) and parts[1:]:
            if m := rx["tail"].search(parts[-1]):
                qty, unit = _to_float(m.group(1)), _norm_unit(m.group(2))

    # ── ① 끝에서 [수량][단위] ────────────────────────────────
    if qty is None:
        if m := rx["tail"].search(s):
            qty, unit = _to_float(m.group(1)), _norm_unit(m.group(2))
            s = s[: m.start()].strip()

    # ── ② 끝에서 모호 수량 ───────────────────────────────────
    if qty is None and unit is None:
        if m := rx["tail_amb"].search(s):
            unit, ambiguous_qty = m.group(1), True
            s = s[: m.start()].strip()

    # ── ③ 앞에서 [수량][단위] ────────────────────────────────
    if qty is None and unit is None:
        if m := rx["head"].match(s):
            qty, unit = _to_float(m.group(1)), _norm_unit(m.group(2))
            s = s[m.end() :].strip()

    # ── ④ 단위 없는 끝자리 수량 ──────────────────────────────
    if qty is None and unit is None:
        if m := rx["tail_bare"].search(s):
            cand = s[: m.start()].strip()
            if cand:  # 재료명이 남을 때만
                qty = _to_float(m.group(1))
                s = cand

    s = _PARTICLE.sub("", s).strip()
    if not s:
        return None

    # ── 괄호 환산값이 있으면 그쪽을 쓴다 (설계 4-3) ──────────
    if pre.conversions:
        if m := re.match(r"(\d+(?:[.,]\d+)?)\s*([a-zA-Z가-힣]+)", pre.conversions[0]):
            qty, unit = _to_float(m.group(1)), _norm_unit(m.group(2))

    name, mods = _strip_modifiers(s)
    return ParsedIngredient(
        raw_text=pre.original,
        name=name,
        quantity=qty,
        unit=unit,
        note=pre.notes[0] if pre.notes else None,
        modifiers=mods,
        substitutes=subs,
        is_optional_hint=bool(pre.optional_hints),
        is_ambiguous_qty=ambiguous_qty,
        position=pos,
    )


def parse(pre: Preprocessed) -> list[ParsedIngredient]:
    """P1 산출 → 재료 리스트. 복합 표현이면 여러 개가 나온다."""
    if not pre.text:
        return []

    parts = [p for p in _SPLIT.split(pre.text) if p.strip()]
    out: list[ParsedIngredient] = []
    for i, part in enumerate(parts, start=1):
        if r := _parse_one(part, pre, i):
            out.append(r)

    # 구분자 없는 복합 의심 — P3 가 실패하면 검수 큐로 (설계 4-3)
    if len(out) == 1 and out[0].quantity is None and out[0].is_ambiguous_qty:
        if len(out[0].name) >= 4 and " " not in out[0].name:
            out[0].split_candidate = True

    # 구분자로 나뉜 경우 수량이 마지막 조각에만 붙어 있으면 앞으로 전파
    if len(out) > 1:
        last = out[-1]
        if last.quantity is not None or last.unit:
            for r in out[:-1]:
                if r.quantity is None and r.unit is None:
                    r.quantity, r.unit = last.quantity, last.unit
                    r.is_ambiguous_qty = last.is_ambiguous_qty
    return out


def normalize(raw_text: str) -> list[ParsedIngredient]:
    """P1 + P2. 정규화 파이프라인의 공개 진입점.

    재료가 아닌 것(조리도구·용기·소모품)은 **지우지 않고 표시한다** —
    `is_non_ingredient=True`. **유저에게 묻는 것이 아니다.** 이 값을 읽어가는
    후속 코드가 각자 알아서 처리한다:

      - `coverage.py`  분모에서 뺀다 (도구를 못 맞혔다고 점수를 깎으면 안 된다)
      - 검수 큐        애초에 담지 않는다 ("도마를 어느 재료에?" 를 2,259번 묻게 된다)
      - 로그·통계      남긴다 — 무엇을 걸렀는지가 데이터 품질 지표다

    지우지 않는 이유도 둘이다. `position` 이 어긋나고("몇 번째 재료인가"가 밀린다),
    "무엇을 걸렀는지" 를 잃는다.
    """
    from features.recommend.ingest.preprocess import non_ingredient_kind

    out = parse(preprocess(raw_text))
    for pi in out:
        k = non_ingredient_kind(pi.name)
        if k:
            pi.is_non_ingredient = True
            pi.non_ingredient_kind = k
    return out
