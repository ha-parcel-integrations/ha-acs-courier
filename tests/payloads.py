"""Sample ACS Courier API payloads shared by the test modules."""

from __future__ import annotations

import copy
import json
from pathlib import Path

CODE = "TEST_CODE"
ACTIVE_CODE = "TEST_ACTIVE"
DELIVERED_CODE = "TEST_DELIVERED"

# ``4`` is the real delivered id, confirmed on captured parcels. Out for
# delivery has no observed id yet, so conftest maps this synthetic one for the
# tests that need such a parcel.
SYNTHETIC_OFD_ID = 998
DELIVERED_ID = 4

_FIXTURE = Path(__file__).parent / "fixtures" / "returned_to_sender.json"


def returned_item(code: str = CODE) -> dict:
    """The item of a real, redacted returned-to-sender response."""
    item = json.loads(_FIXTURE.read_text())["items"][0]
    item["trackingNumber"] = code
    return item


def envelope(item: dict) -> dict:
    """Wrap an item the way the API does."""
    return {"count": 1, "items": [item]}


def not_found_envelope() -> dict:
    """The semantic miss: HTTP 200 with a placeholder item."""
    return envelope(
        {
            "trackingNumber": "0000000000",
            "notes": "Shipment not found",
            "shipmentStatusId": 0,
            "isDelivered": False,
            "isReturned": False,
            "statusHistory": [],
        }
    )


def event(pid: int, date: str, text: str = "Στο κέντρο διαλογής", parent=None) -> dict:
    """One ``statusHistory`` record."""
    record = {
        "controlPid": pid,
        "controlPointDate": date,
        "description": "",
        "controlPoint": text,
        "info": "",
    }
    if parent is not None:
        record["controlParentPid"] = parent
    return record


def in_progress_item(code: str = ACTIVE_CODE, status_id: int = 99) -> dict:
    """A synthetic parcel with an unobserved status id and a mappable history."""
    item = copy.deepcopy(returned_item(code))
    item.update(
        {
            "shipmentStatusId": status_id,
            "isDelivered": False,
            "isReturned": False,
            "deliveryDate": None,
            "notes": "",
            "statusHistory": [
                event(100, "2026-04-21T00:00:00", "Παραλαβή αποστολής"),
                event(300, "2026-04-22T06:01:27", "Στο αρμόδιο κατάστημα παράδοσης"),
            ],
        }
    )
    return item


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel the integration reports as in transit."""
    return in_progress_item(code)


def out_for_delivery_sample(code: str = ACTIVE_CODE) -> dict:
    """A synthetic out-for-delivery parcel (id mapped only inside tests)."""
    return in_progress_item(code, SYNTHETIC_OFD_ID)


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel, on the carrier's own delivered id."""
    item = in_progress_item(code, DELIVERED_ID)
    item["deliveryDate"] = "2026-04-29T13:12:42"
    return item
