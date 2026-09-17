"""요리 계열 배정 — recipe.cuisine_family 를 채운다 (G-30 안 b).

    python -m features.recommend.ingest.cuisine_build
    python -m features.recommend.ingest.cuisine_build --dry-run
    make cuisine-build

`recipe.cuisine_family` 가 46,353건 전수 비어 있었습니다. 그래서 B 가 만든
'좋아하는 음식 유형' 온보딩 문항과 재정렬 슬롯이 **대상 후보를 못 찾습니다.**
에러는 안 나고, 사용자에게는 "내가 고른 문항이 아무 일도 안 한다" 로 보입니다.
`f_cuisine`(가중치 0.04)도 항상 None 입니다.

## 왜 규칙 기반인가

원본에 분류축이 없습니다. `raw_json.categories` 는 고유 45,529종 자유 태그라
축으로 안 접히고, `dish_type`·`cuisine`·`main_ing_cat` 은 실측 전수 0건입니다.

태그를 계열로 옮기는 매핑 배치(안 a)는 약 12시간이 들고, 태그가 아예 없는
레시피가 26.1% 라 상한이 74% 입니다. 규칙 기반(안 b)은 반나절에 61.7% 라
비용 대비가 낫습니다. 부족하면 나중에 안 a 를 얹습니다.

## 순서가 규칙의 절반이다

    ① 태그  ②  비한식 제목  ③ 한식 제목  ④ 시그니처 재료

비한식을 한식보다 먼저 보는 이유: `김치파스타` 는 양식이어야 합니다.
한식 제목을 재료보다 먼저 보는 이유: 다진마늘·참기름·고춧가루가 거의 모든
레시피에 있어(실측 27,359건) 재료를 먼저 보면 **전부 한식이 됩니다.**

## 못 정하면 비운다

전부 실패하면 NULL 입니다. 한식으로 폴백하면 배정률이 100% 가 되지만, 그
21,967건 중에 분류 못 한 양식·퓨전이 섞여 있고 한식으로 박으면 그 사용자가
영영 못 만납니다. 비한식 계열은 폴백을 켜도 숫자가 같습니다.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from features.recommend.enums import CuisineFamily
from features.recommend.ingest.run_log import batch_run
from features.recommend.repository_ingest import load_cuisine_source, set_cuisine_families

logger = logging.getLogger(__name__)

SEEDS = Path(__file__).resolve().parents[4] / "seeds"

#: 완료 기준 — 배정률이 이보다 낮으면 규칙이 망가진 것으로 봅니다.
#: 실측 61.7% 에서 넉넉히 내린 값입니다. 사전이 바뀌어도 크게 안 흔들립니다.
MIN_ASSIGNED_RATIO = 0.50


@dataclass
class Rules:
    """`seeds/cuisine_taxonomy.yaml` 의 rules 절. 정본은 시드입니다."""

    priority: list[str]
    tag_map: dict[str, str]
    #: 제목 신호가 없을 때만 보는 태그. 퓨전이 여기 있습니다 — 표본 20건 중
    #: 10건이 다른 계열이라, 작성자의 '퓨전' 과 우리 fusion 은 뜻이 다릅니다.
    tag_late: dict[str, str]
    non_korean: dict[str, list[str]]
    #: 키워드별 제외어. `커리` 에 `치커리`(채소)가 걸리는 것을 막습니다.
    exclude: dict[str, list[str]]
    korean: list[str]
    signature: dict[str, list[str]]
    sig_min: int
    conf: dict[str, float]

    @classmethod
    def load(cls) -> Rules:
        raw: dict[str, Any] = yaml.safe_load(
            (SEEDS / "cuisine_taxonomy.yaml").read_text(encoding="utf-8")
        )["rules"]
        r = cls(
            priority=list(raw["priority"]),
            tag_map=dict(raw["by_source_tag"]["map"]),
            tag_late=dict(raw.get("by_source_tag_late", {}).get("map", {})),
            non_korean={k: list(v) for k, v in raw["by_title_non_korean"]["map"].items()},
            exclude={
                k: list(v) for k, v in (raw["by_title_non_korean"].get("exclude") or {}).items()
            },
            korean=list(raw["by_title_korean"]["keywords"]),
            signature={k: list(v) for k, v in raw["by_signature_ingredients"]["map"].items()},
            sig_min=int(raw["by_signature_ingredients"]["min"]),
            conf={k: float(raw[k]["conf"]) for k in raw if isinstance(raw[k], dict)},
        )
        r.check()
        return r

    def check(self) -> None:
        """규칙이 내는 계열이 `CuisineFamily` 안에 있는가.

        주의: 시드가 한때 `southeast_asian`(열거형에 없음)과 `italian`(계열이
           아니라 세분 코드)을 뱉었습니다. 그대로 넣으면 온보딩이 고른 값과
           영영 안 만나는데, INSERT 는 성공하므로 아무도 모릅니다.
        """
        valid = {f.value for f in CuisineFamily}
        used = (
            set(self.tag_map.values())
            | set(self.tag_late.values())
            | set(self.non_korean)
            | {"korean"}
            | set(self.signature)
        )
        unknown = sorted(used - valid)
        if unknown:
            raise ValueError(f"CuisineFamily 에 없는 계열입니다: {unknown} (가능: {sorted(valid)})")


@dataclass
class CuisineStats:
    recipes: int = 0
    assigned: int = 0
    by_family: dict[str, int] = field(default_factory=dict)
    by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        return self.assigned / self.recipes if self.recipes else 0.0

    @property
    def passed(self) -> bool:
        return self.ratio >= MIN_ASSIGNED_RATIO and len(self.by_family) >= 5

    def report(self) -> str:
        fam = " · ".join(
            f"{k} {v:,}" for k, v in sorted(self.by_family.items(), key=lambda x: -x[1])
        )
        rule = " · ".join(
            f"{k} {v:,}" for k, v in sorted(self.by_rule.items(), key=lambda x: -x[1])
        )
        return (
            f"레시피 {self.recipes:,}건 · 배정 {self.assigned:,}건 "
            f"({self.ratio * 100:.1f}%, 기준 {MIN_ASSIGNED_RATIO * 100:.0f}% 이상)\n"
            f"  계열별  {fam}\n"
            f"  근거별  {rule}\n"
            f"  나머지 {self.recipes - self.assigned:,}건은 NULL 입니다 — 억지로 채우지 않습니다"
        )


def judge(title: str, tags: set[str], ingredients: set[str], r: Rules) -> tuple[str, str] | None:
    """한 레시피의 계열. (계열, 근거) 또는 None.

    위에서 걸리면 거기서 끝냅니다 — 순서가 규칙의 절반입니다.
    """
    for tag in tags:
        if tag in r.tag_map:
            return r.tag_map[tag], "by_source_tag"

    t = title or ""
    for family, keywords in r.non_korean.items():
        for k in keywords:
            # 제외어가 제목에 있으면 이 키워드는 건너뛴다. '치커리' 가 '커리' 에,
            # '짜파게티' 가 '짜장' 에 걸리는 부분 문자열 함정을 막는다.
            if k in t and not any(x in t for x in r.exclude.get(k, ())):
                return family, "by_title_non_korean"

    if any(k in t for k in r.korean):
        return "korean", "by_title_korean"

    for tag in tags:
        if tag in r.tag_late:
            return r.tag_late[tag], "by_source_tag_late"

    best, hits = "", 0
    for family, names in r.signature.items():
        n = len(ingredients & set(names))
        if n > hits:
            best, hits = family, n
    if hits >= r.sig_min:
        return best, "by_signature_ingredients"

    return None


def build(dry_run: bool = False) -> CuisineStats:
    """전량 배정. 같은 시드·같은 데이터면 같은 결과가 나온다."""
    rules = Rules.load()
    rows = load_cuisine_source()
    st = CuisineStats(recipes=len(rows))

    pairs: list[tuple[int, str, float]] = []
    for rid, title, tags, ings in rows:
        hit = judge(title, set(tags or ()), set(ings or ()), rules)
        if hit is None:
            continue
        family, rule = hit
        pairs.append((rid, family, rules.conf.get(rule, 0.5)))
        st.by_family[family] = st.by_family.get(family, 0) + 1
        st.by_rule[rule] = st.by_rule.get(rule, 0) + 1

    st.assigned = len(pairs)
    if not dry_run:
        set_cuisine_families(pairs)
    return st


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="요리 계열 배정 (G-30 안 b)")
    ap.add_argument("--dry-run", action="store_true", help="세기만 하고 쓰지 않는다")
    a = ap.parse_args(argv)

    if a.dry_run:
        st = build(dry_run=True)
        logger.info("%s", st.report())
        logger.info("(미리보기입니다 — 아무것도 쓰지 않았습니다)")
        return 0

    with batch_run("cuisine", {"min_ratio": MIN_ASSIGNED_RATIO}) as rl:
        st = build()
        rl.input_count = st.recipes
        rl.output_count = st.assigned
        rl.params["ratio"] = round(st.ratio, 4)

    logger.info("%s", "─" * 60)
    logger.info("%s", st.report())
    if st.passed:
        logger.info("통과")
        return 0
    logger.error(
        "미달 — 배정률 %.1f%% (기준 %.0f%%) · 계열 %d종",
        st.ratio * 100,
        MIN_ASSIGNED_RATIO * 100,
        len(st.by_family),
    )
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level="INFO", format="%(message)s")
    sys.exit(main())
