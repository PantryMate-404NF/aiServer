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


class QuotaExceededError(ExternalServiceError):
    """외부 서비스가 호출 한도 초과(429)로 거절했고, 재시도 뒤에도 같았습니다."""


class ImageDecodeError(AppError):
    """업로드된 바이트를 이미지로 열지 못했습니다."""


class OcrPoolNotReadyError(AppError):
    """OCR 워커 풀이 아직 모델을 올리지 않았거나 이미 내려갔습니다."""


class OcrBusyError(AppError):
    """OCR 처리 자리가 제한 시간 안에 나지 않았습니다. 워커는 살아 있고 요청만 몰린 상태입니다."""


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
    # 재촬영만 안내하지 않습니다. 오래된 감열지는 다시 찍어도 글자가 돌아오지 않습니다.
    user_message = "영수증을 인식하지 못했습니다. 다시 촬영하거나 품목을 직접 입력해 주세요."


class LlmUnavailableError(ReceiptError):
    """후처리 LLM 에서 쓸 수 있는 응답을 받지 못했습니다.

    시간 초과, 연결 실패, 빈 응답, 형식이 어긋난 응답이 여기에 듭니다. 호출 한도
    초과는 아래 하위 클래스가 같은 코드에 다른 메시지로 내보냅니다.
    """

    code = "LLM_UNAVAILABLE"
    user_message = "영수증 분석 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요."


class LlmQuotaExceededError(LlmUnavailableError):
    """LLM API 호출 한도를 넘겼습니다. 코드는 같고 메시지만 다릅니다.

    코드를 늘리지 않는 이유는 호출부의 분기가 그대로여야 하기 때문입니다. 원인은
    메시지와 서버 로그로 가립니다. 한도는 기다리면 풀리지만 응답 문제는 그렇지 않을 수
    있어 안내가 다릅니다.
    """

    user_message = "영수증 분석 요청 한도를 초과했습니다. 1분 뒤 다시 시도해 주세요."


class OcrUnavailableError(ReceiptError):
    """OCR 워커 풀이 준비되지 않았거나 깨졌거나 자리가 나지 않았습니다.

    이미지나 LLM 의 문제가 아니라 서버 쪽 사정이라 다른 두 코드와 구분합니다. 같은
    500 이지만 사용자에게는 잠시 후 재시도를 안내합니다.
    """

    code = "OCR_UNAVAILABLE"
    user_message = "영수증 인식 서비스가 혼잡합니다. 잠시 후 다시 시도해 주세요."
