"""Tests for the ACS Courier API client."""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.acs_courier.api import (
    ACSCourierApiClient,
    ACSCourierApiError,
)
from custom_components.acs_courier.const import TRACKING_PAGE_URL

from .payloads import CODE, envelope, not_found_envelope, returned_item

TOKEN_ONE = "tok-one_ABC-123"
TOKEN_TWO = "tok-two_XYZ-789"


def _page(token: str | None) -> str:
    attr = f' publicToken="{token}"' if token else ""
    return f"<html><app-root{attr}></app-root></html>"


class FakeSession:
    """Serves the tracking page and the lookup, recording every request."""

    def __init__(self, lookups, pages=None, page_status=200):
        self.lookups = list(lookups)  # (status, body, content_type[, headers])
        self.pages = list(pages or [_page(TOKEN_ONE)])
        self.page_status = page_status
        self.requests: list[tuple[str, dict]] = []

    def get(self, url, headers=None):
        self.requests.append((url, headers or {}))
        response = AsyncMock()
        if url == TRACKING_PAGE_URL:
            response.status = self.page_status
            response.text = AsyncMock(
                return_value=self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
            )
        else:
            status, body, content_type, *extra = self.lookups.pop(0)
            response.status = status
            response.headers = {"Content-Type": content_type, **(extra[0] if extra else {})}
            if isinstance(body, str):
                response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
            else:
                response.json = AsyncMock(return_value=body)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    def lookup_requests(self):
        return [r for r in self.requests if r[0] != TRACKING_PAGE_URL]


def _ok(body=None):
    return (200, body if body is not None else envelope(returned_item()), "application/json")


async def test_returns_first_item_and_sends_header_pair():
    session = FakeSession([_ok()])
    parcel = await ACSCourierApiClient(session).async_get_parcel(CODE)

    assert parcel["shipmentStatusId"] == 7
    url, headers = session.lookup_requests()[0]
    assert url.endswith(f"/api/parcels/search/{CODE}")
    assert headers["X-Encrypted-Key"] == TOKEN_ONE
    assert headers["X-Country"] == "GR"
    # Mandatory: without it every lookup reports a false "not found".
    assert headers["Origin"] == "https://www.acscourier.net"


async def test_only_the_tracking_page_and_consumer_route_are_called():
    session = FakeSession([_ok()])
    await ACSCourierApiClient(session).async_get_parcel(CODE)
    hosts = {url.split("/")[2] for url, _ in session.requests}
    assert hosts == {"www.acscourier.net", "api.acscourier.net"}
    assert all("ACS_Tracking" not in url for url, _ in session.requests)


async def test_token_is_read_once_and_reused():
    session = FakeSession([_ok(), _ok()])
    client = ACSCourierApiClient(session)
    await client.async_get_parcel(CODE)
    await client.async_get_parcel(CODE)
    assert len([r for r in session.requests if r[0] == TRACKING_PAGE_URL]) == 1


async def test_code_is_url_quoted():
    session = FakeSession([_ok()])
    await ACSCourierApiClient(session).async_get_parcel("a/b c")
    assert session.lookup_requests()[0][0].endswith("a%2Fb%20c")


async def test_semantic_miss_is_none():
    session = FakeSession([_ok(not_found_envelope())])
    assert await ACSCourierApiClient(session).async_get_parcel("0000000000") is None


async def test_json_404_is_none():
    session = FakeSession([(404, {"title": "Not Found"}, "application/problem+json")])
    assert await ACSCourierApiClient(session).async_get_parcel(CODE) is None


async def test_non_json_404_is_an_error():
    session = FakeSession([(404, None, "text/html")])
    with pytest.raises(ACSCourierApiError):
        await ACSCourierApiClient(session).async_get_parcel(CODE)


async def test_empty_items_is_none():
    session = FakeSession([_ok({"count": 0, "items": []})])
    assert await ACSCourierApiClient(session).async_get_parcel(CODE) is None


@pytest.mark.parametrize(
    "body",
    [{"count": 1}, {"items": "x"}, ["not", "a", "dict"], {"items": ["junk"]}, "not json"],
)
async def test_malformed_bodies_are_errors(body):
    session = FakeSession([(200, body, "application/json")])
    with pytest.raises(ACSCourierApiError):
        await ACSCourierApiClient(session).async_get_parcel(CODE)


async def test_other_statuses_are_errors():
    session = FakeSession([(500, None, "text/plain")])
    with pytest.raises(ACSCourierApiError) as err:
        await ACSCourierApiClient(session).async_get_parcel(CODE)
    assert err.value.status_code == 500


async def test_429_carries_retry_after():
    session = FakeSession([(429, None, "", {"Retry-After": "120"})])
    with pytest.raises(ACSCourierApiError) as err:
        await ACSCourierApiClient(session).async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 120.0


@pytest.mark.parametrize("header", [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}])
async def test_429_without_usable_retry_after(header):
    session = FakeSession([(429, None, "", header)])
    with pytest.raises(ACSCourierApiError) as err:
        await ACSCourierApiClient(session).async_get_parcel(CODE)
    assert err.value.retry_after is None


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_refreshes_the_token_once_then_succeeds(status):
    session = FakeSession(
        [(status, None, ""), _ok()], pages=[_page(TOKEN_ONE), _page(TOKEN_TWO)]
    )
    parcel = await ACSCourierApiClient(session).async_get_parcel(CODE)

    assert parcel is not None
    sent = [h["X-Encrypted-Key"] for _, h in session.lookup_requests()]
    assert sent == [TOKEN_ONE, TOKEN_TWO]
    assert len([r for r in session.requests if r[0] == TRACKING_PAGE_URL]) == 2


async def test_second_auth_failure_backs_off_without_more_traffic(caplog):
    session = FakeSession(
        [(401, None, ""), (401, None, "")], pages=[_page(TOKEN_ONE), _page(TOKEN_TWO)]
    )
    client = ACSCourierApiClient(session)
    with pytest.raises(ACSCourierApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 401
    count = len(session.requests)

    with pytest.raises(ACSCourierApiError):
        await client.async_get_parcel(CODE)
    assert len(session.requests) == count
    # neither the token nor the page leaks into logs or error text
    assert TOKEN_ONE not in caplog.text and TOKEN_TWO not in caplog.text
    assert TOKEN_ONE not in str(err.value)


async def test_lookups_resume_after_the_backoff():
    session = FakeSession(
        [(401, None, ""), (401, None, ""), _ok()],
        pages=[_page(TOKEN_ONE), _page(TOKEN_TWO), _page(TOKEN_TWO)],
    )
    client = ACSCourierApiClient(session)
    with pytest.raises(ACSCourierApiError):
        await client.async_get_parcel(CODE)
    client._blocked_until = time.monotonic() - 1
    assert await client.async_get_parcel(CODE) is not None


async def test_page_without_token_is_an_error():
    session = FakeSession([_ok()], pages=[_page(None)])
    with pytest.raises(ACSCourierApiError):
        await ACSCourierApiClient(session).async_get_parcel(CODE)


async def test_page_http_error_is_an_error():
    session = FakeSession([_ok()], page_status=503)
    with pytest.raises(ACSCourierApiError) as err:
        await ACSCourierApiClient(session).async_get_parcel(CODE)
    assert err.value.status_code == 503


async def test_propagates_network_error():
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await ACSCourierApiClient(session).async_get_parcel(CODE)
