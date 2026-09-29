"""백엔드의 두 API 를 읽는 클라이언트 (`backend_client.py`). 네트워크는 타지 않습니다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from features.recommend.backend_client import (
    API_KEY_HEADER,
    BackendClient,
    BackendUnavailableError,
)
from utils.errors import ResponseValidationError

Handler = Callable[[httpx.Request], httpx.Response]


def client(
    handler: Handler,
    *,
    page_size: int = 2,
    on_invalid: Callable[[str, int], None] | None = None,
) -> BackendClient:
    return BackendClient(
        "http://backend.test",
        "the-key",
        ingredients_path="/internal/ai/ingredients",
        recipes_path="/internal/ai/recipes",
        timeout_sec=5,
        page_size=page_size,
        transport=httpx.MockTransport(handler),
        on_invalid=on_invalid,
    )


def recipe(recipe_id: int, **extra: object) -> dict[str, object]:
    return {
        "recipe_id": recipe_id,
        "title": f"레시피 {recipe_id}",
        "ingredients": [{"ingredient_id": 1, "is_main": True}],
        **extra,
    }


def test_every_call_carries_the_internal_key_and_the_page_size() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    client(handler, page_size=500).fetch_ingredients()

    assert seen[0].headers[API_KEY_HEADER] == "the-key"
    assert seen[0].url.path == "/internal/ai/ingredients"
    assert seen[0].url.params["limit"] == "500"


def test_pages_are_followed_until_the_cursor_runs_out() -> None:
    pages = {"": ([1, 2], "a"), "a": ([3, 4], "b"), "b": ([5], None)}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor", "")
        asked.append(cursor)
        ids, nxt = pages[cursor]
        return httpx.Response(200, json={"items": [recipe(i) for i in ids], "next_cursor": nxt})

    got = client(handler).fetch_recipes()

    assert [r.recipe_id for r in got] == [1, 2, 3, 4, 5]
    assert asked == ["", "a", "b"]


def test_a_cursor_that_repeats_does_not_loop_forever() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"items": [recipe(len(calls))], "next_cursor": "same"})

    got = client(handler).fetch_recipes()

    assert len(calls) == 2
    assert [r.recipe_id for r in got] == [1, 2]


def test_the_incremental_call_sends_the_timestamp() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    client(handler).fetch_recipes(datetime(2026, 9, 21, tzinfo=UTC))

    assert seen[0].url.params["updated_after"] == "2026-09-21T00:00:00+00:00"


def test_fields_the_backend_adds_later_are_ignored() -> None:
    """우리가 받는 요청은 모르는 필드를 거부하지만, 백엔드의 응답은 반대입니다."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [recipe(1, thumbnail_url="x", steps=[1, 2])]})

    assert client(handler).fetch_recipes()[0].recipe_id == 1


def test_a_broken_item_is_dropped_and_counted_not_silently_lost() -> None:
    counted: list[tuple[str, int]] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        items = [recipe(i) for i in range(1, 40)] + [{"title": "번호가 없는 레시피"}]
        return httpx.Response(200, json={"items": items})

    got = client(handler, on_invalid=lambda kind, n: counted.append((kind, n))).fetch_recipes()

    assert len(got) == 39
    assert counted == [("recipe", 1)]


def test_many_broken_items_mean_the_contract_split_not_one_bad_row() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        items = [recipe(1), {"id": 2}, {"id": 3}]
        return httpx.Response(200, json={"items": items})

    with pytest.raises(ResponseValidationError, match="계약과 다릅니다"):
        client(handler).fetch_recipes()


def test_a_wrapped_response_is_refused() -> None:
    """2026-09-21 합의 — 공통 응답 래퍼 없이 최상위에 `items` 가 옵니다."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"items": [recipe(1)]}})

    with pytest.raises(ResponseValidationError, match="items"):
        client(handler).fetch_recipes()


@pytest.mark.parametrize("status", [401, 404, 500, 503])
def test_a_failing_backend_is_reported_with_its_status_and_no_secret(status: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "nope"})

    with pytest.raises(BackendUnavailableError) as caught:
        client(handler).fetch_ingredients()

    assert str(status) in str(caught.value)
    assert "the-key" not in str(caught.value)


def test_an_unreachable_backend_is_the_same_kind_of_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(BackendUnavailableError, match="닿지 못했습니다"):
        client(handler).fetch_ingredients()


def test_a_body_that_is_not_json_is_a_contract_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(ResponseValidationError, match="JSON"):
        client(handler).fetch_ingredients()
