"""ACS Courier consumer tracking API client."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    AUTH_BACKOFF_SECONDS,
    COUNTRY_HEADER,
    NOT_FOUND_NOTE,
    SITE_ORIGIN,
    TRACKING_API_URL,
    TRACKING_PAGE_URL,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"""publictoken\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_AUTH_STATUSES = (401, 403)


class ACSCourierApiError(Exception):
    """Raised when an ACS Courier API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"ACS Courier API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class ACSCourierApiClient:
    """Client for the ACS Courier consumer tracking lookup."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session
        self._token: str | None = None
        self._token_lock = asyncio.Lock()
        self._blocked_until = 0.0

    async def _async_fetch_token(self) -> str:
        """Fetch a fresh session token."""
        async with self._session.get(TRACKING_PAGE_URL) as response:
            if response.status != 200:
                raise ACSCourierApiError(
                    f"tracking page HTTP {response.status}",
                    status_code=response.status,
                )
            page = await response.text()
        match = _TOKEN_PATTERN.search(page)
        if match is None:
            raise ACSCourierApiError("tracking page unusable")
        return match.group(1)

    async def _async_session_token(self, rejected: str | None = None) -> str:
        """Return the cached token, re-reading the page when missing or rejected."""
        async with self._token_lock:
            if self._token is None or self._token == rejected:
                self._token = await self._async_fetch_token()
            return self._token

    async def _async_request(self, url: str, token: str) -> tuple[int, Any, str]:
        """Return ``(status, parsed JSON or None, content type)`` for one GET."""
        headers = {
            "X-Encrypted-Key": token,
            "X-Country": COUNTRY_HEADER,
            "Accept": "application/json",
            "Origin": SITE_ORIGIN,
            "Referer": f"{SITE_ORIGIN}/",
        }
        async with self._session.get(url, headers=headers) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise ACSCourierApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            content_type = response.headers.get("Content-Type", "")
            if response.status not in (200, 404):
                return response.status, None, content_type
            try:
                payload = await response.json(content_type=None)
            except ValueError:
                payload = None
            return response.status, payload, content_type

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel item, or ``None`` when ACS reports the code as
        unknown. Any other failure raises :class:`ACSCourierApiError`; network
        errors propagate as ``aiohttp.ClientError``.
        """
        if time.monotonic() < self._blocked_until:
            raise ACSCourierApiError("authorization failed", status_code=401)

        url = TRACKING_API_URL.format(tracking_code=quote(tracking_code, safe=""))
        token = await self._async_session_token()
        status, payload, content_type = await self._async_request(url, token)
        if status in _AUTH_STATUSES:
            token = await self._async_session_token(rejected=token)
            status, payload, content_type = await self._async_request(url, token)
            if status in _AUTH_STATUSES:
                self._blocked_until = time.monotonic() + AUTH_BACKOFF_SECONDS
                _LOGGER.warning(
                    "ACS Courier refused the lookup twice; pausing lookups for 30 minutes"
                )
                raise ACSCourierApiError("authorization failed", status_code=status)

        if status == 404 and "json" in content_type.lower():
            return None
        if status != 200:
            raise ACSCourierApiError(f"HTTP {status}", status_code=status)
        if not isinstance(payload, dict):
            raise ACSCourierApiError("unexpected body (not a JSON object)")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ACSCourierApiError("response carried no items list")
        if not items:
            return None
        item = items[0]
        if not isinstance(item, dict):
            raise ACSCourierApiError("unexpected item (not a JSON object)")
        if item.get("notes") == NOT_FOUND_NOTE:
            return None
        return item
