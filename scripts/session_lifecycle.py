"""Pure lifecycle reducer for blind-box multi-tray sessions.

This module deliberately knows nothing about posterior math or report
rendering. It is the single source of truth for commitment, acceptance,
release, switching, and the single-tray auto-commit rule.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence


ACTION_EVENT_TYPES = frozenset({"hint_used", "display_used", "opened_result"})
COMMITMENT_SOURCES = frozenset({"explicit_event", "first_tool_or_open"})
ACCEPTANCE_SOURCES = frozenset({"explicit_event", "quality_lines_upgrade"})


class LifecycleError(ValueError):
    """Raised when the event ledger violates the lifecycle contract."""


def reduce_lifecycle_events(
    events: Sequence[Mapping[str, Any]],
    *,
    tray_count: int,
) -> Dict[str, Any]:
    """Reduce an ordered event ledger into one auditable lock state."""
    accepted_tray_id: Optional[str] = None
    candidate_tray_id: Optional[str] = None
    candidate_source: Optional[str] = None
    accepted_source: Optional[str] = None
    accepted_commitment_source: Optional[str] = None
    candidate_action_seen = False
    current_event_tray: Optional[str] = None
    auto_commitments: List[Dict[str, Any]] = []

    for event in events:
        event_type = str(event["type"])
        tray_id = str(event["tray_id"])

        if event_type == "tray_switch":
            if accepted_tray_id is not None and tray_id != accepted_tray_id:
                raise LifecycleError(
                    "release the accepted tray before switching to another tray"
                )
            if candidate_tray_id is not None and tray_id != candidate_tray_id:
                raise LifecycleError(
                    "release the candidate tray before switching to another tray"
                )
            current_event_tray = tray_id
            continue

        if event_type == "tray_committed":
            if current_event_tray is not None and tray_id != current_event_tray:
                raise LifecycleError(
                    "tray_committed must target the current event tray"
                )
            if accepted_tray_id == tray_id:
                raise LifecycleError(
                    "the accepted tray is already locked; tray_committed "
                    "only records a new candidate commitment"
                )
            if accepted_tray_id is not None:
                raise LifecycleError(
                    "release the accepted tray before committing to another tray"
                )
            if candidate_tray_id == tray_id:
                raise LifecycleError(
                    "the candidate tray is already committed; tray_committed "
                    "only records a new commitment"
                )
            if candidate_tray_id is not None:
                raise LifecycleError(
                    "release the candidate tray before committing to another "
                    "tray; tray_committed only records a new commitment"
                )
            source = str(event.get("source", "explicit_event"))
            if source not in COMMITMENT_SOURCES:
                raise LifecycleError("tray_committed source is invalid")
            if source == "first_tool_or_open" and tray_count != 1:
                raise LifecycleError(
                    "first_tool_or_open auto commitment is valid only for a "
                    "single-tray session"
                )
            candidate_tray_id = tray_id
            candidate_source = source
            candidate_action_seen = False
            if source == "first_tool_or_open":
                auto_commitments.append(
                    {
                        "seq": int(event["seq"]),
                        "tray_id": tray_id,
                        "trigger_seq": int(event.get("trigger_seq", event["seq"])),
                        "trigger": str(event.get("trigger", "")),
                        "source": source,
                    }
                )
            continue

        if event_type == "tray_accepted":
            if current_event_tray is not None and tray_id != current_event_tray:
                raise LifecycleError(
                    "tray_accepted must target the current event tray"
                )
            if accepted_tray_id is not None:
                raise LifecycleError(
                    "release the accepted tray before accepting another tray"
                )
            if candidate_tray_id is not None and candidate_tray_id != tray_id:
                raise LifecycleError(
                    "release the candidate tray before accepting another tray"
                )
            source = str(event.get("source", "explicit_event"))
            if source not in ACCEPTANCE_SOURCES:
                raise LifecycleError("tray_accepted source is invalid")
            if source == "quality_lines_upgrade" and (
                candidate_tray_id != tray_id or not candidate_action_seen
            ):
                raise LifecycleError(
                    "quality_lines_upgrade requires a candidate tray with a "
                    "real card/open action"
                )
            accepted_commitment_source = (
                candidate_source if source == "quality_lines_upgrade" else None
            )
            candidate_tray_id = None
            candidate_source = None
            candidate_action_seen = False
            accepted_tray_id = tray_id
            accepted_source = source
            continue

        if event_type == "tray_released":
            if accepted_tray_id == tray_id:
                accepted_tray_id = None
                accepted_source = None
                accepted_commitment_source = None
            elif candidate_tray_id == tray_id:
                candidate_tray_id = None
                candidate_source = None
                candidate_action_seen = False
            else:
                raise LifecycleError(
                    "tray_released must target the currently accepted or "
                    "candidate tray"
                )
            continue

        if event_type == "stop_rule_override":
            if current_event_tray is not None and tray_id != current_event_tray:
                raise LifecycleError(
                    "stop_rule_override must target the current event tray"
                )
            continue

        if event_type not in ACTION_EVENT_TYPES:
            continue

        if tray_id not in {accepted_tray_id, candidate_tray_id}:
            if accepted_tray_id is not None or candidate_tray_id is not None:
                raise LifecycleError(
                    f"session event {event['seq']}: release the locked tray "
                    "before recording an action on another tray"
                )
            if tray_count != 1:
                raise LifecycleError(
                    f"session event {event['seq']}: multi-tray actions require "
                    "an explicit tray_committed event before using a card or "
                    "opening a box"
                )
            candidate_tray_id = tray_id
            candidate_source = "first_tool_or_open"
            candidate_action_seen = True
            auto_commitments.append(
                {
                    "seq": int(event["seq"]),
                    "tray_id": tray_id,
                    "trigger_seq": int(event["seq"]),
                    "trigger": event_type,
                    "source": "first_tool_or_open",
                }
            )
        elif candidate_tray_id == tray_id:
            candidate_action_seen = True

    return {
        "active_event_tray_id": current_event_tray,
        "accepted_tray_id": accepted_tray_id,
        "candidate_tray_id": candidate_tray_id,
        "candidate_source": candidate_source,
        "accepted_source": accepted_source,
        "accepted_commitment_source": accepted_commitment_source,
        "candidate_action_seen": candidate_action_seen,
        "auto_commitments": auto_commitments,
    }


def inject_derived_lifecycle_events(
    events: Sequence[Mapping[str, Any]],
    *,
    auto_commitments: Sequence[Mapping[str, Any]],
    acceptance_points: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Insert derived commitments/acceptances and return a contiguous ledger."""
    auto_by_seq = {int(item["trigger_seq"]): item for item in auto_commitments}
    acceptance_by_seq = {
        int(item["trigger_seq"]): item for item in acceptance_points
    }
    canonical: List[Dict[str, Any]] = []
    canonical_auto: List[Dict[str, Any]] = []
    canonical_acceptances: List[Dict[str, Any]] = []

    for original in events:
        original_seq = int(original["seq"])
        auto = auto_by_seq.get(original_seq)
        commitment: Optional[Dict[str, Any]] = None
        if auto is not None:
            commitment = {
                "seq": len(canonical) + 1,
                "type": "tray_committed",
                "tray_id": str(auto["tray_id"]),
                "source": "first_tool_or_open",
                "trigger": str(auto["trigger"]),
                "reason": "首次真实用卡或开盒自动记录候选承诺",
            }
            canonical.append(commitment)

        event = copy.deepcopy(dict(original))
        event["seq"] = len(canonical) + 1
        canonical.append(event)

        if commitment is not None:
            commitment["trigger_seq"] = event["seq"]
            canonical_auto.append(
                {
                    "seq": commitment["seq"],
                    "tray_id": commitment["tray_id"],
                    "trigger_seq": event["seq"],
                    "trigger": commitment["trigger"],
                    "source": commitment["source"],
                }
            )

        acceptance = acceptance_by_seq.get(original_seq)
        if acceptance is not None:
            accepted_event = {
                "seq": len(canonical) + 1,
                "type": "tray_accepted",
                "tray_id": str(acceptance["tray_id"]),
                "source": "quality_lines_upgrade",
                "trigger_seq": event["seq"],
                "reason": "真实线索使全部质量线通过，自动升级为已接受",
            }
            canonical.append(accepted_event)
            canonical_acceptances.append(copy.deepcopy(accepted_event))

    return {
        "events": canonical,
        "auto_commitments": canonical_auto,
        "auto_acceptances": canonical_acceptances,
    }


def summarize_lifecycle(
    *,
    accepted_tray_id: Optional[str],
    candidate_tray_id: Optional[str],
    commitment_source: Optional[str],
    accepted_source: Optional[str] = None,
    accepted_commitment_source: Optional[str] = None,
    candidate_qualified: bool = False,
) -> Dict[str, Any]:
    """Return the stable reader-facing open/candidate/accepted block."""
    legacy_upgrade = bool(
        accepted_tray_id is None
        and candidate_tray_id is not None
        and candidate_qualified
    )
    persisted_upgrade = bool(
        accepted_tray_id is not None
        and accepted_source == "quality_lines_upgrade"
    )
    upgraded_from_candidate = legacy_upgrade or persisted_upgrade
    if accepted_tray_id is not None:
        phase = "accepted"
        accepted_via = accepted_source or "explicit_event"
    elif legacy_upgrade:
        phase = "accepted"
        accepted_via = "quality_lines_upgrade"
    elif candidate_tray_id is not None:
        phase = "candidate"
        accepted_via = None
    else:
        phase = "open"
        accepted_via = None
    labels = {
        "open": "未承诺（可自由换端）",
        "candidate": "候选承诺（已选定，尚未达到全部质量线）",
        "accepted": "已接受（锁定）",
    }
    effective_commitment_source = commitment_source
    if persisted_upgrade:
        effective_commitment_source = accepted_commitment_source
    return {
        "phase": phase,
        "phase_label": labels[phase],
        "accepted_tray_id": accepted_tray_id,
        "candidate_tray_id": candidate_tray_id,
        "commitment_source": (
            effective_commitment_source if phase != "open" else None
        ),
        "upgraded_from_candidate": upgraded_from_candidate,
        "upgrade_basis": (
            "real_clues_pass_all_quality_lines"
            if upgraded_from_candidate
            else None
        ),
        "accepted_via": accepted_via,
        "release_required_before_switch": phase != "open",
    }
