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
