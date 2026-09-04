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


class ImageDecodeError(AppError):
    """업로드된 바이트를 이미지로 열지 못했습니다."""


class OcrPoolNotReadyError(AppError):
    """OCR 워커 풀이 아직 모델을 올리지 않았거나 이미 내려갔습니다."""


class ReceiptError(AppError):
    """영수증 1장의 처리가 실패했습니다.

    응답 계약(500 + receipt_id + error)이 붙는 유일한 갈래입니다. 하위 클래스가
    code 와 user_message 를 정하고, 예외 핸들러가 그대로 응답 본문에 싣습니다.
    """

    code = "INTERNAL_ERROR"
    user_message = "영수증 처리에 실패했습니다."

    def __init__(self, receipt_id: str) -> None:
        super().__init__(f"{self.code} receipt_id={receipt_id}")
        self.receipt_id = receipt_id


class OcrEmptyError(ReceiptError):
    """이미지를 디코딩하지 못했거나 인식된 텍스트가 없습니다."""

    code = "OCR_EMPTY"
    user_message = "영수증을 인식하지 못했습니다. 다시 촬영해 주세요."


class LlmUnavailableError(ReceiptError):
    """후처리 LLM 호출이 재시도 후에도 실패했습니다."""

    code = "LLM_UNAVAILABLE"
    user_message = "영수증 분석에 실패했습니다. 잠시 후 다시 시도해 주세요."
