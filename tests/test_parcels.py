"""Tests for the pure parcel-mapping helpers."""
import logging
from datetime import datetime, timedelta, timezone

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.acs_courier.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.acs_courier.parcels import (
    apply_delivered_filter,
    build_history,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    usable_events,
)

from .payloads import (
    delivered_sample,
    event,
    in_progress_item,
    out_for_delivery_sample,
    returned_item,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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

# ---------------------------------------------------------------------------
# status mapping
# ---------------------------------------------------------------------------


def test_status_7_is_returning_even_though_is_delivered_is_true():
    """isDelivered is true on a returned parcel; the id decides."""
    raw = returned_item()
    assert raw["isDelivered"] is True
    parcel = normalize_parcel(raw)
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_missing_status_id_is_unknown_without_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status(None) is ParcelStatus.UNKNOWN
    assert not caplog.records


def test_unmapped_id_falls_back_to_newest_mappable_event(caplog):
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(in_progress_item())
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Στο αρμόδιο κατάστημα παράδοσης"
    assert not [r for r in caplog.records if "shipmentStatusId" in r.getMessage()]


def test_unmapped_id_without_mappable_event_is_unknown_and_warns_once(caplog):
    raw = in_progress_item()
    raw["statusHistory"] = []
    with caplog.at_level(logging.WARNING):
        first = normalize_parcel(raw)
        normalize_parcel(raw)
    assert first["status"] is ParcelStatus.UNKNOWN
    assert first["raw_status"] == "99"
    warnings = [r for r in caplog.records if "shipmentStatusId=99" in r.getMessage()]
    assert len(warnings) == 1
    assert "unrecognised_status.yml" in warnings[0].getMessage()


def test_null_id_uses_a_mappable_event():
    raw = in_progress_item()
    raw["shipmentStatusId"] = None
    assert normalize_parcel(raw)["status"] is ParcelStatus.IN_TRANSIT


def test_resolved_shapes_do_not_warn(caplog):
    # pickupStation (origin depot) and the Athens timezone are settled, and
    # expectedDeliveryDate is mapped to planned_from, so a normal parcel is silent.
    raw = returned_item()
    with caplog.at_level(logging.WARNING):
        parcel = normalize_parcel(raw)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "pickupStation" not in text
    assert "Europe/Athens" not in text
    assert "expectedDeliveryDate" not in text
    assert parcel["planned_from"] == "2026-05-18T00:00:00+03:00"


def test_unmapped_event_pid_warns_once_and_keeps_null_status(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_event_status(event(777, "2026-01-01T00:00:00")) is None
        assert map_event_status(event(777, "2026-01-01T00:00:00")) is None
    assert len([r for r in caplog.records if "controlPid=777" in r.getMessage()]) == 1


def test_event_without_pid_is_silently_unmapped(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_event_status({"controlPointDate": "2026-01-01T00:00:00"}) is None
    assert not caplog.records


def test_children_inherit_parent_but_issue_resolution_does_not(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_event_status(event(201, "2026-01-01", parent=200)) is ParcelStatus.IN_TRANSIT
        assert map_event_status(event(306, "2026-01-01", parent=300)) is None
    assert not caplog.records


def test_delivered_branch_uses_delivery_date_then_last_event():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42+03:00"
    raw = delivered_sample()
    raw["deliveryDate"] = None
    assert normalize_parcel(raw)["delivered_at"] == "2026-04-22T06:01:27+03:00"
    raw["statusHistory"] = []
    assert normalize_parcel(raw)["delivered_at"] is None


def test_date_only_eta_spans_the_whole_day():
    parcel = normalize_parcel(out_for_delivery_sample())
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["planned_from"] == "2026-05-18T00:00:00+03:00"
    assert parcel["planned_to"] == "2026-05-18T23:59:59+03:00"


def test_timed_eta_is_a_point_estimate():
    raw = out_for_delivery_sample()
    raw["expectedDeliveryDate"] = "2026-05-18T14:30:00"
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] == "2026-05-18T14:30:00+03:00"
    assert parcel["planned_to"] is None


def test_no_eta_leaves_the_window_empty():
    raw = out_for_delivery_sample()
    raw["expectedDeliveryDate"] = None
    parcel = normalize_parcel(raw)
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_unmapped_history_code_warns_even_when_buried(caplog):
    # A new code older than the status-giving event must still be surfaced,
    # with history off, so the status map can grow from it.
    raw = in_progress_item()
    raw["statusHistory"] = [
        event(888, "2026-04-20T09:00:00", "Νέο στάδιο"),
        event(300, "2026-04-22T06:01:27", "Στο αρμόδιο κατάστημα παράδοσης"),
    ]
    with caplog.at_level(logging.WARNING):
        normalize_parcel(raw)
    assert len([r for r in caplog.records if "controlPid=888" in r.getMessage()]) == 1


# ---------------------------------------------------------------------------
# timestamps and history
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-05-01T10:00:00Z").utcoffset() == timedelta(0)
    assert parse_iso("2026-05-01T10:00:00").tzinfo is not None
    assert parse_iso("nonsense") is None
    assert parse_iso(None) is None


def test_history_is_sorted_by_date_not_by_control_pid():
    """The wire order is by controlPid; pid 200 is dated after its children."""
    events = usable_events(returned_item())
    assert [e["controlPid"] for e in events] == [100, 201, 202, 300, 203, 306, 620, 200]
    dates = [e["controlPointDate"] for e in events]
    assert dates == sorted(dates)


def test_naive_timestamps_are_athens_local():
    history = build_history(usable_events(returned_item()))
    assert history[0]["timestamp"] == "2026-04-21T00:00:00+03:00"
    assert history[-1]["timestamp"] == "2026-05-06T05:30:45.017000+03:00"


def test_history_entries_use_control_point_text_and_mapped_status():
    history = build_history(usable_events(returned_item()))
    assert history[0] == {
        "timestamp": "2026-04-21T00:00:00+03:00",
        "status": ParcelStatus.IN_TRANSIT,
        "raw_status": "Παραλαβή αποστολής",
    }
    resolution = next(h for h in history if h["raw_status"] == "Επίλυση εκκρεμότητας")
    assert resolution["status"] is None


def test_history_falls_back_to_description_and_caps():
    raw = {
        "statusHistory": [
            {"controlPid": 100, "controlPointDate": f"2026-01-{day:02d}T00:00:00", "description": "d"}
            for day in range(1, 5)
        ]
    }
    history = build_history(usable_events(raw), max_events=2)
    assert len(history) == 2
    assert history[0]["raw_status"] == "d"
    assert history[-1]["timestamp"].startswith("2026-01-04")


def test_missing_null_and_malformed_histories():
    assert usable_events({}) == []
    assert usable_events({"statusHistory": None}) == []
    raw = {
        "statusHistory": [
            "junk",
            {"controlPid": 100, "controlPointDate": "not a date"},
            {"controlPid": 100},
            {"controlPid": 100, "controlPointDate": "2026-01-01T00:00:00"},
        ]
    }
    assert len(usable_events(raw)) == 1
    parcel = normalize_parcel({"trackingNumber": "X"}, include_history=True)
    assert parcel["history"] == []
    assert parcel["status"] is ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None


# ---------------------------------------------------------------------------
# normalize_parcel
# ---------------------------------------------------------------------------


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(returned_item())) == CANONICAL_KEYS


def test_normalize_returned_fixture():
    raw = returned_item()
    parcel = normalize_parcel(raw)
    assert parcel["carrier"] == "ACS Courier"
    assert parcel["barcode"] == "TEST_CODE"
    assert parcel["raw_status"] == "Επιστράφηκε στον αποστολέα"
    assert parcel["sender"] == "REDACTED"
    assert parcel["receiver"] == "REDACTED"
    assert parcel["history"] is None
    assert parcel["url"] is not None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None


def test_normalize_keeps_full_raw_record():
    raw = returned_item()
    assert normalize_parcel(raw)["raw"] is raw
    assert "recipientAddress" in normalize_parcel(raw)["raw"]


def test_normalize_history_is_opt_in():
    assert normalize_parcel(returned_item(), include_history=True)["history"]


def test_normalize_blank_party_fields_become_none_and_no_barcode_no_url():
    parcel = normalize_parcel({"sender": "", "recipientName": "", "recipient": ""})
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["url"] is None


def test_receiver_falls_back_to_recipient():
    assert normalize_parcel({"recipient": "R"})["receiver"] == "R"


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_are_known_values():
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    parcel = normalize_parcel(returned_item())
    with_history = normalize_parcel(returned_item(), include_history=True)
    if "url" in CAPABILITIES:
        assert parcel["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"]
    if "delivery_window" in CAPABILITIES:
        assert parcel["planned_from"] is not None


def test_pending_and_undeclared_fields_are_none():
    """Pending fields stay None until a real parcel confirms them."""
    parcel = normalize_parcel(in_progress_item())
    for field, keys in {
        "delivery_window": ("planned_from", "planned_to"),
        "pickup_point": ("pickup_point",),
        "weight": ("weight",),
        "dimensions": ("dimensions",),
    }.items():
        if field not in CAPABILITIES:
            assert all(parcel[key] is None for key in keys)


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
