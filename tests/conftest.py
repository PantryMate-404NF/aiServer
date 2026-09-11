"""모든 테스트가 공유하는 픽스처.

## 데이터 파트의 단독 실행 스크립트는 pytest 가 수집하지 않습니다

아래 `collect_ignore` 에 적힌 파일들은 pytest 함수가 아니라 **단독 실행
스크립트**입니다. 모듈 최상단에서 검사를 돌리고 `sys.exit()` 로 끝내므로,
pytest 가 수집하는 순간 `SystemExit` 이 올라와 **실행 전체가 INTERNALERROR 로
죽습니다** (09-04 실측). 이 검사들은 `make contract` · `make log-test` ·
`make normalize-test` · `make smoke` · `make ddl-test` 가 돌립니다 —
데이터 파트 기준 합계 242건입니다. 이 가운데 실 DB 가 필요 없는 125건은
2026-09-10 병합에서 직접 돌려 통과를 확인했습니다.

**디렉터리를 통째로 빼지 않고 파일 이름을 적습니다.** `unit/recommend/*.py` 로
빼면 같은 디렉터리에 있는 추천 엔진의 pytest 검사 63건과
`integration/test_receipt_pipeline.py` 까지 함께 사라집니다. 통과 건수가 줄어드는
것이 아니라 **애초에 세어지지 않아서** 깨진 것을 알아챌 수 없습니다. 파일을
새로 추가하는 쪽이 여기 한 줄을 적는 것보다 낫습니다.

⬜ 단독 스크립트를 pytest 함수로 옮기는 것은 별도 작업입니다 (회의 안건 G-08).
"""

from __future__ import annotations

import pytest

import config
from infra import db

#: 모듈을 읽는 것만으로 `sys.exit()` 이 올라오거나 실제 DB 를 물고 도는 파일들.
collect_ignore = [
    "unit/recommend/run.py",
    "unit/recommend/test_batch.py",
    "unit/recommend/test_contract.py",
    "unit/recommend/test_match.py",
    "unit/recommend/test_role.py",
    "unit/recommend/test_writer.py",
    "integration/test_ddl.py",
    "integration/test_smoke.py",
]

ENV = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "aiserver_test",
    "DB_USER": "tester",
    "DB_PASSWORD": "secret",
    "LOG_LEVEL": "DEBUG",
    "INTERNAL_API_KEY": "test-internal-key",
    "GEMINI_API_KEY": "test-gemini-key",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """환경변수를 세우고 설정 캐시를 비웁니다. 앞 테스트의 값이 새지 않게 합니다.

    정리는 하지 않습니다. 테스트가 캐시 함수 자체를 monkeypatch 하면
    teardown 에서 cache_clear 를 부를 수 없기 때문입니다.
    """
    # 개발자 머신의 .env 를 읽지 않게 막습니다. README 대로 .env 를 만들어 둔 사람은
    # 이 줄이 없으면 필수 키 누락 테스트가 그 파일의 값을 읽어 통과해 버립니다.
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_session_factory.cache_clear()
