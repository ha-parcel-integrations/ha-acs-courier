"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-acs-courier/issues/new"
    "?template=unrecognised_status.yml"
)

# The carrier's tracking-point times carry no offset. ACS is a Greek domestic
# carrier (GR/CY, both EET/EEST), so a naive time is Athens local time.
CARRIER_TIMEZONE = "Europe/Athens"

# Item-level ``shipmentStatusId``. Only values seen on a real parcel are
# mapped; the rest fall back to the newest mappable event, then ``unknown``.
_STATUS_MAP: dict[int, ParcelStatus] = {
    4: ParcelStatus.DELIVERED,
    7: ParcelStatus.RETURNING,
}

# History ``controlPid`` values. Children (``controlParentPid``) inherit their
# parent's meaning unless listed themselves.
#
# 400 is deliberately absent: the same code carries both "with a courier for
# delivery" and "in an ACS Smartpoint locker", told apart only by the Greek
# label, and it arrives without a ``controlPointDate`` often enough to be
# dropped before it is read. Mapping it either way would be wrong about half
# the time, so it keeps warning until an in-flight parcel settles it.
_EVENT_MAP: dict[int, ParcelStatus] = {
    100: ParcelStatus.IN_TRANSIT,
    200: ParcelStatus.IN_TRANSIT,
    300: ParcelStatus.IN_TRANSIT,
    600: ParcelStatus.DELIVERED,
    620: ParcelStatus.RETURNING,
}

# Seen events that say nothing about where the parcel is in its journey. 404 is
# the notification ACS sent about a delivery, not the delivery itself.
_NEUTRAL_EVENTS = frozenset({306, 404})

# Codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(kind: str, code: Any) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    key = f"{kind}:{code}"
    if key in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(key)
    _LOGGER.warning(
        "Unrecognised ACS Courier status — help us map it. Open an issue "
        "and paste this line: %s\n  %s=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        kind,
        code,
    )


def map_event_status(event: dict) -> ParcelStatus | None:
    """Map a history entry to a canonical status, or ``None``.

    An unmapped entry keeps ``status: null`` (rather than ``unknown``, so a
    consumer can tell "no mapping" from "mapped to unknown") and warns once.
    """
    pid = event.get("controlPid")
    if pid in _NEUTRAL_EVENTS:
        return None
    if pid in _EVENT_MAP:
        return _EVENT_MAP[pid]
    parent = event.get("controlParentPid")
    if parent in _EVENT_MAP:
        return _EVENT_MAP[parent]
    if pid is not None:
        _warn_unmapped_status("controlPid", pid)
    return None


def map_parcel_status(code: Any, events: list[dict] | None = None) -> ParcelStatus:
    """Map ``shipmentStatusId``; else the newest mappable event; else ``unknown``.

    A missing id reports ``unknown`` silently when no event can stand in; an
    unrecognised one warns once, but only when no event can stand in.
    """
    mapped = _STATUS_MAP.get(code) if code is not None else None
    if mapped is not None:
        return mapped
    for event in reversed(events or []):
        mapped = map_event_status(event)
        if mapped is not None:
            return mapped
    if code is not None:
        _warn_unmapped_status("shipmentStatusId", code)
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _carrier_datetime(value: Any) -> datetime | None:
    """Return an offset-aware datetime for a carrier time, or ``None``."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(CARRIER_TIMEZONE))
    return parsed


def usable_events(raw: dict) -> list[dict]:
    """Return the dated history events, oldest first.

    The wire order follows ``controlPid``, not time, so events are sorted by
    ``controlPointDate``; ties keep the wire order.
    """
    history = raw.get("statusHistory")
    dated = [
        (parsed, event)
        for event in (history if isinstance(history, list) else [])
        if isinstance(event, dict)
        and (parsed := _carrier_datetime(event.get("controlPointDate"))) is not None
    ]
    dated.sort(key=lambda pair: pair[0])
    return [event for _, event in dated]


def _event_text(event: dict) -> str | None:
    """Return the event's label; ``description`` is empty on live payloads."""
    return event.get("controlPoint") or event.get("description") or None


def build_history(
    events: list[dict], *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from oldest-first events.

    Each entry is ``{timestamp, status, raw_status}``, top-level (not under
    ``raw``) so it survives the aggregator's ``strip_raw()``, capped to the
    most recent ``max_events``.
    """
    return [
        {
            "timestamp": _carrier_datetime(event.get("controlPointDate")).isoformat(),
            "status": map_event_status(event),
            "raw_status": _event_text(event),
        }
        for event in events
    ][-max_events:]


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**; a key is ``None`` when
    the carrier does not expose it. ``raw`` is the full item, untouched —
    privacy lives in diagnostics.

    ``isDelivered`` is not consulted: it is true on a returned parcel too.
    """
    # pickupStation is the origin depot (it pairs with a destination* field set
    # and a collection pickupDate), not a recipient pickup point, so it has no
    # canonical home and lives only in raw.
    events = usable_events(raw)
    # Surface every unmapped history code once, not only those above the
    # status-giving event, so a code buried deeper still prompts a report.
    for event in events:
        map_event_status(event)
    status = map_parcel_status(raw.get("shipmentStatusId"), events)
    delivered = status is ParcelStatus.DELIVERED

    stamping = next(
        (event for event in reversed(events) if map_event_status(event) is status),
        events[-1] if events else None,
    )
    raw_status = _event_text(stamping) if stamping else None
    if raw_status is None and raw.get("shipmentStatusId") is not None:
        raw_status = str(raw["shipmentStatusId"])

    delivered_at = None
    if delivered:
        moment = _carrier_datetime(raw.get("deliveryDate"))
        if moment is None and events:
            moment = _carrier_datetime(events[-1].get("controlPointDate"))
        delivered_at = moment.isoformat() if moment else None

    eta = _carrier_datetime(raw.get("expectedDeliveryDate"))
    planned_from = planned_to = None
    if eta is not None:
        planned_from = eta.isoformat()
        # expectedDeliveryDate is a date (midnight), so the ETA covers the whole
        # day; give the window an end so the calendar spans it. A real time, were
        # it ever to appear, is a point estimate and keeps planned_to = None.
        if (eta.hour, eta.minute, eta.second) == (0, 0, 0):
            planned_to = eta.replace(hour=23, minute=59, second=59).isoformat()

    return {
        "carrier": "ACS Courier",
        "barcode": raw.get("trackingNumber"),
        "sender": raw.get("sender") or None,
        "receiver": raw.get("recipientName") or raw.get("recipient") or None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": False,
        "pickup_point": None,
        "url": TRACKING_URL if raw.get("trackingNumber") else None,
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
