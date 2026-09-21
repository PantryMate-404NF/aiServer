"""백엔드가 여는 두 API(재료 사전 · 레시피)를 읽습니다 — API 명세 3절.

레시피와 재료의 정본은 백엔드입니다. AI 서버는 백엔드 DB 에 붙지 않고 이 두 API 로만 받습니다.
여기서는 **받아서 검증하는 데까지** 합니다. 엔진 모델로 바꾸는 것은 `engine/catalog.py` 입니다.

받은 것은 그대로 쓰지 않습니다. 항목마다 모양을 검사하고, 어긋난 항목은 버리되 **셉니다.**
한 건이 깨졌다고 2만 건을 버릴 수는 없고, 조용히 버리면 레시피가 왜 줄었는지 아무도 모릅니다.
어긋난 것이 많으면(`MAX_INVALID_RATIO`) 한 건의 문제가 아니라 계약이 갈린 것이므로 통째로
실패시킵니다 — 반쯤 읽은 사전으로 서빙하는 것보다 어제의 사전으로 서빙하는 편이 낫습니다.

주의: 응답에는 백엔드의 공통 응답 래퍼가 없습니다(2026-09-21 합의). 최상위의 `items` 를 읽습니다.
주의: 모르는 필드는 무시합니다(`extra="ignore"`). 백엔드가 필드를 더해도 동기화가 깨지지 않아야
   합니다. 우리가 **받는** 요청이 모르는 필드를 거부하는 것과는 방향이 반대입니다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from utils.errors import ExternalServiceError, ResponseValidationError

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-Internal-Api-Key"
#: 어긋난 항목이 이 비율을 넘으면 한 건의 문제가 아니라 계약이 갈린 것으로 봅니다.
MAX_INVALID_RATIO = 0.05
#: 커서가 끝나지 않는 응답을 끝없이 따라가지 않습니다. 쪽당 1,000건이면 200만 건입니다.
MAX_PAGES = 2000


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class BackendIngredient(_Payload):
    """재료 사전 한 줄 (API 명세 3.1)."""

    ingredient_id: int
    name: str = Field(min_length=1)
    category: str | None = None
    default_shelf_life_days: int | None = None
    extended_consumption_days: int | None = None
    #: 백엔드가 아직 주지 않는 둘. 없으면 AI 쪽 시드로 대신합니다(`engine/catalog.py`).
    is_staple: bool | None = None
    allergens: list[str] | None = None


class BackendRecipeIngredient(_Payload):
    ingredient_id: int
    name: str | None = None
    is_main: bool = False
    unit: str | None = None


class BackendPopularity(_Payload):
    view_count: int | None = None
    scrap_count: int | None = None
    order_count: int | None = None


class BackendRating(_Payload):
    average: float | None = None
    count: int | None = None


class BackendRecipe(_Payload):
    """레시피 한 건 (API 명세 3.2). `ingredients` 는 레시피에 적힌 순서 그대로입니다."""

    recipe_id: int
    title: str = ""
    cuisine_type: str | None = None
    cooking_time: int | None = None
    difficulty: str | None = None
    #: 증분 응답은 비공개로 바뀌었거나 지워진 레시피도 `false` 로 실어 옵니다.
    is_published: bool = True
    updated_at: datetime | None = None
    ingredients: list[BackendRecipeIngredient] = Field(default_factory=list)
    popularity: BackendPopularity | None = None
    rating: BackendRating | None = None


class BackendUnavailableError(ExternalServiceError):
    """백엔드에 닿지 못했거나 백엔드가 실패로 답했습니다."""


class BackendClient:
    """동기 HTTP 클라이언트. 서빙 스레드가 아니라 동기화 스레드에서만 부릅니다."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        ingredients_path: str,
        recipes_path: str,
        timeout_sec: float,
        page_size: int,
        transport: httpx.BaseTransport | None = None,
        on_invalid: Callable[[str, int], None] | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={API_KEY_HEADER: api_key},
            timeout=timeout_sec,
            transport=transport,
        )
        self._ingredients_path = ingredients_path
        self._recipes_path = recipes_path
        self._page_size = page_size
        self._on_invalid = on_invalid

    def close(self) -> None:
        self._http.close()

    def fetch_ingredients(self) -> list[BackendIngredient]:
        """재료 사전 전량. 쪽이 나뉘어 와도(`next_cursor`) 끝까지 읽습니다."""
        return self._fetch_all(self._ingredients_path, {}, BackendIngredient, "ingredient")

    def fetch_recipes(self, updated_after: datetime | None = None) -> list[BackendRecipe]:
        """레시피. `updated_after` 를 주면 그 뒤에 바뀐 것만 옵니다(증분)."""
        params: dict[str, str] = {}
        if updated_after is not None:
            params["updated_after"] = updated_after.isoformat()
        return self._fetch_all(self._recipes_path, params, BackendRecipe, "recipe")

    def _fetch_all[Item: _Payload](
        self, path: str, params: dict[str, str], model: type[Item], kind: str
    ) -> list[Item]:
        items: list[Item] = []
        invalid = 0
        cursor: str | None = None
        seen: set[str] = set()
        for _page in range(MAX_PAGES):
            query = {**params, "limit": str(self._page_size)}
            if cursor:
                query["cursor"] = cursor
            body = self._get(path, query)
            raw = body.get("items")
            if not isinstance(raw, list):
                raise ResponseValidationError(f"{path}: 최상위에 items 배열이 없습니다")
            valid, bad = _validated(raw, model)
            items.extend(valid)
            invalid += bad
            cursor = body.get("next_cursor") or None
            # 같은 커서가 다시 오면 끝나지 않는 응답입니다. 받은 데까지로 멈춥니다.
            if cursor is None or cursor in seen:
                break
            seen.add(cursor)
        total = len(items) + invalid
        if invalid:
            logger.warning("backend %s: %d of %d items failed validation", kind, invalid, total)
            if self._on_invalid is not None:
                self._on_invalid(kind, invalid)
        if total and invalid / total > MAX_INVALID_RATIO:
            raise ResponseValidationError(
                f"{path}: {total}건 중 {invalid}건이 계약과 다릅니다. 명세 3절과 대조하십시오"
            )
        return items

    def _get(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.get(path, params=query)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise BackendUnavailableError(f"{path}: 백엔드가 {status} 로 답했습니다") from error
        except httpx.HTTPError as error:
            raise BackendUnavailableError(f"{path}: 백엔드에 닿지 못했습니다") from error
        except ValueError as error:
            raise ResponseValidationError(f"{path}: 응답이 JSON 이 아닙니다") from error
        if not isinstance(body, dict):
            raise ResponseValidationError(f"{path}: 응답의 최상위가 객체가 아닙니다")
        return body


def _validated[Item: _Payload](raw: Sequence[object], model: type[Item]) -> tuple[list[Item], int]:
    valid: list[Item] = []
    invalid = 0
    for entry in raw:
        try:
            valid.append(model.model_validate(entry))
        except ValidationError:
            invalid += 1
    return valid, invalid
