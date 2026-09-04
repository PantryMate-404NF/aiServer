"""예외 계층. 호출부가 무엇이 실패했는지 구분할 수 있어야 합니다."""

from __future__ import annotations

from utils import errors


def test_every_custom_error_inherits_app_error() -> None:
    for name in ("ConfigError", "RepositoryError", "ExternalServiceError"):
        assert issubclass(getattr(errors, name), errors.AppError)


def test_response_validation_error_is_an_external_service_error() -> None:
    assert issubclass(errors.ResponseValidationError, errors.ExternalServiceError)
    assert issubclass(errors.ResponseValidationError, errors.AppError)


def test_receipt_errors_carry_contract_code_and_receipt_id() -> None:
    """500 본문의 code 와 receipt_id 는 예외가 들고 옵니다. 핸들러가 지어내지 않습니다."""
    error = errors.OcrEmptyError("01K4A7Q3ZV8XG2M5W9R1DTF6HJ")

    assert isinstance(error, errors.ReceiptError)
    assert error.receipt_id == "01K4A7Q3ZV8XG2M5W9R1DTF6HJ"
    assert error.code == "OCR_EMPTY"
    assert errors.LlmUnavailableError("x").code == "LLM_UNAVAILABLE"


def test_receipt_error_message_does_not_leak_ocr_text() -> None:
    """예외 문자열은 로그로 나갑니다. 원문이 섞이면 개인정보가 로그에 남습니다."""
    assert str(errors.OcrEmptyError("rid")) == "OCR_EMPTY receipt_id=rid"
