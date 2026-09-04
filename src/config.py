"""환경변수를 읽어 설정 객체로 만드는 단일 진입점."""

from __future__ import annotations

from functools import lru_cache

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """실행 시작 시점에 필수 환경변수 누락을 검출합니다."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

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
    # v2 는 v1 에서 주류를 비식재료로 옮긴 것입니다. 출력 스키마는 v1 과 같습니다.
    receipt_prompt_version: int = 2

    @property
    def database_dsn(self) -> str:
        """접속 정보는 이 DSN 하나로만 나갑니다."""
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 객체는 프로세스당 하나만 만듭니다."""
    return Settings()  # type: ignore[call-arg]
