"""Tests for ACS Courier device triggers."""
from unittest.mock import AsyncMock

from custom_components.acs_courier.const import DOMAIN
from custom_components.acs_courier.device_trigger import (
    TRIGGER_EVENTS,
    async_attach_trigger,
    async_get_triggers,
)


async def test_get_triggers_returns_all_four(hass):
    triggers = await async_get_triggers(hass, "device123")
    types = {t["type"] for t in triggers}
    assert types == {
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    }
    for trigger in triggers:
        assert trigger["domain"] == DOMAIN
        assert trigger["device_id"] == "device123"


def test_trigger_events_map_to_domain_prefix():
    assert TRIGGER_EVENTS["parcel_registered"] == f"{DOMAIN}_parcel_registered"


async def test_attach_trigger_fires_only_for_its_device(hass):
    action = AsyncMock()
    unsub = await async_attach_trigger(
        hass,
        {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": "device123",
            "type": "parcel_delivered",
        },
        action,
        {"trigger_data": {}, "variables": {}},
    )

    hass.bus.async_fire(TRIGGER_EVENTS["parcel_delivered"], {"device_id": "other"})
    hass.bus.async_fire(TRIGGER_EVENTS["parcel_delivered"], {"device_id": "device123"})
    await hass.async_block_till_done()
    unsub()

    assert action.call_count == 1
    event = action.call_args.args[0]["trigger"]["event"]
    assert event.data["device_id"] == "device123"
