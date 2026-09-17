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

import pydantic
import pytest

from features.recommend.enums import ALLERGEN_GROUPS
from features.recommend.schema import OnboardingIn

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


@pytest.mark.parametrize("bad", ["NUT", "nuts", "견과류", "WHEAT", "MILK", ""])
def test_onboarding_rejects_unknown_groups(bad: str) -> None:
    """DDL 주석이 지목한 실패 값들. 요청 단계에서 막아야 사용자가 원인을 압니다."""
    with pytest.raises(pydantic.ValidationError):
        OnboardingIn(allergy_groups=[bad], scales=[0, 0, 0], picks=[0])


def test_onboarding_accepts_the_canonical_ten() -> None:
    parsed = OnboardingIn(allergy_groups=list(ALLERGEN_GROUPS), scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == list(ALLERGEN_GROUPS)


def test_duplicates_are_folded() -> None:
    """같은 그룹을 두 번 고른 것은 한 번으로 둡니다 (preferred_cuisines 와 같은 취급)."""
    parsed = OnboardingIn(allergy_groups=["nut", "sesame", "nut"], scales=[0, 0, 0], picks=[0])
    assert parsed.allergy_groups == ["nut", "sesame"]
