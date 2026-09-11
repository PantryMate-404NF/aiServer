"""환경변수를 읽어 설정 객체로 만드는 단일 진입점.

03 의 2절 — **이 파일 하나가 환경변수를 읽습니다.** 다른 파일에서 `os.environ`
을 호출하지 않습니다. 없는 키는 처리 중간이 아니라 **실행 시작 시점에** 터져야
합니다. 중간에 터지면 어느 입력에서 실패했는지 추적이 어려워집니다.

    from config import get_settings
    get_settings().candidate_limit

🔴 **비밀값은 여기 기본값으로 두지 않습니다.** `.env` 에만 두고 환경변수로
   읽습니다. 특히 `REVIEW_SALT` 는 후기 624,422건의 작성자 해시를 만든 값이라
   기본값을 주면 "없어도 도는" 착각을 만듭니다 — 없으면 없다고 말해야 합니다.

튜닝 상수도 전부 여기 있습니다. 임계값·가중치·타임아웃처럼 데이터와 환경에
따라 다시 맞춰야 하는 값을 코드에 박으면 조정 자체를 막습니다 (03 의 2절).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """실행 시작 시점에 필수 환경변수 누락을 검출합니다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── DB ────────────────────────────────────────────────────────
    # 주의: 기본값을 주지 않습니다. 없으면 시작 시점에 터져야 합니다.
    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    #: 이 애플리케이션의 테이블이 사는 스키마. Engine 이 커넥션마다 걸어 준다.
    db_schema: str = "reco"

    log_level: str = "INFO"
    internal_api_key: str

    # 영수증 OCR. 워커 하나가 상주 메모리 약 2.5GB 를 쓰므로 2코어 8GB 서버는 1 입니다.
    ocr_workers: int = 3
    # 업로드 상한은 FE·BE 가 앞단에서 거르지만, 계약을 어긴 요청이 와도 서버가
    # 버텨야 하므로 같은 값을 여기에도 둡니다.
    max_upload_bytes: int = 10 * 1024 * 1024
    # ULID 는 26자입니다. 여유를 두되 무한정 받지는 않습니다.
    max_receipt_id_length: int = 64
    # 총 화소가 이 값을 넘으면 비율을 유지한 채 줄여서 인식합니다. 긴 변이 아니라 총 화소로
    # 재는 이유는 영수증이 세로로 길고 좁아 긴 변 기준이 폭을 뭉개기 때문입니다.
    ocr_max_pixels: int = 1_500_000
    # 국소 대비 보정 강도와 타일 크기. 영수증 13장 비교에서 정한 값이며 감열지에서
    # 글자를 살리면서 배경 노이즈를 과하게 키우지 않는 지점입니다.
    ocr_clahe_clip_limit: float = 2.0
    ocr_clahe_tile_grid: int = 8
    # y 중심이 글자 높이의 이 배수 안이면 같은 줄로 봅니다.
    ocr_same_line_height_ratio: float = 0.6
    # 기동 시 워커 하나가 예열 작업을 붙잡고 있는 시간. 이 시간이 0 이면 먼저 뜬 워커가
    # 예열 작업을 전부 집어가 나머지 워커가 뜨지 않습니다.
    ocr_warmup_hold_sec: float = 0.3

    # 후처리 LLM
    gemini_api_key: str
    gemini_model: str = "gemini-3.5-flash-lite"
    # Gemini API 가 10초 미만의 데드라인을 400 으로 거부합니다. 이 값이 하한입니다.
    # 백엔드 타임아웃 30초 = OCR 최악 10초 + LLM 최악 11초 + 여유입니다.
    llm_timeout_sec: int = 10
    llm_max_retries: int = 1
    llm_backoff_base_sec: float = 0.5
    # v2 는 v1 에서 주류를 비식재료로 옮긴 것이고, v3 은 이름 교정을 금지해 OCR 원문을
    # 그대로 내보냅니다. 오타를 LLM 이 짐작해 고치면 원문이 사라져 사전 매칭이 손댈 것이
    # 없어집니다. v4 는 판정 기준을 정답 셋(ocr_poc/eval/ground_truth.json)에 맞춘
    # 것입니다. 담배와 그 자리에서 먹는 완제품을 비식재료로 옮기고, 품목이 없는 카드
    # 매출전표를 빈 목록으로 처리합니다. v5 는 구매일 규칙만 손봤습니다. 발급일시를
    # 구매일로 쓰지 않게 하고, 날짜 자리의 깨진 글자는 숫자로 복원하게 했습니다.
    # 출력 스키마는 다섯 버전이 같습니다.
    receipt_prompt_version: int = 5

    # ── 커넥션 풀 ─────────────────────────────────────────────────
    #: 주의: min 을 max 와 같게 둡니다. 작으면 반납 때 초과분을 닫아 매 요청이
    #:    새 커넥션을 엽니다 — 동시 8요청 p50 32ms. 같게 두면 2.5ms 입니다
    #:    (09-02 실측, psycopg2 풀 기준. SQLAlchemy 도 같은 이유로 맞춥니다).
    pg_max_conn: int = 10
    pg_min_conn: int | None = None
    pool_timeout_sec: int = 30

    # ── 서빙 ──────────────────────────────────────────────────────
    #: 후보 조회 상한. Retrieval 이 이만큼만 보고 자릅니다
    #: (`deploy/init/04_functions.sql` 의 `p_limit`).
    candidate_limit: int = 500
    #: 탐색 풀 = 상위 N (설계 5-3-3). trace params 의 `explore_pool_size` 와 같아야 합니다.
    explore_pool_size: int = 200
    #: propensity 추정 MC 반복 수.
    propensity_mc: int = 200

    # 주의: LLM 타임아웃·재시도는 위쪽 `llm_timeout_sec` · `llm_max_retries` 하나뿐이다.
    #    09-07 병합 때 추천 쪽이 같은 뜻의 `llm_timeout_s`(30) · `llm_max_retries`(3)
    #    를 아래에 또 두어, 나중 정의가 영수증 값(10·1)을 덮었다. 조용히 덮였고
    #    gemini 재시도가 1회에서 3회로 늘어 테스트가 깨져서야 드러났다.
    #    같은 뜻의 설정을 두 이름으로 두지 않는다.

    # ── 배치 ──────────────────────────────────────────────────────
    ingest_batch: int = 2000

    # ── 비밀값 ────────────────────────────────────────────────────
    #: 주의: 기본값 없음. 없으면 None — 부르는 쪽이 멈춰야 합니다.
    #:    새로 만들면 이미 적재된 후기의 작성자 해시와 어긋나고 되돌릴 수 없습니다.
    review_salt: str | None = None

    @property
    def pool_min(self) -> int:
        return self.pg_max_conn if self.pg_min_conn is None else self.pg_min_conn

    @property
    def database_dsn(self) -> str:
        """SQLAlchemy 용 DSN. 접속 정보는 이 하나로만 나갑니다."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                path=self.db_name,
            )
        )

    @property
    def libpq_url(self) -> str:
        """드라이버 접두어가 없는 형태. `psql` 과 적재 스크립트가 씁니다."""
        return self.database_dsn.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 객체는 프로세스당 하나만 만듭니다."""
    return Settings()  # type: ignore[call-arg]
