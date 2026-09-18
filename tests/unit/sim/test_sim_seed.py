"""기획 xlsx → 시뮬 시드 변환기의 불변식 검사. DB 없이 돕니다.

검사 대상: scripts/sim/amplify_events.py · scripts/sim/convert_planning_data.py
픽스처: tests/fixtures/sim/planning_v0.4 (기획 v0.4 원본 11개 파일)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "tests" / "fixtures" / "sim" / "planning_v0.4"
SCRIPTS = ROOT / "scripts" / "sim"
N_WARM = 20


def run(script: str, *args: str) -> None:
    subprocess.run(  # noqa: S603  # 인자는 전부 이 파일이 정한 상수·경로
        [sys.executable, str(SCRIPTS / script), *args], check=True, capture_output=True
    )


@pytest.fixture(scope="module")
def seed_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    amp = tmp_path_factory.mktemp("amp")
    out = tmp_path_factory.mktemp("seed")
    run("amplify_events.py", "--src", str(SRC), "--out", str(amp))
    run("convert_planning_data.py", "--src", str(amp), "--out", str(out))
    return out


@pytest.fixture(scope="module")
def stats(seed_dir: Path) -> dict[str, int]:
    return json.loads((seed_dir / "stats.json").read_text(encoding="utf-8"))


def test_every_user_becomes_a_row(seed_dir: Path, stats: dict[str, int]) -> None:
    n_users = len(pd.read_excel(SRC / "users.xlsx"))
    assert stats["app_user"] == n_users
    sql = (seed_dir / "01_app_user.sql").read_text(encoding="utf-8")
    assert len(re.findall(r"'sim_u\d{5}'", sql)) == n_users
    assert "DELETE FROM app_user WHERE is_simulated" in sql  # 재실행 가능


def test_amplification_crosses_warm_threshold(stats: dict[str, int]) -> None:
    assert stats["mode_A_behavior"] > 0, "warm(behavior) 유저가 없으면 전환 시나리오를 못 돈다"
    assert stats["mode_B_onboarding"] == 800, "B 집단은 기획대로 이벤트가 없어야 한다"
    assert "mode_B_behavior" not in stats


def test_user_vector_invariants(seed_dir: Path) -> None:
    sql = (seed_dir / "03_user_vector.sql").read_text(encoding="utf-8")
    rows = re.findall(r"\((\d+), ARRAY\[([^\]]+)\]::real\[\], (\d+), (\d+), '(\w+)'", sql)
    assert len(rows) == 1600
    for _uid, vec, n_events, _n_pos, mode in rows:
        assert len(vec.split(",")) == 6
        ne = int(n_events)
        expected = "onboarding" if ne == 0 else ("behavior" if ne >= N_WARM else "blended")
        assert mode == expected


def test_pantry_names_exist_in_ingredient_seed(seed_dir: Path) -> None:
    names = set(pd.read_csv(ROOT / "seeds" / "ingredient.csv")["name"])
    sql = (seed_dir / "05_pantry_item.sql").read_text(encoding="utf-8")
    used = set(re.findall(r"WHERE name = '([^']+)'", sql))
    assert used and used <= names


def test_event_log_contract(seed_dir: Path) -> None:
    sql = (seed_dir / "06_event_log.sql").read_text(encoding="utf-8")
    types = set(re.findall(r"'RCP\d{4}', '(\w+)', 'd-", sql))
    assert types == {"click", "cook"}
    assert "impression" not in types  # 서버가 자동 기록. 시드가 넣지 않는다
    assert "RAISE EXCEPTION" in sql  # published 부족 시 적재 중단 가드


def test_engine_scenario_passes_without_a_db() -> None:
    """저장소의 시드를 엔진에 직접 넣는 시나리오. 시드 형식·RCP 매핑·불변식·콜드→웜 전환을 봅니다.

    앞 200명이면 A 집단의 웜 유저가 들어 있어 전환 시나리오까지 돕니다. 판정은 종료 코드입니다.
    """
    done = subprocess.run(  # noqa: S603  # 인자는 전부 이 파일이 정한 상수·경로
        [sys.executable, str(SCRIPTS / "scenario_engine.py"), "--limit", "200"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    assert "RESULT: PASS" in done.stdout


def test_committed_seed_matches_the_generator(seed_dir: Path) -> None:
    """저장소의 `deploy/seed/sim/*.sql` 은 생성물이고, 생성기와 같아야 합니다 (09-18).

    09-17 에 변환기에 `buckwheat` 이 들어갔는데 시드는 재생성되지 않아 11명의 메밀 알러지가
    저장소 시드에 없었습니다 — 시드가 결과물이라 diff 로는 안 보입니다. 줄 끝은 무시합니다
    (Windows 체크아웃이 CRLF 로 바꿉니다).
    """
    committed = ROOT / "deploy" / "seed" / "sim"
    for generated in sorted(seed_dir.glob("*.sql")):
        expected = (committed / generated.name).read_text(encoding="utf-8").splitlines()
        assert generated.read_text(encoding="utf-8").splitlines() == expected, generated.name
