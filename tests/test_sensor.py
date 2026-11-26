"""Tests for ACS Courier sensor property logic."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.acs_courier import sensor as sensor_module
from custom_components.acs_courier.const import ParcelStatus
from custom_components.acs_courier.sensor import (
    ACSCourierAwaitingPickupSensor,
    ACSCourierDeliveredParcelsSensor,
    ACSCourierEnRouteToPickupPointSensor,
    ACSCourierIncomingParcelsSensor,
    ACSCourierLastUpdateSensor,
    ACSCourierNextDeliverySensor,
    ACSCourierParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _coordinator(data: list[dict], delivered: list[dict] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.delivered = delivered if delivered is not None else []
    return coordinator


def _parcel(
    barcode: str,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    pickup: bool = False,
    planned_from: str | None = None,
    planned_to: str | None = None,
) -> dict:
    return {
        "carrier": "ACS Courier",
        "barcode": barcode,
        "sender": "Sender",
        "receiver": None,
        "status": status,
        "pickup": pickup,
        "planned_from": planned_from,
        "planned_to": planned_to,
    }


def test_incoming_counts_and_lists():
    coordinator = _coordinator([_parcel("A"), _parcel("B")])
    sensor = ACSCourierIncomingParcelsSensor(
        coordinator, _entry(), lambda _: None, set()
    )
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_parcel_sensor_status_and_attributes():
    parcel = _parcel("A", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = ACSCourierParcelSensor(_coordinator([parcel]), _entry(), "A")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.extra_state_attributes["barcode"] == "A"


def test_parcel_sensor_missing_barcode():
    sensor = ACSCourierParcelSensor(_coordinator([_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_picks_earliest():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="2099-05-02T10:00:00Z"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = ACSCourierNextDeliverySensor(coordinator, _entry())
    assert sensor.native_value == datetime(2099, 5, 1, 10, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_carries_the_contents():
    parcel = _parcel("A", planned_from="2099-05-01T10:00:00Z")
    parcel["contents"] = "Running shoes"
    sensor = ACSCourierNextDeliverySensor(_coordinator([parcel]), _entry())
    assert sensor.extra_state_attributes["contents"] == "Running shoes"


def test_next_delivery_none_without_moments():
    sensor = ACSCourierNextDeliverySensor(_coordinator([_parcel("A")]), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_unparseable_moment():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="not-a-date"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = ACSCourierNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_delivered_sensor():
    coordinator = _coordinator(
        [], delivered=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = ACSCourierDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_last_update_sensor():
    coordinator = _coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    coordinator.last_success_time = moment
    sensor = ACSCourierLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment


def test_next_delivery_skips_a_window_that_has_ended():
    coordinator = _coordinator(
        [
            _parcel(
                "OLD",
                planned_from="2020-05-01T00:00:00Z",
                planned_to="2020-05-01T23:59:59Z",
            ),
            _parcel("NEW", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = ACSCourierNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "NEW"


def test_en_route_counts_only_active_pickup_parcels_not_yet_there():
    parcels = [
        _parcel("EN_ROUTE", pickup=True),
        _parcel("READY", pickup=True, status=ParcelStatus.AT_PICKUP_POINT),
        _parcel("BACK", pickup=True, status=ParcelStatus.RETURNING),
        _parcel("HOME"),
    ]
    sensor = ACSCourierEnRouteToPickupPointSensor(_coordinator(parcels), _entry())
    assert sensor.unique_id == "e1_en_route_to_pickup_point"
    assert [p["barcode"] for p in sensor.extra_state_attributes["parcels"]] == [
        "EN_ROUTE",
    ]


def test_awaiting_pickup_counts_parcels_at_the_point():
    parcels = [
        _parcel("EN_ROUTE", pickup=True),
        _parcel("READY", pickup=True, status=ParcelStatus.AT_PICKUP_POINT),
        _parcel("HOME"),
    ]
    sensor = ACSCourierAwaitingPickupSensor(_coordinator(parcels), _entry())
    assert sensor.unique_id == "e1_awaiting_pickup"
    assert sensor.native_value == 1
    assert [p["barcode"] for p in sensor.extra_state_attributes["parcels"]] == ["READY"]


def test_pickup_sensors_read_zero_without_data():
    coordinator = _coordinator([])
    coordinator.data = None
    assert ACSCourierEnRouteToPickupPointSensor(coordinator, _entry()).native_value == 0
    assert ACSCourierAwaitingPickupSensor(coordinator, _entry()).native_value == 0


async def test_setup_registers_pickup_sensors_and_keeps_their_registry_entries(
    monkeypatch,
):
    registry = MagicMock()
    kept = [
        MagicMock(
            domain="sensor", unique_id="e1_awaiting_pickup", entity_id="sensor.a"
        ),
        MagicMock(
            domain="sensor",
            unique_id="e1_en_route_to_pickup_point",
            entity_id="sensor.b",
        ),
    ]
    monkeypatch.setattr(sensor_module.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        sensor_module.er, "async_entries_for_config_entry", lambda _r, _e: kept
    )
    entry = _entry()
    entry.runtime_data.coordinator = _coordinator([])
    added: list = []
    await sensor_module.async_setup_entry(MagicMock(), entry, added.extend)

    registry.async_remove.assert_not_called()
    kinds = {type(entity) for entity in added}
    assert ACSCourierAwaitingPickupSensor in kinds
    assert ACSCourierEnRouteToPickupPointSensor in kinds
