"""알러지 그룹 어휘가 DDL 과 어긋나지 않는지. DB 없이 파일만 읽습니다.

## 왜 검사가 필요한가

09-17 에 저장소 안에서 어휘가 **세 갈래**였습니다.

    deploy/init/02_schema.sql          소문자 10종      ← 정본. DB 가 CHECK 로 막는다
    B 문서                              19종
    scripts/generate_mock_fixtures.py  대문자 18종      EGG·MILK·WHEAT…

겹치는 `PEACH`·`SHELLFISH`·`BUCKWHEAT` 조차 대소문자가 달랐습니다. mock 생성기는
DB 를 거치지 않아 CHECK 에 안 걸리고, 그래서 **조용히** 갈렸습니다.

DDL 주석이 적어 둔 대로 오타 하나가 알러지를 통째로 무력화합니다 — '견과류'·'NUT'·
'nuts' 를 넣으면 차단 재료가 0종이 되고 에러도 없습니다. 어휘가 갈리는 것 자체를
검사로 막습니다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from features.recommend.enums import (
    ALLERGEN_GROUPS,
    ALLERGEN_LABELS,
    ALLERGEN_UNSUPPORTED,
    normalize_allergen,
)
from features.recommend.schema import OnboardingIn

#: 온보딩 화면이 내놓는 알러지 선택지. 식약처 표시 대상 19종입니다.
#: 이 목록이 바뀌면 아래 검사가 먼저 빨개져야 합니다 — 화면만 고치고 서버가 모르면
#: 그 항목을 고른 사용자는 차단이 안 된 채 성공 응답을 받습니다.
SCREEN_LABELS: tuple[str, ...] = (
    "알류(가금류)",
    "우유",
    "메밀",
    "땅콩",
    "대두",
    "밀",
    "고등어",
    "게",
    "새우",
    "돼지고기",
    "복숭아",
    "토마토",
    "호두",
    "아황산류",
    "닭고기",
    "쇠고기",
    "오징어",
    "조개류(굴,전복,홍합 포함)",
    "잣",
)

DDL = Path(__file__).resolve().parents[3] / "deploy" / "init" / "02_schema.sql"


def _ddl_allergens() -> set[str]:
    """02_schema.sql 의 user_allergy CHECK 에서 허용 값을 뽑습니다."""
    text = DDL.read_text(encoding="utf-8")
    match = re.search(r"allergen_group IS NULL OR allergen_group IN \(([^)]*)\)", text, re.S)
    assert match, "user_allergy 의 allergen_group CHECK 를 못 찾았습니다"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_enum_matches_the_ddl_check() -> None:
    """정본은 DDL 입니다. 여기가 갈리면 요청은 통과하고 INSERT 가 터집니다."""
    assert set(ALLERGEN_GROUPS) == _ddl_allergens()


def test_no_duplicates() -> None:
    assert len(ALLERGEN_GROUPS) == len(set(ALLERGEN_GROUPS))


def test_the_sim_script_copy_matches() -> None:
    """`scripts/sim/convert_planning_data.py` 는 src 를 import 하지 않아 사본을 듭니다.

    주의: 09-17 에 그 사본에 buckwheat 이 빠져 있었습니다. 값이 전부 정본의
       부분집합이라 DDL CHECK 를 어기지 않고, 시뮬 유저에게 메밀 알러지가
       영영 안 생기는 것으로만 나타났습니다 — 아무도 안 알아챕니다.
    """
    src = (
        Path(__file__).resolve().parents[3] / "scripts" / "sim" / "convert_planning_data.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"ALLERGEN_GROUPS = \[(.*?)\]", src, re.S)
    assert match, "convert_planning_data 의 ALLERGEN_GROUPS 를 못 찾았습니다"
    assert set(re.findall(r'"([a-z_]+)"', match.group(1))) == set(ALLERGEN_GROUPS)


@pytest.mark.parametrize("bad", ["nuts", "WHEAT", "MILK", "글루텐프리"])
def test_unknown_groups_are_reported_not_rejected(bad: str) -> None:
    """모르는 값은 버리되 조용히 버리지 않습니다.

    예전에는 여기서 요청 전체를 거부했습니다. 그러면 알러지만 빠지는 게 아니라
    picks·scales 까지 사라지고, 그 사용자는 알러지 0건이 되어 차단이 아예 꺼집니다.
    """
    parsed = OnboardingIn(allergy_groups=["dairy", bad], scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == ["dairy"], "아는 값은 살아남아야 합니다"
    assert parsed.unmapped_allergens == [bad], "모르는 값은 돌려줘야 합니다"


def test_blank_entries_are_dropped_quietly() -> None:
    """빈 문자열은 사용자의 뜻이 아니라 호출 쪽 실수입니다. 보고 목록을 더럽히지 않습니다."""
    parsed = OnboardingIn(allergy_groups=["dairy", "", "  "], scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == ["dairy"]
    assert parsed.unmapped_allergens == []


def test_case_variants_of_a_canonical_code_resolve() -> None:
    """`NUT` 은 코드의 대소문자 변형일 뿐입니다. normalize_cuisine 과 같은 취급입니다."""
    parsed = OnboardingIn(allergy_groups=["NUT"], scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == ["nut"]
    assert parsed.unmapped_allergens == []


def test_korean_labels_resolve_to_codes() -> None:
    parsed = OnboardingIn(allergy_groups=["우유", "메밀", "잣"], scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == ["dairy", "buckwheat", "nut"]
    assert parsed.unmapped_allergens == []


def test_label_table_points_only_at_canonical_groups() -> None:
    """표가 정본 밖의 코드를 가리키면 DB CHECK 에서 INSERT 가 터집니다."""
    assert set(ALLERGEN_LABELS.values()) <= set(ALLERGEN_GROUPS)


def test_label_and_unsupported_do_not_overlap() -> None:
    """같은 표기가 양쪽에 있으면 막히는지 아닌지를 읽는 사람이 알 수 없습니다."""
    assert not (set(ALLERGEN_LABELS) & set(ALLERGEN_UNSUPPORTED))


def test_every_screen_label_is_either_mapped_or_declared_unsupported() -> None:
    """화면 선택지 19종이 전부 표에 있어야 합니다.

    빠진 것이 있으면 그 항목을 고른 사용자는 아무 경고 없이 차단에서 빠집니다.
    """
    known = set(ALLERGEN_LABELS) | set(ALLERGEN_UNSUPPORTED)
    missing = [label for label in SCREEN_LABELS if label not in known]
    assert not missing, f"표에 없는 화면 선택지: {missing}"


def test_squid_is_not_mapped_to_shellfish() -> None:
    """오징어를 shellfish 로 보내면 막혔다고 믿는 사용자에게 두족류가 그대로 나갑니다.

    shellfish 그룹에 두족류가 한 종도 없어, 두족류 레시피 1,141건 중 841건이
    차단을 켜도 지나갑니다. 과소차단이라 거부보다 위험합니다.
    """
    assert normalize_allergen("오징어") is None
    assert "오징어" in ALLERGEN_UNSUPPORTED


def test_unsupported_entries_survive_as_a_report() -> None:
    """못 막는 것도 응답에 남아야 합니다. 조용히 사라지면 아무도 모릅니다."""
    parsed = OnboardingIn(
        allergy_groups=["우유", "아황산류", "돼지고기"], scales=[0, 0, 0], picks=[0]
    )
    assert parsed.allergy_groups == ["dairy"]
    assert parsed.unmapped_allergens == ["아황산류", "돼지고기"]


def test_the_mock_catalog_speaks_the_same_vocabulary() -> None:
    """09-18 부터 Mock 생성기는 그룹을 `seeds/ingredient.csv` 에서 읽습니다 (파트 B).

    여기가 갈리면 Mock 페르소나의 알러지 컷이 실 DB 와 다른 재료를 자릅니다.
    """
    catalog = json.loads(
        (Path(__file__).resolve().parents[2] / "fixtures" / "recommend" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    groups = set(catalog["allergen_groups"])
    assert groups and groups <= set(ALLERGEN_GROUPS), groups - set(ALLERGEN_GROUPS)


def test_onboarding_accepts_the_canonical_ten() -> None:
    parsed = OnboardingIn(allergy_groups=list(ALLERGEN_GROUPS), scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == list(ALLERGEN_GROUPS)


def test_duplicates_are_folded() -> None:
    """같은 그룹을 두 번 고른 것은 한 번으로 둡니다 (preferred_cuisines 와 같은 취급)."""
    parsed = OnboardingIn(allergy_groups=["nut", "sesame", "nut"], scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == ["nut", "sesame"]
