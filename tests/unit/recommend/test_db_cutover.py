"""DB 전환 점검표가 잊히지 않게 막는 장치입니다.

지금 추천 엔진은 실 DB 없이 돕니다. 그래서 목업·기본값·빈 이력으로 채워 둔 자리가
여럿 있고, **그 자리들은 전부 에러를 내지 않습니다.** DB 를 붙이는 순간 조용히 틀린
값을 내거나 아무 일도 하지 않게 됩니다.

아래 검사는 그 자리들이 **아직 전환 전 상태임을 못 박습니다.** 누군가 그중 하나를
건드리면 검사가 깨지고, 실패 메시지가 점검표를 가리킵니다. 점검표를 읽지 않고
전환하는 경로를 없애는 것이 목적입니다.

점검표 원본: `docs/recommend/recommend_engine_db_cutover.md`

## 이 검사가 깨졌을 때 할 일

1. 위 문서에서 해당 항목(M-NN)을 엽니다.
2. 그 항목의 "확인 근거" 를 실제로 돌려 통과시킵니다.
3. 문서의 상태를 바꾸고, 여기 있는 못을 그 항목의 완료 조건을 재는 검사로 바꿉니다.

**못을 그냥 지우지 않습니다.** 지우면 다음 사람이 같은 자리를 다시 밟습니다.
"""

from __future__ import annotations

import importlib
import inspect
import tomllib
from pathlib import Path

import pytest

from config import get_settings
from features.recommend import service
from features.recommend.engine import rerank
from features.recommend.engine.context import build_context
from features.recommend.policy import RankingPolicy
from features.recommend.schema import HealthOut

#: 점검표에 있는 항목 전부. 문서와 이 목록이 어긋나면 아래 검사가 잡습니다.
CUTOVER_IDS = tuple(f"M-{n:02d}" for n in range(1, 17))

CHECKLIST = Path(__file__).resolve().parents[3] / "docs/recommend/recommend_engine_db_cutover.md"

HOWTO = "docs/recommend/recommend_engine_db_cutover.md 의 {item} 을 처리한 뒤 이 검사를 갱신하세요."


def test_the_checklist_lists_every_item() -> None:
    """문서와 이 파일이 같은 항목을 봅니다. 한쪽만 늘면 나머지가 잊힙니다."""
    text = CHECKLIST.read_text(encoding="utf-8")
    missing = [item for item in CUTOVER_IDS if item not in text]
    assert not missing, f"점검표에 없는 항목: {missing}"


def test_router_still_serves_the_mock() -> None:
    """M-01. 라우터가 아직 `engine/mock.py` 를 부릅니다.

    실엔진에 연결하면 이 검사가 깨집니다. 그때 함께 처리해야 하는 것이
    M-02(후보 조회) · M-05(로그 적재) · M-06(난수 시드)입니다. 셋 중 하나라도
    빠지면 응답은 200 인데 로그가 비거나 재현이 안 됩니다.
    """
    router_module = importlib.import_module("features.recommend.router")
    assert "mock." in inspect.getsource(router_module), HOWTO.format(item="M-01")


def test_the_engine_does_not_write_logs_yet() -> None:
    """M-05. `service` 가 아직 `write_recommendation` 을 부르지 않습니다.

    부르기 시작하면 `config_hash`·`warm_alpha`·`stats_version` 을 함께 넘겨야
    합니다. 안 넘겨도 행은 저장되고 `not_reproducible` 플래그만 붙습니다 —
    에러가 나지 않으므로 그 요청의 점수는 영영 재현되지 않습니다.
    """
    assert "write_recommendation" not in inspect.getsource(service), HOWTO.format(item="M-05")


def test_no_repository_function_fills_the_user_history() -> None:
    """M-03. 사용자 이력을 읽어 오는 저장소 함수가 아직 없습니다.

    없는 동안 `f_ing_pref`·`f_cooccur` 는 전건 None 이고 가중치 0.21 이 순위에
    관여하지 않습니다. 붙는 순간 추천 결과가 바뀌므로 그때 Mock 판정을 다시
    재야 합니다.
    """
    repository = importlib.import_module("features.recommend.repository")
    loaders = [
        name
        for name in dir(repository)
        if not name.startswith("_") and ("history" in name.lower() or "user_pref" in name.lower())
    ]
    assert not loaders, HOWTO.format(item="M-03") + f" (발견: {loaders})"


def test_the_logged_seed_is_the_seed_that_was_used() -> None:
    """M-06. `rank_candidates` 는 추적의 `rng_seed` 로 난수원을 직접 만듭니다.

    호출자가 난수원을 따로 넘기던 때는 로그의 시드로 재현이 안 됐습니다(평가 스크립트가
    실제로 `SystemRandom` 을 넘기며 `rng_seed=user_id` 를 적었습니다). 남은 것은 라우터가
    요청마다 시드를 정해 넘기고 응답과 함께 남기는 일입니다.
    """
    assert "rng" not in inspect.signature(service.rank_candidates).parameters, HOWTO.format(
        item="M-06"
    )
    assert "rng_seed" in inspect.signature(service.rank_candidates).parameters
    assert "rng_seed" not in inspect.signature(rerank.rerank).parameters, HOWTO.format(item="M-06")


def test_settings_and_policy_hold_the_same_numbers() -> None:
    """M-08. 같은 손잡이가 `Settings` 와 `RankingPolicy` 두 곳에 있습니다.

    엔진은 `RankingPolicy` 만 읽습니다. `.env` 로 `RECO_CANDIDATE_LIMIT` 을 바꿔도
    **아무 일도 일어나지 않고 에러도 나지 않습니다.** 정본을 하나로 합칠 때까지는
    최소한 두 값이 갈라지지 않게 여기서 붙잡습니다.
    """
    settings, policy = get_settings(), RankingPolicy()
    for name in ("candidate_limit", "explore_pool_size", "propensity_mc"):
        assert getattr(settings, name) == getattr(policy, name), (
            f"{name} 이 Settings 와 RankingPolicy 에서 다릅니다. " + HOWTO.format(item="M-08")
        )


def test_the_failure_counters_leave_the_process() -> None:
    """M-07 (2026-09-22 처리). 로그 쓰기 실패 카운터가 `/metrics` 로 나갑니다.

    `write_recommendation` 은 모든 예외를 삼키고 카운터만 올립니다. 그 카운터를 읽는 곳이
    없어서, DB 를 붙인 뒤 적재가 전부 실패해도 API 는 200 이고 아무도 몰랐습니다. 이제 앱이
    뜰 때 `service.counters` 를 지표 수집기에 등록합니다 — 이 줄이 빠지면 다시 아무도 모릅니다.
    """
    main_module = importlib.import_module("main")

    assert "watch_counters(recommend_service.counters)" in inspect.getsource(main_module)


def test_persona_originals_still_live_in_the_json_store() -> None:
    """M-14. 취향 원본을 DB 에서 읽는 저장소 함수가 아직 없습니다.

    `profile_store.JsonProfileStore` 가 정본입니다. `user_vector`·`event_log` 로 옮길 때
    JSON 의 이벤트를 먼저 옮기고, 두 저장소가 같은 페르소나를 내는지 대조한 뒤 바꿉니다.
    """
    repository = importlib.import_module("features.recommend.repository")
    loaders = [
        name
        for name in dir(repository)
        if not name.startswith("_") and ("profile" in name.lower() or "persona" in name.lower())
    ]
    assert not loaders, HOWTO.format(item="M-14") + f" (발견: {loaders})"


def test_the_chosen_cuisines_still_come_from_the_json_store() -> None:
    """M-16. 음식 유형을 `user_preference.pref_cuisines` 에서 읽는 저장소 함수가 아직 없습니다.

    지금은 취향 원본 JSON 의 `cuisines` 가 유일한 출처이고, `build_context` 는 인자를 안 받으면
    거기로 되돌아갑니다. DB 를 붙이면 그 JSON 이 없으므로, 읽어 넘기지 않는 순간 **모든 사용자가
    유형을 고른 적 없는 사람**이 됩니다 — 유형 슬롯도 `f_cuisine` 도 아무 일을 하지 않고 응답은
    200 입니다. M-14 와 같은 변경에서 처리합니다.
    """
    repository = importlib.import_module("features.recommend.repository")
    loaders = [
        name for name in dir(repository) if not name.startswith("_") and "cuisine" in name.lower()
    ]
    assert not loaders, HOWTO.format(item="M-16") + f" (발견: {loaders})"
    # 되돌아가는 자리 자체가 사라지면(인자 필수) 그때도 이 못을 갱신합니다.
    assert "preferred_cuisines" in inspect.signature(build_context).parameters, HOWTO.format(
        item="M-16"
    )


def test_onboarding_and_events_routes_still_serve_the_mock() -> None:
    """M-15. 온보딩과 이벤트 라우트가 아직 `PersonaService` 를 부르지 않습니다.

    연결하면 `save_onboarding` 의 인덱스 검증과 `record_events` 의 카운터가 실서빙에
    들어갑니다. 라우터 실연결(M-01)과 같은 변경에서 처리합니다.
    """
    router_module = importlib.import_module("features.recommend.router")
    assert "PersonaService" not in inspect.getsource(router_module), HOWTO.format(item="M-15")


@pytest.mark.parametrize("name", ["f_ing_pref", "f_cooccur", "f_season"])
def test_features_without_a_data_source_stay_weighted(name: str) -> None:
    """M-03·M-04. 데이터가 없는 피처의 가중치를 0 으로 내리지 않습니다.

    Zero-Drop 이 분자와 분모에서 함께 빼므로 남은 가중치가 비례 재분배됩니다.
    0 으로 내리면 데이터가 와도 켜지지 않습니다 — 그때 코드를 고쳐야 하는데,
    고쳐야 한다는 사실을 아무도 기억하지 못합니다.
    """
    from features.recommend.enums import DEFAULT_WEIGHTS

    assert DEFAULT_WEIGHTS[name] > 0, HOWTO.format(item="M-03")


def test_health_redis_is_still_an_unverified_true() -> None:
    """M-11. `HealthOut.redis` 의 기본값이 참인 채입니다.

    실제로 확인하거나 필드를 빼면 여기가 깨지고, 그때 점검표 M-11 을 닫습니다.
    """
    assert HealthOut.model_fields["redis"].default is True, HOWTO.format(item="M-11")


def test_coverage_still_omits_the_four_tool_files() -> None:
    """M-13. 커버리지 측정 범위에서 뺀 네 파일입니다.

    하나라도 되돌리면 점검표 M-13 과 함께 봅니다.
    """
    root = Path(__file__).resolve().parents[3]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    omitted = [str(entry) for entry in config["tool"]["coverage"]["run"]["omit"]]
    names = {Path(entry).name for entry in omitted}
    assert {"repository.py", "repository_ingest.py", "mock.py"} <= names, HOWTO.format(item="M-13")
    assert any(entry.rstrip("*").endswith("ingest/") for entry in omitted), HOWTO.format(
        item="M-13"
    )
