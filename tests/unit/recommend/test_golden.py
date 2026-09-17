"""골든 픽스처 계약 — DB 없이 A 의 산출물이 모양을 지키는지 봅니다.

`tests/fixtures/recommend/feature_golden.json` 은 B 의 스코어러와 C 의 평가 하네스가
**DB 없이** 쓰는 실데이터 30건입니다. 그 파일이 계약을 깨면 여기서 빨개집니다.

## 왜 make data-gate 로 부족한가

`make golden-check` 가 이미 같은 파일을 보지만 **DB 를 요구합니다**. 그래서
CLAUDE.md 4절의 완료 게이트(`uv run pytest tests/unit`)와 B·C 가 돌리는 검사가
이 파일을 한 번도 지나지 않습니다.

검수 시트를 시드에 넣으면 `feature_version` 이 바뀌고 피처 값이 같이 움직이는데,
**DB 없이 그 회귀를 잡을 수 있는 자산이 이 파일뿐입니다.**

## 값이 아니라 모양을 봅니다

레시피가 늘거나 사전이 바뀌면 값은 당연히 바뀝니다. 여기서 고정하는 것은
B·C 가 코드에서 전제하는 **구조**입니다 — 키 14개, 6축, 경계 케이스의 존재.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "recommend" / "feature_golden.json"

#: ingest/golden.py 의 KEYS 와 같아야 합니다. 둘이 어긋나면 픽스처를 만든 쪽과
#: 읽는 쪽이 다른 계약을 보는 것이라, 여기서 먼저 빨개지는 편이 낫습니다.
EXPECTED_KEYS = {
    "recipe_id",
    "essential_ids",
    "all_ids",
    "category_ids",
    "n_essential",
    "n_total",
    "n_unmatched",
    "flavor_vec",
    "popularity_score",
    "quality_score",
    "cook_minutes",
    "difficulty",
    "cluster_id",
    "feature_version",
}

#: 02_schema.sql 의 CHECK 와 같은 패턴입니다.
VERSION_RE = re.compile(r"^(v[0-9]|test-)")


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    if not FIXTURE.exists():  # pragma: no cover - 파일이 없으면 생성부터 해야 합니다
        pytest.fail(f"골든 픽스처가 없습니다: {FIXTURE}\n  먼저:  make golden-build")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_keys_match_the_contract(golden: dict[str, Any]) -> None:
    """키가 늘거나 줄면 B 의 스코어러가 KeyError 로 죽습니다."""
    assert set(golden["keys"]) == EXPECTED_KEYS


def test_every_recipe_has_every_key(golden: dict[str, Any]) -> None:
    want = set(golden["keys"])
    for row in golden["recipes"]:
        assert set(row) == want, f"recipe_id {row.get('recipe_id')} 의 키가 다릅니다"


def test_flavor_vec_is_six_axes(golden: dict[str, Any]) -> None:
    """D-11 이 6축을 유지하기로 했습니다. 길이가 바뀌면 코사인이 조용히 틀립니다."""
    assert len(golden["flavor_mu"]) == 6
    for row in golden["recipes"]:
        assert len(row["flavor_vec"]) == 6, f"recipe_id {row['recipe_id']}"


def test_counts_match_the_arrays(golden: dict[str, Any]) -> None:
    """n_essential·n_total 이 배열 길이와 어긋나면 coverage 계산이 틀립니다."""
    for row in golden["recipes"]:
        assert row["n_essential"] == len(row["essential_ids"]), f"recipe_id {row['recipe_id']}"
        assert row["n_total"] == len(row["all_ids"]), f"recipe_id {row['recipe_id']}"


def test_essential_is_a_subset_of_all(golden: dict[str, Any]) -> None:
    for row in golden["recipes"]:
        assert set(row["essential_ids"]) <= set(row["all_ids"]), f"recipe_id {row['recipe_id']}"


def test_boundary_cases_are_present(golden: dict[str, Any]) -> None:
    """정상 케이스만 담으면 픽스처의 뜻이 없습니다.

    B 의 스코어러는 아래를 만나면 예외가 아니라 **NaN 이나 만점을 조용히** 냅니다.
    그래서 골든이 이것들을 반드시 들고 있어야 합니다 (ingest/golden.py 의 BUCKETS).
    """
    rows = golden["recipes"]
    zero_essential = [r for r in rows if r["n_total"] > 0 and r["n_essential"] == 0]
    zero_flavor = [r for r in rows if not any(r["flavor_vec"])]
    no_cooktime = [r for r in rows if r["cook_minutes"] is None]

    assert len(zero_essential) >= 5, "필수재료 0개 — coverage 가 1.0 만점이 되는 경로"
    assert len(zero_flavor) >= 1, "맛 벡터 전부 0 — 코사인 분모가 0 이 되는 경로"
    assert len(no_cooktime) >= 1, "조리시간 NULL — 시간 필터가 비교를 못 하는 경로"


def test_feature_version_matches_the_schema_check(golden: dict[str, Any]) -> None:
    """`test-` 로 시작하면 조회가 자동으로 뺍니다 (⓪' 격리 게이트).

    골든은 실배치 산출물이라 `test-` 여서는 안 됩니다 — 그러면 B 가 이 픽스처로
    맞춘 코드가 실서빙에서 한 건도 안 만나게 됩니다.
    """
    for row in golden["recipes"]:
        version = row["feature_version"]
        assert VERSION_RE.match(version), f"스키마 CHECK 위반: {version!r}"
        assert not version.startswith("test-"), f"실배치 픽스처에 합성 접두어: {version!r}"
        assert len(version) <= 16, f"VARCHAR(16) 초과: {version!r}"


def test_stats_version_is_recorded(golden: dict[str, Any]) -> None:
    """어느 μ 로 만든 픽스처인지 남아야 f_taste 를 재현할 수 있습니다."""
    assert isinstance(golden["stats_version"], int)
    assert golden["stats_version"] > 0
    assert golden["mu_n_recipes"] > 0
