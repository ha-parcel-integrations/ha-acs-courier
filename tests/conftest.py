"""pytest configuration for the ACS Courier test suite."""
import sys

import pytest
from pytest_homeassistant_custom_component.plugins import hass  # noqa: F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make ``custom_components.acs_courier`` loadable from config-flow / setup tests."""
    yield


@pytest.fixture(autouse=True)
def reset_one_shot_warnings():
    """Clear the "already warned this session" set between tests.

    ``parcels._unmapped_statuses_logged`` is module-level, so without this a
    test's one-shot-warning assertion would depend on which test already
    triggered that status code first.
    """
    from custom_components.acs_courier import parcels

    parcels._unmapped_statuses_logged.clear()
    yield


@pytest.fixture(autouse=True)
def synthetic_status_ids(monkeypatch):
    """Map two invented ids so delivered / out-for-delivery paths stay testable."""
    from custom_components.acs_courier import parcels
    from custom_components.acs_courier.const import ParcelStatus

    from .payloads import SYNTHETIC_OFD_ID

    monkeypatch.setitem(
        parcels._STATUS_MAP, SYNTHETIC_OFD_ID, ParcelStatus.OUT_FOR_DELIVERY
    )
    yield


if sys.platform == "win32":
    # pytest-homeassistant-custom-component blocks socket *creation*
    # (``disable_socket(allow_unix_socket=True)``) in its per-test setup hook.
    # That is fine on Linux, where asyncio's self-pipe is an AF_UNIX
    # socketpair — but Windows event loops build theirs from AF_INET sockets,
    # so every async test dies with ``SocketBlockedError`` while the event
    # loop fixture is being created. Neutralise the creation block on Windows
    # and keep the network guard as the plugin's connect-time allowlist
    # (``socket_allow_hosts(["127.0.0.1"])``, applied right before the
    # ``disable_socket`` call we swallow here).
    import pytest_socket

    pytest_socket.disable_socket = lambda allow_unix_socket=False: None

    # HA's aiohttp helper hardcodes aiohttp's AsyncResolver, whose aiodns
    # backend refuses the Proactor loop the suite runs on under Windows.
    # Swap in the threaded resolver for the tests — no test resolves DNS.
    import homeassistant.helpers.aiohttp_client as _ha_aiohttp_client
    from aiohttp.resolver import ThreadedResolver

    _ha_aiohttp_client.AsyncResolver = ThreadedResolver
