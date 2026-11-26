"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, date, datetime

import pytest

from custom_components.acs_courier.const import ParcelStatus
from custom_components.acs_courier.parcels import (
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_window,
)

from .payloads import delivered_sample, out_for_delivery_sample, returned_item

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _delivered_with_eta() -> dict:
    sample = delivered_sample()
    sample["expectedDeliveryDate"] = "2026-05-18T00:00:00"
    return sample


def _moment_only() -> dict:
    sample = out_for_delivery_sample()
    sample["expectedDeliveryDate"] = "2026-05-18T13:00:00"
    return sample


def _date_only() -> dict:
    sample = out_for_delivery_sample()
    sample["expectedDeliveryDate"] = "2026-05-18T00:00:00"
    return sample


SAMPLES = {
    "delivered": _delivered_with_eta,
    "active": out_for_delivery_sample,
    "returning": returned_item,
    "moment_only": _moment_only,
    "date_only": _date_only,
    "bare": lambda: {"trackingNumber": "TEST_BARE"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_history_is_none_only_when_the_option_is_off():
    assert normalize_parcel(returned_item())["history"] is None
    assert normalize_parcel(returned_item(), include_history=True)["history"]
    assert normalize_parcel({}, include_history=True)["history"] == []


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_a_returned_parcel_is_not_delivered():
    parcel = normalize_parcel(returned_item())
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_parcel_keeps_its_pickup_flag():
    # No pickup-point delivery is mapped, so a delivered parcel stays false;
    # the helper is what keeps a collected pickup-point parcel true.
    parcel = normalize_parcel(delivered_sample())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])


def test_a_moment_ends_at_the_end_of_that_day():
    parcel = normalize_parcel(_moment_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute) == (13, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == start.date()


def test_a_date_covers_the_whole_day():
    parcel = normalize_parcel(_date_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()


def test_planned_window_shapes():
    assert planned_window(date(2026, 5, 18), date(2026, 5, 20), zone=UTC) == (
        "2026-05-18T00:00:00+00:00",
        "2026-05-20T23:59:59+00:00",
    )
    assert planned_window(
        datetime(2026, 5, 18, 9), datetime(2026, 5, 18, 12), zone=UTC
    ) == ("2026-05-18T09:00:00+00:00", "2026-05-18T12:00:00+00:00")
    assert planned_window("2026-05-18T23:59:59+00:00", None, zone=UTC) == (
        "2026-05-18T23:59:59+00:00",
        None,
    )
    assert planned_window(None, "bad", zone=UTC) == (None, None)
    assert planned_window(5, None, zone=UTC) == (None, None)
