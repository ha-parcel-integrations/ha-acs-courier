"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.acs_courier.const import CONF_PARCELS, CONF_TRACKING_CODE, DOMAIN
from custom_components.acs_courier.coordinator import ACSCourierCoordinator
from custom_components.acs_courier.parcels import normalize_parcel

from .payloads import active_sample, delivered_sample

CLIENT = "custom_components.acs_courier.api.ACSCourierApiClient"
REFRESH = f"{ACSCourierCoordinator.__module__}.ACSCourierCoordinator"
CODE = "EXAMPLE111111"
UNTRACKED = "EXAMPLE555555"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _config_entry(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


async def _setup(hass, codes: list[str] | None = None) -> MockConfigEntry:
    entry = _config_entry([{CONF_TRACKING_CODE: code} for code in codes or []])
    entry.add_to_hass(hass)
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass, [CODE])
    assert _contents(entry, CODE) is None


async def test_an_entry_without_a_store_file_loads(hass, hass_storage):
    assert not hass_storage
    entry = await _setup(hass, [CODE])
    assert entry.state is ConfigEntryState.LOADED
    assert _contents(entry, CODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass, [CODE])

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: CODE, "contents": " Running  shoes "},
    )
    assert _contents(entry, CODE) == "Running shoes"

    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: CODE, "contents": "Winter socks"}
    )
    assert _contents(entry, CODE) == "Winter socks"

    await _call(hass, "update_contents", {CONF_TRACKING_CODE: CODE, "contents": "  "})
    assert _contents(entry, CODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass, [CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
    )

    await _call(hass, "update_contents", {CONF_TRACKING_CODE: CODE})

    assert _contents(entry, CODE) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass, [CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: "example-111 111", "contents": "Running shoes"},
    )
    assert _contents(entry, CODE) == "Running shoes"


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _setup(hass, [CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]


async def test_update_contents_never_polls_and_notifies_once_per_change(hass):
    entry = await _setup(hass, [CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch(f"{CLIENT}.async_get_parcels", new=AsyncMock()) as fetch_batch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: UNTRACKED}, blocking=True
        )
        assert listeners.call_count == 1
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: CODE}, blocking=True
        )
        assert listeners.call_count == 2
    refresh.assert_not_called()
    fetch.assert_not_called()
    fetch_batch.assert_not_called()
    assert _contents(entry, CODE) is None


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ""})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
        )


async def test_track_parcel_stores_contents_for_a_new_code(hass):
    entry = await _setup(hass)
    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
    )

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]
    assert _contents(entry, CODE) == "Running shoes"


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: CODE})

    assert entry.runtime_data.coordinator.contents_store.get(CODE) is None
    assert _contents(entry, CODE) is None


async def test_track_parcel_replaces_and_clears_contents_of_a_tracked_code(hass):
    entry = await _setup(hass, [CODE])
    track = {CONF_TRACKING_CODE: CODE}

    await _call(hass, "track_parcel", {**track, "contents": "Running shoes"})
    assert _contents(entry, CODE) == "Running shoes"

    await _call(hass, "track_parcel", {**track, "contents": "Winter socks"})
    assert _contents(entry, CODE) == "Winter socks"

    await _call(hass, "track_parcel", {**track, "contents": ""})
    assert _contents(entry, CODE) is None
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]


async def test_track_parcel_on_a_tracked_code_never_polls(hass):
    entry = await _setup(hass, [CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"}
    with (
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{CLIENT}.async_get_parcel", new=AsyncMock()) as fetch,
        patch(f"{CLIENT}.async_get_parcels", new=AsyncMock()) as fetch_batch,
        patch.object(
            coordinator,
            "async_update_listeners",
            wraps=coordinator.async_update_listeners,
        ) as listeners,
    ):
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
    assert listeners.call_count == 1
    refresh.assert_not_called()
    fetch.assert_not_called()
    fetch_batch.assert_not_called()
    assert _contents(entry, CODE) == "Running shoes"


async def test_a_newly_tracked_code_still_refreshes_via_the_options_listener(hass):
    entry = await _setup(hass)
    fetch = AsyncMock(side_effect=lambda code: active_sample(code))
    with patch(f"{CLIENT}.async_get_parcel", new=fetch):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
            blocking=True,
        )
        await hass.async_block_till_done()
    fetch.assert_awaited()
    assert _contents(entry, CODE) == "Running shoes"


async def test_track_parcel_with_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
        )


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _setup(hass, [CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: CODE})

    assert entry.runtime_data.coordinator.contents_store.get(CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_untrack_parcel_survives_an_entry_that_is_not_loaded(hass):
    entry = await _setup(hass, [CODE])
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    await hass.services.async_call(
        DOMAIN, "untrack_parcel", {CONF_TRACKING_CODE: CODE}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_PARCELS] == []
    entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_reapply_contents_covers_delivered_parcels(hass):
    entry = await _setup(hass, [CODE])
    coordinator = entry.runtime_data.coordinator
    coordinator.delivered = [normalize_parcel(delivered_sample(CODE))]

    coordinator.contents_store.async_set(CODE, "Running shoes")
    coordinator.async_reapply_contents()
    assert coordinator.delivered[0]["contents"] == "Running shoes"

    coordinator.contents_store.async_set(CODE, None)
    coordinator.async_reapply_contents()
    assert coordinator.delivered[0]["contents"] is None


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass, [CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, CODE) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass, [CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass, [CODE])
    events = []
    for name in ("status_changed", "registered", "delivered", "delivery_time_changed"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
    )

    assert events == []


async def test_contents_follow_the_requested_code_when_the_barcode_differs(hass):
    entry = _config_entry([{CONF_TRACKING_CODE: CODE}])
    entry.add_to_hass(hass)

    def other_barcode(code, *_):
        sample = active_sample(code)
        sample["trackingNumber"] = "EXAMPLEOTHER"
        return sample

    with patch(f"{CLIENT}.async_get_parcel", new=AsyncMock(side_effect=other_barcode)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: CODE, "contents": "Running shoes"},
        )
        parcel = entry.runtime_data.coordinator.data[0]
        assert parcel["barcode"] == "EXAMPLEOTHER"
        assert parcel["contents"] == "Running shoes"


async def test_contents_survive_reapply_and_poll_for_a_parcel_without_a_barcode(hass):
    entry = _config_entry([{CONF_TRACKING_CODE: CODE}])
    entry.add_to_hass(hass)

    def no_barcode(code, *_):
        sample = active_sample(code)
        sample["trackingNumber"] = None
        return sample

    with patch(f"{CLIENT}.async_get_parcel", new=AsyncMock(side_effect=no_barcode)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator
        assert not coordinator.data[0]["barcode"]
        coordinator.contents_store.async_set(CODE, "Running shoes")
        coordinator.async_reapply_contents()
        assert coordinator.data[0]["contents"] == "Running shoes"
        await coordinator.async_refresh()
        assert coordinator.data[0]["contents"] == "Running shoes"
        coordinator.async_reapply_contents()
        assert coordinator.data[0]["contents"] == "Running shoes"
