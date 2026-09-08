"""레시피 맛 벡터 산출 (설계 2-5-1 ⑤ · 02 I-15).

    재료 목록 + 역할  →  recipe_feature.flavor_vec REAL[6]

## 🔑 맛은 주로 양념에서 온다

소고기·감자·양파는 맛 축에 거의 기여하지 않고 고춧가루·간장·식초가 결정한다.
그래서 **집계에서 양념에 더 큰 가중치를 준다** — 단순 평균을 쓰면 재료가 많은
레시피에서 양념 신호가 희석되어 모든 레시피가 비슷해진다.

## 집계 방식을 고르는 것이 시드만큼 중요하다

    mean      재료 수에 희석된다. 재료 12개 레시피는 전부 밋밋해진다
    max       "고추 한 조각"이 매움 0.9 를 만든다. 과대평가
    role_w    역할 가중 평균 — 양념 3 · 필수 1 · 선택 0.5   ← 채택
    top3      축별 상위 3개 평균 — max 와 mean 의 절충

`bench/flavor_agg.py` 가 넷을 비교한다. **판별력(레시피 간 거리)** 으로 고른다 —
아무리 정확해도 모든 레시피가 같은 값을 받으면 `f_taste` 는 무용지물이다.

---

강도(intensity) — 재료가 **얼마나 세게** 맛에 기여하는가


## 문제

"재료가 들어갔다"와 "많이 들어갔다"는 다르다.
`고춧가루 1작은술` 과 `고춧가루 3큰술` 은 같은 매움이 아니다.

정확히 하려면 P5 수량 환산(g)이 필요한데, **그것은 보류하기로 했다**
(4-6 · 있음/없음 필터링만 한다). 그럼에도 **정성적 강도**는 잴 수 있다 —
수량 *숫자* 없이 **단위·제목·순서**만으로.

## 세 신호 — 전부 이미 데이터에 있다

    ① 단위 종류   P2 가 이미 파싱한다. `g/개/마리` = 주재료급, `큰술` = 양념, `약간` = 미량
    ② 제목 등장   "김치찌개"의 김치, "제육볶음"의 돼지고기 — **그 요리의 정체성**이다
    ③ 목록 순서   한국 레시피는 주재료를 앞에, 양념을 뒤에 적는 경향

실측(3건)에서 단위 분포는 `큰술 11 · 개 9 · 약간 6 · g 5 · 공기 2 · 마리 1` 로
**주재료 단위와 양념 단위가 뚜렷하게 갈렸다.** 제목 등장도 5건이 잡혔다
(카레·새우·밥·소고기).

## 🔴 이것은 수량 환산의 대체가 아니다

`고춧가루 1큰술` 과 `3큰술` 을 구분하지 못한다 — 같은 등급을 받는다.
**"등급"이지 "양"이 아니다.** 정확한 비율이 필요해지면 P5 를 해야 한다.
그때까지의 **최선의 근사**이고, 비용이 0 이다 (P2 산출을 그대로 쓴다).
"""
from __future__ import annotations

import re

import io
from pathlib import Path

import yaml

from features.recommend.enums import IngredientRole
from features.recommend.stage import ParsedIngredient

ROOT = Path(__file__).resolve().parents[4]
AXES = ["매움", "짠맛", "단맛", "신맛", "감칠맛", "기름짐"]
N_AXIS = 6

#: 역할별 집계 가중치. **양념이 맛을 만든다**는 도메인 사실을 수치로 넣은 것.
#: ⚠️ 근거는 요리 상식이지 측정이 아니다 — bench/flavor_agg.py 가 대안과 비교한다.
ROLE_WEIGHT = {
    IngredientRole.SEASONING: 3.0,
    IngredientRole.ESSENTIAL: 1.0,
    IngredientRole.OPTIONAL: 0.5,
    IngredientRole.GARNISH: 0.5,
}
DEFAULT_ROLE_WEIGHT = 1.0


class FlavorTable:
    """재료 → 6축 기여. 카테고리 기본값 + 개별 예외 (소비기한과 같은 패턴)."""

    def __init__(self, defaults: dict[str, list[float]], overrides: dict[str, list[float]]):
        self.defaults = defaults
        self.overrides = overrides

    @classmethod
    def from_seeds(cls, seeds: Path = ROOT / "seeds") -> "FlavorTable":
        d = yaml.safe_load(io.open(seeds / "ingredient_flavor.yaml", encoding="utf-8"))
        return cls({x["path"]: x["v"] for x in d["defaults"]},
                   {x["name"]: x["v"] for x in d["overrides"]})

    def of(self, name: str | None, category_path: str | None) -> list[float]:
        """개별 예외 > 카테고리 최장 접두 > 0벡터."""
        if name and name in self.overrides:
            return self.overrides[name]
        if category_path:
            parts = category_path.split(".")
            for i in range(len(parts), 0, -1):
                hit = self.defaults.get(".".join(parts[:i]))
                if hit:
                    return hit
        return [0.0] * N_AXIS


def aggregate(items: list[tuple[list[float], IngredientRole | None]],
              mode: str = "role_w") -> list[float]:
    """재료별 6축 → 레시피 6축.

    Args:
        items: [(6축 벡터, 역할)] — 역할이 None(P4 보류)이면 기본 가중치
        mode: mean | max | role_w | top3
    """
    vecs = [v for v, _ in items]
    if not vecs:
        return [0.0] * N_AXIS
    if mode == "max":
        return [max(v[k] for v in vecs) for k in range(N_AXIS)]
    if mode == "top3":
        out = []
        for k in range(N_AXIS):
            col = sorted((v[k] for v in vecs), reverse=True)[:3]
            out.append(sum(col) / len(col))
        return out
    if mode == "mean":
        return [sum(v[k] for v in vecs) / len(vecs) for k in range(N_AXIS)]
    # role_w — 양념에 가중
    ws = [ROLE_WEIGHT.get(r, DEFAULT_ROLE_WEIGHT) for _, r in items]
    tot = sum(ws) or 1.0
    return [sum(v[k] * w for (v, _), w in zip(items, ws)) / tot for k in range(N_AXIS)]


# ─────────────────────────────────────────────────────────────────
# 강도 — 단위·제목·순서만으로 잰다. 수량 환산(g)의 대체가 아니다.
# ─────────────────────────────────────────────────────────────────


#: 단위 → 강도 등급. **양이 아니라 "그 단위를 쓴다는 것이 뜻하는 규모"** 다.
#:   g·kg·개·마리·대·모·공기 를 쓰면 주재료급이고, 큰술·작은술은 양념 규모다.
UNIT_TIER = {
    # 주재료급 — 무게·개수로 센다
    "g": 1.5, "kg": 2.0, "개": 1.5, "마리": 1.5, "대": 1.4, "모": 1.5,
    "공기": 1.5, "장": 1.2, "쪽": 1.0, "줌": 1.2, "봉지": 1.4, "팩": 1.4,
    # 액체 — 국물요리의 주재료일 수 있다
    "ml": 1.2, "L": 1.8, "컵": 1.3,
    # 양념 규모
    "큰술": 1.0, "작은술": 0.7, "티스푼": 0.7,
    # 미량
    "약간": 0.4, "조금": 0.4, "적당량": 0.6, "꼬집": 0.3,
}
DEFAULT_TIER = 1.0

#: 제목에 등장하면 그 요리의 정체성이다. 가장 강한 신호.
TITLE_BOOST = 2.0
#: 목록 앞쪽 1/3 에 있으면 주재료일 가능성이 높다 (한국 레시피의 관행)
POSITION_BOOST = 1.2
#: 역할 가중 (p5_flavor.ROLE_WEIGHT 와 곱해지지 않도록 여기서는 쓰지 않는다)

_TITLE_STRIP = re.compile(r"[\s\-_()\[\]/·,.]+")


def _norm(s: str) -> str:
    return _TITLE_STRIP.sub("", s)


def in_title(name: str, title: str) -> bool:
    """제목에 재료가 등장하는가. 띄어쓰기·기호를 무시하고 본다.

    🔴 2글자 미만은 오탐이 많아 제외한다 — `밥`·`물` 이 아무 제목에나 걸린다.
    """
    if not name or not title or len(name) < 2:
        return False
    return _norm(name) in _norm(title)


def intensity(p: ParsedIngredient, ingredient_name: str | None,
              title: str, position: int, n_total: int,
              role: IngredientRole | None = None) -> float:
    """맛 집계에 쓸 강도 배수. 1.0 이 기준.

    수량 *숫자* 는 쓰지 않는다 — P5 를 보류했으므로 있어도 신뢰하지 않는다.
    단위 *종류* 만 본다.
    """
    tier = UNIT_TIER.get(p.unit or "", DEFAULT_TIER)
    boost = 1.0
    nm = ingredient_name or p.name
    if in_title(nm, title):
        boost *= TITLE_BOOST
    if n_total >= 6 and position < max(1, n_total // 3):
        boost *= POSITION_BOOST
    # 미량 표시가 있으면 위치·제목 보정을 하지 않는다 — '약간' 이 이긴다
    if p.is_ambiguous_qty:
        boost = min(boost, 1.0)
    return tier * boost
