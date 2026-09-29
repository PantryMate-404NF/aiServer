"""알레르기 라벨 → 막을 재료. 틀리면 사람이 다치는 자리입니다.

백엔드는 사용자의 알레르기를 `["우유", "땅콩"]` 처럼 한글 라벨로 보냅니다(2026-09-21 확인).
엔진이 아는 것은 재료 id 뿐이므로 라벨을 재료 집합으로 풀어야 합니다. 세 가지가 필요합니다.

1. **재료 → 알레르기 군.** 백엔드 재료 사전에는 그 칸이 없습니다. 우리 시드
   (`seeds/ingredient.csv` 의 `allergen_group`)를 이름으로 이어 씁니다. 이름이 다른 것은
   `BACKEND_NAME_ALIASES` 가 잇습니다.
2. **라벨 → 규칙.** 라벨 → 군 의 정본은 `enums.ALLERGEN_LABELS`(계약 어휘)이고, 여기는
   그 위에 동의어와 이름·제목 규칙만 얹습니다(`LABEL_RULES`).
3. **모르는 것을 숨기지 않습니다.** 풀지 못한 라벨과 시드에 없는 재료를 결과에 함께
   돌려줍니다. 조용히 버리면 그 사용자는 보호받지 못하는데 응답은 200 입니다.

## 넓게 막습니다

라벨이 시드의 군과 닿으면 **군 전체**를 막습니다. "새우" 를 고르면 조개류와 김치(젓갈)까지
막히고 "땅콩" 을 고르면 호두까지 막힙니다. 다만 군의 경계는 넘지 않습니다 — 오징어·낙지는
`mollusk` 로 갑각류·조개류(`shellfish`)와 다른 알레르기입니다(09-20 파트 A).

넓게 막는 이유는 덜 막아서 생기는 일(알레르기 반응)과 더 막아서 생기는 일(추천이 조금
줄어듦)의 무게가 다르기 때문입니다. 정밀하게 하려면 재료마다 알레르기를
여러 개 달 수 있어야 하고, 그것은 백엔드 재료 사전의 몫입니다(API 명세 5.3).

## 재료 목록만 믿지 않습니다

백엔드 실데이터에 **제목에는 있는데 재료 행에는 없는** 레시피가 있습니다. 09-21 에 새우
알레르기 사용자의 1위가 「고추장 건새우볶음」 이었습니다 — 그 레시피의 재료 행에 건새우가
없습니다. 제목에 알레르기 재료가 있는데 재료 목록에 없는 비율이 새우 2.8% · 오징어 7.8% ·
땅콩 12.9% 였습니다. 재료 id 로만 막으면 이 구멍은 어떤 라벨 표로도 안 메워집니다.

그래서 `blocks()` 는 둘을 봅니다 — 재료 id 가 겹치는가, **제목에 막힌 재료의 이름이 있는가.**
제목 쪽은 더 넓게 걸립니다("우삼겹" 이 돼지고기에 걸립니다). 넓게 막는 쪽이 맞습니다.

## 여기서 못 막는 것

간장·고추장의 밀, 굴소스의 대두처럼 **가공품 안에 든 다른 알레르기**는 시드가 재료당 군을
하나만 달고 있어 잡지 못합니다. 이 한계는 코드로 메우지 않습니다 — 여기서 따로 표를
들면 정본이 둘이 되고(D-27), 한쪽만 고쳐져도 에러가 나지 않습니다.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from features.recommend.enums import ALLERGEN_GROUPS, ALLERGEN_LABELS

#: 백엔드 재료 이름 → 시드 이름. 백엔드가 더 일반적인 이름을 씁니다(치즈 → 체다치즈).
#:
#: 주의: 여기 빠지면 그 재료의 알레르기 군이 **조용히 비어** 하드컷을 빠져나갑니다.
#:    09-21 에 `통깨` 가 그랬습니다 — 시드에 없는 이름이라 군이 비었고, 통깨는 레시피의
#:    32.8% 에 들어갑니다. 참깨 알레르기 사용자에게 그대로 나갈 자리였습니다.
#:    `test_allergy.py` 가 백엔드 재료 전부가 시드에 닿는지를 검사합니다.
BACKEND_NAME_ALIASES: Mapping[str, str] = {
    "통깨": "참깨",
    "고추": "청양고추",
    "액젓": "멸치액젓",
    "김치": "배추김치",
    "치즈": "체다치즈",
    "카레": "카레가루",
    "머스터드": "머스타드",
    "샐러드채소": "양상추",
    "라면": "라면사리",
    "슈가파우더": "설탕",
    "겨자": "연겨자",
    "계피": "계피가루",
    "코코아가루": "코코아파우더",
    "요거트": "플레인요거트",
    "마늘쫑": "마늘종",
    "진미채": "오징어채",
    "돈가스소스": "돈까스소스",
}


@dataclass(frozen=True)
class AllergenRule:
    """라벨 하나가 막는 범위. 셋은 합집합입니다."""

    #: 시드의 알레르기 군. 그 군의 재료 전부를 막습니다.
    groups: tuple[str, ...] = ()
    #: 재료 이름에 이 조각이 들어 있으면 막습니다. **짧은 조각을 넣지 않습니다** —
    #: "게" 는 "게맛살" 도 잡지만 무엇이든 잡고, "밀" 은 "메밀" 을, "콩" 은 "땅콩" 을 잡습니다.
    keywords: tuple[str, ...] = ()
    #: 재료 이름이 정확히 이것이면 막습니다. 조각으로 잡기 위험한 것은 여기 적습니다.
    names: tuple[str, ...] = ()
    #: **제목에서만** 찾는 말. 재료 사전에 없는 다른 말과 외래어입니다(계란 · 에그 · 쉬림프).
    title_extra: tuple[str, ...] = ()


#: 라벨 끝에 붙어 오는 말. "우유 알레르기" 를 "우유" 로 읽습니다.
_LABEL_SUFFIXES = ("알레르기", "알러지")


def normalize_label(label: str) -> str:
    """공백과 "알레르기" 꼬리를 떼고, 영문 코드는 소문자로. 표의 키도 같은 함수를 지납니다."""
    text = "".join(label.split())
    for suffix in _LABEL_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return text.lower() if text.isascii() else text


#: `enums.ALLERGEN_LABELS` 에 **없는** 다른 말. 같은 군으로 가는 동의어뿐입니다.
#:
#: 주의: 라벨 → 군 의 정본은 `enums.ALLERGEN_LABELS` 입니다(계약 어휘, 파트 A). 여기서 그
#:    표를 다시 적지 않습니다. 09-21 에 같은 표를 여기 따로 들고 있다가 셋이 어긋났습니다 —
#:    식약처 표기 `알류(가금류)` · `조개류(굴,전복,홍합 포함)` 를 이 모듈은 **모르는 라벨로
#:    돌려보냈고**, `오징어` 는 서로 다른 군으로 보냈습니다. 표가 둘이면 한쪽만 고쳐져도
#:    에러가 나지 않습니다(D-27). 아래 `_build_rules()` 에서 정본이 언제나 이깁니다.
_SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "난류": ("egg",),
    "달걀": ("egg",),
    "계란": ("egg",),
    "메추리알": ("egg",),
    "유제품": ("dairy",),
    "치즈": ("dairy",),
    "버터": ("dairy",),
    "아몬드": ("nut",),
    "견과": ("nut",),
    "콩": ("soy",),
    "두부": ("soy",),
    "밀가루": ("gluten",),
    "글루텐": ("gluten",),
    "깨": ("sesame",),
    "들깨": ("sesame",),
    "생선": ("fish",),
    "어류": ("fish",),
    "갑각류": ("shellfish",),
    "조개": ("shellfish",),
    "굴": ("shellfish",),
    "전복": ("shellfish",),
    "홍합": ("shellfish",),
    # 두족류는 갑각류·조개류와 다른 알레르기입니다(`enums` 의 mollusk 주석). 섞지 않습니다.
    "낙지": ("mollusk",),
    "문어": ("mollusk",),
    "주꾸미": ("mollusk",),
    "쭈꾸미": ("mollusk",),
    "연체류": ("mollusk",),
    "두족류": ("mollusk",),
    "해산물": ("fish", "shellfish", "mollusk"),
}

#: 군마다 **제목에서** 찾는 말. 재료 사전에 없는 다른 말과 외래어입니다.
#:
#: 주의: "잣" 을 넣지 않습니다. 실데이터에서 걸린 2건이 전부 「감잣국」 이었습니다. 한 글자
#:    조각은 다른 낱말 속에 들어갑니다. 잣은 재료 id 쪽(견과 군)에서 막힙니다.
_TITLE_WORDS: Mapping[str, tuple[str, ...]] = {
    "egg": ("계란", "에그", "오믈렛", "스크램블"),
    "dairy": ("크림", "라떼", "밀크", "치즈"),
    "nut": ("피넛", "캐슈", "피칸", "피스타치오", "잣죽"),
    "fish": ("생선", "연어", "갈치", "조기", "동태", "명태", "황태"),
    "shellfish": ("쉬림프", "감바스", "꽃게", "대게", "게장", "조개", "홍합", "전복"),
    "mollusk": ("쭈꾸미", "주꾸미", "낙지", "문어"),
}

#: 군마다 **재료 이름에서** 찾는 조각. 시드가 모르는 새 재료(칵테일새우)를 위한 안전판입니다.
_NAME_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "shellfish": ("새우",),
    "mollusk": ("오징어", "낙지", "문어"),
}

#: 군이 없어 **이름으로만** 막는 라벨. `enums.ALLERGEN_UNSUPPORTED` 가 "그룹으로는 못 막는다"
#: 고 적은 것들입니다. 온보딩 응답(`unmapped_allergens`)은 이 라벨을 못 막았다고 돌려주지만,
#: 추천 요청의 하드컷은 재료 이름으로 막습니다 — 약속보다 더 막는 쪽이라 안전한 방향입니다.
#: `아황산류` 는 재료 사전에 표제어가 없어 여기서도 못 막고 모르는 라벨로 돌려줍니다.
_NAME_ONLY: Mapping[str, AllergenRule] = {
    "토마토": AllergenRule(keywords=("토마토",), names=("케첩",)),
    "돼지고기": AllergenRule(
        keywords=("돼지",),
        names=("베이컨", "소시지", "햄", "스팸", "삼겹살", "목살"),
        title_extra=("삼겹", "제육", "돈까스", "돈가스", "족발", "보쌈", "수육"),
    ),
    "쇠고기": AllergenRule(keywords=("소고기", "쇠고기"), names=("다시다", "차돌박이")),
    "소고기": AllergenRule(keywords=("소고기", "쇠고기"), names=("다시다", "차돌박이")),
    "닭고기": AllergenRule(keywords=("닭",), title_extra=("치킨",)),
}


def _group_rule(groups: tuple[str, ...]) -> AllergenRule:
    return AllergenRule(
        groups=groups,
        keywords=tuple(word for group in groups for word in _NAME_KEYWORDS.get(group, ())),
        title_extra=tuple(word for group in groups for word in _TITLE_WORDS.get(group, ())),
    )


def _build_rules() -> dict[str, AllergenRule]:
    """동의어 → 계약 어휘 → 영문 코드 순으로 덮어씁니다. **뒤가 이기므로 정본이 이깁니다.**"""
    by_label: dict[str, tuple[str, ...]] = dict(_SYNONYMS)
    by_label.update({label: (code,) for label, code in ALLERGEN_LABELS.items()})
    by_label.update({code: (code,) for code in ALLERGEN_GROUPS})
    rules = {normalize_label(label): _group_rule(groups) for label, groups in by_label.items()}
    rules.update({normalize_label(label): rule for label, rule in _NAME_ONLY.items()})
    return rules


#: 라벨 → 규칙. 키는 `normalize_label()` 을 지난 모양입니다.
#:
#: 주의: 여기 없는 라벨은 `resolve()` 가 `unknown_labels` 로 돌려줍니다. 새 라벨을
#:    조용히 무시하면 그 사용자는 보호받지 못합니다.
LABEL_RULES: Mapping[str, AllergenRule] = _build_rules()


@dataclass(frozen=True)
class AllergyResolution:
    """라벨을 푼 결과. 막을 재료와 **풀지 못한 것**을 함께 담습니다."""

    blocked_ids: frozenset[int] = frozenset()
    #: 규칙에 없는 라벨. 비어 있지 않으면 그 사용자는 그만큼 보호받지 못합니다.
    unknown_labels: tuple[str, ...] = ()
    #: 라벨별로 무엇이 막혔는가. 로그와 검수용입니다.
    by_label: Mapping[str, frozenset[int]] = field(default_factory=dict)
    #: 제목에서 찾을 말 — 막힌 재료의 이름과 규칙의 조각들. `blocks()` 가 씁니다.
    title_keywords: tuple[str, ...] = ()


def load_seed_allergen_groups(path: Path) -> dict[str, str]:
    """`seeds/ingredient.csv` 에서 {재료 이름: 알레르기 군}. 군이 없는 재료는 빈 문자열입니다.

    빈 문자열과 "행이 없음" 은 다릅니다. 앞은 "알레르기 군이 없는 재료" 이고 뒤는 "모르는
    재료" 입니다. 그래서 군이 없어도 키는 남깁니다.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["name"]: row["allergen_group"].strip() for row in csv.DictReader(handle)}


def ingredient_groups(
    names: Mapping[int, str], seed_groups: Mapping[str, str]
) -> tuple[dict[int, str], tuple[str, ...]]:
    """백엔드 재료 id → 알레르기 군, 그리고 **시드에 닿지 못한 이름**.

    닿지 못한 재료는 알레르기를 모르는 재료입니다. 호출자는 그 목록이 비어 있는지 봐야
    합니다 — 비어 있지 않으면 하드컷에 구멍이 있습니다.
    """
    groups: dict[int, str] = {}
    unmapped: list[str] = []
    for ingredient_id, name in names.items():
        seed_name = name if name in seed_groups else BACKEND_NAME_ALIASES.get(name, "")
        if seed_name not in seed_groups:
            unmapped.append(name)
            continue
        groups[ingredient_id] = seed_groups[seed_name]
    return groups, tuple(sorted(unmapped))


def resolve(
    labels: Iterable[str],
    names: Mapping[int, str],
    groups: Mapping[int, str | Sequence[str]],
) -> AllergyResolution:
    """알레르기 라벨 → 막을 재료 id. 모르는 라벨은 버리지 않고 돌려줍니다.

    `names` 는 백엔드 재료 id → 이름, `groups` 는 재료 id → 알레르기 군입니다. 군은 하나일
    수도(`ingredient_groups()` 의 결과, AI 쪽 시드) 여럿일 수도(백엔드의 `allergens` 배열)
    있습니다 — 간장은 대두이면서 밀입니다. 여럿이면 하나만 걸려도 막습니다.
    이름 규칙(조각·정확)은 시드에 닿지 못한 재료에도 걸립니다 — 군을 몰라도 이름은 압니다.
    """
    blocked: set[int] = set()
    unknown: list[str] = []
    by_label: dict[str, frozenset[int]] = {}
    in_title: set[str] = set()
    for raw in labels:
        label = normalize_label(raw)
        if not label:
            continue
        rule = LABEL_RULES.get(label)
        if rule is None:
            unknown.append(raw)
            continue
        hit = frozenset(
            ingredient_id
            for ingredient_id, name in names.items()
            if any(group in rule.groups for group in _as_groups(groups.get(ingredient_id)))
            or name in rule.names
            or any(keyword in name for keyword in rule.keywords)
        )
        by_label[label] = hit
        blocked |= hit
        in_title.update(rule.keywords, rule.names, rule.title_extra)
    in_title.update(names[ingredient_id] for ingredient_id in blocked)
    return AllergyResolution(
        blocked_ids=frozenset(blocked),
        unknown_labels=tuple(unknown),
        by_label=by_label,
        title_keywords=tuple(sorted(word for word in in_title if word)),
    )


def _as_groups(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def blocks(resolution: AllergyResolution, ingredient_ids: frozenset[int], title: str) -> bool:
    """이 레시피를 막아야 하는가. 재료 id 가 겹치거나 **제목에 막힌 재료가 적혀 있으면** 막습니다.

    ① 조회가 후보를 고를 때 이 함수 하나로 거릅니다. 재료 목록만 보면 실데이터의 빠진
    재료 행을 놓칩니다(모듈 설명). 제목 검사는 더 넓게 걸리며 그것이 의도입니다.
    """
    if ingredient_ids & resolution.blocked_ids:
        return True
    return any(word in title for word in resolution.title_keywords)
