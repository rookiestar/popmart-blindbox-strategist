"""Shared synthetic session builders for lifecycle/report tests."""

from __future__ import annotations

import copy


_MISSING = object()


def make_box(box_id, excluded, **extra):
    payload = {
        "id": str(box_id),
        "excluded": list(excluded),
        "status": "available",
    }
    payload.update(extra)
    return payload


def make_unique_tray(
    tray_id,
    exclusions,
    *,
    designs,
    hint_labels=None,
    participation=None,
):
    model = {"type": "unique_regular", "designs": list(designs)}
    if hint_labels is not None:
        model["hint_labels"] = list(hint_labels)
    tray = {
        "id": tray_id,
        "model": model,
        "boxes": [
            make_box(index + 1, excluded)
            for index, excluded in enumerate(exclusions)
        ],
    }
    if participation is not None:
        tray["participation"] = participation
    return tray


def make_session_payload(
    trays,
    events,
    *,
    series,
    preferences,
    tools,
    active=None,
    accepted=None,
    candidate=_MISSING,
    draws_used=None,
):
    if draws_used is None:
        draws_used = sum(
            1 for event in events if event["type"] == "opened_result"
        )
    payload = {
        "session_schema_version": 1,
        "series": series,
        "active_tray_id": active or trays[0]["id"],
        "accepted_tray_id": accepted,
        "draws_used": int(draws_used),
        "preferences": copy.deepcopy(preferences),
        "tools": copy.deepcopy(tools),
        "trays": copy.deepcopy(trays),
        "events": copy.deepcopy(events),
        "meta": {"provenance": "synthetic"},
    }
    if candidate is not _MISSING:
        payload["candidate_tray_id"] = candidate
    return payload
