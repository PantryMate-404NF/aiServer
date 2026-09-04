"""애플리케이션 예외 계층. 모든 커스텀 예외가 AppError 를 상속합니다."""

from __future__ import annotations


class AppError(Exception):
    """이 애플리케이션이 발생시키는 모든 예외의 최상위."""


class ConfigError(AppError):
    """설정값이 없거나 형식이 잘못됐습니다."""


class RepositoryError(AppError):
    """데이터 저장소 접근이 실패했습니다."""


class ExternalServiceError(AppError):
    """외부 서비스 호출이 실패했습니다."""


class ResponseValidationError(ExternalServiceError):
    """외부 서비스 응답이 기대한 스키마와 다릅니다."""
