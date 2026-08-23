#!/usr/bin/env python3
"""Exact posterior solver for case-based blind-box selection.

The core model is a bipartite perfect matching: each box receives exactly one
item and each item in the selected scenario appears exactly once. Exclusions,
known reveals, sold-but-unknown boxes, and opened boxes are all conditioned on
jointly; boxes are never treated as independent.

The script uses only Python's standard library.
"""

from __future__ import annotations

import argparse
import copy
import functools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


class StateError(ValueError):
    """Raised when the input state is inconsistent or underspecified."""


AVAILABLE_STATUS = "available"
DRAWABLE_STATUSES = {AVAILABLE_STATUS}
KNOWN_NON_DRAWABLE_STATUSES = {"opened"}
UNKNOWN_NON_DRAWABLE_STATUSES = {"sold_unknown", "unavailable_unknown", "reserved_unknown"}
ALLOWED_STATUSES = DRAWABLE_STATUSES | KNOWN_NON_DRAWABLE_STATUSES | UNKNOWN_NON_DRAWABLE_STATUSES

STRATEGY_NAMES = {
    "risk_first": "稳妥避雷",
    "guardrail": "守住底线",
    "balanced": "整体最满意",
    "target_only": "随便中个喜欢",
    "top_target_first": "只冲最爱",
    "resale_ev": "保值优先",
}
STRATEGY_RULES = {
    "risk_first": "先压低排序靠前的不喜欢款风险，再比较其他不喜欢款和喜欢款概率。",
    "guardrail": "只在硬雷概率不超过上限的盒中选平均评分最高者；若都超线则停止抽盒。",
    "balanced": "直接选择概率加权后的平均评分最高者，高分款可以补偿低分款风险。",
    "target_only": "选择命中任一喜欢款概率最高者，喜欢顺序只用于打破平局。",
    "top_target_first": "先选择命中第一喜欢款概率最高者，再依次比较其他喜欢款。",
    "resale_ev": "选择概率加权后的预期二手价值最高者。",
}
STRATEGY_ALIASES = {
    **{mode: mode for mode in STRATEGY_NAMES},
    **{name: mode for mode, name in STRATEGY_NAMES.items()},
    "先避雷": "risk_first",
    "最讨厌款优先避开": "risk_first",
    "硬雷优先": "risk_first",
    "避雷优先": "risk_first",
    "守底线": "guardrail",
    "底线内最优": "guardrail",
    "硬雷不过线，再选高分": "guardrail",
    "硬雷门槛＋综合评分": "guardrail",
    "硬雷门槛+综合评分": "guardrail",
    "总体最满意": "balanced",
    "平均评分最高": "balanced",
    "综合评分最高": "balanced",
    "喜欢就行": "target_only",
    "任一喜欢款概率最高": "target_only",
    "任一喜欢优先": "target_only",
    "纯冲喜欢": "target_only",
    "最爱款概率最高": "top_target_first",
    "最爱优先": "top_target_first",
    "第一目标优先": "top_target_first",
    "优先保值": "resale_ev",
    "预期二手价值最高": "resale_ev",
    "二手价值优先": "resale_ev",
}

SCORE_TIER_KEYS = (
    "favorite",
    "liked",
    "acceptable",
    "neutral",
    "neutral_disappointed",
    "light_dislike",
    "hard_avoid",
)

SESSION_SCHEMA_VERSION = 1
SESSION_EVENT_TYPES = {
    "tray_switch",
    "hint_used",
    "display_used",
    "opened_result",
    "tray_accepted",
    "tray_released",
    "stop_rule_override",
}
STOP_RULE_KEYS = {
    "min_like_any_pp",
    "min_favorite_any_pp",
    "max_dislike_any_pp",
    "max_hard_avoid_pp",
    "min_expected_score",
    "max_draws",
}
HINT_MECHANISM_TYPES = {"uniform_wrong_label"}
HINT_MECHANISM_STATUSES = {"assumed", "confirmed"}


@dataclass(frozen=True)
class Scenario:
    name: str
    prior: float
    designs: Tuple[str, ...]


@dataclass
class ScenarioResult:
    name: str
    prior: float
    valid_assignments: int
    marginal_counts: Dict[str, Dict[str, int]]


@dataclass
class PosteriorResult:
    marginals: Dict[str, Dict[str, float]]
    scenario_results: List[ScenarioResult]
    scenario_posteriors: Dict[str, float]
    evidence_weight: float
    exact_valid_assignments: Optional[int]


def _read_json(path: str) -> Dict[str, Any]:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _stable_box_sort_key(box_id: str) -> Tuple[int, Any]:
    try:
        return (0, int(box_id))
    except (TypeError, ValueError):
        return (1, str(box_id))


def _score_tier(score: float) -> str:
    if score == 10:
        return "favorite"
    if 6 <= score < 10:
        return "liked"
    if 0 < score < 6:
        return "acceptable"
    if score == 0:
        return "neutral"
    if -5 < score < 0:
        return "neutral_disappointed"
    if -9 < score <= -5:
        return "light_dislike"
    if -10 <= score <= -9:
        return "hard_avoid"
    raise StateError("preference scores must be between -10 and 10")


def _build_score_tiers(scores: Mapping[str, float]) -> Dict[str, List[str]]:
    tiers = {key: [] for key in SCORE_TIER_KEYS}
    for label, score in scores.items():
        tiers[_score_tier(score)].append(label)

    for key in ("favorite", "liked", "acceptable"):
        tiers[key].sort(key=lambda label: (-scores[label], label))
    tiers["neutral"].sort()
    for key in ("neutral_disappointed", "light_dislike", "hard_avoid"):
        tiers[key].sort(key=lambda label: (scores[label], label))
    return tiers


def _normalize_hint_mechanism(model: MutableMapping[str, Any]) -> Dict[str, str]:
    raw = model.get("hint_mechanism")
    if raw is None:
        mechanism = {
            "type": "uniform_wrong_label",
            "status": "assumed",
        }
    elif isinstance(raw, str):
        mechanism = {
            "type": raw.strip(),
            "status": "assumed",
        }
    elif isinstance(raw, Mapping):
        mechanism = {
            "type": str(raw.get("type", "")).strip(),
            "status": str(raw.get("status", "assumed")).strip(),
        }
    else:
        raise StateError("model.hint_mechanism must be a string or object")

    if mechanism["type"] not in HINT_MECHANISM_TYPES:
        raise StateError(
            "model.hint_mechanism.type must be uniform_wrong_label"
        )
    if mechanism["status"] not in HINT_MECHANISM_STATUSES:
        raise StateError(
            "model.hint_mechanism.status must be assumed or confirmed"
        )
    model["hint_mechanism"] = mechanism
    return mechanism


def _normalize_state(raw: Mapping[str, Any]) -> Dict[str, Any]:
    state = copy.deepcopy(dict(raw))
    boxes = state.get("boxes")
    if not isinstance(boxes, list) or not boxes:
        raise StateError("state.boxes must be a non-empty list")

    seen_ids: set[str] = set()
    for box in boxes:
        if not isinstance(box, dict):
            raise StateError("each box must be an object")
        box_id = str(box.get("id", "")).strip()
        if not box_id:
            raise StateError("each box requires a non-empty id")
        if box_id in seen_ids:
            raise StateError(f"duplicate box id: {box_id}")
        seen_ids.add(box_id)
        box["id"] = box_id
        box["excluded"] = sorted(set(str(x) for x in box.get("excluded", [])))
        box["known"] = None if box.get("known") in (None, "") else str(box.get("known"))
        box["status"] = str(box.get("status", AVAILABLE_STATUS))
        if box["status"] not in ALLOWED_STATUSES:
            raise StateError(
                f"box {box_id}: unsupported status {box['status']!r}; "
                f"use one of {sorted(ALLOWED_STATUSES)}"
            )
        box["tool_used"] = bool(box.get("tool_used", False))
        if box["known"] is not None and box["known"] in box["excluded"]:
            raise StateError(f"box {box_id}: known design is also excluded")
        if box["status"] == "opened" and box["known"] is None:
            raise StateError(f"box {box_id}: opened boxes require a known design")

    model = state.get("model")
    if not isinstance(model, dict):
        raise StateError("state.model must be an object")
    model_type = model.get("type", "unique_regular")
    scenarios: List[Scenario] = []
    if model_type == "unique_regular":
        designs = tuple(str(x) for x in model.get("designs", []))
        if len(designs) != len(boxes):
            raise StateError(
                "unique_regular requires exactly one design per box: "
                f"got {len(designs)} designs and {len(boxes)} boxes"
            )
        if len(set(designs)) != len(designs):
            raise StateError("model.designs contains duplicates")
        scenarios.append(Scenario("unique_regular", 1.0, designs))
    elif model_type == "mixture":
        raw_scenarios = model.get("scenarios", [])
        if not raw_scenarios:
            raise StateError("mixture model requires model.scenarios")
        for index, s in enumerate(raw_scenarios):
            name = str(s.get("name", f"scenario_{index + 1}"))
            prior = float(s.get("prior", 0.0))
            designs = tuple(str(x) for x in s.get("designs", []))
            if prior < 0:
                raise StateError(f"scenario {name}: prior must be non-negative")
            if len(designs) != len(boxes):
                raise StateError(
                    f"scenario {name}: expected {len(boxes)} designs, got {len(designs)}"
                )
            if len(set(designs)) != len(designs):
                raise StateError(f"scenario {name}: designs contain duplicates")
            scenarios.append(Scenario(name, prior, designs))
        prior_sum = sum(s.prior for s in scenarios)
        if prior_sum <= 0:
            raise StateError("mixture scenario priors must sum to a positive number")
        scenarios = [Scenario(s.name, s.prior / prior_sum, s.designs) for s in scenarios]
    else:
        raise StateError(f"unsupported model.type: {model_type}")
    _normalize_hint_mechanism(model)

    union_designs = set(d for s in scenarios for d in s.designs)
    hint_labels = model.get("hint_labels")
    if hint_labels is None:
        # For a regular model, all designs are plausible labels. For a mixture,
        # callers should override this when secret items are never shown as hints.
        hint_labels = sorted(union_designs)
    hint_labels = [str(x) for x in hint_labels]
    if len(set(hint_labels)) != len(hint_labels):
        raise StateError("model.hint_labels contains duplicates")

    for box in boxes:
        unknown_labels = set(box["excluded"]) - set(hint_labels) - union_designs
        if unknown_labels:
            raise StateError(
                f"box {box['id']}: exclusions not found in design/hint universe: "
                f"{sorted(unknown_labels)}"
            )
        if box["known"] is not None and box["known"] not in union_designs:
            raise StateError(
                f"box {box['id']}: known design {box['known']!r} is not in any scenario"
            )

    preferences = state.setdefault("preferences", {})
    liked_supplied = "liked" in preferences
    disliked_supplied = "disliked" in preferences
    hard_avoid_supplied = "hard_avoid" in preferences
    explicit_liked = [str(x) for x in preferences.get("liked", [])]
    explicit_disliked = [str(x) for x in preferences.get("disliked", [])]
    explicit_hard_avoid = [str(x) for x in preferences.get("hard_avoid", [])]
    requested_strategy = preferences.get("strategy")
    requested_mode = preferences.get("objective_mode")
    if requested_strategy is not None:
        strategy_key = str(requested_strategy).strip()
        mapped_mode = STRATEGY_ALIASES.get(strategy_key)
        if mapped_mode is None:
            raise StateError(
                "preferences.strategy must be one of "
                f"{list(STRATEGY_NAMES.values())}"
            )
        if requested_mode is not None and str(requested_mode) != mapped_mode:
            raise StateError(
                "preferences.strategy conflicts with preferences.objective_mode"
            )
        preferences["objective_mode"] = mapped_mode
    else:
        preferences["objective_mode"] = str(requested_mode or "risk_first")
    if preferences["objective_mode"] not in STRATEGY_NAMES:
        raise StateError(
            "preferences.objective_mode must be risk_first, target_only, "
            "top_target_first, guardrail, balanced, or resale_ev"
        )
    preferences["strategy"] = STRATEGY_NAMES[preferences["objective_mode"]]

    raw_scores = preferences.get("scores", preferences.get("utility_scores", {}))
    if not isinstance(raw_scores, dict):
        raise StateError("preferences.scores must be an object")
    unknown_score_labels = set(str(k) for k in raw_scores) - union_designs
    if unknown_score_labels:
        raise StateError(
            "preferences.scores contains unknown designs: "
            f"{sorted(unknown_score_labels)}"
        )
    scores: Dict[str, float] = {}
    for label, value in raw_scores.items():
        try:
            score = float(value)
        except (TypeError, ValueError) as exc:
            raise StateError(f"preference score for {label!r} must be numeric") from exc
        if not math.isfinite(score):
            raise StateError(f"preference score for {label!r} must be finite")
        _score_tier(score)
        scores[str(label)] = score

    score_default_supplied = "score_default" in preferences
    if score_default_supplied:
        try:
            score_default = float(preferences["score_default"])
        except (TypeError, ValueError) as exc:
            raise StateError("preferences.score_default must be numeric") from exc
        if not math.isfinite(score_default):
            raise StateError("preferences.score_default must be finite")
        _score_tier(score_default)
        preferences["score_default"] = score_default
        if scores:
            for design in union_designs:
                scores.setdefault(design, score_default)

    score_tiers = _build_score_tiers(scores)
    preferences["score_tiers"] = score_tiers
    preferences["liked"] = (
        explicit_liked
        if liked_supplied
        else score_tiers["favorite"] + score_tiers["liked"]
    )
    preferences["disliked"] = (
        explicit_disliked
        if disliked_supplied
        else score_tiers["hard_avoid"] + score_tiers["light_dislike"]
    )
    preferences["hard_avoid"] = (
        explicit_hard_avoid
        if hard_avoid_supplied
        else list(score_tiers["hard_avoid"])
    )
    derived_source = "scores" if scores else "empty_default"
    preferences["preference_sources"] = {
        "liked": "explicit" if liked_supplied else derived_source,
        "disliked": "explicit" if disliked_supplied else derived_source,
        "hard_avoid": "explicit" if hard_avoid_supplied else derived_source,
    }

    if set(preferences["liked"]) & set(preferences["disliked"]):
        overlap = sorted(set(preferences["liked"]) & set(preferences["disliked"]))
        raise StateError(f"designs cannot be both liked and disliked: {overlap}")
    for label in preferences["liked"] + preferences["disliked"]:
        if label not in union_designs:
            raise StateError(f"preference label {label!r} is not in any scenario")

    scoring_mode = preferences["objective_mode"] in {"guardrail", "balanced"}
    explicit_scoring_strategy = requested_strategy is not None and scoring_mode
    if (
        preferences["objective_mode"] == "guardrail"
        or explicit_scoring_strategy
    ) and not scores:
        raise StateError(
            f"strategy {preferences['strategy']} requires preferences.scores"
        )
    if scoring_mode and scores and set(scores) != union_designs:
        missing_scores = sorted(union_designs - set(scores))
        raise StateError(
            "scoring strategies require every design to have a score; "
            f"missing {missing_scores}. Set preferences.score_default for the rest."
        )
    preferences["scores"] = scores
    preferences["score_source"] = (
        "custom" if scores else "legacy_rank_weights"
    )

    if len(set(preferences["hard_avoid"])) != len(preferences["hard_avoid"]):
        raise StateError("preferences.hard_avoid contains duplicates")
    unknown_hard_avoid = set(preferences["hard_avoid"]) - union_designs
    if unknown_hard_avoid:
        raise StateError(
            "preferences.hard_avoid contains unknown designs: "
            f"{sorted(unknown_hard_avoid)}"
        )
    hard_limit = preferences.get("hard_avoid_max_pp")
    if hard_limit is not None:
        hard_limit = float(hard_limit)
        if not 0 <= hard_limit <= 100:
            raise StateError("preferences.hard_avoid_max_pp must be between 0 and 100")
    preferences["hard_avoid_max_pp"] = hard_limit
    if preferences["objective_mode"] == "guardrail":
        if not preferences["hard_avoid"]:
            raise StateError("strategy 守住底线 requires preferences.hard_avoid")
        if hard_limit is None:
            raise StateError("strategy 守住底线 requires preferences.hard_avoid_max_pp")

    raw_stop_rules = preferences.get("stop_rules", {})
    if not isinstance(raw_stop_rules, dict):
        raise StateError("preferences.stop_rules must be an object")
    stop_rules: Dict[str, float | int] = {}
    for key in (
        "min_like_any_pp",
        "min_favorite_any_pp",
        "max_dislike_any_pp",
        "max_hard_avoid_pp",
    ):
        if key not in raw_stop_rules:
            continue
        value = float(raw_stop_rules[key])
        if not 0 <= value <= 100:
            raise StateError(f"preferences.stop_rules.{key} must be between 0 and 100")
        stop_rules[key] = value
    if "min_expected_score" in raw_stop_rules:
        value = float(raw_stop_rules["min_expected_score"])
        if not math.isfinite(value):
            raise StateError(
                "preferences.stop_rules.min_expected_score must be finite"
            )
        stop_rules["min_expected_score"] = value
    if "max_draws" in raw_stop_rules:
        value = int(raw_stop_rules["max_draws"])
        if value < 0:
            raise StateError("preferences.stop_rules.max_draws must be non-negative")
        stop_rules["max_draws"] = value
    if "max_hard_avoid_pp" in stop_rules and not preferences["hard_avoid"]:
        raise StateError(
            "preferences.stop_rules.max_hard_avoid_pp requires preferences.hard_avoid"
        )
    if "min_favorite_any_pp" in stop_rules and not score_tiers["favorite"]:
        raise StateError(
            "preferences.stop_rules.min_favorite_any_pp requires at least one "
            "design scored +10"
        )
    if "min_expected_score" in stop_rules and not scores:
        raise StateError(
            "preferences.stop_rules.min_expected_score requires preferences.scores"
        )
    preferences["stop_rules"] = stop_rules

    preferences["tie_tolerance_pp"] = float(preferences.get("tie_tolerance_pp", 0.5))
    if (
        not math.isfinite(preferences["tie_tolerance_pp"])
        or preferences["tie_tolerance_pp"] < 0
    ):
        raise StateError("tie_tolerance_pp must be non-negative")
    min_tool_uplift_supplied = "min_tool_uplift_pp" in preferences
    preferences["min_tool_uplift_pp"] = float(
        preferences.get(
            "min_tool_uplift_pp",
            preferences["tie_tolerance_pp"],
        )
    )
    if (
        not math.isfinite(preferences["min_tool_uplift_pp"])
        or preferences["min_tool_uplift_pp"] < 0
    ):
        raise StateError("min_tool_uplift_pp must be non-negative")
    preferences["min_tool_uplift_source"] = (
        "explicit" if min_tool_uplift_supplied else "tie_tolerance_pp"
    )

    tools = state.setdefault("tools", {})
    tools["hint_cards"] = int(tools.get("hint_cards", 0))
    # "display_cards" is the user-facing name; "reveal_cards" remains a
    # backward-compatible alias for older state files.
    tools["display_cards"] = int(
        tools.get("display_cards", tools.get("reveal_cards", 0))
    )
    tools["reveal_cards"] = tools["display_cards"]
    if tools["hint_cards"] < 0 or tools["display_cards"] < 0:
        raise StateError("tool counts must be non-negative")

    state["boxes"] = sorted(boxes, key=lambda b: _stable_box_sort_key(b["id"]))
    state["_scenarios"] = scenarios
    state["_union_designs"] = sorted(union_designs)
    state["_hint_labels"] = hint_labels
    return state


def _normalize_stop_rule_override_value(
    rule: str, value: Any
) -> float | int | None:
    if value is None:
        return None
    if rule == "max_draws":
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise StateError(
                "stop_rule_override max_draws values must be integers or null"
            ) from exc
        if normalized < 0:
            raise StateError(
                "stop_rule_override max_draws values must be non-negative"
            )
        return normalized
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise StateError(
            f"stop_rule_override {rule} values must be numeric or null"
        ) from exc
    if not math.isfinite(normalized):
        raise StateError(
            f"stop_rule_override {rule} values must be finite"
        )
    if rule != "min_expected_score" and not 0 <= normalized <= 100:
        raise StateError(
            f"stop_rule_override {rule} values must be between 0 and 100"
        )
    return normalized


def _normalize_session_event(
    raw_event: Mapping[str, Any],
    expected_seq: int,
    tray_states: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(raw_event, Mapping):
        raise StateError("each session event must be an object")
    event = copy.deepcopy(dict(raw_event))
    try:
        seq = int(event.get("seq"))
    except (TypeError, ValueError) as exc:
        raise StateError("session events require integer seq values") from exc
    if seq != expected_seq:
        raise StateError(
            f"session event seq must be contiguous from 1; expected {expected_seq}, got {seq}"
        )
    event_type = str(event.get("type", "")).strip()
    if event_type not in SESSION_EVENT_TYPES:
        raise StateError(
            f"session event {seq}: unsupported type {event_type!r}; "
            f"use one of {sorted(SESSION_EVENT_TYPES)}"
        )
    tray_id = str(event.get("tray_id", "")).strip()
    if tray_id not in tray_states:
        raise StateError(f"session event {seq}: unknown tray_id {tray_id!r}")
    event["seq"] = seq
    event["type"] = event_type
    event["tray_id"] = tray_id

    if event_type in {"tray_switch", "tray_accepted"}:
        if "reason" in event:
            event["reason"] = str(event["reason"]).strip()
        return event

    if event_type == "tray_released":
        reason = str(event.get("reason", "")).strip()
        if not reason:
            raise StateError(
                f"session event {seq}: tray release requires a concise reason"
            )
        event["reason"] = reason
        return event

    if event_type == "stop_rule_override":
        rule = str(event.get("rule", "")).strip()
        if rule not in STOP_RULE_KEYS:
            raise StateError(
                f"session event {seq}: unsupported stop rule {rule!r}; "
                f"use one of {sorted(STOP_RULE_KEYS)}"
            )
        old_value = _normalize_stop_rule_override_value(
            rule, event.get("old_value")
        )
        new_value = _normalize_stop_rule_override_value(
            rule, event.get("new_value")
        )
        if old_value == new_value:
            raise StateError(
                f"session event {seq}: stop rule override must change the value"
            )
        reason = str(event.get("reason", "")).strip()
        if not reason:
            raise StateError(
                f"session event {seq}: stop rule override requires a concise reason"
            )
        event.update(
            {
                "rule": rule,
                "old_value": old_value,
                "new_value": new_value,
                "reason": reason,
            }
        )
        return event

    box_id = str(event.get("box_id", "")).strip()
    boxes_by_id = {box["id"]: box for box in tray_states[tray_id]["boxes"]}
    if box_id not in boxes_by_id:
        raise StateError(
            f"session event {seq}: unknown box_id {box_id!r} in tray {tray_id!r}"
        )
    event["box_id"] = box_id
    box = boxes_by_id[box_id]

    if event_type == "hint_used":
        excluded = str(event.get("excluded", "")).strip()
        if not excluded or excluded not in box["excluded"] or not box["tool_used"]:
            raise StateError(
                f"session event {seq}: hint result must match the tray state"
            )
        event["excluded"] = excluded
    else:
        design = str(event.get("design", "")).strip()
        if not design or design != box["known"]:
            raise StateError(
                f"session event {seq}: revealed design must match the tray state"
            )
        event["design"] = design
        if event_type == "display_used" and not box["tool_used"]:
            raise StateError(
                f"session event {seq}: display result requires tool_used=true"
            )
        if event_type == "opened_result" and box["status"] != "opened":
            raise StateError(
                f"session event {seq}: opened result requires status=opened"
            )
    return event


def _normalize_session(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize legacy state or a multi-tray session envelope.

    Legacy inputs become an implicit one-tray session internally while their
    CLI report remains backward compatible.
    """
    if not isinstance(raw, Mapping):
        raise StateError("input must be a JSON object")

    if "trays" not in raw:
        state = _normalize_state(raw)
        tray_id = str(
            (state.get("meta") or {}).get("tray_id", "tray-1")
        ).strip() or "tray-1"
        draws_used = sum(
            1 for box in state["boxes"] if box["status"] == "opened"
        )
        state["_tray_id"] = tray_id
        state["_session_draws_used"] = draws_used
        return {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "active_tray_id": tray_id,
            "accepted_tray_id": None,
            "tools": copy.deepcopy(state["tools"]),
            "draws_used": draws_used,
            "events": [],
            "_legacy_input": True,
            "_tray_states": {tray_id: state},
        }

    try:
        version = int(raw.get("session_schema_version", SESSION_SCHEMA_VERSION))
    except (TypeError, ValueError) as exc:
        raise StateError("session_schema_version must be an integer") from exc
    if version != SESSION_SCHEMA_VERSION:
        raise StateError(
            f"unsupported session_schema_version: {version}; "
            f"expected {SESSION_SCHEMA_VERSION}"
        )

    raw_trays = raw.get("trays")
    if not isinstance(raw_trays, list) or not raw_trays:
        raise StateError("session.trays must be a non-empty list")
    shared = {
        key: copy.deepcopy(raw[key])
        for key in ("series", "preferences", "tools", "market_values")
        if key in raw
    }
    if "preferences" not in shared:
        raise StateError("session.preferences must be an object")
    if "tools" not in shared:
        shared["tools"] = {}

    tray_states: Dict[str, Dict[str, Any]] = {}
    for raw_tray in raw_trays:
        if not isinstance(raw_tray, Mapping):
            raise StateError("each session tray must be an object")
        tray = copy.deepcopy(dict(raw_tray))
        tray_id = str(tray.pop("id", "")).strip()
        if not tray_id:
            raise StateError("each session tray requires a non-empty id")
        if tray_id in tray_states:
            raise StateError(f"duplicate tray id: {tray_id}")
        duplicated_globals = sorted(
            key for key in ("preferences", "tools", "market_values") if key in tray
        )
        if duplicated_globals:
            raise StateError(
                f"tray {tray_id}: keep {duplicated_globals} at session level"
            )
        tray_raw = copy.deepcopy(shared)
        tray_raw.update(tray)
        state = _normalize_state(tray_raw)
        state["_tray_id"] = tray_id
        tray_states[tray_id] = state

    active_tray_id = str(raw.get("active_tray_id", "")).strip()
    if active_tray_id not in tray_states:
        raise StateError("active_tray_id must identify one session tray")
    raw_accepted_tray_id = raw.get("accepted_tray_id")
    accepted_tray_id = (
        None
        if raw_accepted_tray_id in (None, "")
        else str(raw_accepted_tray_id).strip()
    )
    if accepted_tray_id is not None and accepted_tray_id not in tray_states:
        raise StateError("accepted_tray_id must identify one session tray")

    opened_total = sum(
        1
        for state in tray_states.values()
        for box in state["boxes"]
        if box["status"] == "opened"
    )
    try:
        draws_used = int(raw.get("draws_used", opened_total))
    except (TypeError, ValueError) as exc:
        raise StateError("session.draws_used must be an integer") from exc
    if draws_used < 0:
        raise StateError("session.draws_used must be non-negative")
    if draws_used != opened_total:
        raise StateError(
            "session.draws_used must equal the opened-box total across all trays"
        )
    for state in tray_states.values():
        state["_session_draws_used"] = draws_used

    raw_events = raw.get("events", [])
    if not isinstance(raw_events, list):
        raise StateError("session.events must be a list")
    events = [
        _normalize_session_event(event, index, tray_states)
        for index, event in enumerate(raw_events, start=1)
    ]
    tool_event_boxes: set[Tuple[str, str]] = set()
    opened_event_boxes: set[Tuple[str, str]] = set()
    lifecycle_lock: Optional[str] = None
    current_event_tray: Optional[str] = None
    override_chains: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        event_type = event["type"]
        tray_id = event["tray_id"]
        if event_type == "tray_switch":
            if lifecycle_lock is not None and tray_id != lifecycle_lock:
                raise StateError(
                    "release the accepted tray before switching to another tray"
                )
            current_event_tray = tray_id
        elif event_type == "tray_accepted":
            if current_event_tray is not None and tray_id != current_event_tray:
                raise StateError(
                    "tray_accepted must target the current event tray"
                )
            if lifecycle_lock is not None:
                raise StateError(
                    "release the accepted tray before accepting another tray"
                )
            lifecycle_lock = tray_id
        elif event_type == "tray_released":
            if lifecycle_lock != tray_id:
                raise StateError(
                    "tray_released must target the currently accepted tray"
                )
            lifecycle_lock = None
        elif event_type == "stop_rule_override":
            if (
                current_event_tray is not None
                and tray_id != current_event_tray
            ):
                raise StateError(
                    "stop_rule_override must target the current event tray"
                )
            override_chains.setdefault(event["rule"], []).append(event)
        elif event_type in {"hint_used", "display_used"}:
            key = (event["tray_id"], event["box_id"])
            if key in tool_event_boxes or key in opened_event_boxes:
                raise StateError(
                    "session events must record at most one tool before opening "
                    f"box {key[1]!r} in tray {key[0]!r}"
                )
            tool_event_boxes.add(key)
        elif event_type == "opened_result":
            key = (event["tray_id"], event["box_id"])
            if key in opened_event_boxes:
                raise StateError(
                    f"session events repeat opened_result for tray/box {key}"
                )
            opened_event_boxes.add(key)

    if lifecycle_lock != accepted_tray_id:
        raise StateError(
            "accepted_tray_id must match the tray acceptance/release event history"
        )
    if accepted_tray_id is not None and accepted_tray_id != active_tray_id:
        raise StateError(
            "the accepted tray must remain active until an explicit release event"
        )

    final_stop_rules = tray_states[active_tray_id]["preferences"]["stop_rules"]
    for rule, rule_events in override_chains.items():
        for previous, current in zip(rule_events, rule_events[1:]):
            if current["old_value"] != previous["new_value"]:
                raise StateError(
                    f"stop_rule_override chain for {rule} is not contiguous"
                )
        final_value = final_stop_rules.get(rule)
        if rule_events[-1]["new_value"] != final_value:
            raise StateError(
                f"latest stop_rule_override for {rule} must match session preferences"
            )

    state_tool_boxes = {
        (tray_id, box["id"])
        for tray_id, state in tray_states.items()
        for box in state["boxes"]
        if box["tool_used"]
    }
    state_opened_boxes = {
        (tray_id, box["id"])
        for tray_id, state in tray_states.items()
        for box in state["boxes"]
        if box["status"] == "opened"
    }
    if tool_event_boxes != state_tool_boxes:
        raise StateError(
            "session tool events must exactly match boxes with tool_used=true"
        )
    if opened_event_boxes != state_opened_boxes:
        raise StateError(
            "session opened_result events must exactly match opened boxes"
        )

    switches = [event for event in events if event["type"] == "tray_switch"]
    if switches and switches[-1]["tray_id"] != active_tray_id:
        raise StateError(
            "active_tray_id must match the latest tray_switch event"
        )

    active_state = tray_states[active_tray_id]
    return {
        "session_schema_version": version,
        "active_tray_id": active_tray_id,
        "accepted_tray_id": accepted_tray_id,
        "tools": copy.deepcopy(active_state["tools"]),
        "draws_used": draws_used,
        "events": events,
        "_legacy_input": False,
        "_tray_states": tray_states,
    }


def _scenario_analysis(state: Mapping[str, Any], scenario: Scenario) -> ScenarioResult:
    boxes: List[Dict[str, Any]] = state["boxes"]
    designs = list(scenario.designs)
    design_set = set(designs)

    known_by_box: Dict[str, str] = {}
    seen_known: set[str] = set()
    for box in boxes:
        known = box["known"]
        if known is None:
            continue
        if known not in design_set or known in box["excluded"] or known in seen_known:
            return ScenarioResult(scenario.name, scenario.prior, 0, {})
        known_by_box[box["id"]] = known
        seen_known.add(known)

    unknown_boxes = [b for b in boxes if b["known"] is None]
    remaining_designs = [d for d in designs if d not in seen_known]
    if len(unknown_boxes) != len(remaining_designs):
        return ScenarioResult(scenario.name, scenario.prior, 0, {})

    design_to_bit = {d: 1 << i for i, d in enumerate(remaining_designs)}
    candidate_masks: Dict[str, int] = {}
    for box in unknown_boxes:
        mask = 0
        excluded = set(box["excluded"])
        for d in remaining_designs:
            if d not in excluded:
                mask |= design_to_bit[d]
        if mask == 0:
            return ScenarioResult(scenario.name, scenario.prior, 0, {})
        candidate_masks[box["id"]] = mask

    ordered_boxes = sorted(
        unknown_boxes,
        key=lambda b: (candidate_masks[b["id"]].bit_count(), _stable_box_sort_key(b["id"])),
    )
    masks = [candidate_masks[b["id"]] for b in ordered_boxes]
    m = len(ordered_boxes)

    @functools.lru_cache(maxsize=None)
    def suffix(i: int, used_mask: int) -> int:
        if i == m:
            return 1
        total = 0
        choices = masks[i] & ~used_mask
        while choices:
            bit = choices & -choices
            choices -= bit
            total += suffix(i + 1, used_mask | bit)
        return total

    total = suffix(0, 0)
    if total == 0:
        return ScenarioResult(scenario.name, scenario.prior, 0, {})

    forward: List[Dict[int, int]] = [{0: 1}]
    for i in range(m):
        nxt: Dict[int, int] = {}
        for used_mask, count in forward[i].items():
            choices = masks[i] & ~used_mask
            while choices:
                bit = choices & -choices
                choices -= bit
                new_mask = used_mask | bit
                nxt[new_mask] = nxt.get(new_mask, 0) + count
        forward.append(nxt)

    marginal_counts: Dict[str, Dict[str, int]] = {
        box["id"]: {d: 0 for d in designs} for box in boxes
    }
    for box_id, known in known_by_box.items():
        marginal_counts[box_id][known] = total

    bit_to_design = {bit: d for d, bit in design_to_bit.items()}
    for i, box in enumerate(ordered_boxes):
        box_id = box["id"]
        for used_mask, prefix_count in forward[i].items():
            choices = masks[i] & ~used_mask
            while choices:
                bit = choices & -choices
                choices -= bit
                completion_count = suffix(i + 1, used_mask | bit)
                if completion_count:
                    marginal_counts[box_id][bit_to_design[bit]] += prefix_count * completion_count

    return ScenarioResult(scenario.name, scenario.prior, total, marginal_counts)


def analyze_posterior(state: Mapping[str, Any]) -> PosteriorResult:
    scenario_results = [_scenario_analysis(state, s) for s in state["_scenarios"]]
    weighted_counts = [r.prior * r.valid_assignments for r in scenario_results]
    evidence_weight = sum(weighted_counts)
    if evidence_weight <= 0:
        raise StateError("no valid complete-case assignments satisfy the supplied constraints")

    boxes = state["boxes"]
    union_designs: List[str] = state["_union_designs"]
    marginals: Dict[str, Dict[str, float]] = {
        box["id"]: {d: 0.0 for d in union_designs} for box in boxes
    }
    scenario_posteriors: Dict[str, float] = {}
    for result, weighted in zip(scenario_results, weighted_counts):
        posterior_s = weighted / evidence_weight
        scenario_posteriors[result.name] = posterior_s
        if result.valid_assignments == 0:
            continue
        for box_id, counts in result.marginal_counts.items():
            for d, count in counts.items():
                marginals[box_id][d] += (
                    result.prior * count / evidence_weight
                )

    # Remove tiny floating artifacts and validate normalization.
    for box_id, probs in marginals.items():
        for d, p in list(probs.items()):
            if abs(p) < 1e-15:
                probs[d] = 0.0
        total_p = sum(probs.values())
        if not math.isclose(total_p, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise StateError(f"internal error: posterior for box {box_id} sums to {total_p}")

    exact_valid_assignments: Optional[int]
    if len(scenario_results) == 1 and math.isclose(scenario_results[0].prior, 1.0):
        exact_valid_assignments = scenario_results[0].valid_assignments
    else:
        exact_valid_assignments = None

    return PosteriorResult(
        marginals=marginals,
        scenario_results=scenario_results,
        scenario_posteriors=scenario_posteriors,
        evidence_weight=evidence_weight,
        exact_valid_assignments=exact_valid_assignments,
    )


def _rank_weights(items: Sequence[str]) -> Dict[str, int]:
    n = len(items)
    return {item: n - i for i, item in enumerate(items)}


def metrics_for_box(
    state: Mapping[str, Any], posterior: PosteriorResult, box_id: str
) -> Dict[str, Any]:
    probs = posterior.marginals[box_id]
    prefs = state["preferences"]
    liked = prefs["liked"]
    disliked = prefs["disliked"]
    favorite = prefs["score_tiers"]["favorite"]
    like_weights = _rank_weights(liked)
    dislike_weights = _rank_weights(disliked)

    liked_probs = {d: probs.get(d, 0.0) for d in liked}
    disliked_probs = {d: probs.get(d, 0.0) for d in disliked}
    favorite_probs = {d: probs.get(d, 0.0) for d in favorite}
    hard_avoid = prefs["hard_avoid"]
    hard_avoid_probs = {d: probs.get(d, 0.0) for d in hard_avoid}
    p_like = sum(liked_probs.values())
    p_dislike = sum(disliked_probs.values())
    p_hard_avoid = sum(hard_avoid_probs.values())
    like_weighted = sum(like_weights[d] * p for d, p in liked_probs.items())
    dislike_weighted = sum(dislike_weights[d] * p for d, p in disliked_probs.items())
    scores = prefs["scores"]
    expected_score = (
        sum(probs.get(d, 0.0) * scores[d] for d in scores) if scores else None
    )

    market_values = state.get("market_values", {}) or {}
    resale_ev = None
    if market_values:
        resale_ev = sum(probs.get(d, 0.0) * float(v) for d, v in market_values.items())

    return {
        "box_id": box_id,
        "p_like_any": p_like,
        "p_favorite_any": sum(favorite_probs.values()),
        "p_dislike_any": p_dislike,
        "liked_probabilities": liked_probs,
        "favorite_probabilities": favorite_probs,
        "disliked_probabilities": disliked_probs,
        "p_hard_avoid": p_hard_avoid,
        "hard_avoid_probabilities": hard_avoid_probs,
        "expected_score": expected_score,
        "liked_weighted_score": like_weighted,
        "disliked_weighted_loss": dislike_weighted,
        "resale_ev": resale_ev,
    }


def _probability_bucket(value: float, tol: float) -> float | int:
    """Quantize probabilities into stable tolerance buckets.

    Pairwise "abs(a-b) <= tol" comparisons can be non-transitive. Bucketing
    gives a deterministic total order while still allowing lower-priority
    criteria to decide when values are practically close.
    """
    if tol <= 0:
        return value
    return int(math.floor(value / tol + 0.5))


def metric_comparison_key(
    metrics: Mapping[str, Any], state: Mapping[str, Any]
) -> Tuple[Any, ...]:
    """Return a lower-is-better objective key for a box or expected policy."""
    prefs = state["preferences"]
    mode = prefs["objective_mode"]
    tol = prefs["tie_tolerance_pp"] / 100.0
    liked = prefs["liked"]
    disliked = prefs["disliked"]
    q = lambda x: _probability_bucket(float(x), tol)

    if mode == "risk_first":
        # Two-stage objective: first minimize severity-weighted disliked risk,
        # then total disliked risk; only after that chase liked designs. This
        # keeps likes from compensating for disliked outcomes while still using
        # the user's dislike ordering without brittle strict lexicographic noise.
        primary = [
            q(metrics["disliked_weighted_loss"]),
            q(metrics["p_dislike_any"]),
            -q(metrics["p_like_any"]),
            -q(metrics["liked_weighted_score"]),
        ]
        primary += [-q(metrics["liked_probabilities"].get(d, 0.0)) for d in liked]
        exact = [
            float(metrics["disliked_weighted_loss"]),
            float(metrics["p_dislike_any"]),
            -float(metrics["p_like_any"]),
            -float(metrics["liked_weighted_score"]),
        ]
        exact += [-float(metrics["liked_probabilities"].get(d, 0.0)) for d in liked]
        return tuple(primary + exact)

    if mode == "target_only":
        primary = [-q(metrics["p_like_any"])]
        primary += [-q(metrics["liked_probabilities"].get(d, 0.0)) for d in liked]
        exact = [-float(metrics["p_like_any"])]
        exact += [-float(metrics["liked_probabilities"].get(d, 0.0)) for d in liked]
        exact += [-float(metrics["liked_weighted_score"])]
        return tuple(primary + exact)

    if mode == "top_target_first":
        primary = [-q(metrics["liked_probabilities"].get(d, 0.0)) for d in liked]
        primary += [-q(metrics["p_like_any"])]
        exact = [-float(metrics["liked_probabilities"].get(d, 0.0)) for d in liked]
        exact += [-float(metrics["p_like_any"]), -float(metrics["liked_weighted_score"])]
        return tuple(primary + exact)

    if mode == "guardrail":
        hard_limit = float(prefs["hard_avoid_max_pp"]) / 100.0
        p_hard_avoid = float(metrics["p_hard_avoid"])
        expected_score = metrics.get("expected_score")
        if expected_score is None:
            raise StateError("strategy 守住底线 requires preferences.scores")
        within_limit = p_hard_avoid <= hard_limit + 1e-12
        if within_limit:
            return (
                0,
                -float(expected_score),
                -float(metrics["p_like_any"]),
                float(metrics["p_dislike_any"]),
                p_hard_avoid,
            )
        return (
            1,
            p_hard_avoid,
            -float(expected_score),
            -float(metrics["p_like_any"]),
            float(metrics["p_dislike_any"]),
        )

    if mode == "balanced":
        expected_score = metrics.get("expected_score")
        if expected_score is None:
            expected_score = float(metrics["liked_weighted_score"]) - float(
                metrics["disliked_weighted_loss"]
            )
        return (
            -float(expected_score),
            -float(metrics["p_like_any"]),
            float(metrics["p_dislike_any"]),
        )

    if mode == "resale_ev":
        value = metrics.get("resale_ev")
        if value is None:
            raise StateError("resale_ev objective requires state.market_values")
        return (-float(value),)

    raise StateError(f"unsupported objective mode: {mode}")


def compare_metrics(
    a: Mapping[str, Any], b: Mapping[str, Any], state: Mapping[str, Any]
) -> int:
    ka = metric_comparison_key(a, state)
    kb = metric_comparison_key(b, state)
    if ka == kb:
        return 0
    return -1 if ka < kb else 1


def _action_metric_comparison_key(
    metrics: Mapping[str, Any], state: Mapping[str, Any]
) -> Tuple[Any, ...]:
    """Stabilize policy comparisons so numerical dust cannot spend a card."""
    return tuple(
        round(value, 12) if isinstance(value, float) else value
        for value in metric_comparison_key(metrics, state)
    )


def _primary_tool_metric(
    metrics: Mapping[str, Any], state: Mapping[str, Any]
) -> Tuple[float, str]:
    """Return a higher-is-better primary utility normalized to a 0–1 span."""
    prefs = state["preferences"]
    mode = prefs["objective_mode"]
    if mode == "risk_first":
        scale = max(len(prefs["disliked"]), 1)
        return (
            -float(metrics["disliked_weighted_loss"]) / scale,
            "severity_weighted_dislike_reduction",
        )
    if mode == "target_only":
        return float(metrics["p_like_any"]), "p_like_any"
    if mode == "top_target_first":
        top = prefs["liked"][0] if prefs["liked"] else None
        return (
            float(metrics["liked_probabilities"].get(top, 0.0)),
            "p_top_liked",
        )
    if mode in {"guardrail", "balanced"}:
        expected_score = metrics.get("expected_score")
        if expected_score is not None:
            return (float(expected_score) + 10.0) / 20.0, "expected_score_range"
        scale = max(len(prefs["liked"]), len(prefs["disliked"]), 1)
        utility = float(metrics["liked_weighted_score"]) - float(
            metrics["disliked_weighted_loss"]
        )
        return utility / (2.0 * scale) + 0.5, "legacy_utility_range"
    if mode == "resale_ev":
        values = [float(value) for value in (state.get("market_values") or {}).values()]
        span = max(values) - min(values) if values else 0.0
        if span <= 0:
            return 0.0, "resale_value_range"
        return (
            (float(metrics["resale_ev"]) - min(values)) / span,
            "resale_value_range",
        )
    raise StateError(f"unsupported objective mode: {mode}")


def _primary_tool_uplift_pp(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    state: Mapping[str, Any],
) -> Tuple[float, str]:
    current_value, metric_name = _primary_tool_metric(current, state)
    baseline_value, _ = _primary_tool_metric(baseline, state)
    return 100.0 * (current_value - baseline_value), metric_name


def _primary_terminal_values_equivalent(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    state: Mapping[str, Any],
) -> bool:
    delta_pp, _ = _primary_tool_uplift_pp(a, b, state)
    tolerance = float(state["preferences"]["tie_tolerance_pp"])
    return abs(delta_pp) <= tolerance + 1e-12


def _sort_metrics(metrics: List[Dict[str, Any]], state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        metrics,
        key=lambda row: (metric_comparison_key(row, state), _stable_box_sort_key(row["box_id"])),
    )


def available_box_metrics(
    state: Mapping[str, Any], posterior: PosteriorResult
) -> List[Dict[str, Any]]:
    rows = [
        metrics_for_box(state, posterior, box["id"])
        for box in state["boxes"]
        if box["status"] in DRAWABLE_STATUSES
    ]
    if not rows:
        raise StateError("there are no drawable boxes")
    return _sort_metrics(rows, state)


def _terminal_best(state: Mapping[str, Any], posterior: PosteriorResult) -> Dict[str, Any]:
    return available_box_metrics(state, posterior)[0]


def _expected_metric_template(state: Mapping[str, Any]) -> Dict[str, Any]:
    liked = state["preferences"]["liked"]
    favorite = state["preferences"]["score_tiers"]["favorite"]
    disliked = state["preferences"]["disliked"]
    hard_avoid = state["preferences"]["hard_avoid"]
    return {
        "box_id": "expected_after_tool",
        "p_like_any": 0.0,
        "p_favorite_any": 0.0,
        "p_dislike_any": 0.0,
        "liked_probabilities": {d: 0.0 for d in liked},
        "favorite_probabilities": {d: 0.0 for d in favorite},
        "disliked_probabilities": {d: 0.0 for d in disliked},
        "p_hard_avoid": 0.0,
        "hard_avoid_probabilities": {d: 0.0 for d in hard_avoid},
        "expected_score": 0.0 if state["preferences"]["scores"] else None,
        "liked_weighted_score": 0.0,
        "disliked_weighted_loss": 0.0,
        "resale_ev": 0.0 if state.get("market_values") else None,
    }


def _accumulate_metrics(target: MutableMapping[str, Any], source: Mapping[str, Any], weight: float) -> None:
    target["p_like_any"] += weight * source["p_like_any"]
    target["p_favorite_any"] += weight * source["p_favorite_any"]
    target["p_dislike_any"] += weight * source["p_dislike_any"]
    target["p_hard_avoid"] += weight * source["p_hard_avoid"]
    target["liked_weighted_score"] += weight * source["liked_weighted_score"]
    target["disliked_weighted_loss"] += weight * source["disliked_weighted_loss"]
    for d, p in source["liked_probabilities"].items():
        target["liked_probabilities"][d] += weight * p
    for d, p in source["favorite_probabilities"].items():
        target["favorite_probabilities"][d] += weight * p
    for d, p in source["disliked_probabilities"].items():
        target["disliked_probabilities"][d] += weight * p
    for d, p in source["hard_avoid_probabilities"].items():
        target["hard_avoid_probabilities"][d] += weight * p
    if target.get("expected_score") is not None and source.get("expected_score") is not None:
        target["expected_score"] += weight * source["expected_score"]
    if target.get("resale_ev") is not None and source.get("resale_ev") is not None:
        target["resale_ev"] += weight * source["resale_ev"]


def _state_signature_for_posterior(state: Mapping[str, Any]) -> str:
    minimal = {
        "boxes": [
            {
                "id": b["id"],
                "excluded": sorted(b["excluded"]),
                "known": b["known"],
            }
            for b in state["boxes"]
        ],
        "model": state["model"],
    }
    return json.dumps(minimal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _public_state_copy(state: Mapping[str, Any]) -> Dict[str, Any]:
    public = copy.deepcopy(
        {k: v for k, v in state.items() if not k.startswith("_")}
    )
    if "_session_draws_used" in state:
        public["_session_draws_used"] = int(state["_session_draws_used"])
    return public


def _state_signature_for_plan(
    state: Mapping[str, Any], depth: int, beam_width: int = 3
) -> str:
    # beam_width only affects depth-2 results, so it is excluded from the
    # depth-1 key to keep cache entries shared across beam settings.
    payload = {
        "depth": depth,
        "beam_width": beam_width if depth == 2 else None,
        "state": _public_state_copy(state),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _metric_delta(
    current: Mapping[str, Any], baseline: Mapping[str, Any]
) -> Dict[str, Any]:
    return {
        "p_like_any_pp": 100.0
        * (current["p_like_any"] - baseline["p_like_any"]),
        "p_favorite_any_pp": 100.0
        * (current["p_favorite_any"] - baseline["p_favorite_any"]),
        "p_dislike_any_pp": 100.0
        * (current["p_dislike_any"] - baseline["p_dislike_any"]),
        "p_hard_avoid_pp": 100.0
        * (current["p_hard_avoid"] - baseline["p_hard_avoid"]),
        "expected_score": (
            None
            if current.get("expected_score") is None
            else current["expected_score"] - baseline["expected_score"]
        ),
        "liked_weighted": current["liked_weighted_score"]
        - baseline["liked_weighted_score"],
        "disliked_weighted": current["disliked_weighted_loss"]
        - baseline["disliked_weighted_loss"],
        "resale_ev": (
            None
            if current.get("resale_ev") is None
            else current["resale_ev"] - baseline["resale_ev"]
        ),
    }


def _action_summary(action: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "tool": action["tool"],
        "action": action["action"],
        "box_id": action["box_id"],
        "expected_draw_probability": action["expected_draw_probability"],
        "expected_tools_used": action.get("expected_tools_used", 0.0),
        "primary_uplift_pp": action.get("primary_uplift_pp", 0.0),
        "primary_metric": action.get("primary_metric"),
        "passes_tool_uplift_gate": action.get("passes_tool_uplift_gate", True),
        "tool_gate_reason": action.get("tool_gate_reason"),
        "uplift_vs_no_card": action.get("uplift_vs_no_card"),
    }


def plan_tools(
    state: Mapping[str, Any],
    posterior: PosteriorResult,
    depth: int = 1,
    *,
    beam_width: int = 3,
    _posterior_cache: Optional[MutableMapping[str, PosteriorResult]] = None,
    _plan_cache: Optional[MutableMapping[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Plan at most one or two adaptive card actions before drawing or stopping.

    At depth 2, the second layer is expanded only for the top ``beam_width``
    depth-1 card actions; every other card action keeps its depth-1 evaluation
    and is labelled ``depth_evaluated: 1``. Set ``beam_width=0`` to expand
    every action (the untruncated exact pass).
    """
    if depth not in {1, 2}:
        raise StateError("planning depth must be 1 or 2")

    posterior_cache = _posterior_cache if _posterior_cache is not None else {}
    plan_cache = _plan_cache if _plan_cache is not None else {}
    posterior_cache.setdefault(_state_signature_for_posterior(state), posterior)
    plan_key = _state_signature_for_plan(state, depth, beam_width)
    if plan_key in plan_cache:
        return plan_cache[plan_key]

    depth_one: Optional[Dict[str, Any]] = None
    beam: Optional[set] = None
    if depth == 2:
        depth_one = plan_tools(
            state,
            posterior,
            depth=1,
            _posterior_cache=posterior_cache,
            _plan_cache=plan_cache,
        )
        if beam_width:
            ranked_card_actions = [
                action
                for action in depth_one["action_ranking"]
                if action["tool"] != "none"
            ]
            beam = {
                (action["tool"], action["box_id"])
                for action in ranked_card_actions[:beam_width]
            }

    def analyze_cached(next_state: Mapping[str, Any]) -> Tuple[Dict[str, Any], PosteriorResult]:
        normalized = _normalize_state(next_state)
        key = _state_signature_for_posterior(normalized)
        if key not in posterior_cache:
            posterior_cache[key] = analyze_posterior(normalized)
        return normalized, posterior_cache[key]

    baseline = _terminal_best(state, posterior)
    baseline_decision = evaluate_draw_decision(state, baseline)
    if baseline_decision["should_draw"]:
        baseline_policy_metrics = baseline
    else:
        baseline_policy_metrics = _expected_metric_template(state)
        baseline_policy_metrics["box_id"] = "no_draw"
    actions: List[Dict[str, Any]] = [
        {
            "tool": "none",
            "action": (
                "direct_draw" if baseline_decision["should_draw"] else "stop"
            ),
            "box_id": (
                baseline["box_id"] if baseline_decision["should_draw"] else None
            ),
            "expected_terminal_metrics": baseline_policy_metrics,
            "expected_draw_probability": (
                1.0 if baseline_decision["should_draw"] else 0.0
            ),
            "branches": [],
            "draw_decision": baseline_decision,
            "depth_evaluated": depth,
            "expected_tools_used": 0.0,
        }
    ]

    eligible_boxes = [
        b
        for b in state["boxes"]
        if b["status"] == AVAILABLE_STATUS and not b["tool_used"] and b["known"] is None
    ]

    def evaluate_branch(
        next_state: Mapping[str, Any],
        p_outcome: float,
        outcome: str,
        expected: MutableMapping[str, Any],
        expand_continuation: bool,
    ) -> Tuple[Dict[str, Any], float, float]:
        next_norm, next_post = analyze_cached(next_state)
        best = _terminal_best(next_norm, next_post)
        draw_decision = evaluate_draw_decision(next_norm, best)
        branch = {
            "outcome": outcome,
            "probability": p_outcome,
            "best_box_after_outcome": best["box_id"],
            "best_metrics_after_outcome": best,
            "recommended_draw_after_outcome": (
                best["box_id"] if draw_decision["should_draw"] else None
            ),
            "draw_decision_after_outcome": draw_decision,
        }

        if depth == 1 or not expand_continuation:
            if draw_decision["should_draw"]:
                _accumulate_metrics(expected, best, p_outcome)
                return branch, p_outcome, 0.0
            return branch, 0.0, 0.0

        continuation = plan_tools(
            next_norm,
            next_post,
            depth=depth - 1,
            _posterior_cache=posterior_cache,
            _plan_cache=plan_cache,
        )
        next_action = continuation["recommended_action"]
        _accumulate_metrics(
            expected,
            next_action["expected_terminal_metrics"],
            p_outcome,
        )
        branch["recommended_draw_after_outcome"] = (
            next_action["box_id"]
            if (
                next_action["tool"] == "none"
                and next_action["action"] == "direct_draw"
            )
            else None
        )
        branch["next_action_after_outcome"] = _action_summary(next_action)
        return (
            branch,
            p_outcome * next_action["expected_draw_probability"],
            p_outcome * float(next_action["expected_tools_used"]),
        )

    if state["tools"]["hint_cards"] > 0:
        hint_labels = list(state["_hint_labels"])
        if len(state["_scenarios"]) > 1 and any(
            set(hint_labels) != set(s.designs) for s in state["_scenarios"]
        ):
            raise StateError(
                "exact hint-card value planning is disabled for mixture/secret models "
                "when the platform cannot reveal every modeled design label. The hint "
                "outcome likelihood then depends on whether the true item is hidden. "
                "Use a regular-only sensitivity run, or evaluate display cards instead."
            )
        for box in eligible_boxes:
            box_id = box["id"]
            remaining_labels = [d for d in hint_labels if d not in set(box["excluded"])]
            outcome_prob: Dict[str, float] = {d: 0.0 for d in remaining_labels}
            for actual, p_actual in posterior.marginals[box_id].items():
                if p_actual <= 0:
                    continue
                choices = [d for d in remaining_labels if d != actual]
                if not choices:
                    continue
                each = p_actual / len(choices)
                for label in choices:
                    outcome_prob[label] += each

            total_outcome_p = sum(outcome_prob.values())
            if total_outcome_p <= 0:
                continue
            outcome_prob = {
                label: probability / total_outcome_p
                for label, probability in outcome_prob.items()
                if probability > 0
            }
            expected = _expected_metric_template(state)
            draw_probability = 0.0
            expected_additional_tools = 0.0
            branches: List[Dict[str, Any]] = []
            for excluded_label, p_outcome in sorted(
                outcome_prob.items(), key=lambda kv: (-kv[1], kv[0])
            ):
                next_state = _public_state_copy(state)
                next_box = next(b for b in next_state["boxes"] if str(b["id"]) == box_id)
                next_box["excluded"] = sorted(
                    set(next_box.get("excluded", [])) | {excluded_label}
                )
                next_box["tool_used"] = True
                next_state["tools"]["hint_cards"] = max(
                    0, int(next_state["tools"].get("hint_cards", 0)) - 1
                )
                try:
                    (
                        branch,
                        branch_draw_probability,
                        branch_additional_tools,
                    ) = evaluate_branch(
                        next_state,
                        p_outcome,
                        f"not {excluded_label}",
                        expected,
                        beam is None or ("hint", box_id) in beam,
                    )
                except StateError:
                    continue
                branches.append(branch)
                draw_probability += branch_draw_probability
                expected_additional_tools += branch_additional_tools
            actions.append(
                {
                    "tool": "hint",
                    "action": "use_tool",
                    "box_id": box_id,
                    "expected_terminal_metrics": expected,
                    "expected_draw_probability": draw_probability,
                    "branches": branches,
                    "depth_evaluated": (
                        depth
                        if beam is None or ("hint", box_id) in beam
                        else 1
                    ),
                    "expected_tools_used": 1.0 + expected_additional_tools,
                }
            )

    if state["tools"]["display_cards"] > 0:
        for box in eligible_boxes:
            box_id = box["id"]
            expected = _expected_metric_template(state)
            draw_probability = 0.0
            expected_additional_tools = 0.0
            branches: List[Dict[str, Any]] = []
            for actual, p_outcome in sorted(
                posterior.marginals[box_id].items(), key=lambda kv: (-kv[1], kv[0])
            ):
                if p_outcome <= 0:
                    continue
                next_state = _public_state_copy(state)
                next_box = next(b for b in next_state["boxes"] if str(b["id"]) == box_id)
                next_box["known"] = actual
                next_box["tool_used"] = True
                next_state["tools"]["display_cards"] = max(
                    0, int(next_state["tools"].get("display_cards", 0)) - 1
                )
                next_state["tools"]["reveal_cards"] = next_state["tools"]["display_cards"]
                (
                    branch,
                    branch_draw_probability,
                    branch_additional_tools,
                ) = evaluate_branch(
                    next_state,
                    p_outcome,
                    actual,
                    expected,
                    beam is None or ("display", box_id) in beam,
                )
                branches.append(branch)
                draw_probability += branch_draw_probability
                expected_additional_tools += branch_additional_tools
            actions.append(
                {
                    "tool": "display",
                    "action": "use_tool",
                    "box_id": box_id,
                    "expected_terminal_metrics": expected,
                    "expected_draw_probability": draw_probability,
                    "branches": branches,
                    "depth_evaluated": (
                        depth
                        if beam is None or ("display", box_id) in beam
                        else 1
                    ),
                    "expected_tools_used": 1.0 + expected_additional_tools,
                }
            )

    tool_order = {"none": 0, "display": 1, "hint": 2}
    for action in actions:
        uplift = _metric_delta(
            action["expected_terminal_metrics"],
            baseline_policy_metrics,
        )
        action["uplift_vs_no_card"] = uplift
        action["uplift_vs_direct_draw"] = uplift
        primary_uplift_pp, primary_metric = _primary_tool_uplift_pp(
            action["expected_terminal_metrics"],
            baseline_policy_metrics,
            state,
        )
        action["primary_uplift_pp"] = primary_uplift_pp
        action["primary_metric"] = primary_metric
        if action["tool"] == "none":
            action["passes_tool_uplift_gate"] = True
            action["tool_gate_reason"] = "no_card_baseline"
        elif baseline_decision["should_draw"]:
            threshold = state["preferences"]["min_tool_uplift_pp"]
            action["passes_tool_uplift_gate"] = (
                primary_uplift_pp + 1e-12 >= threshold
            )
            action["tool_gate_reason"] = (
                "practical_uplift_met"
                if action["passes_tool_uplift_gate"]
                else "below_min_tool_uplift"
            )
        else:
            action["passes_tool_uplift_gate"] = (
                float(action["expected_draw_probability"]) > 0.0
            )
            action["tool_gate_reason"] = (
                "rescue_route"
                if action["passes_tool_uplift_gate"]
                else "no_qualifying_branch"
            )

    actions = sorted(
        actions,
        key=lambda action: (
            0 if action["passes_tool_uplift_gate"] else 1,
            _action_metric_comparison_key(
                action["expected_terminal_metrics"], state
            ),
            round(float(action["expected_tools_used"]), 12),
            tool_order[action["tool"]],
            _stable_box_sort_key(action["box_id"]),
        ),
    )

    result = {
        "planning_depth": depth,
        "baseline_best_draw": baseline,
        "baseline_draw_decision": baseline_decision,
        "baseline_policy_metrics": baseline_policy_metrics,
        "recommended_action": actions[0],
        "action_ranking": actions,
        "min_tool_uplift_pp": state["preferences"]["min_tool_uplift_pp"],
        "planning_note": (
            "Direct draw or stop competes at every layer. Use only the first "
            "recommended action, then apply the real outcome and rerun."
        ),
    }
    plan_cache[plan_key] = result

    if depth == 2:
        assert depth_one is not None  # computed upfront for beam selection
        if beam is not None:
            truncated = sum(
                1
                for action in actions
                if action["tool"] != "none" and action["depth_evaluated"] == 1
            )
            if truncated:
                result["beam_note"] = (
                    f"Depth-2 continuation was expanded only for the top "
                    f"{beam_width} depth-1 card actions; {truncated} card "
                    "action(s) keep their depth-1 evaluation (see each "
                    "action's depth_evaluated). Use beam_width=0 for an "
                    "untruncated exact pass."
                )
        depth_one_action = depth_one["recommended_action"]
        selected_identity = (
            result["recommended_action"]["tool"],
            result["recommended_action"]["action"],
            result["recommended_action"]["box_id"],
        )
        depth_one_identity = (
            depth_one_action["tool"],
            depth_one_action["action"],
            depth_one_action["box_id"],
        )
        result["depth_1_recommended_action"] = _action_summary(depth_one_action)
        result["gain_vs_one_card_horizon"] = _metric_delta(
            result["recommended_action"]["expected_terminal_metrics"],
            depth_one_action["expected_terminal_metrics"],
        )
        result["first_action_changed_vs_depth_1"] = (
            selected_identity != depth_one_identity
        )
        result["terminal_value_practically_equivalent_to_depth_1"] = (
            _primary_terminal_values_equivalent(
                result["recommended_action"]["expected_terminal_metrics"],
                depth_one_action["expected_terminal_metrics"],
                state,
            )
        )
        result["planning_note"] += (
            " The reported gain compares a two-card horizon with drawing after "
            "at most one card; it is not a claim that rolling one-step replanning "
            "is worse when both choose the same first action."
        )

    return result


def plan_one_tool(state: Mapping[str, Any], posterior: PosteriorResult) -> Dict[str, Any]:
    """Backward-compatible one-card planner."""
    return plan_tools(state, posterior, depth=1)


def evaluate_draw_decision(
    state: Mapping[str, Any], best: Mapping[str, Any]
) -> Dict[str, Any]:
    """Evaluate explicit stopping rules against the current best drawable box."""
    prefs = state["preferences"]
    stop_rules = prefs["stop_rules"]
    reasons: List[str] = []
    tray_opened_count = sum(
        1 for box in state["boxes"] if box["status"] == "opened"
    )
    opened_count = int(state.get("_session_draws_used", tray_opened_count))

    max_draws = stop_rules.get("max_draws")
    if max_draws is not None and opened_count >= max_draws:
        reasons.append(
            f"已抽 {opened_count} 盒，达到最多 {max_draws} 盒"
        )
    min_like = stop_rules.get("min_like_any_pp")
    if min_like is not None and 100.0 * float(best["p_like_any"]) < min_like:
        reasons.append(
            f"喜欢款概率 {100.0 * float(best['p_like_any']):.2f}% "
            f"低于 {min_like:.2f}%"
        )
    min_favorite = stop_rules.get("min_favorite_any_pp")
    if (
        min_favorite is not None
        and 100.0 * float(best["p_favorite_any"]) < min_favorite
    ):
        reasons.append(
            f"最爱款概率 {100.0 * float(best['p_favorite_any']):.2f}% "
            f"低于 {min_favorite:.2f}%"
        )
    max_dislike = stop_rules.get("max_dislike_any_pp")
    if max_dislike is not None and 100.0 * float(best["p_dislike_any"]) > max_dislike:
        reasons.append(
            f"不喜欢款概率 {100.0 * float(best['p_dislike_any']):.2f}% "
            f"高于 {max_dislike:.2f}%"
        )
    max_hard = stop_rules.get("max_hard_avoid_pp")
    if max_hard is not None and 100.0 * float(best["p_hard_avoid"]) > max_hard:
        reasons.append(
            f"硬雷概率 {100.0 * float(best['p_hard_avoid']):.2f}% "
            f"高于 {max_hard:.2f}%"
        )
    min_score = stop_rules.get("min_expected_score")
    if min_score is not None and float(best["expected_score"]) < min_score:
        reasons.append(
            f"期望评分 {float(best['expected_score']):.2f} "
            f"低于 {min_score:.2f}"
        )

    if prefs["objective_mode"] == "guardrail":
        hard_limit = float(prefs["hard_avoid_max_pp"])
        if 100.0 * float(best["p_hard_avoid"]) > hard_limit:
            reasons.append(
                f"没有盒子满足硬雷不超过 {hard_limit:.2f}% 的底线"
            )

    return {
        "should_draw": not reasons,
        "best_box_id": best["box_id"],
        "opened_count": opened_count,
        "tray_opened_count": tray_opened_count,
        "stop_rules_configured": bool(stop_rules),
        "reasons": reasons,
    }


def _report_stop_rule_checks(
    state: Mapping[str, Any], best: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Expose every configured draw boundary in one machine-checkable shape."""
    prefs = state["preferences"]
    stop_rules = prefs["stop_rules"]
    checks = _tray_acceptance_profile(state, best)
    max_draws = stop_rules.get("max_draws")
    if max_draws is not None:
        tray_opened_count = sum(
            1 for box in state["boxes"] if box["status"] == "opened"
        )
        opened_count = int(
            state.get("_session_draws_used", tray_opened_count)
        )
        checks.append(
            {
                "rule": "max_draws",
                "operator": "<",
                "threshold": int(max_draws),
                "actual": opened_count,
                "margin": int(max_draws) - opened_count,
                "unit": "count",
                "passed": opened_count < int(max_draws),
            }
        )
    return checks


def _tray_acceptance_profile(
    state: Mapping[str, Any], best: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    prefs = state["preferences"]
    stop_rules = prefs["stop_rules"]
    specs = (
        ("min_like_any_pp", "p_like_any", ">=", "pp"),
        ("min_favorite_any_pp", "p_favorite_any", ">=", "pp"),
        ("max_dislike_any_pp", "p_dislike_any", "<=", "pp"),
        ("max_hard_avoid_pp", "p_hard_avoid", "<=", "pp"),
    )
    checks: List[Dict[str, Any]] = []
    for rule, metric, operator, unit in specs:
        if rule not in stop_rules:
            continue
        actual = 100.0 * float(best[metric])
        threshold = float(stop_rules[rule])
        passed = actual >= threshold if operator == ">=" else actual <= threshold
        margin = actual - threshold if operator == ">=" else threshold - actual
        checks.append(
            {
                "rule": rule,
                "operator": operator,
                "threshold": threshold,
                "actual": actual,
                "margin": margin,
                "unit": unit,
                "passed": passed,
            }
        )

    if "min_expected_score" in stop_rules:
        actual = float(best["expected_score"])
        threshold = float(stop_rules["min_expected_score"])
        checks.append(
            {
                "rule": "min_expected_score",
                "operator": ">=",
                "threshold": threshold,
                "actual": actual,
                "margin": actual - threshold,
                "unit": "score",
                "passed": actual >= threshold,
            }
        )

    if prefs["objective_mode"] == "guardrail":
        actual = 100.0 * float(best["p_hard_avoid"])
        threshold = float(prefs["hard_avoid_max_pp"])
        checks.append(
            {
                "rule": "hard_avoid_max_pp",
                "operator": "<=",
                "threshold": threshold,
                "actual": actual,
                "margin": threshold - actual,
                "unit": "pp",
                "passed": actual <= threshold,
            }
        )
    return checks


def assess_tray(
    state: Mapping[str, Any],
    best: Mapping[str, Any],
    tool_plan: Mapping[str, Any],
) -> Dict[str, Any]:
    """Classify whether the currently reserved tray is ready to work."""
    decision = evaluate_draw_decision(state, best)
    profile = _tray_acceptance_profile(state, best)
    max_draws = state["preferences"]["stop_rules"].get("max_draws")
    if max_draws is not None and decision["opened_count"] >= max_draws:
        status = "session_stop"
        recommendation = "stop"
    elif profile and decision["should_draw"] and all(
        check["passed"] for check in profile
    ):
        status = "ready"
        recommendation = "keep"
    elif not profile:
        status = "needs_acceptance_rules"
        recommendation = "configure_rules"
    elif (
        tool_plan["recommended_action"]["tool"] != "none"
        and float(tool_plan["recommended_action"]["expected_draw_probability"]) > 0
    ):
        status = "tool_dependent"
        recommendation = "keep_if_using_tool"
    else:
        status = "switch"
        recommendation = "switch"

    return {
        "status": status,
        "recommendation": recommendation,
        "direct_best_box_id": best["box_id"],
        "direct_draw_decision": decision,
        "acceptance_profile": profile,
        "failed_acceptance_rules": [
            check for check in profile if not check["passed"]
        ],
        "default_start_rule": (
            "all_acceptance_rules_pass_after_default_shake"
        ),
        "comparison_basis": "posterior_metrics",
        "future_tray_improvement_guaranteed": False,
        "planning_depth": int(tool_plan["planning_depth"]),
        "one_card_action": _action_summary(tool_plan["recommended_action"]),
    }


def _model_reporting_contract(
    state: Mapping[str, Any],
    *,
    hint_planning_active: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    model_type = state["model"].get("type", "unique_regular")
    regular_only = model_type == "unique_regular"
    hint_mechanism = copy.deepcopy(state["model"]["hint_mechanism"])
    conditional_on = [
        "complete_no_duplicate_case",
        "truthful_clues",
    ]
    if regular_only:
        conditional_on.append("regular_only_scope")
        statement = (
            "以下概率是在常规款整盒无重复、端内模型成立且线索真实条件下"
            "计算的条件概率。"
        )
    else:
        conditional_on.append("declared_scenario_priors")
        statement = (
            "以下概率是在所声明情景先验、整盒无重复、端内模型成立且线索"
            "真实条件下计算的条件概率。"
        )
    if hint_planning_active:
        conditional_on.append("declared_hint_mechanism")

    warnings: List[Dict[str, str]] = []
    if regular_only:
        warnings.append(
            {
                "code": "regular_only_scope",
                "severity": "warning",
                "applies_to": "all_probabilities",
                "message": (
                    "当前仅建模常规款；隐藏款及替换规则未计入，所有百分比均为"
                    "常规款范围下的条件概率。"
                ),
            }
        )
    if hint_planning_active and hint_mechanism["status"] == "assumed":
        warnings.append(
            {
                "code": "hint_mechanism_assumed",
                "severity": "warning",
                "applies_to": "tool_plan",
                "message": (
                    "提示卡按均匀返回一个尚未显示的错误标签建模；机制未经"
                    "确认，道具价值仅在该假设成立时有效。"
                ),
            }
        )

    return (
        {
            "scope": (
                "regular_only" if regular_only else "declared_mixture"
            ),
            "hidden_designs_included": not regular_only,
            "probability_kind": "conditional",
            "conditional_on": conditional_on,
            "probability_statement": statement,
            "hint_mechanism": hint_mechanism,
        },
        warnings,
    )


def build_report(
    state: Mapping[str, Any],
    include_plan: bool = False,
    plan_depth: Optional[int] = None,
    screen_tray: bool = False,
    beam_width: int = 3,
) -> Dict[str, Any]:
    if plan_depth is not None and plan_depth not in {1, 2}:
        raise StateError("planning depth must be 1 or 2")
    if screen_tray and plan_depth == 2:
        raise StateError(
            "tray screening uses planning depth 1; run depth 2 only after "
            "deciding to keep the tray"
        )
    if include_plan and plan_depth is None:
        plan_depth = 1
    if screen_tray and plan_depth is None:
        plan_depth = 1

    posterior = analyze_posterior(state)
    ranked = available_box_metrics(state, posterior)
    boxes_by_id = {b["id"]: b for b in state["boxes"]}
    all_box_rows: List[Dict[str, Any]] = []
    for metrics in ranked:
        box_id = metrics["box_id"]
        explicitly_excluded = set(boxes_by_id[box_id]["excluded"])
        options = [
            {
                "design": d,
                "probability": p,
                "globally_impossible": bool(p == 0.0),
            }
            for d, p in sorted(
                posterior.marginals[box_id].items(), key=lambda kv: (-kv[1], kv[0])
            )
            if d not in explicitly_excluded
        ]
        row = dict(metrics)
        row["status"] = boxes_by_id[box_id]["status"]
        row["tool_used"] = boxes_by_id[box_id]["tool_used"]
        row["explicitly_excluded"] = sorted(explicitly_excluded)
        row["remaining_options_desc"] = options
        row["remaining_options_probability_sum"] = sum(
            item["probability"] for item in options
        )
        all_box_rows.append(row)

    model_contract, model_warnings = _model_reporting_contract(
        state,
        hint_planning_active=(
            plan_depth is not None
            and state["tools"]["hint_cards"] > 0
            and any(
                box["status"] == AVAILABLE_STATUS
                and not box["tool_used"]
                and box["known"] is None
                for box in state["boxes"]
            )
        ),
    )
    model_summary: Dict[str, Any] = {
        "type": state["model"].get("type", "unique_regular"),
        "designs": list(state["_union_designs"]),
        "exact_valid_assignments": posterior.exact_valid_assignments,
        "scenario_posteriors": posterior.scenario_posteriors,
        "scenario_valid_assignments": {
            r.name: r.valid_assignments for r in posterior.scenario_results
        },
        "assumption": (
            "Each scenario is a complete no-duplicate case; sold-but-unknown boxes "
            "remain latent and continue to constrain the other boxes."
        ),
        **model_contract,
    }

    scores = state["preferences"]["scores"]
    warnings: List[str] = []
    if scores:
        unscored = set(state["_union_designs"]) - set(scores)
        if unscored:
            warnings.append(
                f"expected_score 仅基于 {len(scores)}/{len(state['_union_designs'])} "
                f"款打分，未打分款按 0 计算：{'、'.join(sorted(unscored))}。"
                "补全 scores 或设置 score_default 可消除该警告。"
            )

    draw_decision = evaluate_draw_decision(state, all_box_rows[0])
    report = {
        "series": state.get("series"),
        "objective_mode": state["preferences"]["objective_mode"],
        "strategy_name": state["preferences"]["strategy"],
        "strategy_rule": STRATEGY_RULES[state["preferences"]["objective_mode"]],
        "ranking_policy": {
            "tie_tolerance_pp": state["preferences"]["tie_tolerance_pp"],
            "hard_avoid_max_pp": state["preferences"]["hard_avoid_max_pp"],
        },
        "preference_summary": {
            "liked": state["preferences"]["liked"],
            "disliked": state["preferences"]["disliked"],
            "hard_avoid": state["preferences"]["hard_avoid"],
            "score_tiers": state["preferences"]["score_tiers"],
            "scores": state["preferences"]["scores"],
            "sources": state["preferences"]["preference_sources"],
        },
        "tool_policy": {
            "min_tool_uplift_pp": state["preferences"]["min_tool_uplift_pp"],
            "source": state["preferences"]["min_tool_uplift_source"],
        },
        "model_summary": model_summary,
        "ranking": all_box_rows,
        "top_3": [row["box_id"] for row in all_box_rows[:3]],
        "stop_rules": copy.deepcopy(state["preferences"]["stop_rules"]),
        "stop_rule_checks": _report_stop_rule_checks(
            state,
            all_box_rows[0],
        ),
        "draw_decision": draw_decision,
        "model_warnings": model_warnings,
    }
    if warnings:
        report["warnings"] = warnings
    if plan_depth is not None:
        tool_plan = plan_tools(
            state, posterior, depth=plan_depth, beam_width=beam_width
        )
        if screen_tray:
            report["tray_screening"] = assess_tray(
                state,
                all_box_rows[0],
                tool_plan,
            )
            trimmed = {
                key: report[key]
                for key in (
                    "series",
                    "objective_mode",
                    "strategy_name",
                    "strategy_rule",
                    "preference_summary",
                    "tool_policy",
                    "model_summary",
                    "model_warnings",
                    "tray_screening",
                )
            }
            if warnings:
                trimmed["warnings"] = warnings
            return trimmed
        report["next_tool_plan"] = tool_plan
    return report


def build_session_report(
    session: Mapping[str, Any],
    include_plan: bool = False,
    plan_depth: Optional[int] = None,
    screen_tray: bool = False,
    beam_width: int = 3,
) -> Dict[str, Any]:
    """Build one reader-facing report that preserves every tray."""
    active_tray_id = session["active_tray_id"]
    accepted_tray_id = session["accepted_tray_id"]
    tray_reports: Dict[str, Dict[str, Any]] = {}
    for tray_id, state in session["_tray_states"].items():
        is_active = tray_id == active_tray_id
        tray_reports[tray_id] = build_report(
            state,
            include_plan=include_plan if is_active else False,
            plan_depth=plan_depth if is_active else None,
            screen_tray=screen_tray if is_active else False,
            beam_width=beam_width,
        )
        if "tray_screening" in tray_reports[tray_id]:
            profile = tray_reports[tray_id]["tray_screening"][
                "acceptance_profile"
            ]
            decision = tray_reports[tray_id]["tray_screening"][
                "direct_draw_decision"
            ]
        else:
            profile = _tray_acceptance_profile(
                state,
                tray_reports[tray_id]["ranking"][0],
            )
            decision = tray_reports[tray_id]["draw_decision"]
        tray_reports[tray_id]["tray_lock"] = {
            "is_accepted": tray_id == accepted_tray_id,
            "accepted_tray_id": accepted_tray_id,
            "release_required_before_switch": accepted_tray_id is not None,
            "currently_qualified": (
                bool(profile)
                and decision["should_draw"]
                and all(check["passed"] for check in profile)
            ),
        }

    active_state = session["_tray_states"][active_tray_id]
    max_draws = active_state["preferences"]["stop_rules"].get("max_draws")
    active_report = tray_reports[active_tray_id]
    active_decision = active_report.get("draw_decision")
    if active_decision is None:
        active_decision = active_report["tray_screening"][
            "direct_draw_decision"
        ]
    active_tool_action: Optional[Mapping[str, Any]] = None
    if "next_tool_plan" in active_report:
        active_tool_action = active_report["next_tool_plan"][
            "recommended_action"
        ]
    elif "tray_screening" in active_report:
        active_tool_action = active_report["tray_screening"][
            "one_card_action"
        ]

    if accepted_tray_id is None:
        session_recommendation = {
            "action": "follow_active_tray_report",
            "tray_id": active_tray_id,
            "release_required_before_switch": False,
        }
    else:
        session_recommendation = {
            "action": (
                "continue_with_accepted_tray"
                if active_decision["should_draw"]
                else (
                    "continue_with_accepted_tray_tool_plan"
                    if (
                        active_tool_action is not None
                        and active_tool_action["tool"] != "none"
                        and float(
                            active_tool_action["expected_draw_probability"]
                        )
                        > 0
                    )
                    else "stop_or_release_accepted_tray"
                )
            ),
            "tray_id": accepted_tray_id,
            "release_required_before_switch": True,
        }
        screening = active_report.get("tray_screening")
        if screening is not None:
            screening["accepted_tray_id"] = accepted_tray_id
            screening["release_required_before_switch"] = True
            if screening["recommendation"] == "switch":
                screening["unlocked_recommendation"] = "switch"
                screening["status"] = "accepted_review"
                screening["recommendation"] = "release_before_switch"

    acceptance_events = [
        copy.deepcopy(event)
        for event in session["events"]
        if event["type"] in {"tray_accepted", "tray_released"}
    ]
    stop_rule_overrides = [
        copy.deepcopy(event)
        for event in session["events"]
        if event["type"] == "stop_rule_override"
    ]
    return {
        "session_summary": {
            "session_schema_version": session["session_schema_version"],
            "active_tray_id": active_tray_id,
            "accepted_tray_id": accepted_tray_id,
            "lock_status": (
                "accepted" if accepted_tray_id is not None else "open"
            ),
            "tray_ids": list(session["_tray_states"]),
            "tools": copy.deepcopy(session["tools"]),
            "draws_used": session["draws_used"],
            "max_draws": max_draws,
            "event_count": len(session["events"]),
            "stop_rule_override_count": len(stop_rule_overrides),
        },
        "session_recommendation": session_recommendation,
        "tray_reports": tray_reports,
        "actual_events": copy.deepcopy(session["events"]),
        "session_review": {
            "acceptance_lifecycle": acceptance_events,
            "stop_rule_overrides": stop_rule_overrides,
        },
    }


REPORT_RULE_LABELS = {
    "min_like_any_pp": "喜欢款至少",
    "min_favorite_any_pp": "最爱款至少",
    "max_dislike_any_pp": "不喜欢款不超过",
    "max_hard_avoid_pp": "硬雷不超过",
    "min_expected_score": "期望评分至少",
    "max_draws": "最多抽盒数",
    "hard_avoid_max_pp": "策略硬雷上限",
}


def _active_user_report(
    report: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], Optional[str]]:
    """Resolve the active tray without letting callers silently pick a tray."""
    if "tray_reports" not in report:
        return report, None
    summary = report.get("session_summary")
    tray_reports = report.get("tray_reports")
    if not isinstance(summary, Mapping) or not isinstance(
        tray_reports, Mapping
    ):
        raise StateError(
            "user report validation failed: session report is incomplete"
        )
    active_tray_id = summary.get("active_tray_id")
    if active_tray_id not in tray_reports:
        raise StateError(
            "user report validation failed: active tray is missing"
        )
    recommendation = report.get("session_recommendation")
    if (
        not isinstance(recommendation, Mapping)
        or recommendation.get("tray_id") != active_tray_id
    ):
        raise StateError(
            "user report validation failed: session action targets the wrong tray"
        )
    return tray_reports[active_tray_id], str(active_tray_id)


def _action_identity(action: Mapping[str, Any]) -> Tuple[Any, Any, Any]:
    return action.get("tool"), action.get("action"), action.get("box_id")


def _numbers_match(a: Any, b: Any, *, tolerance: float = 1e-12) -> bool:
    try:
        return math.isclose(
            float(a),
            float(b),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
    except (TypeError, ValueError):
        return False


def validate_user_report(
    report: Mapping[str, Any],
    *,
    screen_tray: bool = False,
) -> None:
    """Fail closed when a reader-facing report is incomplete or inconsistent."""
    active, _ = _active_user_report(report)
    errors: List[str] = []

    if screen_tray:
        screening = active.get("tray_screening")
        if not isinstance(screening, Mapping):
            errors.append("screening payload is missing")
        else:
            for key in (
                "status",
                "recommendation",
                "direct_draw_decision",
                "acceptance_profile",
                "one_card_action",
            ):
                if key not in screening:
                    errors.append(f"screening field {key} is missing")
            profile = screening.get("acceptance_profile")
            if not isinstance(profile, list):
                errors.append("screening acceptance lines are missing")
            else:
                for check in profile:
                    if not isinstance(check, Mapping) or not {
                        "rule",
                        "threshold",
                        "actual",
                        "passed",
                    }.issubset(check):
                        errors.append("screening acceptance line is incomplete")
            action = screening.get("one_card_action")
            if not isinstance(action, Mapping) or not {
                "tool",
                "action",
                "box_id",
            }.issubset(action):
                errors.append("screening action is incomplete")
        model_summary = active.get("model_summary")
        if not isinstance(model_summary, Mapping) or not model_summary.get(
            "probability_statement"
        ):
            errors.append("model probability statement is missing")
        if errors:
            raise StateError(
                "user report validation failed: " + "; ".join(errors)
            )
        return

    for key in (
        "strategy_name",
        "strategy_rule",
        "ranking",
        "top_3",
        "stop_rules",
        "stop_rule_checks",
        "draw_decision",
        "next_tool_plan",
        "model_summary",
        "model_warnings",
    ):
        if key not in active:
            errors.append(f"required field {key} is missing")
    if errors:
        raise StateError(
            "user report validation failed: " + "; ".join(errors)
        )

    objective_mode = active.get("objective_mode")
    if (
        objective_mode not in STRATEGY_NAMES
        or active.get("strategy_name") != STRATEGY_NAMES[objective_mode]
        or active.get("strategy_rule") != STRATEGY_RULES[objective_mode]
    ):
        errors.append("strategy contract is inconsistent")

    model_summary = active["model_summary"]
    designs = (
        model_summary.get("designs")
        if isinstance(model_summary, Mapping)
        else None
    )
    if (
        not isinstance(designs, list)
        or not designs
        or len(designs) != len(set(designs))
    ):
        errors.append("model design list is incomplete")
        designs = []
    if not isinstance(model_summary, Mapping) or not model_summary.get(
        "probability_statement"
    ):
        errors.append("model probability statement is missing")
    if not isinstance(active["model_warnings"], list):
        errors.append("model warnings are malformed")

    ranking = active["ranking"]
    top_3 = active["top_3"]
    if not isinstance(ranking, list) or not ranking:
        errors.append("ranking is empty")
        ranking = []
    expected_top = [
        row.get("box_id")
        for row in ranking[:3]
        if isinstance(row, Mapping)
    ]
    if not isinstance(top_3, list) or top_3 != expected_top:
        errors.append("top_3 does not match ranking")

    design_set = set(designs)
    for row in ranking[:3]:
        if not isinstance(row, Mapping):
            errors.append("ranking row is malformed")
            continue
        box_id = row.get("box_id")
        if row.get("status") not in DRAWABLE_STATUSES:
            errors.append(f"box {box_id} is not drawable")
        explicit = row.get("explicitly_excluded")
        options = row.get("remaining_options_desc")
        if not isinstance(explicit, list) or not isinstance(options, list):
            errors.append(f"option coverage for box {box_id} is missing")
            continue
        explicit_designs = set(explicit) & design_set
        option_designs: set[str] = set()
        probabilities_by_design: Dict[str, float] = {}
        probability_sum = 0.0
        for option in options:
            if not isinstance(option, Mapping):
                errors.append(f"option coverage for box {box_id} is malformed")
                continue
            design = option.get("design")
            if design in option_designs:
                errors.append(f"option coverage for box {box_id} has duplicates")
            option_designs.add(design)
            try:
                probability = float(option.get("probability"))
            except (TypeError, ValueError):
                errors.append(
                    f"probability for box {box_id}/{design} is invalid"
                )
                continue
            if not math.isfinite(probability) or not -1e-12 <= probability <= 1 + 1e-12:
                errors.append(
                    f"probability for box {box_id}/{design} is out of range"
                )
            if isinstance(design, str):
                probabilities_by_design[design] = probability
            probability_sum += probability
            expected_global_zero = probability == 0.0
            if bool(option.get("globally_impossible")) != expected_global_zero:
                errors.append(
                    f"global-zero marker for box {box_id}/{design} is inconsistent"
                )
        expected_options = design_set - explicit_designs
        if option_designs != expected_options:
            errors.append(f"option coverage mismatch for box {box_id}")
        if option_designs & explicit_designs:
            errors.append(
                f"explicit exclusions overlap options for box {box_id}"
            )
        preference_summary = active.get("preference_summary")
        if not isinstance(preference_summary, Mapping):
            errors.append("preference summary is malformed")
        else:
            aggregate_specs = (
                ("liked", "p_like_any", "liked_probabilities"),
                ("disliked", "p_dislike_any", "disliked_probabilities"),
                ("hard_avoid", "p_hard_avoid", "hard_avoid_probabilities"),
            )
            score_tiers = preference_summary.get("score_tiers")
            favorite = (
                score_tiers.get("favorite", [])
                if isinstance(score_tiers, Mapping)
                else []
            )
            for labels_key, aggregate_key, detail_key in aggregate_specs:
                labels = preference_summary.get(labels_key)
                details = row.get(detail_key)
                if not isinstance(labels, list) or not isinstance(
                    details, Mapping
                ):
                    errors.append(
                        f"preference probabilities for box {box_id} are malformed"
                    )
                    continue
                expected_details = {
                    design: probabilities_by_design.get(design, 0.0)
                    for design in labels
                }
                if set(details) != set(expected_details) or any(
                    not _numbers_match(
                        details.get(design),
                        probability,
                    )
                    for design, probability in expected_details.items()
                ):
                    errors.append(
                        f"preference probabilities for box {box_id} are inconsistent"
                    )
                expected_aggregate = sum(expected_details.values())
                if not _numbers_match(
                    row.get(aggregate_key),
                    expected_aggregate,
                ):
                    errors.append(
                        f"summary probability for box {box_id}/{aggregate_key} "
                        "is inconsistent"
                    )
            expected_favorite_details = {
                design: probabilities_by_design.get(design, 0.0)
                for design in favorite
            }
            favorite_details = row.get("favorite_probabilities")
            if not isinstance(favorite_details, Mapping) or set(
                favorite_details
            ) != set(expected_favorite_details) or any(
                not _numbers_match(
                    favorite_details.get(design),
                    probability,
                )
                for design, probability in expected_favorite_details.items()
            ):
                errors.append(
                    f"favorite probabilities for box {box_id} are inconsistent"
                )
            if not _numbers_match(
                row.get("p_favorite_any"),
                sum(expected_favorite_details.values()),
            ):
                errors.append(
                    f"summary probability for box {box_id}/p_favorite_any "
                    "is inconsistent"
                )
            scores = preference_summary.get("scores")
            if isinstance(scores, Mapping) and scores:
                expected_score = sum(
                    probabilities_by_design.get(design, 0.0)
                    * float(score)
                    for design, score in scores.items()
                )
                if row.get("expected_score") is None or not _numbers_match(
                    row["expected_score"],
                    expected_score,
                ):
                    errors.append(
                        f"expected score for box {box_id} is inconsistent"
                    )
            elif row.get("expected_score") is not None:
                errors.append(
                    f"expected score for box {box_id} has no score model"
                )
        if not math.isclose(
            probability_sum,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            errors.append(
                f"probability sum for box {box_id} is {probability_sum:.12f}"
            )
        try:
            reported_sum = float(row["remaining_options_probability_sum"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"reported probability sum for box {box_id} is missing")
        else:
            if not math.isclose(
                reported_sum,
                probability_sum,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                errors.append(
                    f"reported probability sum for box {box_id} is inconsistent"
                )

    draw_decision = active["draw_decision"]
    if not isinstance(draw_decision, Mapping):
        errors.append("draw decision is malformed")
        draw_decision = {}
    elif ranking and draw_decision.get("best_box_id") != ranking[0].get(
        "box_id"
    ):
        errors.append("draw decision targets the wrong box")

    stop_rules = active["stop_rules"]
    checks = active["stop_rule_checks"]
    if not isinstance(stop_rules, Mapping) or not isinstance(checks, list):
        errors.append("stop lines are malformed")
        stop_rules = {}
        checks = []
    expected_check_rules = set(stop_rules)
    ranking_policy = active.get("ranking_policy")
    if (
        active.get("objective_mode") == "guardrail"
        and isinstance(ranking_policy, Mapping)
        and ranking_policy.get("hard_avoid_max_pp") is not None
    ):
        expected_check_rules.add("hard_avoid_max_pp")
    actual_check_rules = [
        check.get("rule") for check in checks if isinstance(check, Mapping)
    ]
    if set(actual_check_rules) != expected_check_rules or len(
        actual_check_rules
    ) != len(set(actual_check_rules)):
        errors.append("stop line coverage does not match configured rules")

    best = ranking[0] if ranking else {}
    expected_actuals = {
        "min_like_any_pp": 100.0 * float(best.get("p_like_any", 0.0)),
        "min_favorite_any_pp": 100.0
        * float(best.get("p_favorite_any", 0.0)),
        "max_dislike_any_pp": 100.0
        * float(best.get("p_dislike_any", 0.0)),
        "max_hard_avoid_pp": 100.0
        * float(best.get("p_hard_avoid", 0.0)),
        "hard_avoid_max_pp": 100.0
        * float(best.get("p_hard_avoid", 0.0)),
        "min_expected_score": best.get("expected_score"),
        "max_draws": draw_decision.get("opened_count"),
    }
    expected_thresholds = dict(stop_rules)
    if "hard_avoid_max_pp" in expected_check_rules:
        expected_thresholds["hard_avoid_max_pp"] = ranking_policy[
            "hard_avoid_max_pp"
        ]
    expected_operators = {
        "min_like_any_pp": ">=",
        "min_favorite_any_pp": ">=",
        "max_dislike_any_pp": "<=",
        "max_hard_avoid_pp": "<=",
        "min_expected_score": ">=",
        "max_draws": "<",
        "hard_avoid_max_pp": "<=",
    }
    for check in checks:
        if not isinstance(check, Mapping):
            errors.append("stop line is malformed")
            continue
        rule = check.get("rule")
        if rule not in expected_actuals:
            errors.append(f"stop line {rule} is unsupported")
            continue
        expected_actual = expected_actuals[rule]
        try:
            actual = float(check.get("actual"))
            threshold = float(check.get("threshold"))
        except (TypeError, ValueError):
            errors.append(f"stop line {rule} has invalid values")
            continue
        if expected_actual is None or not math.isclose(
            actual,
            float(expected_actual),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            errors.append(f"stop line {rule} uses the wrong metric")
        operator = check.get("operator")
        if not math.isclose(
            threshold,
            float(expected_thresholds[rule]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            errors.append(f"stop line {rule} uses the wrong threshold")
        if operator != expected_operators[rule]:
            errors.append(f"stop line {rule} uses the wrong operator")
        expected_pass = {
            ">=": actual >= threshold,
            "<=": actual <= threshold,
            "<": actual < threshold,
        }.get(operator)
        if expected_pass is None or bool(check.get("passed")) != expected_pass:
            errors.append(f"stop line {rule} has an inconsistent result")

    should_draw = bool(draw_decision.get("should_draw"))
    if should_draw != all(bool(check.get("passed")) for check in checks):
        errors.append("draw decision contradicts stop lines")
    reasons = draw_decision.get("reasons")
    if not isinstance(reasons, list) or (should_draw and reasons) or (
        not should_draw and not reasons
    ):
        errors.append("draw decision reasons are inconsistent")

    plan = active["next_tool_plan"]
    if not isinstance(plan, Mapping):
        errors.append("action plan is malformed")
    else:
        recommended = plan.get("recommended_action")
        action_ranking = plan.get("action_ranking")
        if not isinstance(recommended, Mapping) or not isinstance(
            action_ranking, list
        ) or not action_ranking or not isinstance(action_ranking[0], Mapping):
            errors.append("action plan is incomplete")
        else:
            if _action_identity(recommended) != _action_identity(
                action_ranking[0]
            ):
                errors.append("action ranking contradicts recommended action")
            tool, action, box_id = _action_identity(recommended)
            ranked_box_ids = {
                row.get("box_id")
                for row in ranking
                if isinstance(row, Mapping)
            }
            if tool == "none" and action == "direct_draw":
                if not should_draw or box_id != draw_decision.get(
                    "best_box_id"
                ):
                    errors.append("action contradicts draw decision")
            elif tool == "none" and action == "stop":
                if should_draw or box_id is not None:
                    errors.append("action contradicts draw decision")
            elif tool in {"hint", "display"} and action == "use_tool":
                if box_id not in ranked_box_ids or float(
                    recommended.get("expected_draw_probability", 0.0)
                ) <= 0:
                    errors.append("action targets an invalid tool route")
            else:
                errors.append("action is unsupported")

    if errors:
        raise StateError(
            "user report validation failed: " + "; ".join(errors)
        )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _percent(probability: Any) -> str:
    return f"{100.0 * float(probability):.2f}%"


def _pp(value: Any) -> str:
    return f"{float(value):.2f}%"


def _without_terminal_period(text: Any) -> str:
    return str(text).rstrip().rstrip("。.!！")


def _stop_rule_value_text(rule: str, value: Any) -> str:
    if value is None:
        return "未配置"
    if rule.endswith("_pp"):
        return _pp(value)
    if rule == "max_draws":
        return f"{int(value)}盒"
    if rule == "min_expected_score":
        return f"{float(value):.2f}"
    return str(value)


def _latest_event_clause(
    report: Mapping[str, Any],
    tray_id: Optional[str],
) -> str:
    events = report.get("actual_events")
    if tray_id is None or not isinstance(events, list) or not events:
        return ""
    event = events[-1]
    if not isinstance(event, Mapping) or event.get("tray_id") != tray_id:
        return ""
    event_type = event.get("type")
    box_id = _markdown_cell(event.get("box_id", ""))
    if event_type == "hint_used":
        excluded = _markdown_cell(event.get("excluded", ""))
        return f"最新信息：{box_id}号排除 {excluded}；"
    if event_type == "display_used":
        design = _markdown_cell(event.get("design", ""))
        return f"最新信息：{box_id}号显示为 {design}；"
    if event_type == "opened_result":
        design = _markdown_cell(event.get("design", ""))
        return f"最新信息：{box_id}号开出 {design}；"
    if event_type == "tray_switch":
        return f"最新信息：已切换至 {_markdown_cell(tray_id)}；"
    if event_type == "tray_accepted":
        return f"最新信息：已保留 {_markdown_cell(tray_id)}；"
    if event_type == "tray_released":
        return f"最新信息：已释放 {_markdown_cell(tray_id)}；"
    if event_type == "stop_rule_override":
        rule = str(event.get("rule", ""))
        label = REPORT_RULE_LABELS.get(rule, rule)
        old_text = _stop_rule_value_text(rule, event.get("old_value"))
        new_text = _stop_rule_value_text(rule, event.get("new_value"))
        return f"最新信息：已将{label}从 {old_text} 调整为 {new_text}；"
    return ""


def _strategy_comparison_sentence(
    report: Mapping[str, Any],
    *,
    event_clause: str = "",
) -> str:
    ranking = report["ranking"]
    first = ranking[0]
    if len(ranking) == 1:
        return event_clause + (
            f"只有 {first['box_id']} 号可选，喜欢款 "
            f"{_percent(first['p_like_any'])}，不喜欢款 "
            f"{_percent(first['p_dislike_any'])}。"
        )
    second = ranking[1]
    mode = report["objective_mode"]
    lower_is_better = False
    secondary = ""
    if mode == "risk_first":
        label = "加权雷区风险"
        first_value = 100.0 * float(first["disliked_weighted_loss"])
        second_value = 100.0 * float(second["disliked_weighted_loss"])
        suffix = "点"
        lower_is_better = True
        secondary = (
            f"，不喜欢款 {_percent(first['p_dislike_any'])} vs "
            f"{_percent(second['p_dislike_any'])}"
        )
    elif mode == "target_only":
        label = "喜欢款"
        first_value = 100.0 * float(first["p_like_any"])
        second_value = 100.0 * float(second["p_like_any"])
        suffix = "%"
    elif mode == "top_target_first":
        liked = report["preference_summary"]["liked"]
        target = liked[0] if liked else "第一喜欢款"
        label = target
        first_value = 100.0 * float(
            first["liked_probabilities"].get(target, 0.0)
        )
        second_value = 100.0 * float(
            second["liked_probabilities"].get(target, 0.0)
        )
        suffix = "%"
        secondary = (
            f"，任一喜欢款 {_percent(first['p_like_any'])} vs "
            f"{_percent(second['p_like_any'])}"
        )
    elif mode == "guardrail":
        label = "期望评分"
        first_value = float(first["expected_score"])
        second_value = float(second["expected_score"])
        suffix = ""
        secondary = (
            f"，硬雷 {_percent(first['p_hard_avoid'])} vs "
            f"{_percent(second['p_hard_avoid'])}"
        )
    elif mode == "balanced":
        label = "期望评分"
        if first.get("expected_score") is None:
            first_value = float(first["liked_weighted_score"]) - float(
                first["disliked_weighted_loss"]
            )
            second_value = float(second["liked_weighted_score"]) - float(
                second["disliked_weighted_loss"]
            )
        else:
            first_value = float(first["expected_score"])
            second_value = float(second["expected_score"])
        suffix = ""
    else:
        label = "预期二手价值"
        first_value = float(first["resale_ev"])
        second_value = float(second["resale_ev"])
        suffix = ""

    delta = abs(first_value - second_value)
    favorable = (
        first_value < second_value
        if lower_is_better
        else first_value > second_value
    )
    if math.isclose(first_value, second_value, abs_tol=1e-12):
        comparison = "持平，由后续指标破平"
    elif favorable:
        direction = "低" if lower_is_better else "高"
        comparison = f"首选{direction} {delta:.2f}{suffix}"
    else:
        tolerance = float(
            report.get("ranking_policy", {}).get("tie_tolerance_pp", 0.0)
        )
        comparison = (
            f"相差 {delta:.2f}{suffix}，处于 {tolerance:.2f} 点排序容差后"
            "由后续指标胜出"
        )
    return event_clause + (
        f"首选 {first['box_id']} 号对次优 {second['box_id']} 号："
        f"{label} {first_value:.2f}{suffix} vs "
        f"{second_value:.2f}{suffix}{secondary}，{comparison}。"
    )


def _tool_name(tool: Any) -> str:
    return {"hint": "提示卡", "display": "显示卡"}.get(str(tool), "卡片")


def _action_sentence(report: Mapping[str, Any]) -> str:
    decision = report["draw_decision"]
    plan = report["next_tool_plan"]
    action = plan["recommended_action"]
    tool = action["tool"]
    if tool in {"hint", "display"}:
        direct = "直接抽已过线" if decision["should_draw"] else "直接抽未过线"
        if action.get("tool_gate_reason") == "rescue_route":
            detail = (
                f"该路线约有 "
                f"{100.0 * float(action['expected_draw_probability']):.2f}% "
                "结果可过线"
            )
        else:
            detail = (
                f"主指标提升 {float(action.get('primary_uplift_pp', 0.0)):.2f} "
                f"个百分点，门槛 "
                f"{float(plan.get('min_tool_uplift_pp', 0.0)):.2f} 个百分点"
            )
        return (
            f"{direct}；对 {action['box_id']} 号使用{_tool_name(tool)}后"
            f"{detail}，因此先用卡。"
        )
    if action["action"] == "stop":
        reasons = "；".join(decision["reasons"])
        has_card_routes = any(
            candidate.get("tool") != "none"
            for candidate in plan["action_ranking"]
        )
        card_reason = (
            "卡片规划也没有合格分支"
            if has_card_routes
            else "当前没有可用卡"
        )
        return f"直接抽未通过停止线：{reasons}；{card_reason}，因此停止。"

    card_actions = [
        candidate
        for candidate in plan["action_ranking"]
        if candidate.get("tool") != "none"
    ]
    if not card_actions:
        return "直接抽已通过全部停止线，且当前没有可用卡，因此直接抽。"
    best_uplift = max(
        float(candidate.get("primary_uplift_pp", 0.0))
        for candidate in card_actions
    )
    threshold = float(plan.get("min_tool_uplift_pp", 0.0))
    if best_uplift + 1e-12 < threshold:
        return (
            f"直接抽已通过全部停止线；卡片最高主指标提升 "
            f"{best_uplift:.2f} 个百分点，低于 {threshold:.2f} "
            "个百分点门槛，因此不用卡。"
        )
    return (
        "直接抽已通过全部停止线；比较全部卡片动作后无卡方案仍最优，"
        "因此不用卡。"
    )


def _conclusion_and_next_action(
    report: Mapping[str, Any],
) -> Tuple[str, str]:
    action = report["next_tool_plan"]["recommended_action"]
    if action["tool"] in {"hint", "display"}:
        tool = _tool_name(action["tool"])
        conclusion = f"建议先对 {action['box_id']} 号使用{tool}。"
        next_action = (
            f"对 {action['box_id']} 号使用{tool}；拿到真实结果后更新状态并"
            "重新生成完整报告，再决定抽哪盒。"
        )
        return conclusion, next_action
    if action["action"] == "direct_draw":
        box_id = action["box_id"]
        return (
            f"建议抽 {box_id} 号。",
            f"抽 {box_id} 号；开盒后记录结果，再判断是否继续。",
        )
    return "建议停止，不抽。", "停止本轮；只有确认修改停止线后才重新计算。"


def _render_top_summary(report: Mapping[str, Any]) -> List[str]:
    lines = [
        "| 排名 | 盒号 | 喜欢款 | 最爱款 | 不喜欢款 | 硬雷 | 期望评分 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(report["ranking"][:3], start=1):
        expected_score = row.get("expected_score")
        score_text = (
            "—" if expected_score is None else f"{float(expected_score):.2f}"
        )
        lines.append(
            f"| {rank} | {row['box_id']}号 | "
            f"{_percent(row['p_like_any'])} | "
            f"{_percent(row['p_favorite_any'])} | "
            f"{_percent(row['p_dislike_any'])} | "
            f"{_percent(row['p_hard_avoid'])} | {score_text} |"
        )
    return lines


def _render_probability_matrix(report: Mapping[str, Any]) -> List[str]:
    candidates = report["ranking"][:3]
    header = "| 款式 | " + " | ".join(
        f"{_markdown_cell(row['box_id'])}号" for row in candidates
    ) + " |"
    divider = "|---|" + "---:|" * len(candidates)
    by_box: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    excluded: Dict[str, set[str]] = {}
    for row in candidates:
        box_id = str(row["box_id"])
        by_box[box_id] = {
            str(option["design"]): option
            for option in row["remaining_options_desc"]
        }
        excluded[box_id] = set(row["explicitly_excluded"])

    lines = [header, divider]
    for design in report["model_summary"]["designs"]:
        cells = [_markdown_cell(design)]
        for row in candidates:
            box_id = str(row["box_id"])
            if design in excluded[box_id]:
                cells.append("已排除")
                continue
            option = by_box[box_id][design]
            if option["globally_impossible"]:
                cells.append("0.00%（全局约束）")
            else:
                cells.append(_percent(option["probability"]))
        lines.append("| " + " | ".join(cells) + " |")
    sums = "；".join(
        f"{row['box_id']}号 "
        f"{100.0 * float(row['remaining_options_probability_sum']):.6f}%"
        for row in candidates
    )
    lines.extend(
        [
            "",
            f"未舍入校验：{sums}；显示值可能有舍入差。",
        ]
    )
    return lines


def _render_stop_lines(checks: Sequence[Mapping[str, Any]]) -> List[str]:
    if not checks:
        return ["未配置停止线；当前只按策略排序。"]
    lines = [
        "| 规则 | 当前值 | 要求 | 结果 |",
        "|---|---:|---:|---|",
    ]
    for check in checks:
        unit = check["unit"]
        if unit == "pp":
            actual = _pp(check["actual"])
            threshold = f"{check['operator']} {_pp(check['threshold'])}"
        elif unit == "count":
            actual = f"{int(check['actual'])}盒"
            threshold = (
                f"{check['operator']} {int(check['threshold'])}盒"
            )
        else:
            actual = f"{float(check['actual']):.2f}"
            threshold = (
                f"{check['operator']} {float(check['threshold']):.2f}"
            )
        operator = threshold.replace(">=", "≥").replace("<=", "≤")
        lines.append(
            f"| {REPORT_RULE_LABELS.get(check['rule'], check['rule'])} | "
            f"{actual} | {operator} | "
            f"{'通过' if check['passed'] else '未通过'} |"
        )
    return lines


def _reader_warning_message(warning: Mapping[str, Any]) -> str:
    if warning.get("code") == "regular_only_scope":
        return "隐藏款：默认未计入。"
    return str(warning.get("message", "")).strip()


def _render_model_notes(report: Mapping[str, Any]) -> List[str]:
    model = report["model_summary"]
    lines = [f"- {model['probability_statement']}"]
    assignments = model.get("exact_valid_assignments")
    if assignments is not None:
        lines.append(f"- 有效整盒分配：{int(assignments):,}。")
    seen: set[str] = set()
    for warning in report.get("model_warnings", []):
        message = _reader_warning_message(warning)
        if message and message not in seen:
            lines.append(f"- {message}")
            seen.add(message)
    for warning in report.get("warnings", []):
        message = str(warning).strip()
        if message and message not in seen:
            lines.append(f"- {message}")
            seen.add(message)
    return lines


def _render_rule_overrides(report: Mapping[str, Any]) -> List[str]:
    review = report.get("session_review")
    if not isinstance(review, Mapping):
        return []
    overrides = review.get("stop_rule_overrides")
    if not isinstance(overrides, list) or not overrides:
        return []
    lines = ["### 已确认变更", ""]
    for event in overrides:
        if not isinstance(event, Mapping):
            continue
        rule = str(event.get("rule"))
        label = REPORT_RULE_LABELS.get(rule, rule)
        old_value = event.get("old_value")
        new_value = event.get("new_value")
        old_text = _stop_rule_value_text(rule, old_value)
        new_text = _stop_rule_value_text(rule, new_value)
        reason = _markdown_cell(event.get("reason", ""))
        lines.append(
            f"- {label}：{old_text} → {new_text}（{reason}）。"
        )
    return lines if len(lines) > 2 else []


def _render_screening_markdown(
    report: Mapping[str, Any],
    tray_id: Optional[str],
) -> str:
    screening = report["tray_screening"]
    status = screening["status"]
    conclusions = {
        "ready": "建议保留本端，并进入正式决策。",
        "tool_dependent": "本端仅在按计划用卡时值得保留。",
        "switch": "建议换端。",
        "session_stop": "已触发全局抽数上限，建议停止。",
        "needs_acceptance_rules": "先补充质量线，再判断是否保留本端。",
        "accepted_review": "当前端已锁定；若要换端，先确认释放原因。",
    }
    recommendation = screening["recommendation"]
    if recommendation == "release_before_switch":
        conclusion = "当前端已锁定；若要换端，先确认释放原因。"
    else:
        conclusion = conclusions.get(status, f"当前状态：{status}。")
    lines = [
        f"# {_markdown_cell(report.get('series') or '盲盒')}｜端筛选快报",
        "",
    ]
    if tray_id is not None:
        lines.extend([f"当前端：{_markdown_cell(tray_id)}", ""])
    lines.extend(["## 结论", "", conclusion, "", "## 质量线", ""])
    lines.extend(_render_stop_lines(screening["acceptance_profile"]))
    action = screening["one_card_action"]
    if recommendation == "keep":
        next_action = "保留本端，并生成完整决策报告。"
    elif recommendation == "keep_if_using_tool":
        next_action = (
            f"对 {action['box_id']} 号使用{_tool_name(action['tool'])}；"
            "按真实结果重算后再决定。"
        )
    elif recommendation == "release_before_switch":
        next_action = "先确认释放原因并记录，再换端。"
    elif recommendation == "switch":
        next_action = "换端；新端不保证更优，按同一质量线重新筛选。"
    elif recommendation == "configure_rules":
        next_action = "补充至少一条喜欢、不喜欢、硬雷或评分质量线。"
    else:
        reasons = screening["direct_draw_decision"].get("reasons", [])
        next_action = "停止本轮"
        if reasons:
            next_action += "：" + "；".join(reasons)
        next_action += "。"
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            next_action,
            "",
            "## 模型口径",
            "",
            f"- {report['model_summary']['probability_statement']}",
        ]
    )
    for warning in report.get("model_warnings", []):
        lines.append(f"- {_reader_warning_message(warning)}")
    return "\n".join(lines).rstrip() + "\n"


def render_user_markdown(
    report: Mapping[str, Any],
    *,
    screen_tray: bool = False,
) -> str:
    """Render the validated standard report; never fall back to a summary."""
    validate_user_report(report, screen_tray=screen_tray)
    active, tray_id = _active_user_report(report)
    if screen_tray:
        return _render_screening_markdown(active, tray_id)

    conclusion, next_action = _conclusion_and_next_action(active)
    event_clause = _latest_event_clause(report, tray_id)
    strategy_sentence = (
        f"本轮采用「{active['strategy_name']}」："
        f"{_without_terminal_period(active['strategy_rule'])}。"
    )
    lines = [
        f"# {_markdown_cell(active.get('series') or '盲盒')}｜决策报告",
        "",
    ]
    if tray_id is not None:
        lines.extend([f"当前端：{_markdown_cell(tray_id)}", ""])
    lines.extend(
        [
            "## 结论",
            "",
            f"**{conclusion}**",
            "",
            "## 决策依据",
            "",
            f"- {strategy_sentence}",
            f"- {_strategy_comparison_sentence(active, event_clause=event_clause)}",
            f"- {_action_sentence(active)}",
            "",
            "## TOP 3 汇总",
            "",
        ]
    )
    lines.extend(_render_top_summary(active))
    lines.extend(["", "## 全款概率矩阵", ""])
    lines.extend(_render_probability_matrix(active))
    lines.extend(["", "## 停止线", ""])
    lines.extend(_render_stop_lines(active["stop_rule_checks"]))
    override_lines = _render_rule_overrides(report)
    if override_lines:
        lines.extend(["", *override_lines])
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            next_action,
            "",
            "## 模型口径",
            "",
        ]
    )
    lines.extend(_render_model_notes(active))
    return "\n".join(lines).rstrip() + "\n"


def _round_floats(obj: Any, digits: int = 8) -> Any:
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, list):
        return [_round_floats(x, digits) for x in obj]
    if isinstance(obj, dict):
        return {k: _round_floats(v, digits) for k, v in obj.items()}
    return obj


COMPACT_BRANCH_KEYS = (
    "outcome",
    "probability",
    "best_box_after_outcome",
    "recommended_draw_after_outcome",
    "next_action_after_outcome",
)


def _compact_branch(branch: Mapping[str, Any]) -> Dict[str, Any]:
    """Reduce one outcome branch to its decision-relevant summary.

    The workflow is adaptive: apply one real outcome, then rerun the solver on
    the updated state. Full per-branch metrics are therefore audit data; keep
    them only with ``--full-branches``.
    """
    compact = {key: branch[key] for key in COMPACT_BRANCH_KEYS if key in branch}
    decision = branch.get("draw_decision_after_outcome")
    if decision is not None and not decision["should_draw"]:
        compact["stop_reasons"] = list(decision["reasons"])
    return compact


def _slim_plan(
    plan: Mapping[str, Any], top_actions: int, full_branches: bool
) -> Dict[str, Any]:
    slimmed = dict(plan)
    if full_branches:
        ranking = list(plan["action_ranking"])
    else:
        ranking = []
        for action in plan["action_ranking"]:
            compact_action = dict(action)
            compact_action["branches"] = [
                _compact_branch(branch) for branch in action["branches"]
            ]
            ranking.append(compact_action)
    if top_actions > 0 and len(ranking) > top_actions:
        slimmed["other_actions_ranked"] = [
            _action_summary(action) for action in ranking[top_actions:]
        ]
        ranking = ranking[:top_actions]
    slimmed["action_ranking"] = ranking
    if ranking:
        slimmed["recommended_action"] = ranking[0]
    if isinstance(slimmed.get("baseline_best_draw"), Mapping):
        slimmed["baseline_best_draw"] = slimmed["baseline_best_draw"]["box_id"]
    return slimmed


def _slim_report(
    report: Mapping[str, Any], top_actions: int, full_branches: bool
) -> Dict[str, Any]:
    """Output-layer slimming; plan_tools results themselves stay complete."""
    if "tray_reports" in report:
        slimmed = dict(report)
        slimmed["tray_reports"] = {
            tray_id: _slim_report(tray_report, top_actions, full_branches)
            for tray_id, tray_report in report["tray_reports"].items()
        }
        return slimmed
    plan = report.get("next_tool_plan")
    if plan is None:
        return dict(report)
    slimmed = dict(report)
    slimmed["next_tool_plan"] = _slim_plan(plan, top_actions, full_branches)
    return slimmed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", help="Path to state JSON, or - for stdin")
    parser.add_argument(
        "--plan-one",
        action="store_true",
        help="Backward-compatible alias for --plan-depth 1",
    )
    parser.add_argument(
        "--plan-depth",
        type=int,
        choices=(1, 2),
        help="Plan up to one or two adaptive card actions before drawing/stopping",
    )
    parser.add_argument(
        "--screen-tray",
        action="store_true",
        help=(
            "Assess whether to keep the current tray; defaults to one-card "
            "planning and reports reusable acceptance lines"
        ),
    )
    parser.add_argument(
        "--top-actions",
        type=int,
        default=3,
        help=(
            "Keep only the top N ranked tool actions in next_tool_plan; "
            "truncated ones collapse to other_actions_ranked summaries. "
            "0 keeps every action."
        ),
    )
    parser.add_argument(
        "--full-branches",
        action="store_true",
        help=(
            "Keep full per-branch metrics in next_tool_plan instead of the "
            "compact decision summary (audit mode)."
        ),
    )
    parser.add_argument(
        "--beam-width",
        type=int,
        default=3,
        help=(
            "At depth 2, expand the second layer only for the top N depth-1 "
            "card actions. 0 disables truncation (exact but slower)."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help=(
            "Output JSON (backward-compatible default) or the validated "
            "reader-facing Markdown report"
        ),
    )
    parser.add_argument("--indent", type=int, default=0)
    parser.add_argument("--digits", type=int, default=8)
    args = parser.parse_args(argv)

    try:
        if args.top_actions < 0:
            raise StateError("--top-actions must be >= 0")
        if args.beam_width < 0:
            raise StateError("--beam-width must be >= 0")
        session = _normalize_session(_read_json(args.state))
        if args.plan_one and args.plan_depth not in {None, 1}:
            raise StateError("--plan-one cannot be combined with --plan-depth 2")
        requested_depth = 1 if args.plan_one else args.plan_depth
        if args.format == "markdown" and requested_depth is None:
            requested_depth = 1
        if session["_legacy_input"]:
            state = session["_tray_states"][session["active_tray_id"]]
            report = build_report(
                state,
                plan_depth=requested_depth,
                screen_tray=args.screen_tray,
                beam_width=args.beam_width,
            )
        else:
            report = build_session_report(
                session,
                plan_depth=requested_depth,
                screen_tray=args.screen_tray,
                beam_width=args.beam_width,
            )
        if args.format == "markdown":
            markdown = render_user_markdown(
                report,
                screen_tray=args.screen_tray,
            )
        else:
            report = _slim_report(
                report,
                args.top_actions,
                args.full_branches,
            )
    except (OSError, json.JSONDecodeError, StateError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    if args.format == "markdown":
        print(markdown, end="")
        return 0

    print(
        json.dumps(
            _round_floats(report, args.digits),
            ensure_ascii=False,
            indent=args.indent,
            sort_keys=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
