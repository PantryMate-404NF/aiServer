"""예외 계층. 호출부가 무엇이 실패했는지 구분할 수 있어야 합니다."""

from __future__ import annotations

from utils import errors


def test_every_custom_error_inherits_app_error() -> None:
    for name in ("ConfigError", "RepositoryError", "ExternalServiceError"):
        assert issubclass(getattr(errors, name), errors.AppError)


def test_response_validation_error_is_an_external_service_error() -> None:
    assert issubclass(errors.ResponseValidationError, errors.ExternalServiceError)
    assert issubclass(errors.ResponseValidationError, errors.AppError)
