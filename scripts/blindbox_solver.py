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

try:
    from scripts.session_lifecycle import (
        COMMITMENT_SOURCES,
        LifecycleError,
        inject_derived_lifecycle_events,
        reduce_lifecycle_events,
        summarize_lifecycle,
    )
except ModuleNotFoundError:  # Direct execution: python scripts/blindbox_solver.py
    from session_lifecycle import (  # type: ignore[no-redef]
        COMMITMENT_SOURCES,
        LifecycleError,
        inject_derived_lifecycle_events,
        reduce_lifecycle_events,
        summarize_lifecycle,
    )

try:
    from scripts.review_metrics import (
        ReviewMetricError,
        failure_probability as _pure_review_failure_probability,
        outcome_class_probabilities as _pure_review_outcome_classes,
        primary_metric_change as _pure_review_primary_metric_change,
        quality_lines as _pure_review_quality_lines,
        strongest_alternative as _pure_review_strongest_alternative,
    )
except ModuleNotFoundError:  # Direct execution from scripts/
    from review_metrics import (  # type: ignore[no-redef]
        ReviewMetricError,
        failure_probability as _pure_review_failure_probability,
        outcome_class_probabilities as _pure_review_outcome_classes,
        primary_metric_change as _pure_review_primary_metric_change,
        quality_lines as _pure_review_quality_lines,
        strongest_alternative as _pure_review_strongest_alternative,
    )

try:
    from scripts.blindbox_cli import COMPACT_BRANCH_KEYS
except ModuleNotFoundError:  # Direct execution from scripts/
    from blindbox_cli import COMPACT_BRANCH_KEYS  # type: ignore[no-redef]

try:
    from scripts.preference_policy import (
        BRIEFING_BASELINE_METRIC_BY_RULE,
        BRIEFING_REFERENCE_ANCHORS,
        BRIEFING_REFERENCE_STEP_PP,
        BRIEFING_RULE_LABELS,
        SCORE_TIER_KEYS,
        SCORE_TIER_LABELS,
        SCORE_TIER_RANGES,
        PreferencePolicyError,
        briefing_reference_rules as _pure_briefing_reference_rules,
        briefing_reference_value as _pure_briefing_reference_value,
        build_score_tiers as _pure_build_score_tiers,
        preference_tier_conflicts as _pure_preference_tier_conflicts,
        score_tier as _pure_score_tier,
    )
except ModuleNotFoundError:  # Direct execution from scripts/
    from preference_policy import (  # type: ignore[no-redef]
        BRIEFING_BASELINE_METRIC_BY_RULE,
        BRIEFING_REFERENCE_ANCHORS,
        BRIEFING_REFERENCE_STEP_PP,
        BRIEFING_RULE_LABELS,
        SCORE_TIER_KEYS,
        SCORE_TIER_LABELS,
        SCORE_TIER_RANGES,
        PreferencePolicyError,
        briefing_reference_rules as _pure_briefing_reference_rules,
        briefing_reference_value as _pure_briefing_reference_value,
        build_score_tiers as _pure_build_score_tiers,
        preference_tier_conflicts as _pure_preference_tier_conflicts,
        score_tier as _pure_score_tier,
    )


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
    "top_target_first": (
        "逐款评分时先选择所有最高分款的合计概率；显式给出喜欢顺序时，"
        "再按该顺序逐款比较。"
    ),
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

SESSION_SCHEMA_VERSION = 1
SESSION_EVENT_TYPES = {
    "tray_switch",
    "hint_used",
    "display_used",
    "opened_result",
    "tray_committed",
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
    "min_resale_ev",
    "max_draws",
}
QUALITY_STOP_RULE_KEYS = (
    "min_like_any_pp",
    "min_favorite_any_pp",
    "min_expected_score",
    "min_resale_ev",
    "max_dislike_any_pp",
    "max_hard_avoid_pp",
)
# Tray-level participation: "history" keeps a tray for review only. Released
# and inoperable trays are derived from events and box states, not declared.
TRAY_PARTICIPATION_MODES = {"active", "history"}
# Tray commitment ladder between the plain active tray and the accepted tray:
# a candidate commitment is "selected but not yet qualifying". It comes from
# an explicit tray_committed event, or in single-tray mode an automatic event
# immediately before the first real card/open, and upgrades to accepted once
# real clues pass every quality line.
TRAY_LIFECYCLE_PHASES = {"open", "candidate", "accepted"}
TRAY_COMMITMENT_SOURCES = COMMITMENT_SOURCES
TRAY_LIFECYCLE_PHASE_LABELS = {
    "open": "未承诺（可自由换端）",
    "candidate": "候选承诺（已选定，尚未达到全部质量线）",
    "accepted": "已接受（锁定）",
}
TRAY_COMPARISON_STATUS_RANK = {
    "ready": 0,
    "tool_dependent": 1,
    "switch": 2,
    "needs_acceptance_rules": 3,
    "session_stop": 4,
}
TRAY_COMPARISON_STATUS_LABELS = {
    "ready": "直接可做",
    "tool_dependent": "依赖道具",
    "switch": "建议换端",
    "session_stop": "本轮停止",
    "needs_acceptance_rules": "需先设质量线",
}
TRAY_COMPARISON_EXCLUDED_REASONS = {
    "released": "已释放",
    "history": "历史只读",
    "inoperable": "无可用盒",
}
COMPARISON_DEPTH_TWO_HEAD_CANDIDATES = 2
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


def _reject_duplicate_json_keys(
    pairs: Sequence[Tuple[str, Any]],
) -> Dict[str, Any]:
    """Build one JSON object while failing closed on duplicate keys."""
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StateError(
                f"duplicate JSON key {key!r}; repeated fields, including "
                "conflicting design scores, must be resolved explicitly"
            )
        result[key] = value
    return result


def _read_json(path: str) -> Dict[str, Any]:
    if path == "-":
        return json.load(
            sys.stdin,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=_reject_duplicate_json_keys)


def _stable_box_sort_key(box_id: str) -> Tuple[int, Any]:
    try:
        return (0, int(box_id))
    except (TypeError, ValueError):
        return (1, str(box_id))


def _bit_count(value: int) -> int:
    """Count set bits on Python versions before int.bit_count()."""
    native_bit_count = getattr(value, "bit_count", None)
    if native_bit_count is not None:
        return int(native_bit_count())
    return bin(value).count("1")


def _score_tier(score: float) -> str:
    try:
        return _pure_score_tier(score)
    except PreferencePolicyError as exc:
        raise StateError(str(exc)) from exc


def _build_score_tiers(scores: Mapping[str, float]) -> Dict[str, List[str]]:
    try:
        return _pure_build_score_tiers(scores)
    except PreferencePolicyError as exc:
        raise StateError(str(exc)) from exc


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
    raw_market_values = state.get("market_values", {})
    if raw_market_values is None:
        raw_market_values = {}
    if not isinstance(raw_market_values, Mapping):
        raise StateError("state.market_values must be an object")
    market_value_labels = {str(label) for label in raw_market_values}
    unknown_market_labels = market_value_labels - union_designs
    if unknown_market_labels:
        raise StateError(
            "state.market_values contains unknown designs: "
            f"{sorted(unknown_market_labels)}"
        )
    market_values: Dict[str, float] = {}
    for label, value in raw_market_values.items():
        try:
            market_value = float(value)
        except (TypeError, ValueError) as exc:
            raise StateError(
                f"market value for {label!r} must be numeric"
            ) from exc
        if not math.isfinite(market_value) or market_value < 0:
            raise StateError(
                f"market value for {label!r} must be finite and non-negative"
            )
        market_values[str(label)] = market_value
    state["market_values"] = market_values
    state["_market_value_coverage"] = {
        "total_designs": len(union_designs),
        "valued_design_count": len(market_values),
        "complete": set(market_values) == union_designs,
        "missing_values": sorted(union_designs - set(market_values)),
        "currency": "CNY",
    }
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
    raw_explicit_score_tiers = preferences.get("explicit_score_tiers", {})
    if not isinstance(raw_explicit_score_tiers, Mapping):
        raise StateError("preferences.explicit_score_tiers must be an object")
    unknown_tier_keys = set(raw_explicit_score_tiers) - set(SCORE_TIER_KEYS)
    if unknown_tier_keys:
        raise StateError(
            "preferences.explicit_score_tiers contains unknown tiers: "
            f"{sorted(unknown_tier_keys)}"
        )
    explicit_score_tiers: Dict[str, List[str]] = {}
    explicit_tier_designs: set[str] = set()
    for tier, raw_members in raw_explicit_score_tiers.items():
        if not isinstance(raw_members, list):
            raise StateError(
                f"preferences.explicit_score_tiers.{tier} must be a list"
            )
        members = [str(member) for member in raw_members]
        duplicates = explicit_tier_designs & set(members)
        if len(set(members)) != len(members) or duplicates:
            raise StateError(
                "a design may appear in only one explicit score tier: "
                f"{sorted(duplicates or set(members))}"
            )
        unknown_members = set(members) - union_designs
        if unknown_members:
            raise StateError(
                "preferences.explicit_score_tiers contains unknown designs: "
                f"{sorted(unknown_members)}"
            )
        explicit_score_tiers[str(tier)] = members
        explicit_tier_designs.update(members)
    preferences["explicit_score_tiers"] = explicit_score_tiers
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
    if (
        preferences["objective_mode"] == "resale_ev"
        and not state["_market_value_coverage"]["complete"]
    ):
        raise StateError(
            "strategy 保值优先 requires current market_values for every design; "
            f"missing {state['_market_value_coverage']['missing_values']}"
        )

    if "scores" in preferences and "utility_scores" in preferences:
        dual_scores = preferences.get("scores")
        legacy_scores = preferences.get("utility_scores")
        if not isinstance(dual_scores, Mapping) or not isinstance(
            legacy_scores, Mapping
        ):
            raise StateError(
                "preferences.scores and preferences.utility_scores must be objects"
            )
        normalized_dual = {str(k): v for k, v in dual_scores.items()}
        normalized_legacy = {str(k): v for k, v in legacy_scores.items()}
        if normalized_dual != normalized_legacy:
            raise StateError(
                "preferences.scores conflicts with preferences.utility_scores; "
                "a design cannot carry two different scores"
            )
    raw_scores = preferences.get("scores", preferences.get("utility_scores", {}))
    if not isinstance(raw_scores, dict):
        raise StateError("preferences.scores must be an object")
    explicit_score_labels = set(str(k) for k in raw_scores)
    unknown_score_labels = explicit_score_labels - union_designs
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
    score_default_confirmed = preferences.get("score_default_confirmed", False)
    if not isinstance(score_default_confirmed, bool):
        raise StateError("preferences.score_default_confirmed must be boolean")
    if score_default_supplied:
        try:
            score_default = float(preferences["score_default"])
        except (TypeError, ValueError) as exc:
            raise StateError("preferences.score_default must be numeric") from exc
        if not math.isfinite(score_default):
            raise StateError("preferences.score_default must be finite")
        _score_tier(score_default)
        preferences["score_default"] = score_default
        if score_default_confirmed:
            for design in union_designs:
                scores.setdefault(design, score_default)

    # Tool branches normalize a public copy of an already normalized state.
    # Generated lists are not an explicit preference order. Preserve their
    # provenance unless the caller actually edited the list.
    previous_tiers = preferences.get("score_tiers", {})
    previous_sources = preferences.get("preference_sources", {})
    if isinstance(previous_tiers, Mapping) and isinstance(previous_sources, Mapping):
        previous_groups = {
            "liked": (
                previous_tiers.get("favorite", []) + previous_tiers.get("liked", [])
            ),
            "disliked": (
                previous_tiers.get("hard_avoid", [])
                + previous_tiers.get("light_dislike", [])
            ),
            "hard_avoid": previous_tiers.get("hard_avoid", []),
        }
        if (
            previous_sources.get("liked") in {"scores", "empty_default"}
            and explicit_liked == previous_groups["liked"]
        ):
            liked_supplied = False
        if (
            previous_sources.get("disliked") in {"scores", "empty_default"}
            and explicit_disliked == previous_groups["disliked"]
        ):
            disliked_supplied = False
        if (
            previous_sources.get("hard_avoid") in {"scores", "empty_default"}
            and explicit_hard_avoid == previous_groups["hard_avoid"]
        ):
            hard_avoid_supplied = False

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
    if "min_resale_ev" in raw_stop_rules:
        value = float(raw_stop_rules["min_resale_ev"])
        if not math.isfinite(value) or value < 0:
            raise StateError(
                "preferences.stop_rules.min_resale_ev must be finite and "
                "non-negative"
            )
        stop_rules["min_resale_ev"] = value
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
    if (
        "min_resale_ev" in stop_rules
        and not state["_market_value_coverage"]["complete"]
    ):
        raise StateError(
            "preferences.stop_rules.min_resale_ev requires market_values for "
            "every design"
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
    state["_score_coverage"] = {
        "total_designs": len(union_designs),
        "explicit_score_count": len(explicit_score_labels),
        "scored_design_count": len(scores),
        "complete": set(scores) == union_designs,
        "missing_scores": sorted(union_designs - set(scores)),
        "filled_by_score_default": sorted(set(scores) - explicit_score_labels),
        "score_default_used": bool(set(scores) - explicit_score_labels),
        "score_default_supplied": score_default_supplied,
        "score_default_confirmed": score_default_confirmed,
    }
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
    if rule.endswith("_pp") and not 0 <= normalized <= 100:
        raise StateError(
            f"stop_rule_override {rule} values must be between 0 and 100"
        )
    if rule == "min_resale_ev" and normalized < 0:
        raise StateError(
            "stop_rule_override min_resale_ev values must be non-negative"
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

    if event_type in {"tray_switch", "tray_committed", "tray_accepted"}:
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


BRIEFING_DESIGN_SOURCE_KEYS = ("scores", "liked", "disliked", "hard_avoid")


def _normalize_briefing(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize a zero-tray preference briefing.

    A briefing collects series-level preferences before any tray is observed.
    It is expanded into a synthetic tray with one unconstrained box per regular
    design, so the posterior engine yields the exact uniform blind-draw
    baseline without any box-position evidence. The synthetic boxes are
    computational scaffolding only; they never reach a reader-facing report.
    """
    try:
        version = int(raw.get("session_schema_version", SESSION_SCHEMA_VERSION))
    except (TypeError, ValueError) as exc:
        raise StateError("session_schema_version must be an integer") from exc
    if version != SESSION_SCHEMA_VERSION:
        raise StateError(
            f"unsupported session_schema_version: {version}; "
            f"expected {SESSION_SCHEMA_VERSION}"
        )

    try:
        regular_count = int(raw["regular_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StateError(
            "preference briefing requires an integer regular_count"
        ) from exc
    if regular_count < 1:
        raise StateError("briefing.regular_count must be at least 1")

    raw_model = raw.get("model")
    if raw_model is not None:
        if not isinstance(raw_model, Mapping):
            raise StateError("briefing.model must be an object")
        if str(raw_model.get("type", "unique_regular")) != "unique_regular":
            raise StateError(
                "preference briefings require a complete regular design table; "
                "mixture models need a real tray"
            )

    preferences = raw.get("preferences", {})
    if not isinstance(preferences, Mapping):
        raise StateError("briefing.preferences must be an object")
    designs = raw.get("designs")
    if designs is None and isinstance(raw_model, Mapping):
        designs = raw_model.get("designs")
    if designs is None:
        mentioned: set[str] = set()
        for key in BRIEFING_DESIGN_SOURCE_KEYS:
            value = preferences.get(key)
            if isinstance(value, Mapping):
                mentioned.update(str(label) for label in value)
            elif isinstance(value, list):
                mentioned.update(str(label) for label in value)
        market_values = raw.get("market_values")
        if isinstance(market_values, Mapping):
            mentioned.update(str(label) for label in market_values)
        if not mentioned:
            raise StateError(
                "preference briefing requires designs, or preferences that "
                "mention every regular design"
            )
        if len(mentioned) != regular_count:
            raise StateError(
                "briefing design coverage is incomplete: preferences mention "
                f"{len(mentioned)} designs but regular_count is "
                f"{regular_count}; refusing to guess the missing designs"
            )
        designs = sorted(mentioned)
    if not isinstance(designs, list) or not designs:
        raise StateError("briefing.designs must be a non-empty list")
    design_list = [str(design) for design in designs]
    if len(set(design_list)) != len(design_list):
        raise StateError("briefing.designs contains duplicates")
    if len(design_list) != regular_count:
        raise StateError(
            f"briefing.designs lists {len(design_list)} designs but "
            f"regular_count is {regular_count}"
        )

    pseudo_state = {
        "series": raw.get("series"),
        "model": {"type": "unique_regular", "designs": design_list},
        "boxes": [
            {
                "id": str(index + 1),
                "excluded": [],
                "known": None,
                "status": AVAILABLE_STATUS,
                "tool_used": False,
            }
            for index in range(regular_count)
        ],
        "preferences": copy.deepcopy(dict(preferences)),
        "tools": copy.deepcopy(dict(raw.get("tools") or {})),
        "market_values": copy.deepcopy(dict(raw.get("market_values") or {})),
    }
    state = _normalize_state(pseudo_state)
    coverage = state["_score_coverage"]
    if not coverage["complete"]:
        if "score_default" in preferences and not coverage[
            "score_default_confirmed"
        ]:
            raise StateError(
                "preference briefing cannot fill omitted designs until "
                "preferences.score_default_confirmed=true"
            )
        raise StateError(
            "preference briefing requires complete scores for every regular "
            f"design; missing {coverage['missing_scores']}"
        )
    return state


def _normalize_session(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize legacy state or a multi-tray session envelope.

    Legacy inputs become an implicit one-tray session internally while their
    CLI report remains backward compatible. Zero-tray preference briefings
    normalize into a synthetic uniform tray for baseline math only.
    """
    if not isinstance(raw, Mapping):
        raise StateError("input must be a JSON object")

    if "trays" not in raw and "boxes" not in raw and "regular_count" in raw:
        state = _normalize_briefing(raw)
        return {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "active_tray_id": None,
            "accepted_tray_id": None,
            "candidate_tray_id": None,
            "tools": copy.deepcopy(state["tools"]),
            "draws_used": 0,
            "events": [],
            "_legacy_input": False,
            "_briefing_input": True,
            "_briefing_state": state,
            "_candidate_source": None,
            "_auto_commitments": [],
        }

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
        # Lossless state upgrade: a legacy single tray that already recorded a
        # real card or open is auto-committed as the candidate without asking
        # the user to say "lock the tray".
        legacy_committed = any(
            box["tool_used"] or box["status"] == "opened"
            for box in state["boxes"]
        )
        return {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "active_tray_id": tray_id,
            "accepted_tray_id": None,
            "candidate_tray_id": tray_id if legacy_committed else None,
            "tools": copy.deepcopy(state["tools"]),
            "draws_used": draws_used,
            "events": [],
            "_legacy_input": True,
            "_tray_states": {tray_id: state},
            "_candidate_source": (
                "first_tool_or_open" if legacy_committed else None
            ),
            "_auto_commitments": (
                [
                    {
                        "tray_id": tray_id,
                        "source": "first_tool_or_open",
                        "basis": "legacy_state_tool_used_or_opened_box",
                    }
                ]
                if legacy_committed
                else []
            ),
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
    tray_participations: Dict[str, str] = {}
    canonical_series = shared.get("series")
    for raw_tray in raw_trays:
        if not isinstance(raw_tray, Mapping):
            raise StateError("each session tray must be an object")
        tray = copy.deepcopy(dict(raw_tray))
        tray_id = str(tray.pop("id", "")).strip()
        if not tray_id:
            raise StateError("each session tray requires a non-empty id")
        if tray_id in tray_states:
            raise StateError(f"duplicate tray id: {tray_id}")
        participation = tray.pop("participation", "active")
        if participation not in TRAY_PARTICIPATION_MODES:
            raise StateError(
                f"tray {tray_id}: participation must be one of "
                f"{sorted(TRAY_PARTICIPATION_MODES)}"
            )
        tray_series = tray.pop("series", None)
        if tray_series not in (None, ""):
            tray_series = str(tray_series)
            if canonical_series in (None, ""):
                canonical_series = tray_series
                shared["series"] = tray_series
            elif str(canonical_series) != tray_series:
                raise StateError(
                    "multi-tray sessions require every tray to use the same series"
                )
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
        tray_participations[tray_id] = str(participation)

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
    # candidate_tray_id is optional: when absent it is filled losslessly from
    # the derived commitment (explicit event or first real card/open), so old
    # session states keep normalizing without migration.
    raw_candidate_tray_id = raw.get("candidate_tray_id")
    explicit_candidate_tray_id = (
        None
        if raw_candidate_tray_id in (None, "")
        else str(raw_candidate_tray_id).strip()
    )
    if explicit_candidate_tray_id is not None:
        if explicit_candidate_tray_id not in tray_states:
            raise StateError(
                "candidate_tray_id must identify one session tray"
            )

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
    try:
        raw_lifecycle = reduce_lifecycle_events(
            events,
            tray_count=len(tray_states),
        )
    except LifecycleError as exc:
        raise StateError(str(exc)) from exc
    tool_event_boxes: set[Tuple[str, str]] = set()
    opened_event_boxes: set[Tuple[str, str]] = set()
    override_chains: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        event_type = event["type"]
        if event_type == "stop_rule_override":
            override_chains.setdefault(event["rule"], []).append(event)
        elif event_type in {"hint_used", "display_used", "opened_result"}:
            if event_type in {"hint_used", "display_used"}:
                key = (event["tray_id"], event["box_id"])
                if key in tool_event_boxes or key in opened_event_boxes:
                    raise StateError(
                        "session events must record at most one tool before "
                        f"opening box {key[1]!r} in tray {key[0]!r}"
                    )
                tool_event_boxes.add(key)
            else:
                key = (event["tray_id"], event["box_id"])
                if key in opened_event_boxes:
                    raise StateError(
                        f"session events repeat opened_result for tray/box {key}"
                    )
                opened_event_boxes.add(key)

    if raw_lifecycle["accepted_tray_id"] != accepted_tray_id:
        raise StateError(
            "accepted_tray_id must match the tray acceptance/release event history"
        )
    if explicit_candidate_tray_id is not None:
        if explicit_candidate_tray_id != raw_lifecycle["candidate_tray_id"]:
            raise StateError(
                "candidate_tray_id must match the tray commitment event history"
            )
    if (
        raw_lifecycle["candidate_tray_id"] is not None
        and raw_lifecycle["accepted_tray_id"] is not None
    ):
        raise StateError(
            "a session cannot hold both a candidate and an accepted tray"
        )
    if accepted_tray_id is not None and accepted_tray_id != active_tray_id:
        raise StateError(
            "the accepted tray must remain active until an explicit release event"
        )
    if (
        raw_lifecycle["candidate_tray_id"] is not None
        and raw_lifecycle["candidate_tray_id"] != active_tray_id
    ):
        raise StateError(
            "the candidate tray must remain active until an explicit release "
            "event"
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
    provisional = {
        "session_schema_version": version,
        "series": active_state.get("series"),
        "active_tray_id": active_tray_id,
        "accepted_tray_id": raw_lifecycle["accepted_tray_id"],
        "candidate_tray_id": raw_lifecycle["candidate_tray_id"],
        "tools": copy.deepcopy(active_state["tools"]),
        "draws_used": draws_used,
        "events": events,
        "_legacy_input": False,
        "_tray_states": tray_states,
        "_tray_participations": tray_participations,
        "_candidate_source": raw_lifecycle["candidate_source"],
        "_accepted_source": raw_lifecycle["accepted_source"],
        "_accepted_commitment_source": raw_lifecycle[
            "accepted_commitment_source"
        ],
        "_auto_commitments": raw_lifecycle["auto_commitments"],
        "_auto_acceptances": [],
    }
    acceptance_points = _derived_acceptance_points(provisional)
    canonical = inject_derived_lifecycle_events(
        events,
        auto_commitments=raw_lifecycle["auto_commitments"],
        acceptance_points=acceptance_points,
    )
    try:
        final_lifecycle = reduce_lifecycle_events(
            canonical["events"],
            tray_count=len(tray_states),
        )
    except LifecycleError as exc:
        raise StateError(str(exc)) from exc
    if (
        final_lifecycle["accepted_tray_id"] is not None
        and final_lifecycle["accepted_tray_id"] != active_tray_id
    ):
        raise StateError(
            "the accepted tray must remain active until an explicit release event"
        )
    if (
        final_lifecycle["candidate_tray_id"] is not None
        and final_lifecycle["candidate_tray_id"] != active_tray_id
    ):
        raise StateError(
            "the candidate tray must remain active until an explicit release event"
        )
    provisional.update(
        {
            "accepted_tray_id": final_lifecycle["accepted_tray_id"],
            "candidate_tray_id": final_lifecycle["candidate_tray_id"],
            "events": canonical["events"],
            "_candidate_source": final_lifecycle["candidate_source"],
            "_accepted_source": final_lifecycle["accepted_source"],
            "_accepted_commitment_source": final_lifecycle[
                "accepted_commitment_source"
            ],
            "_auto_commitments": canonical["auto_commitments"],
            "_auto_acceptances": canonical["auto_acceptances"],
        }
    )
    return provisional


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
        key=lambda b: (
            _bit_count(candidate_masks[b["id"]]),
            _stable_box_sort_key(b["id"]),
        ),
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


def _score_derived_target_groups(
    preferences: Mapping[str, Any],
) -> List[List[str]]:
    """Group score-derived liked designs without inventing an order inside ties."""
    if preferences["preference_sources"]["liked"] != "scores":
        return []
    scores = preferences["scores"]
    grouped: Dict[float, List[str]] = {}
    for design in preferences["liked"]:
        grouped.setdefault(float(scores[design]), []).append(design)
    return [
        sorted(grouped[score])
        for score in sorted(grouped, reverse=True)
    ]


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
        score_groups = _score_derived_target_groups(prefs)
        if score_groups:
            grouped_probabilities = [
                sum(
                    float(metrics["liked_probabilities"].get(design, 0.0))
                    for design in group
                )
                for group in score_groups
            ]
            primary = [-q(value) for value in grouped_probabilities]
            primary += [-q(metrics["p_like_any"])]
            exact = [-value for value in grouped_probabilities]
            exact += [
                -float(metrics["p_like_any"]),
                -float(metrics["liked_weighted_score"]),
            ]
            return tuple(primary + exact)
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
        expected_score = metrics.get("expected_score")
        return (
            -float(value),
            -float(expected_score) if expected_score is not None else 0.0,
            float(metrics["p_hard_avoid"]),
            float(metrics["p_dislike_any"]),
        )

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
        score_groups = _score_derived_target_groups(prefs)
        if score_groups:
            metric_name = (
                "p_favorite_any"
                if float(prefs["scores"][score_groups[0][0]]) == 10.0
                else "p_top_score_group"
            )
            return (
                sum(
                    float(metrics["liked_probabilities"].get(design, 0.0))
                    for design in score_groups[0]
                ),
                metric_name,
            )
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
    # Rank the feasible choices first, keeping the strategy's ordering within
    # each group. If none qualify, retain the best failed choice for diagnosis.
    return sorted(
        _sort_metrics(rows, state),
        key=lambda row: not evaluate_draw_decision(state, row)["should_draw"],
    )


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
    min_resale = stop_rules.get("min_resale_ev")
    if min_resale is not None and float(best["resale_ev"]) < min_resale:
        reasons.append(
            f"预期二手价值 ¥{float(best['resale_ev']):.2f} "
            f"低于 ¥{min_resale:.2f}"
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

    if "min_resale_ev" in stop_rules:
        actual = float(best["resale_ev"])
        threshold = float(stop_rules["min_resale_ev"])
        checks.append(
            {
                "rule": "min_resale_ev",
                "operator": ">=",
                "threshold": threshold,
                "actual": actual,
                "margin": actual - threshold,
                "unit": "cny",
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
        row["stop_rule_checks"] = _report_stop_rule_checks(state, metrics)
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


def _calibration_primary_spec(
    state: Mapping[str, Any],
) -> Dict[str, str]:
    preferences = state["preferences"]
    mode = preferences["objective_mode"]
    if mode == "top_target_first":
        if preferences["preference_sources"]["liked"] != "scores":
            raise StateError(
                "score-first calibration for strategy 只冲最爱 requires "
                "score-derived liked designs; omit the explicit liked field or "
                "continue with the legacy ordered-target workflow"
            )
        if not preferences["score_tiers"]["favorite"]:
            raise StateError(
                "strategy 只冲最爱 requires at least one design scored +10 "
                "for preference calibration"
            )
        return {
            "metric": "p_favorite_any_pp",
            "rule": "min_favorite_any_pp",
            "direction": "max",
            "label": "最爱款合计",
        }
    if mode == "target_only":
        if not preferences["liked"]:
            raise StateError(
                "strategy 随便中个喜欢 requires at least one liked design "
                "for preference calibration"
            )
        return {
            "metric": "p_like_any_pp",
            "rule": "min_like_any_pp",
            "direction": "max",
            "label": "喜欢款合计",
        }
    if mode in {"balanced", "guardrail"}:
        return {
            "metric": "expected_score",
            "rule": "min_expected_score",
            "direction": "max",
            "label": "期望评分",
        }
    if mode == "risk_first":
        if preferences["disliked"]:
            return {
                "metric": "p_dislike_any_pp",
                "rule": "max_dislike_any_pp",
                "direction": "min",
                "label": "不喜欢款合计",
            }
        if preferences["liked"]:
            return {
                "metric": "p_like_any_pp",
                "rule": "min_like_any_pp",
                "direction": "max",
                "label": "喜欢款合计",
            }
        return {
            "metric": "expected_score",
            "rule": "min_expected_score",
            "direction": "max",
            "label": "期望评分",
        }
    if mode == "resale_ev":
        coverage = state["_market_value_coverage"]
        if not coverage["complete"]:
            raise StateError(
                "strategy 保值优先 calibration requires current market_values "
                f"for every design; missing {coverage['missing_values']}"
            )
        return {
            "metric": "resale_ev",
            "rule": "min_resale_ev",
            "direction": "max",
            "label": "预期二手价值",
        }
    raise StateError("unsupported strategy for preference calibration")


def _calibration_row(
    metrics: Mapping[str, Any],
    rank: int,
    *,
    include_resale: bool = False,
) -> Dict[str, Any]:
    row = {
        "rank": rank,
        "box_id": metrics["box_id"],
        "p_like_any_pp": 100.0 * float(metrics["p_like_any"]),
        "p_favorite_any_pp": 100.0 * float(metrics["p_favorite_any"]),
        "p_dislike_any_pp": 100.0 * float(metrics["p_dislike_any"]),
        "p_hard_avoid_pp": 100.0 * float(metrics["p_hard_avoid"]),
        "expected_score": float(metrics["expected_score"]),
    }
    if include_resale:
        row["resale_ev"] = float(metrics["resale_ev"])
    return row


def _calibration_dimensions(
    state: Mapping[str, Any],
    primary: Mapping[str, str],
) -> List[Tuple[str, str]]:
    dimensions = [(primary["metric"], primary["direction"])]
    preferences = state["preferences"]
    if (
        primary["metric"] == "resale_ev"
        and ("expected_score", "max") not in dimensions
    ):
        dimensions.append(("expected_score", "max"))
    if (
        preferences["disliked"]
        and primary["metric"] != "p_dislike_any_pp"
    ):
        dimensions.append(("p_dislike_any_pp", "min"))
    if (
        preferences["hard_avoid"]
        and primary["metric"] != "p_hard_avoid_pp"
    ):
        dimensions.append(("p_hard_avoid_pp", "min"))
    return dimensions


def _calibration_dominates(
    candidate: Mapping[str, Any],
    other: Mapping[str, Any],
    dimensions: Sequence[Tuple[str, str]],
) -> bool:
    no_worse = True
    strictly_better = False
    for metric, direction in dimensions:
        candidate_value = float(candidate[metric])
        other_value = float(other[metric])
        if direction == "max":
            if candidate_value + 1e-12 < other_value:
                no_worse = False
            if candidate_value > other_value + 1e-12:
                strictly_better = True
        else:
            if candidate_value > other_value + 1e-12:
                no_worse = False
            if candidate_value + 1e-12 < other_value:
                strictly_better = True
    return no_worse and strictly_better


def _calibration_frontier(
    rows: Sequence[Mapping[str, Any]],
    dimensions: Sequence[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    unique_rows: List[Mapping[str, Any]] = []
    signatures: set[Tuple[float, ...]] = set()
    for row in rows:
        signature = tuple(round(float(row[metric]), 12) for metric, _ in dimensions)
        if signature in signatures:
            continue
        signatures.add(signature)
        unique_rows.append(row)
    return [
        dict(row)
        for row in unique_rows
        if not any(
            _calibration_dominates(other, row, dimensions)
            for other in unique_rows
            if other is not row
        )
    ]


def _calibration_primary_sort_value(
    row: Mapping[str, Any],
    primary: Mapping[str, str],
) -> float:
    value = float(row[primary["metric"]])
    return -value if primary["direction"] == "max" else value


def _outward_candidate_threshold(rule: str, actual: float) -> float:
    if rule == "min_expected_score":
        return math.floor(actual * 10.0 + 1e-12) / 10.0
    if rule == "min_resale_ev":
        return float(math.floor(actual + 1e-12))
    if rule.startswith("min_"):
        return float(math.floor(actual + 1e-12))
    return float(math.ceil(actual - 1e-12))


def _calibration_candidate_rules(
    row: Mapping[str, Any],
    state: Mapping[str, Any],
    primary: Mapping[str, str],
) -> Dict[str, float]:
    metric_by_rule = {
        "min_like_any_pp": "p_like_any_pp",
        "min_favorite_any_pp": "p_favorite_any_pp",
        "max_dislike_any_pp": "p_dislike_any_pp",
        "max_hard_avoid_pp": "p_hard_avoid_pp",
        "min_expected_score": "expected_score",
        "min_resale_ev": "resale_ev",
    }
    requested_rules = [primary["rule"]]
    if primary["metric"] == "resale_ev":
        requested_rules.append("min_expected_score")
    if state["preferences"]["disliked"]:
        requested_rules.append("max_dislike_any_pp")
    if state["preferences"]["hard_avoid"]:
        requested_rules.append("max_hard_avoid_pp")
    rules: Dict[str, float] = {}
    for rule in QUALITY_STOP_RULE_KEYS:
        if rule not in requested_rules:
            continue
        actual = float(row[metric_by_rule[rule]])
        rules[rule] = _outward_candidate_threshold(rule, actual)
    return rules


def _calibration_choices(
    frontier: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    primary: Mapping[str, str],
) -> List[Dict[str, Any]]:
    if not frontier:
        return []
    preferences = state["preferences"]
    primary_labels = {
        "p_favorite_any_pp": "最爱优先",
        "p_like_any_pp": "喜欢优先",
        "p_dislike_any_pp": "总雷最低",
        "expected_score": "评分优先",
        "resale_ev": "保值优先",
    }
    selectors: List[Tuple[str, Any]] = [
        (
            primary_labels[primary["metric"]],
            lambda row: (
                _calibration_primary_sort_value(row, primary),
                float(row["p_hard_avoid_pp"]),
                float(row["p_dislike_any_pp"]),
                -float(row["expected_score"]),
                _stable_box_sort_key(row["box_id"]),
            ),
        )
    ]
    if (
        preferences["hard_avoid"]
        and primary["metric"] != "p_hard_avoid_pp"
    ):
        selectors.append(
            (
                "硬雷最低",
                lambda row: (
                    float(row["p_hard_avoid_pp"]),
                    _calibration_primary_sort_value(row, primary),
                    float(row["p_dislike_any_pp"]),
                    -float(row["expected_score"]),
                    _stable_box_sort_key(row["box_id"]),
                ),
            )
        )
    if (
        preferences["disliked"]
        and primary["metric"] != "p_dislike_any_pp"
    ):
        selectors.append(
            (
                "总雷最低",
                lambda row: (
                    float(row["p_dislike_any_pp"]),
                    _calibration_primary_sort_value(row, primary),
                    float(row["p_hard_avoid_pp"]),
                    -float(row["expected_score"]),
                    _stable_box_sort_key(row["box_id"]),
                ),
            )
        )

    selected: List[Tuple[str, Mapping[str, Any]]] = []
    selected_box_ids: set[str] = set()
    for label, sort_key in selectors:
        row = min(frontier, key=sort_key)
        box_id = str(row["box_id"])
        if box_id in selected_box_ids:
            continue
        selected_box_ids.add(box_id)
        selected.append((label, row))

    goal_row = selected[0][1]
    choices: List[Dict[str, Any]] = []
    for index, (label, row) in enumerate(selected):
        metric_keys = [
            "p_favorite_any_pp",
            "p_like_any_pp",
            "p_dislike_any_pp",
            "p_hard_avoid_pp",
            "expected_score",
        ]
        if row.get("resale_ev") is not None:
            metric_keys.append("resale_ev")
        choices.append(
            {
                "choice": str(index + 1),
                "orientation": label,
                "box_id": row["box_id"],
                "actual": {
                    key: row[key] for key in metric_keys
                },
                "tradeoff_vs_goal_choice": {
                    "favorite_delta_pp": (
                        float(row["p_favorite_any_pp"])
                        - float(goal_row["p_favorite_any_pp"])
                    ),
                    "like_delta_pp": (
                        float(row["p_like_any_pp"])
                        - float(goal_row["p_like_any_pp"])
                    ),
                    "dislike_reduction_pp": (
                        float(goal_row["p_dislike_any_pp"])
                        - float(row["p_dislike_any_pp"])
                    ),
                    "hard_avoid_reduction_pp": (
                        float(goal_row["p_hard_avoid_pp"])
                        - float(row["p_hard_avoid_pp"])
                    ),
                    "expected_score_delta": (
                        float(row["expected_score"])
                        - float(goal_row["expected_score"])
                    ),
                    "resale_reduction_cny": (
                        None
                        if row.get("resale_ev") is None
                        else (
                            float(goal_row["resale_ev"])
                            - float(row["resale_ev"])
                        )
                    ),
                },
                "suggested_stop_rules": _calibration_candidate_rules(
                    row,
                    state,
                    primary,
                ),
            }
        )
    return choices


def build_preference_calibration_report(
    state: Mapping[str, Any],
    *,
    tray_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a score-first boundary calibration without a draw recommendation."""
    coverage = copy.deepcopy(state["_score_coverage"])
    if (
        coverage.get("score_default_supplied")
        and not coverage["score_default_confirmed"]
        and not coverage["complete"]
    ):
        raise StateError(
            "preference calibration cannot fill omitted designs until "
            "preferences.score_default_confirmed=true"
        )
    if not coverage["complete"]:
        raise StateError(
            "preference calibration requires every design to have a score; "
            f"missing {coverage['missing_scores']}. Give every design a score, "
            "or use score_default only after the user confirms one shared score."
        )
    if coverage["score_default_used"] and not coverage["score_default_confirmed"]:
        raise StateError(
            "preference calibration requires "
            "preferences.score_default_confirmed=true when score_default fills "
            "unlisted designs"
        )

    primary = _calibration_primary_spec(state)
    base_report = build_report(state)
    rows = [
        _calibration_row(
            metrics,
            rank,
            include_resale=primary["metric"] == "resale_ev",
        )
        for rank, metrics in enumerate(base_report["ranking"], start=1)
    ]
    dimensions = _calibration_dimensions(state, primary)
    frontier = _calibration_frontier(rows, dimensions)
    frontier_ids = {str(row["box_id"]) for row in frontier}
    for row in rows:
        row["pareto_frontier"] = str(row["box_id"]) in frontier_ids

    ranges = {
        metric: {
            "min": min(float(row[metric]) for row in rows),
            "max": max(float(row[metric]) for row in rows),
        }
        for metric in [
            "p_favorite_any_pp",
            "p_like_any_pp",
            "p_dislike_any_pp",
            "p_hard_avoid_pp",
            "expected_score",
            *(
                ["resale_ev"]
                if primary["metric"] == "resale_ev"
                else []
            ),
        ]
    }
    target_unreachable = (
        primary["direction"] == "max"
        and primary["metric"] in {
            "p_favorite_any_pp",
            "p_like_any_pp",
            "resale_ev",
        }
        and ranges[primary["metric"]]["max"] <= 1e-12
    )
    choices = (
        []
        if target_unreachable
        else _calibration_choices(frontier, state, primary)
    )
    existing_stop_rules = copy.deepcopy(state["preferences"]["stop_rules"])
    if coverage["score_default_used"]:
        coverage["score_default"] = state["preferences"]["score_default"]

    return {
        "report_type": "preference_calibration",
        "series": state.get("series"),
        "tray_id": tray_id,
        "status": (
            "target_unreachable"
            if target_unreachable
            else "needs_confirmation"
        ),
        "strategy_name": state["preferences"]["strategy"],
        "strategy_rule": STRATEGY_RULES[
            state["preferences"]["objective_mode"]
        ],
        "scope": {
            "evidence": "current_active_tray",
            "box_metrics": "direct_drawable_boxes_before_new_tools",
            "confirmed_rules_apply_to": "current_series_session",
            "recalibrate_when": [
                "series_changes",
                "scores_change_materially",
            ],
        },
        "score_coverage": coverage,
        "market_value_coverage": copy.deepcopy(
            state["_market_value_coverage"]
        ),
        "scores": copy.deepcopy(state["preferences"]["scores"]),
        "score_tiers": copy.deepcopy(
            state["preferences"]["score_tiers"]
        ),
        "primary_metric": primary,
        "attainable_ranges": ranges,
        "all_boxes": rows,
        "pareto_box_ids": [
            row["box_id"]
            for row in frontier
        ],
        "choices": choices,
        "existing_stop_rules": existing_stop_rules,
        "stop_rules_mutated": False,
        "confirmation_required": True,
        "candidate_rounding": {
            "probability_pp": "outward_to_whole_percentage_point",
            "expected_score": "outward_to_one_decimal",
            "resale_ev": "outward_to_whole_cny",
        },
        "model_summary": base_report["model_summary"],
        "model_warnings": base_report["model_warnings"],
    }


def _session_lifecycle_summary(
    *,
    accepted_tray_id: Optional[str],
    candidate_tray_id: Optional[str],
    commitment_source: Optional[str],
    candidate_qualified: bool,
    accepted_source: Optional[str] = None,
    accepted_commitment_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper around the shared pure lifecycle summary."""
    return summarize_lifecycle(
        accepted_tray_id=accepted_tray_id,
        candidate_tray_id=candidate_tray_id,
        commitment_source=commitment_source,
        accepted_source=accepted_source,
        accepted_commitment_source=accepted_commitment_source,
        candidate_qualified=candidate_qualified,
    )


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
    candidate_tray_id = session.get("candidate_tray_id")
    commitment_source = session.get("_candidate_source")
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
        currently_qualified = (
            bool(profile)
            and decision["should_draw"]
            and all(check["passed"] for check in profile)
        )
        is_accepted = tray_id == accepted_tray_id
        is_candidate = (
            not is_accepted
            and candidate_tray_id is not None
            and tray_id == candidate_tray_id
        )
        tray_reports[tray_id]["tray_lock"] = {
            "is_accepted": is_accepted,
            "is_candidate": is_candidate,
            "phase": (
                "accepted"
                if is_accepted
                else "candidate"
                if is_candidate
                else "uncommitted"
            ),
            "accepted_tray_id": accepted_tray_id,
            "candidate_tray_id": candidate_tray_id,
            "release_required_before_switch": (
                accepted_tray_id is not None or candidate_tray_id is not None
            ),
            "currently_qualified": currently_qualified,
            "commitment_source": (
                commitment_source if is_candidate else None
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

    active_currently_qualified = tray_reports[active_tray_id]["tray_lock"][
        "currently_qualified"
    ]
    lifecycle = _session_lifecycle_summary(
        accepted_tray_id=accepted_tray_id,
        candidate_tray_id=candidate_tray_id,
        commitment_source=commitment_source,
        candidate_qualified=bool(active_currently_qualified),
        accepted_source=session.get("_accepted_source"),
        accepted_commitment_source=session.get(
            "_accepted_commitment_source"
        ),
    )
    # The derived candidate upgrade behaves exactly like an explicit
    # acceptance for every recommendation that follows.
    effective_accepted_tray_id = (
        accepted_tray_id
        if accepted_tray_id is not None
        else (
            candidate_tray_id
            if lifecycle["upgraded_from_candidate"]
            else None
        )
    )

    if lifecycle["phase"] == "accepted":
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
            "tray_id": effective_accepted_tray_id,
            "release_required_before_switch": True,
        }
        screening = active_report.get("tray_screening")
        if screening is not None:
            screening["accepted_tray_id"] = effective_accepted_tray_id
            screening["release_required_before_switch"] = True
            if screening["recommendation"] == "switch":
                screening["unlocked_recommendation"] = "switch"
                screening["status"] = "accepted_review"
                screening["recommendation"] = "release_before_switch"
    elif lifecycle["phase"] == "candidate":
        session_recommendation = {
            "action": (
                "continue_with_candidate_tray"
                if active_decision["should_draw"]
                else (
                    "continue_with_candidate_tray_tool_plan"
                    if (
                        active_tool_action is not None
                        and active_tool_action["tool"] != "none"
                        and float(
                            active_tool_action["expected_draw_probability"]
                        )
                        > 0
                    )
                    else "stop_or_release_candidate_tray"
                )
            ),
            "tray_id": candidate_tray_id,
            "release_required_before_switch": True,
        }
        screening = active_report.get("tray_screening")
        if screening is not None:
            screening["candidate_tray_id"] = candidate_tray_id
            screening["release_required_before_switch"] = True
            if screening["recommendation"] == "switch":
                screening["unlocked_recommendation"] = "switch"
                screening["status"] = "candidate_review"
                screening["recommendation"] = "release_before_switch"
    else:
        session_recommendation = {
            "action": "follow_active_tray_report",
            "tray_id": active_tray_id,
            "release_required_before_switch": False,
        }

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
            "candidate_tray_id": candidate_tray_id,
            "lock_status": lifecycle["phase"],
            "tray_lifecycle": lifecycle,
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
            "commitment_lifecycle": {
                "commit_events": [
                    copy.deepcopy(event)
                    for event in session["events"]
                    if event["type"] == "tray_committed"
                ],
                "auto_commitments": copy.deepcopy(
                    session.get("_auto_commitments") or []
                ),
                "phase": lifecycle["phase"],
                "upgraded_from_candidate": lifecycle[
                    "upgraded_from_candidate"
                ],
            },
            "stop_rule_overrides": stop_rule_overrides,
        },
    }


def _tray_comparison_row(
    tray_id: str,
    state: Mapping[str, Any],
    *,
    beam_width: int,
) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Assess one tray independently and derive its comparison sort key.

    The key never mixes tuple shapes across status classes: the status rank
    resolves first, and the tail is only compared between trays of the same
    status. Ready trays reuse the exact strategy ordering keys; tool-dependent
    trays lead with the probability of a fully qualifying branch.
    """
    posterior = analyze_posterior(state)
    best = available_box_metrics(state, posterior)[0]
    tool_plan = plan_tools(state, posterior, depth=1, beam_width=beam_width)
    assessment = assess_tray(state, best, tool_plan)
    action = tool_plan["recommended_action"]
    terminal = action["expected_terminal_metrics"]
    status = assessment["status"]
    status_rank = TRAY_COMPARISON_STATUS_RANK[status]
    tol = state["preferences"]["tie_tolerance_pp"] / 100.0
    if status == "tool_dependent":
        primary_value, _ = _primary_tool_metric(terminal, state)
        tail: Tuple[Any, ...] = (
            -_probability_bucket(
                float(action["expected_draw_probability"]), tol
            ),
            -_probability_bucket(primary_value, tol),
            float(terminal["p_dislike_any"]),
            float(terminal["p_hard_avoid"]),
            round(float(action.get("expected_tools_used", 0.0)), 12),
            tray_id,
        )
    else:
        tail = (metric_comparison_key(best, state), tray_id)
    row = {
        "tray_id": tray_id,
        "status": status,
        "status_label": TRAY_COMPARISON_STATUS_LABELS[status],
        "direct_best_box_id": best["box_id"],
        "metrics": {
            "p_like_any_pp": 100.0 * float(best["p_like_any"]),
            "p_favorite_any_pp": 100.0 * float(best["p_favorite_any"]),
            "p_dislike_any_pp": 100.0 * float(best["p_dislike_any"]),
            "p_hard_avoid_pp": 100.0 * float(best["p_hard_avoid"]),
            "expected_score": best.get("expected_score"),
            "resale_ev": best.get("resale_ev"),
        },
        "acceptance_profile": assessment["acceptance_profile"],
        "first_tool_action": _action_summary(action),
        "post_tool_draw_probability_pp": 100.0
        * float(action["expected_draw_probability"]),
        "expected_tools_used": float(
            action.get("expected_tools_used", 0.0)
        ),
        "drawable_box_count": sum(
            1 for box in state["boxes"] if box["status"] in DRAWABLE_STATUSES
        ),
        "tray_opened_count": sum(
            1 for box in state["boxes"] if box["status"] == "opened"
        ),
    }
    sort_key = (status_rank, tail)
    row["strategy_ranking_key"] = _comparison_key_to_json(sort_key)
    return sort_key, row


def _comparison_key_to_json(value: Any) -> Any:
    """Convert a nested tuple sort key into a JSON-stable list tree."""
    if isinstance(value, tuple):
        return [_comparison_key_to_json(item) for item in value]
    return value


def _comparison_key_from_json(value: Any) -> Any:
    """Freeze a JSON list tree back into a comparable tuple tree."""
    if isinstance(value, list):
        return tuple(_comparison_key_from_json(item) for item in value)
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    raise ValueError("comparison ranking keys contain only lists and scalars")


def build_tray_comparison_report(
    session: Mapping[str, Any],
    compare_depth: int = 1,
    beam_width: int = 3,
) -> Dict[str, Any]:
    """Compare every still-operable tray under the shared strategy and lines.

    Each tray is solved independently; released, history, and inoperable trays
    stay out of the action ranking and are reported for review only.
    """
    if compare_depth not in {1, 2}:
        raise StateError("comparison planning depth must be 1 or 2")
    tray_states = session["_tray_states"]
    if len(tray_states) < 2:
        raise StateError(
            "tray comparison needs a session with at least two trays; a "
            "single tray keeps using --screen-tray or the formal report"
        )
    series_values = {
        str(state.get("series", "")).strip()
        for state in tray_states.values()
    }
    if len(series_values) != 1 or not next(iter(series_values), ""):
        raise StateError(
            "tray comparison requires every tray to declare the same series"
        )
    participations = session.get("_tray_participations", {})
    released_tray_ids = {
        event["tray_id"]
        for event in session["events"]
        if event["type"] == "tray_released"
    }

    ranked_pairs: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
    excluded_trays: List[Dict[str, Any]] = []
    for tray_id, state in tray_states.items():
        drawable = sum(
            1 for box in state["boxes"] if box["status"] in DRAWABLE_STATUSES
        )
        # Inoperable is the physical fact: with no drawable box the tray
        # supports no action regardless of its lifecycle state.
        if drawable == 0:
            reason = "inoperable"
        elif tray_id in released_tray_ids:
            reason = "released"
        elif participations.get(tray_id, "active") == "history":
            reason = "history"
        else:
            reason = None
        if reason is not None:
            excluded_trays.append(
                {
                    "tray_id": tray_id,
                    "reason": reason,
                    "reason_label": TRAY_COMPARISON_EXCLUDED_REASONS[reason],
                    "drawable_box_count": drawable,
                    "opened_box_count": sum(
                        1
                        for box in state["boxes"]
                        if box["status"] == "opened"
                    ),
                }
            )
            continue
        sort_key, row = _tray_comparison_row(
            tray_id, state, beam_width=beam_width
        )
        row["is_active_tray"] = tray_id == session["active_tray_id"]
        row["is_accepted_tray"] = tray_id == session["accepted_tray_id"]
        row["is_candidate_tray"] = (
            tray_id == session.get("candidate_tray_id")
            and not row["is_accepted_tray"]
        )
        ranked_pairs.append((sort_key, row))

    ranked_pairs.sort(key=lambda item: item[0])
    rows = [row for _, row in ranked_pairs]
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    candidate_tray_id = session.get("candidate_tray_id")
    candidate_row = next(
        (row for row in rows if row["tray_id"] == candidate_tray_id), None
    )
    lifecycle = _session_lifecycle_summary(
        accepted_tray_id=session["accepted_tray_id"],
        candidate_tray_id=candidate_tray_id,
        commitment_source=session.get("_candidate_source"),
        candidate_qualified=(
            candidate_row is not None and candidate_row["status"] == "ready"
        ),
        accepted_source=session.get("_accepted_source"),
        accepted_commitment_source=session.get(
            "_accepted_commitment_source"
        ),
    )

    depth_two: Optional[Dict[str, Any]] = None
    if compare_depth == 2:
        total_cards = (
            session["tools"]["hint_cards"]
            + session["tools"]["display_cards"]
        )
        if total_cards < 2:
            raise StateError(
                "depth-two comparison requires at least two available cards; "
                "rerun with --compare-depth 1"
            )
        head_ids = [
            row["tray_id"]
            for row in rows[:COMPARISON_DEPTH_TWO_HEAD_CANDIDATES]
        ]
        for tray_id in head_ids:
            state = tray_states[tray_id]
            posterior = analyze_posterior(state)
            plan = plan_tools(
                state, posterior, depth=2, beam_width=beam_width
            )
            one_card = plan_tools(
                state, posterior, depth=1, beam_width=beam_width
            )
            primary_gain_pp, primary_metric = _primary_tool_uplift_pp(
                plan["recommended_action"]["expected_terminal_metrics"],
                one_card["recommended_action"]["expected_terminal_metrics"],
                state,
            )
            row = next(row for row in rows if row["tray_id"] == tray_id)
            row["depth_two_detail"] = {
                "recommended_action": _action_summary(
                    plan["recommended_action"]
                ),
                "first_action_changed_vs_depth_1": plan[
                    "first_action_changed_vs_depth_1"
                ],
                "terminal_value_practically_equivalent_to_depth_1": plan[
                    "terminal_value_practically_equivalent_to_depth_1"
                ],
                "primary_gain_vs_one_card_pp": primary_gain_pp,
                "primary_metric": primary_metric,
                "gain_vs_one_card_horizon": plan[
                    "gain_vs_one_card_horizon"
                ],
            }
        for row in rows:
            if "depth_two_detail" not in row:
                row["depth_two_note"] = (
                    "两步规划只扩展排名最靠前的端；本端保持一步时域结果。"
                )
        depth_two = {
            "head_candidate_tray_ids": head_ids,
            "cards_available": int(total_cards),
            "horizon_note": (
                "横比默认一步规划；本报告仅对头部候选端扩展两步时域，"
                "并单独标注首步动作是否变化。"
            ),
        }

    recommendation: Dict[str, Any]
    if rows:
        top = rows[0]
        planning_horizon = "one_card"
        comparison_basis = "one_step"
        first_action = top["first_tool_action"]
        if compare_depth == 2 and "depth_two_detail" in top:
            first_action = top["depth_two_detail"]["recommended_action"]
            planning_horizon = "two_card"
            comparison_basis = "two_step_head_candidates"
        non_actionable = top["status"] in {
            "switch",
            "session_stop",
            "needs_acceptance_rules",
        }
        recommendation = {
            "recommended_tray_id": top["tray_id"],
            "status": top["status"],
            "first_action": None if non_actionable else first_action,
            "action": "stop_or_review" if non_actionable else "execute_first_action",
            "planning_horizon": planning_horizon,
            "comparison_basis": comparison_basis,
            "release_required_before_switch": (
                (
                    session["accepted_tray_id"] is not None
                    and session["accepted_tray_id"] != top["tray_id"]
                )
                or (
                    candidate_tray_id is not None
                    and candidate_tray_id != top["tray_id"]
                )
            ),
            "future_tray_improvement_guaranteed": False,
        }
        if non_actionable:
            recommendation["action_reason"] = (
                "靠前端没有可直接执行的动作：建议停止本轮或先补齐质量线/端信息。"
            )
        elif (
            top["status"] == "tool_dependent"
            and session["accepted_tray_id"] != top["tray_id"]
            and candidate_tray_id != top["tray_id"]
        ):
            # Selecting a tool-dependent tray records a candidate commitment;
            # it is never disguised as directly qualified.
            recommendation["commitment_after_action"] = {
                "phase": "candidate",
                "record_event": "tray_committed",
                "note": (
                    "选定该端后记录候选承诺（依赖道具，不是直接合格）；"
                    "真实线索通过全部质量线后升级为已接受。"
                ),
            }
    else:
        recommendation = {
            "recommended_tray_id": None,
            "status": None,
            "first_action": None,
            "action": "stop_or_review",
            "planning_horizon": "one_card" if compare_depth == 1 else "two_card",
            "comparison_basis": "no_operable_trays",
            "release_required_before_switch": False,
            "future_tray_improvement_guaranteed": False,
        }

    model_tray_id = recommendation["recommended_tray_id"] or session[
        "active_tray_id"
    ]
    model_state = tray_states[model_tray_id]
    model_contract, model_warnings = _model_reporting_contract(
        model_state,
        hint_planning_active=(
            model_state["tools"]["hint_cards"] > 0
            and any(
                box["status"] == AVAILABLE_STATUS
                and not box["tool_used"]
                and box["known"] is None
                for box in model_state["boxes"]
            )
        ),
    )
    comparison: Dict[str, Any] = {
        "planning_depth": compare_depth,
        "comparable_tray_count": len(rows),
        "rows": rows,
        "excluded_trays": excluded_trays,
        "rescue_probability_semantics": (
            "道具结果后仍可抽（达到全部质量线）的分支概率，不是中奖率。"
        ),
        "ranking_policy": {
            "basis": "existing_strategy_metrics",
            "status_order": [
                "ready",
                "tool_dependent",
                "switch",
                "needs_acceptance_rules",
                "session_stop",
            ],
            "tool_dependent_primary": "p_qualifying_branch",
            "prefer_fewer_cards_within_tolerance": True,
            "note": (
                "直接合格端沿用既有策略指标排序，只有超过既有道具提升门槛"
                "才建议用卡，实用容差内等价时优先不用卡；依赖道具端先比"
                "达线分支概率，再比策略主指标、风险、预期用卡数和稳定端 ID。"
                "排序不创建新的隐含策略。"
            ),
        },
        "future_tray_improvement_guaranteed": False,
    }
    if depth_two is not None:
        comparison["depth_two"] = depth_two
    return {
        "report_type": "tray_comparison",
        "series": model_state.get("series"),
        "objective_mode": model_state["preferences"]["objective_mode"],
        "strategy_name": model_state["preferences"]["strategy"],
        "strategy_rule": STRATEGY_RULES[
            model_state["preferences"]["objective_mode"]
        ],
        "session_summary": {
            "session_schema_version": session["session_schema_version"],
            "active_tray_id": session["active_tray_id"],
            "accepted_tray_id": session["accepted_tray_id"],
            "candidate_tray_id": candidate_tray_id,
            "lock_status": lifecycle["phase"],
            "tray_lifecycle": lifecycle,
            "tray_ids": list(tray_states),
            "tools": copy.deepcopy(session["tools"]),
            "draws_used": session["draws_used"],
            "max_draws": model_state["preferences"]["stop_rules"].get(
                "max_draws"
            ),
            "event_count": len(session["events"]),
        },
        "comparison": comparison,
        "recommendation": recommendation,
        "stop_rules": copy.deepcopy(
            model_state["preferences"]["stop_rules"]
        ),
        "model_summary": {
            "type": model_state["model"].get("type", "unique_regular"),
            "designs": list(model_state["_union_designs"]),
            "independent_trays": (
                "每端独立建模；不同端之间不建立概率相关性，已售未知盒"
                "保留在各端全局约束中。"
            ),
            **model_contract,
        },
        "model_warnings": model_warnings,
    }


SESSION_REVIEW_STOP_CONCLUSIONS = frozenset(
    {
        "budget_exhausted",
        "stopped_below_quality_lines",
        "still_recommend_drawing",
        "no_drawable_box_remains",
    }
)

SESSION_REVIEW_STOP_CONCLUSION_LABELS = {
    "budget_exhausted": "已达到整轮抽盒上限，停止",
    "stopped_below_quality_lines": "质量线未达标，主动停止",
    "still_recommend_drawing": "按当前质量线仍可继续抽",
    "no_drawable_box_remains": "当前端已无可抽盒位",
}

REVIEW_UNRECOVERABLE_LEGACY = "legacy_state_without_event_ledger"

SESSION_REVIEW_TOOL_LABELS = {"hint": "提示卡", "display": "显示卡"}

SESSION_REVIEW_EVENT_LABELS = {
    "tray_switch": "切端",
    "tray_committed": "候选承诺",
    "tray_accepted": "接受",
    "tray_released": "释放",
    "stop_rule_override": "停止线调整",
}


def _replay_baseline_trays(
    session: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Deep-copy tray states rewound to the pre-event session baseline.

    The envelope stores final box states, so every recorded event is undone in
    reverse order: hint events remove their exclusion, display events clear
    the revealed design, and openings reopen the box. Validation guarantees
    each tool/open box has a ledger event, so the rewind is exhaustive.
    """
    trays = copy.deepcopy(session["_tray_states"])
    per_tray_events: Dict[str, List[Mapping[str, Any]]] = {
        tray_id: [] for tray_id in trays
    }
    for event in session["events"]:
        per_tray_events[event["tray_id"]].append(event)
    for tray_id, tray_events in per_tray_events.items():
        boxes = {box["id"]: box for box in trays[tray_id]["boxes"]}
        for event in reversed(tray_events):
            event_type = event["type"]
            if event_type == "hint_used":
                box = boxes[event["box_id"]]
                if event["excluded"] in box["excluded"]:
                    box["excluded"].remove(event["excluded"])
                box["tool_used"] = False
            elif event_type == "display_used":
                box = boxes[event["box_id"]]
                box["known"] = None
                box["tool_used"] = False
            elif event_type == "opened_result":
                box = boxes[event["box_id"]]
                box["status"] = AVAILABLE_STATUS
                box["known"] = None
    return trays


def _replay_baseline_tools(session: Mapping[str, Any]) -> Dict[str, Any]:
    """Remaining inventory plus consumed cards equals the session baseline."""
    tools = copy.deepcopy(session["tools"])
    for event in session["events"]:
        if event["type"] == "hint_used":
            tools["hint_cards"] = int(tools.get("hint_cards", 0)) + 1
        elif event["type"] == "display_used":
            tools["display_cards"] = int(tools.get("display_cards", 0)) + 1
    return tools


def _replay_stop_rules(session: Mapping[str, Any]) -> Dict[str, Any]:
    """Final stop rules rewound through every override's recorded old value."""
    stop_rules = copy.deepcopy(
        session["_tray_states"][session["active_tray_id"]]["preferences"][
            "stop_rules"
        ]
    )
    for event in reversed(session["events"]):
        if event["type"] == "stop_rule_override":
            if event["old_value"] is None:
                stop_rules.pop(event["rule"], None)
            else:
                stop_rules[event["rule"]] = event["old_value"]
    return stop_rules


def _replay_context(
    state: MutableMapping[str, Any],
    stop_rules: Mapping[str, Any],
    tools: Mapping[str, Any],
    draws_used: int,
) -> None:
    state["preferences"]["stop_rules"] = copy.deepcopy(
        dict(stop_rules)
    )
    state["tools"] = copy.deepcopy(dict(tools))
    state["_session_draws_used"] = int(draws_used)


def _derived_acceptance_points(
    session: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Find real action points that first make a candidate fully qualify.

    Explicit acceptance within the same commitment segment wins and suppresses
    a derived duplicate. Otherwise the caller inserts one tray_accepted event
    immediately after the qualifying action, including before a later release.
    """
    if session.get("_legacy_input") or not session.get("events"):
        return []
    trays = _replay_baseline_trays(session)
    tools = _replay_baseline_tools(session)
    stop_rules = _replay_stop_rules(session)
    draws_used = 0
    candidate_tray_id: Optional[str] = None
    accepted_tray_id: Optional[str] = None
    pending: Optional[Dict[str, Any]] = None
    points: List[Dict[str, Any]] = []
    tray_count = len(trays)

    for event in session["events"]:
        event_type = event["type"]
        tray_id = event["tray_id"]
        if event_type == "tray_committed":
            candidate_tray_id = tray_id
            accepted_tray_id = None
            pending = None
            continue
        if event_type == "tray_accepted":
            if pending is not None and pending["tray_id"] == tray_id:
                pending = None
            candidate_tray_id = None
            accepted_tray_id = tray_id
            continue
        if event_type == "tray_released":
            if pending is not None:
                points.append(pending)
                pending = None
            if candidate_tray_id == tray_id:
                candidate_tray_id = None
            if accepted_tray_id == tray_id:
                accepted_tray_id = None
            continue
        if event_type == "stop_rule_override":
            if event["new_value"] is None:
                stop_rules.pop(event["rule"], None)
            else:
                stop_rules[event["rule"]] = event["new_value"]
            continue
        if event_type not in {"hint_used", "display_used", "opened_result"}:
            continue

        if candidate_tray_id is None and accepted_tray_id is None:
            if tray_count != 1:
                continue
            candidate_tray_id = tray_id

        state = trays[tray_id]
        boxes = {box["id"]: box for box in state["boxes"]}
        box = boxes[event["box_id"]]
        _replay_context(state, stop_rules, tools, draws_used)
        if event_type == "hint_used":
            box["excluded"].append(event["excluded"])
            box["tool_used"] = True
            tools["hint_cards"] = max(
                0,
                int(tools.get("hint_cards", 0)) - 1,
            )
        elif event_type == "display_used":
            box["known"] = event["design"]
            box["tool_used"] = True
            tools["display_cards"] = max(
                0,
                int(tools.get("display_cards", 0)) - 1,
            )
        else:
            box["status"] = "opened"
            box["known"] = event["design"]
            draws_used += 1
        _replay_context(state, stop_rules, tools, draws_used)

        if (
            candidate_tray_id == tray_id
            and accepted_tray_id is None
            and pending is None
            and _replay_tray_qualified(state)
        ):
            pending = {
                "trigger_seq": int(event["seq"]),
                "tray_id": tray_id,
            }
            accepted_tray_id = tray_id
            candidate_tray_id = None

    if pending is not None:
        points.append(pending)
    return points


def _review_outcome_class_probabilities(
    box_probs: Mapping[str, float],
    preferences: Mapping[str, Any],
) -> Dict[str, float]:
    return _pure_review_outcome_classes(box_probs, preferences)


def _review_failure_probability(
    box_probs: Mapping[str, float],
    preferences: Mapping[str, Any],
    class_probabilities: Mapping[str, float],
) -> Tuple[float, str]:
    return _pure_review_failure_probability(
        box_probs,
        preferences,
        class_probabilities,
    )


def _review_primary_metric_change(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    try:
        return _pure_review_primary_metric_change(before, after, state)
    except ReviewMetricError as exc:
        raise StateError(str(exc)) from exc


def _review_quality_lines(
    checks: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    return _pure_review_quality_lines(checks)


def _review_strongest_alternative(
    rows: Sequence[Mapping[str, Any]], chosen_index: int
) -> Optional[Dict[str, Any]]:
    return _pure_review_strongest_alternative(rows, chosen_index)


def _review_snapshot_plan(
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    posterior = analyze_posterior(state)
    return plan_tools(state, posterior, depth=1, beam_width=0)


def _replay_tray_qualified(state: Mapping[str, Any]) -> bool:
    """Mirror the #19 derived upgrade check at one replayed time point."""
    try:
        rows = available_box_metrics(state, analyze_posterior(state))
    except StateError:
        return False
    if not rows:
        return False
    best = rows[0]
    profile = _tray_acceptance_profile(state, best)
    decision = evaluate_draw_decision(state, best)
    return (
        bool(profile)
        and bool(decision["should_draw"])
        and all(check["passed"] for check in profile)
    )


def _review_stop_conclusion(
    session: Mapping[str, Any],
) -> Tuple[str, str]:
    state = session["_tray_states"][session["active_tray_id"]]
    drawable = [
        box for box in state["boxes"] if box["status"] in DRAWABLE_STATUSES
    ]
    draws_used = int(session["draws_used"])
    max_draws = state["preferences"]["stop_rules"].get("max_draws")
    if not drawable:
        return (
            "no_drawable_box_remains",
            SESSION_REVIEW_STOP_CONCLUSION_LABELS[
                "no_drawable_box_remains"
            ],
        )
    if max_draws is not None and draws_used >= int(max_draws):
        return (
            "budget_exhausted",
            SESSION_REVIEW_STOP_CONCLUSION_LABELS["budget_exhausted"],
        )
    posterior = analyze_posterior(state)
    best = _terminal_best(state, posterior)
    decision = evaluate_draw_decision(state, best)
    if decision["should_draw"]:
        return (
            "still_recommend_drawing",
            SESSION_REVIEW_STOP_CONCLUSION_LABELS[
                "still_recommend_drawing"
            ],
        )
    return (
        "stopped_below_quality_lines",
        SESSION_REVIEW_STOP_CONCLUSION_LABELS[
            "stopped_below_quality_lines"
        ],
    )


def _replay_session(session: Mapping[str, Any]) -> Dict[str, Any]:
    """Deterministically replay the ledger into per-event review records."""
    trays = _replay_baseline_trays(session)
    tools = _replay_baseline_tools(session)
    stop_rules = _replay_stop_rules(session)
    draws_used = 0
    openings: List[Dict[str, Any]] = []
    tool_cards: List[Dict[str, Any]] = []
    lifecycle: List[Dict[str, Any]] = []
    candidate_lock: Optional[str] = None
    accepted_lock: Optional[str] = None

    for event in session["events"]:
        event_type = event["type"]
        tray_id = event["tray_id"]
        if event_type in {
            "tray_switch",
            "tray_committed",
            "tray_accepted",
            "tray_released",
            "stop_rule_override",
        }:
            entry: Dict[str, Any] = {
                "seq": event["seq"],
                "type": event_type,
                "tray_id": tray_id,
            }
            if "reason" in event:
                entry["reason"] = event["reason"]
            if event_type == "stop_rule_override":
                entry.update(
                    {
                        "rule": event["rule"],
                        "old_value": event["old_value"],
                        "new_value": event["new_value"],
                    }
                )
                if event["new_value"] is None:
                    stop_rules.pop(event["rule"], None)
                else:
                    stop_rules[event["rule"]] = event["new_value"]
            elif event_type == "tray_committed":
                entry["source"] = event.get("source", "explicit_event")
                if "trigger_seq" in event:
                    entry["trigger_seq"] = event["trigger_seq"]
                if "trigger" in event:
                    entry["trigger"] = event["trigger"]
                candidate_lock = tray_id
            elif event_type == "tray_accepted":
                entry["source"] = event.get("source", "explicit_event")
                if "trigger_seq" in event:
                    entry["trigger_seq"] = event["trigger_seq"]
                accepted_lock = tray_id
                candidate_lock = None
            elif event_type == "tray_released":
                if accepted_lock == tray_id:
                    accepted_lock = None
                else:
                    candidate_lock = None
            lifecycle.append(entry)
            continue

        state = trays[tray_id]
        boxes = {box["id"]: box for box in state["boxes"]}
        box = boxes[event["box_id"]]
        _replay_context(state, stop_rules, tools, draws_used)
        if tray_id not in {accepted_lock, candidate_lock}:
            # The ledger validator already guaranteed no other lock can be
            # held here, so this is the single-tray auto commitment.
            candidate_lock = tray_id
            lifecycle.append(
                {
                    "seq": event["seq"],
                    "type": "tray_committed",
                    "tray_id": tray_id,
                    "source": "first_tool_or_open",
                    "trigger": event_type,
                    "reason": "首次真实用卡或开盒自动记录候选承诺",
                }
            )

        if event_type == "opened_result":
            posterior = analyze_posterior(state)
            rows = available_box_metrics(state, posterior)
            box_probs = posterior.marginals[event["box_id"]]
            design = event["design"]
            p_actual = float(box_probs.get(design, 0.0))
            possible = sorted(
                (d for d, p in box_probs.items() if p > 0),
                key=lambda d: (-float(box_probs[d]), d),
            )
            rank = (
                possible.index(design) + 1 if p_actual > 0 else None
            )
            chosen_row = next(
                row for row in rows if row["box_id"] == event["box_id"]
            )
            chosen_index = rows.index(chosen_row)
            preferences = state["preferences"]
            disliked_set = set(preferences["disliked"]) | set(
                preferences["hard_avoid"]
            )
            class_probabilities = _review_outcome_class_probabilities(
                box_probs, preferences
            )
            failure_probability, failure_semantics = (
                _review_failure_probability(
                    box_probs,
                    preferences,
                    class_probabilities,
                )
            )
            checks = _tray_acceptance_profile(state, chosen_row)
            decision = evaluate_draw_decision(state, chosen_row)
            openings.append(
                {
                    "seq": event["seq"],
                    "tray_id": tray_id,
                    "box_id": event["box_id"],
                    "design": design,
                    "recoverable": True,
                    "actual_design_prior_pp": 100.0 * p_actual,
                    "actual_design_rank": rank,
                    "possible_designs_ranked": [
                        {
                            "design": candidate,
                            "probability_pp": 100.0
                            * float(box_probs[candidate]),
                        }
                        for candidate in possible
                    ],
                    "outcome_class_probabilities_pp": {
                        key: 100.0 * value
                        for key, value in class_probabilities.items()
                    },
                    "accepted_failure_pp": 100.0 * failure_probability,
                    "failure_semantics": failure_semantics,
                    "quality_lines_at_draw": _review_quality_lines(checks),
                    "should_draw_at_decision": bool(decision["should_draw"]),
                    "stop_reasons_at_decision": list(
                        decision["reasons"]
                    ),
                    "chosen_was_optimal": chosen_index == 0,
                    "strongest_alternative": _review_strongest_alternative(
                        rows, chosen_index
                    ),
                    "liked_hit": design in set(preferences["liked"]),
                    "hard_avoid_hit": design
                    in set(preferences["hard_avoid"]),
                    "disliked_hit": design in disliked_set,
                }
            )
            box["status"] = "opened"
            box["known"] = design
            draws_used += 1
            _replay_context(state, stop_rules, tools, draws_used)
            continue

        # hint_used / display_used: snapshot before applying the card.
        tool = "hint" if event_type == "hint_used" else "display"
        before_plan = _review_snapshot_plan(state)
        before_best = before_plan["baseline_best_draw"]
        before_draw_decision = evaluate_draw_decision(state, before_best)
        matching_action = next(
            (
                action
                for action in before_plan["action_ranking"]
                if action["tool"] == tool
                and action["box_id"] == event["box_id"]
            ),
            None,
        )
        if tool == "hint":
            box["excluded"].append(event["excluded"])
            tools["hint_cards"] = max(0, int(tools.get("hint_cards", 0)) - 1)
            real_result: Dict[str, Any] = {"excluded": event["excluded"]}
        else:
            box["known"] = event["design"]
            tools["display_cards"] = max(
                0, int(tools.get("display_cards", 0)) - 1
            )
            real_result = {"revealed": event["design"]}
        box["tool_used"] = True

        after_state = trays[tray_id]
        _replay_context(after_state, stop_rules, tools, draws_used)
        after_plan = _review_snapshot_plan(after_state)
        after_best = after_plan["baseline_best_draw"]
        after_draw_decision = evaluate_draw_decision(after_state, after_best)
        primary_metric_change = _review_primary_metric_change(
            before_best,
            after_best,
            after_state,
        )
        before_action_identity = (
            bool(before_draw_decision["should_draw"]),
            before_best["box_id"]
            if before_draw_decision["should_draw"]
            else None,
        )
        after_action_identity = (
            bool(after_draw_decision["should_draw"]),
            after_best["box_id"]
            if after_draw_decision["should_draw"]
            else None,
        )
        tool_cards.append(
            {
                "seq": event["seq"],
                "tray_id": tray_id,
                "box_id": event["box_id"],
                "tool": tool,
                "recoverable": True,
                "real_result": real_result,
                "ex_ante_drawable_branch_pp": (
                    100.0
                    * float(matching_action["expected_draw_probability"])
                    if matching_action is not None
                    else None
                ),
                "ex_ante_branch_available": matching_action is not None,
                "best_box_before": before_best["box_id"],
                "best_box_after": after_best["box_id"],
                "best_p_like_before_pp": 100.0
                * float(before_best["p_like_any"]),
                "best_p_like_after_pp": 100.0
                * float(after_best["p_like_any"]),
                "primary_metric_change": primary_metric_change,
                "primary_metric_change_pp": (
                    primary_metric_change["delta"]
                    if primary_metric_change["unit"] == "percentage_points"
                    else None
                ),
                "ranking_changed": (
                    before_best["box_id"] != after_best["box_id"]
                ),
                "decision_changed": (
                    before_action_identity != after_action_identity
                ),
                "action_before": (
                    "draw" if before_draw_decision["should_draw"] else "stop"
                ),
                "action_after": (
                    "draw" if after_draw_decision["should_draw"] else "stop"
                ),
                "cards_remaining_after": int(
                    tools.get(
                        "hint_cards"
                        if tool == "hint"
                        else "display_cards",
                        0,
                    )
                ),
            }
        )

    return {
        "recoverable": True,
        "unrecoverable_reason": None,
        "unrecoverable_items": [],
        "baseline": {
            "draws_used": 0,
            "tools": _replay_baseline_tools(session),
        },
        "openings": openings,
        "tool_cards": tool_cards,
        "lifecycle": lifecycle,
    }


def _legacy_session_review_replay(
    session: Mapping[str, Any],
) -> Dict[str, Any]:
    """Legacy single-tray states carry no ledger: mark, never fabricate."""
    state = session["_tray_states"][session["active_tray_id"]]
    opened_boxes = sum(
        1 for box in state["boxes"] if box["status"] == "opened"
    )
    tool_boxes = sum(1 for box in state["boxes"] if box["tool_used"])
    items = [
        {
            "field": "openings",
            "reason": REVIEW_UNRECOVERABLE_LEGACY,
        },
        {
            "field": "tool_cards",
            "reason": REVIEW_UNRECOVERABLE_LEGACY,
        },
        {
            "field": "quality_lines_at_draw",
            "reason": REVIEW_UNRECOVERABLE_LEGACY,
        },
        {
            "field": "stop_rule_overrides",
            "reason": REVIEW_UNRECOVERABLE_LEGACY,
        },
    ]
    return {
        "recoverable": False,
        "unrecoverable_reason": REVIEW_UNRECOVERABLE_LEGACY,
        "unrecoverable_items": items,
        "baseline": {
            "draws_used": None,
            "tools": None,
            "opened_boxes_total": opened_boxes,
            "tool_boxes_total": tool_boxes,
        },
        "openings": [],
        "tool_cards": [],
        "lifecycle": [],
    }


def _review_bias_checks(
    openings: Sequence[Mapping[str, Any]],
    overrides: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    def chosen_p_like(opening: Mapping[str, Any]) -> float:
        return round(
            float(
                opening["outcome_class_probabilities_pp"]["liked"]
            ),
            6,
        )

    def flush_chain(chain: List[Mapping[str, Any]]) -> None:
        if len(chain) < 2:
            return
        miss_chains.append(
            {
                "seqs": [opening["seq"] for opening in chain],
                "chosen_p_like_pp": [
                    chosen_p_like(opening) for opening in chain
                ],
            }
        )

    despite_failing = [
        opening["seq"]
        for opening in openings
        if opening["quality_lines_at_draw"]
        and not all(
            check["passed"] for check in opening["quality_lines_at_draw"]
        )
    ]
    miss_chains: List[Dict[str, Any]] = []
    current_chain: List[Mapping[str, Any]] = []
    for opening in openings:
        if opening["liked_hit"]:
            flush_chain(current_chain)
            current_chain = []
        else:
            current_chain.append(opening)
    flush_chain(current_chain)
    gambler_chains = [
        chain
        for chain in miss_chains
        if all(
            later <= earlier + 1e-9
            for earlier, later in zip(
                chain["chosen_p_like_pp"], chain["chosen_p_like_pp"][1:]
            )
        )
    ]
    return {
        "sunk_cost_risk": bool(despite_failing),
        "sunk_cost_evidence": {
            "openings_despite_failing_lines": despite_failing,
            "stop_rule_overrides_total": len(overrides),
        },
        "gambler_fallacy_risk": bool(gambler_chains),
        "gambler_fallacy_evidence": {
            "consecutive_miss_chains": miss_chains,
            "non_increasing_chains": gambler_chains,
        },
        "semantics": (
            "全部检查只使用决策时点的事前信息；连续未中且所抽盒喜欢概率"
            "未上升仍继续抽，标记为可能的赌徒谬误；质量线未过仍开盒，"
            "标记为可能的沉没成本。"
        ),
    }


def build_session_review_report(
    session: Mapping[str, Any],
) -> Dict[str, Any]:
    """Replay the event ledger into a validated whole-session review."""
    if session.get("_briefing_input"):
        raise StateError(
            "a preference briefing has no trays to review; observe a real "
            "tray first"
        )
    legacy = bool(session.get("_legacy_input"))
    replay = (
        _legacy_session_review_replay(session)
        if legacy
        else _replay_session(session)
    )
    openings = replay["openings"]
    overrides = [
        entry
        for entry in replay["lifecycle"]
        if entry["type"] == "stop_rule_override"
    ]

    active_state = session["_tray_states"][session["active_tray_id"]]
    model_summary, model_warnings = _model_reporting_contract(
        active_state,
        hint_planning_active=bool(active_state["tools"].get("hint_cards")),
    )
    conclusion, conclusion_label = _review_stop_conclusion(session)

    non_optimal = [
        opening["seq"] for opening in openings if not opening["chosen_was_optimal"]
    ]
    zero_probability = [
        opening["seq"]
        for opening in openings
        if opening["actual_design_rank"] is None
    ]
    outcome_counts = {
        "liked_hits": sum(1 for opening in openings if opening["liked_hit"]),
        "disliked_hits": sum(
            1 for opening in openings if opening["disliked_hit"]
        ),
        "hard_avoid_hits": sum(
            1 for opening in openings if opening["hard_avoid_hit"]
        ),
        "neutral_results": sum(
            1
            for opening in openings
            if not opening["liked_hit"] and not opening["disliked_hit"]
        ),
        "openings_total": len(openings),
    }

    return {
        "report_type": "session_review",
        # Session-schema states keep the series on every tray state (it is a
        # shared global), while legacy states keep it at the top level.
        "series": session.get("series", active_state.get("series")),
        "session_summary": {
            "tray_ids": sorted(session["_tray_states"]),
            "active_tray_id": session["active_tray_id"],
            "accepted_tray_id": session.get("accepted_tray_id"),
            "candidate_tray_id": session.get("candidate_tray_id"),
            "remaining_tools": copy.deepcopy(session["tools"]),
            "draws_used": int(session["draws_used"]),
            "event_count": len(session["events"]),
        },
        "replay": replay,
        "stop_rule_overrides": [
            copy.deepcopy(entry) for entry in overrides
        ],
        "global_counters": {
            "remaining_tools": copy.deepcopy(session["tools"]),
            "draws_used": int(session["draws_used"]),
            "max_draws": active_state["preferences"]["stop_rules"].get(
                "max_draws"
            ),
            "opened_boxes": int(session["draws_used"]),
            "final_stop_conclusion": conclusion,
            "final_stop_conclusion_label": conclusion_label,
        },
        "decision_quality": {
            "all_openings_optimal": not non_optimal,
            "non_optimal_openings": non_optimal,
            "openings_despite_failing_lines": [
                opening["seq"]
                for opening in openings
                if opening["quality_lines_at_draw"]
                and not all(
                    check["passed"]
                    for check in opening["quality_lines_at_draw"]
                )
            ],
            "semantics": (
                "只与决策时点的最强备选比较，不使用开盒后信息倒推。"
            ),
        },
        "outcome_quality": outcome_counts,
        "model_quality": {
            "zero_probability_openings": zero_probability,
            "model_scope": model_summary,
            "model_warnings": model_warnings,
        },
        "bias_checks": _review_bias_checks(openings, overrides),
    }


def validate_tray_comparison_report(report: Mapping[str, Any]) -> None:
    errors: List[str] = []
    if report.get("report_type") != "tray_comparison":
        errors.append("report_type must be 'tray_comparison'")
    comparison = report.get("comparison")
    if not isinstance(comparison, Mapping):
        raise StateError(
            "tray comparison validation failed: missing comparison section"
        )
    rows = comparison.get("rows")
    excluded = comparison.get("excluded_trays")
    if not isinstance(rows, list) or not isinstance(excluded, list):
        raise StateError(
            "tray comparison validation failed: rows and excluded_trays "
            "must be lists"
        )
    depth = comparison.get("planning_depth")
    if depth not in {1, 2}:
        errors.append("planning_depth must be 1 or 2")

    session_summary = report.get("session_summary")
    if not isinstance(session_summary, Mapping):
        raise StateError(
            "tray comparison validation failed: missing session_summary"
        )
    tray_ids = list(session_summary.get("tray_ids", []))
    row_ids = [row.get("tray_id") for row in rows]
    excluded_ids = [item.get("tray_id") for item in excluded]
    if sorted(row_ids + excluded_ids) != sorted(tray_ids):
        errors.append(
            "every session tray must appear exactly once in either rows or "
            "excluded_trays"
        )
    if len(set(row_ids)) != len(row_ids):
        errors.append("ranked rows contain duplicate tray ids")
    if [row.get("rank") for row in rows] != list(range(1, len(rows) + 1)):
        errors.append("row ranks must be contiguous starting from 1")

    lifecycle = session_summary.get("tray_lifecycle")
    if not isinstance(lifecycle, Mapping):
        errors.append("session tray lifecycle is missing")
    else:
        errors.extend(_lifecycle_block_errors(lifecycle))
        if session_summary.get("lock_status") != lifecycle.get("phase"):
            errors.append("session lock status contradicts the lifecycle")
        if session_summary.get("candidate_tray_id") != lifecycle.get(
            "candidate_tray_id"
        ):
            errors.append("session candidate contradicts the lifecycle")
        phase = lifecycle.get("phase")
        if phase in {"candidate", "accepted"}:
            locked_id = lifecycle.get("accepted_tray_id")
            if locked_id is None:
                locked_id = lifecycle.get("candidate_tray_id")
            if locked_id not in tray_ids:
                errors.append(
                    "the committed tray must be a session tray"
                )
            if locked_id != session_summary.get("active_tray_id"):
                errors.append(
                    "the committed tray must remain the active tray"
                )

    status_sequence = []
    strategy_ranking_keys: List[Any] = []
    for row in rows:
        status = row.get("status")
        if status not in TRAY_COMPARISON_STATUS_RANK:
            errors.append(f"row {row.get('tray_id')!r}: unknown status")
            continue
        status_sequence.append(TRAY_COMPARISON_STATUS_RANK[status])
        try:
            strategy_ranking_keys.append(
                _comparison_key_from_json(row.get("strategy_ranking_key"))
            )
        except (TypeError, ValueError):
            errors.append(
                f"row {row.get('tray_id')!r}: strategy ranking key is malformed"
            )
        if row.get("status_label") != TRAY_COMPARISON_STATUS_LABELS.get(
            status
        ):
            errors.append(
                f"row {row.get('tray_id')!r}: status_label mismatch"
            )
        metrics = row.get("metrics")
        if not isinstance(metrics, Mapping):
            errors.append(f"row {row.get('tray_id')!r}: missing metrics")
            continue
        for key in (
            "p_like_any_pp",
            "p_favorite_any_pp",
            "p_dislike_any_pp",
            "p_hard_avoid_pp",
        ):
            value = metrics.get(key)
            if value is None or not 0.0 <= float(value) <= 100.0:
                errors.append(
                    f"row {row.get('tray_id')!r}: {key} must be within [0, 100]"
                )
        rescue = row.get("post_tool_draw_probability_pp")
        if rescue is None or not 0.0 <= float(rescue) <= 100.0:
            errors.append(
                f"row {row.get('tray_id')!r}: post_tool_draw_probability_pp "
                "must be within [0, 100]"
            )
        action = row.get("first_tool_action")
        if not isinstance(action, Mapping) or action.get("tool") not in {
            "none",
            "hint",
            "display",
        }:
            errors.append(
                f"row {row.get('tray_id')!r}: first_tool_action is malformed"
            )
        elif status == "tool_dependent" and (
            action["tool"] == "none" or float(rescue) <= 0.0
        ):
            errors.append(
                f"row {row.get('tray_id')!r}: tool_dependent trays need a "
                "card action with a positive qualifying-branch probability"
            )
    if status_sequence != sorted(status_sequence):
        errors.append(
            "rows must keep the status order ready < tool_dependent < switch "
            "< needs_acceptance_rules < session_stop"
        )
    if (
        len(strategy_ranking_keys) == len(rows)
        and strategy_ranking_keys != sorted(strategy_ranking_keys)
    ):
        errors.append("rows violate the existing strategy ranking")
    tool_dependent_rows = [
        row for row in rows if row.get("status") == "tool_dependent"
    ]
    rescues = [
        float(row["post_tool_draw_probability_pp"])
        for row in tool_dependent_rows
    ]
    if rescues != sorted(rescues, reverse=True):
        errors.append(
            "tool_dependent rows must not gain rank with a lower "
            "qualifying-branch probability"
        )

    for item in excluded:
        if item.get("reason") not in TRAY_COMPARISON_EXCLUDED_REASONS:
            errors.append(
                f"excluded tray {item.get('tray_id')!r}: unknown reason"
            )

    recommendation = report.get("recommendation")
    if not isinstance(recommendation, Mapping):
        raise StateError(
            "tray comparison validation failed: missing recommendation"
        )
    if rows:
        if recommendation.get("recommended_tray_id") != rows[0]["tray_id"]:
            errors.append(
                "the recommended tray must be the top-ranked operable tray"
            )
    elif recommendation.get("recommended_tray_id") is not None:
        errors.append(
            "a session without operable trays cannot recommend a tray"
        )
    if (
        comparison.get("future_tray_improvement_guaranteed") is not False
        or recommendation.get("future_tray_improvement_guaranteed") is not
        False
    ):
        errors.append("future tray improvement must never be guaranteed")
    semantics = comparison.get("rescue_probability_semantics", "")
    if "不是中奖率" not in str(semantics):
        errors.append(
            "qualifying-branch probabilities must be labeled as not a "
            "win rate"
        )

    if rows:
        top = rows[0]
        commitment = recommendation.get("commitment_after_action")
        needs_commitment = (
            top.get("status") == "tool_dependent"
            and recommendation.get("action") == "execute_first_action"
            and session_summary.get("accepted_tray_id") != top.get("tray_id")
            and session_summary.get("candidate_tray_id") != top.get("tray_id")
        )
        if needs_commitment and not isinstance(commitment, Mapping):
            errors.append(
                "a tool-dependent recommendation must state the candidate "
                "commitment step"
            )
        if isinstance(commitment, Mapping) and (
            not needs_commitment
            or commitment.get("phase") != "candidate"
            or commitment.get("record_event") != "tray_committed"
        ):
            errors.append(
                "commitment_after_action is malformed or premature"
            )

    if depth == 2:
        depth_two = comparison.get("depth_two")
        if not isinstance(depth_two, Mapping):
            errors.append("depth-two comparison requires its horizon section")
        else:
            head_ids = depth_two.get("head_candidate_tray_ids", [])
            row_id_set = set(row_ids)
            if not head_ids or not set(head_ids) <= row_id_set:
                errors.append(
                    "depth-two head candidates must be ranked operable trays"
                )
            if depth_two.get("cards_available", 0) < 2:
                errors.append(
                    "depth-two comparison requires at least two cards"
                )
            for row in rows:
                if row["tray_id"] in set(head_ids):
                    detail = row.get("depth_two_detail")
                    if not isinstance(detail, Mapping) or not isinstance(
                        detail.get("first_action_changed_vs_depth_1"), bool
                    ):
                        errors.append(
                            f"row {row['tray_id']!r}: missing depth-two detail"
                        )
                elif "depth_two_note" not in row:
                    errors.append(
                        f"row {row['tray_id']!r}: missing depth-two note"
                    )

    if errors:
        raise StateError(
            "tray comparison validation failed: " + "; ".join(errors)
        )


REPORT_RULE_LABELS = {
    "min_like_any_pp": "喜欢款至少",
    "min_favorite_any_pp": "最爱款至少",
    "max_dislike_any_pp": "不喜欢款不超过",
    "max_hard_avoid_pp": "硬雷不超过",
    "min_expected_score": "期望评分至少",
    "min_resale_ev": "预期二手价值至少",
    "max_draws": "最多抽盒数",
    "hard_avoid_max_pp": "策略硬雷上限",
}


def validate_preference_calibration_report(
    report: Mapping[str, Any],
) -> None:
    errors: List[str] = []
    if report.get("report_type") != "preference_calibration":
        errors.append("report type")
    if report.get("status") not in {
        "needs_confirmation",
        "target_unreachable",
    }:
        errors.append("status")
    if report.get("stop_rules_mutated") is not False:
        errors.append("stop rules were mutated")
    if report.get("confirmation_required") is not True:
        errors.append("confirmation gate")
    coverage = report.get("score_coverage")
    if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
        errors.append("score coverage")
    primary_metric = report.get("primary_metric")
    primary_rule = (
        primary_metric.get("rule")
        if isinstance(primary_metric, Mapping)
        else None
    )
    if not primary_rule:
        errors.append("primary calibration rule")
    if (
        isinstance(primary_metric, Mapping)
        and primary_metric.get("metric") == "resale_ev"
    ):
        market_coverage = report.get("market_value_coverage")
        if (
            not isinstance(market_coverage, Mapping)
            or market_coverage.get("complete") is not True
            or market_coverage.get("currency") != "CNY"
        ):
            errors.append("market value coverage")
    rows = report.get("all_boxes")
    if not isinstance(rows, list) or not rows:
        errors.append("box metrics")
        rows = []
    rows_by_id = {
        str(row.get("box_id")): row
        for row in rows
        if isinstance(row, Mapping)
    }
    choices = report.get("choices")
    if not isinstance(choices, list):
        errors.append("calibration choices")
        choices = []
    if report.get("status") == "needs_confirmation" and not choices:
        errors.append("calibration choices")
    if report.get("status") == "target_unreachable" and choices:
        errors.append("unreachable target choices")
    forbidden_keys = {
        "draw_decision",
        "next_tool_plan",
        "session_recommendation",
        "tray_screening",
    }
    if forbidden_keys & set(report):
        errors.append("formal recommendation leaked")

    actual_metric_by_rule = {
        "min_like_any_pp": "p_like_any_pp",
        "min_favorite_any_pp": "p_favorite_any_pp",
        "max_dislike_any_pp": "p_dislike_any_pp",
        "max_hard_avoid_pp": "p_hard_avoid_pp",
        "min_expected_score": "expected_score",
        "min_resale_ev": "resale_ev",
    }
    seen_choices: set[str] = set()
    seen_boxes: set[str] = set()
    for choice in choices:
        if not isinstance(choice, Mapping):
            errors.append("calibration choice shape")
            continue
        choice_id = str(choice.get("choice", ""))
        box_id = str(choice.get("box_id", ""))
        if (
            not choice_id
            or choice_id in seen_choices
            or box_id in seen_boxes
            or box_id not in rows_by_id
        ):
            errors.append("calibration choice identity")
        seen_choices.add(choice_id)
        seen_boxes.add(box_id)
        actual = choice.get("actual")
        suggested = choice.get("suggested_stop_rules")
        if not isinstance(actual, Mapping) or not isinstance(
            suggested, Mapping
        ):
            errors.append("candidate boundaries")
            continue
        source_row = rows_by_id.get(box_id)
        if source_row is not None:
            if source_row.get("pareto_frontier") is not True:
                errors.append("candidate outside frontier")
            for metric in (
                "p_favorite_any_pp",
                "p_like_any_pp",
                "p_dislike_any_pp",
                "p_hard_avoid_pp",
                "expected_score",
            ):
                if metric not in actual or not _numbers_match(
                    actual[metric],
                    source_row.get(metric),
                ):
                    errors.append("candidate actual metrics")
            if source_row.get("resale_ev") is not None and (
                "resale_ev" not in actual
                or not _numbers_match(
                    actual["resale_ev"],
                    source_row.get("resale_ev"),
                )
            ):
                errors.append("candidate resale metrics")
        if not set(suggested).issubset(QUALITY_STOP_RULE_KEYS):
            errors.append("candidate rule scope")
            continue
        if primary_rule not in suggested:
            errors.append("candidate primary boundary")
        if primary_rule == "min_resale_ev" and "min_expected_score" not in suggested:
            errors.append("resale preference boundary")
        for rule, threshold in suggested.items():
            metric = actual_metric_by_rule[rule]
            try:
                actual_value = float(actual[metric])
                boundary = float(threshold)
            except (KeyError, TypeError, ValueError):
                errors.append("candidate boundary value")
                continue
            passed = (
                actual_value + 1e-12 >= boundary
                if rule.startswith("min_")
                else actual_value <= boundary + 1e-12
            )
            if not passed:
                errors.append("candidate boundary excludes source box")

    if errors:
        raise StateError(
            "preference calibration validation failed: "
            + "; ".join(sorted(set(errors)))
        )


def _preference_tier_conflicts(
    state: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    try:
        return _pure_preference_tier_conflicts(state)
    except PreferencePolicyError as exc:
        raise StateError(str(exc)) from exc


def _briefing_reference_value(rule: str, baseline_pp: float) -> float:
    return _pure_briefing_reference_value(rule, baseline_pp)


def _briefing_reference_rules(
    state: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    return _pure_briefing_reference_rules(state, baseline)


def build_preference_briefing_report(
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build the zero-tray preference briefing without observing any tray."""
    coverage = state["_score_coverage"]
    if not coverage["complete"]:
        if coverage.get("score_default_supplied") and not coverage[
            "score_default_confirmed"
        ]:
            raise StateError(
                "preference briefing cannot fill omitted designs until "
                "preferences.score_default_confirmed=true"
            )
        raise StateError(
            "preference briefing requires complete scores for every regular "
            f"design; missing {coverage['missing_scores']}"
        )
    posterior = analyze_posterior(state)
    baseline_metrics = metrics_for_box(
        state, posterior, state["boxes"][0]["id"]
    )
    baseline = {
        "regular_count": len(state["_union_designs"]),
        "distribution": "uniform_over_complete_no_duplicate_case",
        "p_favorite_any_pp": 100.0 * baseline_metrics["p_favorite_any"],
        "p_like_any_pp": 100.0 * baseline_metrics["p_like_any"],
        "p_dislike_any_pp": 100.0 * baseline_metrics["p_dislike_any"],
        "p_hard_avoid_pp": 100.0 * baseline_metrics["p_hard_avoid"],
        "expected_score": baseline_metrics["expected_score"],
        "resale_ev": baseline_metrics["resale_ev"],
    }

    preference_conflicts = _preference_tier_conflicts(state)
    reference_rules = (
        _briefing_reference_rules(state, baseline)
        if (
            state["preferences"]["objective_mode"] == "target_only"
            and not preference_conflicts
        )
        else []
    )
    if preference_conflicts:
        status = "preference_conflict"
    elif reference_rules:
        status = "needs_confirmation"
    else:
        status = "calibration_required"
    return {
        "report_type": "preference_briefing",
        "series": state.get("series"),
        "tray_id": None,
        "status": status,
        "strategy_name": state["preferences"]["strategy"],
        "strategy_rule": STRATEGY_RULES[
            state["preferences"]["objective_mode"]
        ],
        "blind_baseline": baseline,
        "score_coverage": copy.deepcopy(state["_score_coverage"]),
        "market_value_coverage": copy.deepcopy(
            state["_market_value_coverage"]
        ),
        "scores": copy.deepcopy(state["preferences"]["scores"]),
        "score_tiers": copy.deepcopy(state["preferences"]["score_tiers"]),
        "preference_conflicts": preference_conflicts,
        "reference_lines": {
            "applies_to_strategies": ["随便中个喜欢"],
            "applicable": bool(reference_rules),
            "rules": reference_rules,
            "rounding": (
                "五个百分点一档；min 线严格上移、max 线严格下移；"
                "弱基线使用 40% / 35% / 20% 平衡锚点"
            ),
            "redirect_other_strategies": (
                "其他策略无端不生成参考线；进入具体端后运行 "
                "--calibrate-preferences，用当前端真实可达取舍确认边界。"
            ),
        },
        "existing_stop_rules": copy.deepcopy(
            state["preferences"]["stop_rules"]
        ),
        "stop_rules_mutated": False,
        "confirmation_required": True,
        "model_summary": {
            "scope": "regular_only",
            "hidden_designs_included": False,
            "probability_kind": "prior_baseline",
            "conditional_on": [
                "complete_no_duplicate_case",
                "regular_only_scope",
            ],
            "probability_statement": (
                "以下数值是常规款整盒无重复、尚未观察任何盒位时的盲抽先验"
                "基线，不是当前端校准结果。"
            ),
        },
        "model_warnings": [
            {
                "code": "regular_only_scope",
                "severity": "warning",
                "applies_to": "all_probabilities",
                "message": (
                    "当前仅建模常规款；隐藏款及替换规则未计入，所有百分比均为"
                    "常规款范围下的条件概率。"
                ),
            },
            {
                "code": "current_tray_not_observed",
                "severity": "warning",
                "applies_to": "reference_lines",
                "message": (
                    "尚未观察任何一端盒位；参考线只与盲抽基线比较，不代表"
                    "任何已观察端的真实水平。"
                ),
            },
        ],
    }


def validate_preference_briefing_report(
    report: Mapping[str, Any],
) -> None:
    errors: List[str] = []
    if report.get("report_type") != "preference_briefing":
        errors.append("report type")
    if report.get("status") not in {
        "needs_confirmation",
        "calibration_required",
        "preference_conflict",
    }:
        errors.append("status")
    if report.get("stop_rules_mutated") is not False:
        errors.append("stop rules were mutated")
    if report.get("confirmation_required") is not True:
        errors.append("confirmation gate")
    coverage = report.get("score_coverage")
    if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
        errors.append("score coverage")
    elif coverage.get("score_default_used") and not coverage.get(
        "score_default_confirmed"
    ):
        errors.append("score default confirmation")
    baseline = report.get("blind_baseline")
    if not isinstance(baseline, Mapping):
        errors.append("blind baseline")
        baseline = {}
    for key in (
        "p_favorite_any_pp",
        "p_like_any_pp",
        "p_dislike_any_pp",
        "p_hard_avoid_pp",
    ):
        value = baseline.get(key)
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 100.0:
            errors.append("baseline probability")
    regular_count = baseline.get("regular_count")
    if not isinstance(regular_count, int) or isinstance(regular_count, bool) or regular_count < 1:
        errors.append("baseline regular count")
    warnings = report.get("model_warnings")
    if not isinstance(warnings, list) or not any(
        isinstance(item, Mapping)
        and item.get("code") == "current_tray_not_observed"
        for item in warnings
    ):
        errors.append("current tray not observed warning")
    reference = report.get("reference_lines")
    if not isinstance(reference, Mapping):
        errors.append("reference lines")
        reference = {}
    rules = reference.get("rules")
    if not isinstance(rules, list):
        errors.append("reference line list")
        rules = []
    if report.get("status") == "needs_confirmation" and not rules:
        errors.append("reference line list")
    if report.get("status") == "calibration_required" and rules:
        errors.append("unexpected reference lines")
    if report.get("status") == "preference_conflict" and rules:
        errors.append("conflicted preferences cannot produce reference lines")
    if rules and report.get("strategy_name") != "随便中个喜欢":
        errors.append("reference line strategy scope")
    seen_rules: set[str] = set()
    for rule_entry in rules:
        if not isinstance(rule_entry, Mapping):
            errors.append("reference line shape")
            continue
        rule = str(rule_entry.get("rule", ""))
        if rule not in BRIEFING_BASELINE_METRIC_BY_RULE or rule in seen_rules:
            errors.append("reference line rule")
        seen_rules.add(rule)
        try:
            baseline_pp = float(rule_entry.get("baseline_pp"))
            suggested = float(rule_entry.get("suggested_value"))
        except (TypeError, ValueError):
            errors.append("reference line values")
            continue
        metric = BRIEFING_BASELINE_METRIC_BY_RULE.get(rule)
        if metric is None or not _numbers_match(
            baseline_pp, baseline.get(metric)
        ):
            errors.append("reference line baseline")
            continue
        if not 0.0 <= suggested <= 100.0:
            errors.append("reference line value range")
        expected = _briefing_reference_value(rule, baseline_pp)
        if not _numbers_match(suggested, expected):
            errors.append("reference line rounding")
        if rule_entry.get("basis") != "blind_baseline_strictly_improved":
            errors.append("reference line basis")
        strictly_improved = (
            suggested > baseline_pp + 1e-12
            if rule.startswith("min_")
            else suggested < baseline_pp - 1e-12
        )
        if not strictly_improved:
            errors.append("reference line must strictly improve baseline")
        if not _numbers_match(
            rule_entry.get("delta_vs_baseline_pp"),
            suggested - baseline_pp,
        ):
            errors.append("reference line delta")
    conflicts = report.get("preference_conflicts")
    if not isinstance(conflicts, list) or any(
        not isinstance(item, Mapping)
        or not item.get("design")
        or (
            item.get("explicit_field") not in {"liked", "disliked", "hard_avoid"}
            and not str(item.get("explicit_field", "")).startswith(
                "explicit_score_tiers."
            )
        )
        or item.get("resolution") != "confirmation_required"
        or item.get("current_effective_source") not in {
            "explicit_field",
            "scores",
        }
        for item in conflicts
    ):
        errors.append("preference conflicts")
    if report.get("status") == "preference_conflict" and not conflicts:
        errors.append("preference conflict status without conflicts")
    if conflicts and report.get("status") != "preference_conflict":
        errors.append("unresolved preference conflicts are not blocking")
    forbidden_keys = {
        "draw_decision",
        "next_tool_plan",
        "session_recommendation",
        "tray_screening",
        "ranking",
        "top_3",
    }
    if forbidden_keys & set(report):
        errors.append("formal recommendation leaked")

    if errors:
        raise StateError(
            "preference briefing validation failed: "
            + "; ".join(sorted(set(errors)))
        )


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


def _lifecycle_block_errors(
    lifecycle: Mapping[str, Any],
) -> List[str]:
    """Structural checks for one commitment-ladder block."""
    errors: List[str] = []
    phase = lifecycle.get("phase")
    if phase not in TRAY_LIFECYCLE_PHASES:
        return ["tray lifecycle phase is invalid"]
    if lifecycle.get("phase_label") != TRAY_LIFECYCLE_PHASE_LABELS[phase]:
        errors.append("tray lifecycle phase label is inconsistent")
    accepted_id = lifecycle.get("accepted_tray_id")
    candidate_id = lifecycle.get("candidate_tray_id")
    upgraded = bool(lifecycle.get("upgraded_from_candidate"))
    source = lifecycle.get("commitment_source")
    if phase == "open":
        if accepted_id is not None or candidate_id is not None:
            errors.append("an open lifecycle cannot name a committed tray")
        if source is not None:
            errors.append("an open lifecycle cannot name a commitment source")
    elif phase == "candidate":
        if candidate_id is None or accepted_id is not None:
            errors.append(
                "a candidate lifecycle must name exactly the candidate tray"
            )
        if upgraded:
            errors.append("a candidate lifecycle cannot be marked upgraded")
        if source not in TRAY_COMMITMENT_SOURCES:
            errors.append("candidate commitment source is invalid")
    else:
        if upgraded:
            if (candidate_id is None) == (accepted_id is None):
                errors.append(
                    "an upgraded acceptance must name exactly one locked tray"
                )
            if (
                lifecycle.get("upgrade_basis")
                != "real_clues_pass_all_quality_lines"
            ):
                errors.append("an upgraded acceptance must state its basis")
            if lifecycle.get("accepted_via") != "quality_lines_upgrade":
                errors.append("an upgraded acceptance must record its path")
            if source not in TRAY_COMMITMENT_SOURCES:
                errors.append("upgraded acceptance lost its commitment source")
        else:
            if accepted_id is None:
                errors.append("an explicit acceptance must name the tray")
            if lifecycle.get("accepted_via") != "explicit_event":
                errors.append("an explicit acceptance must record its event")
            if lifecycle.get("upgrade_basis") is not None:
                errors.append("an explicit acceptance cannot claim an upgrade")
            if source is not None:
                errors.append("an explicit acceptance has no candidate source")
    if lifecycle.get("release_required_before_switch") != (phase != "open"):
        errors.append("lifecycle release requirement contradicts its phase")
    return errors


def validate_session_review_report(report: Mapping[str, Any]) -> None:
    errors: List[str] = []
    if not isinstance(report, Mapping):
        raise StateError("session review report must be an object")
    if report.get("report_type") != "session_review":
        errors.append("report_type must be 'session_review'")

    summary = report.get("session_summary")
    if not isinstance(summary, Mapping):
        errors.append("session summary is missing")
    else:
        for key in (
            "tray_ids",
            "active_tray_id",
            "accepted_tray_id",
            "candidate_tray_id",
            "remaining_tools",
            "draws_used",
            "event_count",
        ):
            if key not in summary:
                errors.append(f"session summary lacks {key}")

    replay = report.get("replay")
    if not isinstance(replay, Mapping):
        errors.append("replay section is missing")
        replay = {}
    recoverable = replay.get("recoverable")
    openings = replay.get("openings", [])
    tool_cards = replay.get("tool_cards", [])
    lifecycle = replay.get("lifecycle", [])
    if recoverable is not True and recoverable is not False:
        errors.append("replay recoverability must be a boolean")
    if recoverable:
        baseline = replay.get("baseline")
        if not isinstance(baseline, Mapping) or not isinstance(
            baseline.get("tools"), Mapping
        ):
            errors.append("a recoverable replay needs its baseline inventory")
    else:
        if not replay.get("unrecoverable_reason"):
            errors.append("an unrecoverable replay must state its reason")
        if not replay.get("unrecoverable_items"):
            errors.append("an unrecoverable replay must list its blind spots")
        if openings or tool_cards:
            errors.append(
                "an unrecoverable replay must not fabricate per-event numbers"
            )

    for opening in openings:
        seq = opening.get("seq")
        label = f"opening {seq}"
        probabilities = opening.get("outcome_class_probabilities_pp", {})
        for key in ("liked", "neutral", "disliked", "hard_avoid"):
            value = probabilities.get(key)
            if value is None or not 0.0 <= float(value) <= 100.0:
                errors.append(f"{label}: outcome class {key} is out of range")
        total = sum(
            float(probabilities[key])
            for key in ("liked", "neutral", "disliked")
            if probabilities.get(key) is not None
        )
        if abs(total - 100.0) > 1e-6:
            errors.append(f"{label}: outcome classes must partition to 100%")
        prior = opening.get("actual_design_prior_pp")
        if prior is None or not 0.0 <= float(prior) <= 100.0:
            errors.append(f"{label}: actual design prior is out of range")
        ranked = opening.get("possible_designs_ranked", [])
        rank = opening.get("actual_design_rank")
        if rank is None:
            errors.append(f"{label}: actual design rank is missing")
        elif not 1 <= int(rank) <= len(ranked):
            errors.append(f"{label}: actual design rank exceeds support")
        else:
            ranked_probabilities = [
                item["probability_pp"] for item in ranked
            ]
            if ranked_probabilities != sorted(
                ranked_probabilities, reverse=True
            ):
                errors.append(f"{label}: possible designs are not ranked")
            if not math.isclose(
                float(ranked[int(rank) - 1]["probability_pp"]),
                float(prior),
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                errors.append(f"{label}: rank and prior disagree")
            if ranked[int(rank) - 1]["design"] != opening.get("design"):
                errors.append(f"{label}: rank points at another design")
        failure = opening.get("accepted_failure_pp")
        if failure is None or not 0.0 <= float(failure) <= 100.0:
            errors.append(f"{label}: accepted failure probability is invalid")
        if opening.get("strongest_alternative") is None and not opening.get(
            "chosen_was_optimal"
        ):
            errors.append(f"{label}: a non-optimal choice needs its alternative")
        for check in opening.get("quality_lines_at_draw", []):
            if check.get("rule") not in STOP_RULE_KEYS:
                errors.append(f"{label}: unknown quality line {check.get('rule')}")
            if check.get("passed") not in (True, False):
                errors.append(f"{label}: quality line pass state is missing")

    for card in tool_cards:
        seq = card.get("seq")
        label = f"tool card {seq}"
        if card.get("tool") not in {"hint", "display"}:
            errors.append(f"{label}: unknown tool")
        branch = card.get("ex_ante_drawable_branch_pp")
        if card.get("ex_ante_branch_available"):
            if branch is None or not 0.0 <= float(branch) <= 100.0:
                errors.append(f"{label}: branch probability is out of range")
        elif branch is not None:
            errors.append(f"{label}: unavailable branch must stay null")
        for key in ("best_p_like_before_pp", "best_p_like_after_pp"):
            value = card.get(key)
            if value is None or not 0.0 <= float(value) <= 100.0:
                errors.append(f"{label}: {key} is out of range")
        primary = card.get("primary_metric_change")
        if not isinstance(primary, Mapping):
            errors.append(f"{label}: primary metric change is missing")
        else:
            if primary.get("direction") not in {
                "higher_is_better",
                "lower_is_better",
            }:
                errors.append(f"{label}: primary metric direction is invalid")
            try:
                before = float(primary["before"])
                after = float(primary["after"])
                delta = float(primary["delta"])
                improvement = float(primary["improvement"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{label}: primary metric values are invalid")
            else:
                if not _numbers_match(delta, after - before):
                    errors.append(f"{label}: primary metric delta is inconsistent")
                expected_improvement = (
                    delta
                    if primary.get("direction") == "higher_is_better"
                    else -delta
                )
                if not _numbers_match(improvement, expected_improvement):
                    errors.append(
                        f"{label}: primary metric improvement is inconsistent"
                    )
                legacy_pp = card.get("primary_metric_change_pp")
                if primary.get("unit") == "percentage_points":
                    if not _numbers_match(legacy_pp, delta):
                        errors.append(
                            f"{label}: probability-point compatibility value is wrong"
                        )
                elif legacy_pp is not None:
                    errors.append(
                        f"{label}: non-probability primary metrics cannot claim pp"
                    )
        if card.get("decision_changed") not in (True, False):
            errors.append(f"{label}: decision change state is missing")
        if card.get("ranking_changed") not in (True, False):
            errors.append(f"{label}: ranking change state is missing")
        if card.get("action_before") not in {"draw", "stop"} or card.get(
            "action_after"
        ) not in {"draw", "stop"}:
            errors.append(f"{label}: action decision is missing")
        if card.get("cards_remaining_after") is None:
            errors.append(f"{label}: remaining card count is missing")

    lifecycle_seqs = [entry.get("seq") for entry in lifecycle]
    if lifecycle_seqs != sorted(lifecycle_seqs):
        errors.append("lifecycle entries must keep ascending order")
    if len({(entry.get("seq"), entry.get("type")) for entry in lifecycle}) != len(
        lifecycle
    ):
        errors.append(
            "lifecycle entries must be unique per event and type; a derived "
            "commitment and its upgrade may share one triggering seq"
        )
    overrides = report.get("stop_rule_overrides", [])
    replay_overrides = [
        entry
        for entry in lifecycle
        if entry.get("type") == "stop_rule_override"
    ]
    if overrides != replay_overrides:
        errors.append("stop-rule overrides diverge from the replay lifecycle")
    for entry in overrides:
        if entry.get("rule") not in STOP_RULE_KEYS:
            errors.append("stop-rule override names an unknown rule")
        if entry.get("old_value") == entry.get("new_value"):
            errors.append("stop-rule override must change the value")
        if not entry.get("reason"):
            errors.append("stop-rule override lost its reason")

    counters = report.get("global_counters")
    if not isinstance(counters, Mapping):
        errors.append("global counters are missing")
    else:
        if counters.get("final_stop_conclusion") not in (
            SESSION_REVIEW_STOP_CONCLUSIONS
        ):
            errors.append("final stop conclusion is not a known verdict")
        if not counters.get("final_stop_conclusion_label"):
            errors.append("final stop conclusion lost its label")
        if recoverable:
            if counters.get("draws_used") != len(openings):
                errors.append("draw count disagrees with the replay openings")
        draws_used = counters.get("draws_used")
        max_draws = counters.get("max_draws")
        if draws_used is None or int(draws_used) < 0:
            errors.append("draw count is missing")
        if max_draws is not None and int(max_draws) < int(draws_used):
            errors.append("draw count exceeds the configured cap")

    decision = report.get("decision_quality")
    if not isinstance(decision, Mapping):
        errors.append("decision quality section is missing")
    else:
        non_optimal = decision.get("non_optimal_openings", [])
        expected_non_optimal = [
            opening["seq"]
            for opening in openings
            if not opening.get("chosen_was_optimal")
        ]
        if non_optimal != expected_non_optimal:
            errors.append("decision quality diverges from the openings")
        if decision.get("all_openings_optimal") != (not expected_non_optimal):
            errors.append("decision quality optimality flag is inconsistent")

    outcome = report.get("outcome_quality")
    if not isinstance(outcome, Mapping):
        errors.append("outcome quality section is missing")
    else:
        if outcome.get("openings_total") != len(openings):
            errors.append("outcome totals disagree with the openings")
        counted = (
            outcome.get("liked_hits", 0)
            + outcome.get("neutral_results", 0)
            + outcome.get("disliked_hits", 0)
        )
        if counted != len(openings):
            errors.append("outcome classes must partition the openings")

    model = report.get("model_quality")
    if not isinstance(model, Mapping):
        errors.append("model quality section is missing")
    else:
        scope = model.get("model_scope")
        if not isinstance(scope, Mapping) or not scope.get(
            "probability_statement"
        ):
            errors.append("model scope statement is missing")

    bias = report.get("bias_checks")
    if not isinstance(bias, Mapping):
        errors.append("bias checks are missing")
    else:
        for key in ("sunk_cost_risk", "gambler_fallacy_risk"):
            if bias.get(key) not in (True, False):
                errors.append(f"bias check {key} must be a boolean")

    if errors:
        raise StateError(
            "session review validation failed: " + "; ".join(errors)
        )


def validate_user_report(
    report: Mapping[str, Any],
    *,
    screen_tray: bool = False,
) -> None:
    """Fail closed when a reader-facing report is incomplete or inconsistent."""
    active, _ = _active_user_report(report)
    errors: List[str] = []
    lifecycle = _report_lifecycle(report)
    if lifecycle is not None:
        errors.extend(_lifecycle_block_errors(lifecycle))

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
        "min_resale_ev": best.get("resale_ev"),
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
        "min_resale_ev": ">=",
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

    summary = report.get("session_summary")
    if isinstance(summary, Mapping):
        if lifecycle is None:
            errors.append("session tray lifecycle is missing")
        else:
            phase = lifecycle.get("phase")
            if summary.get("lock_status") != phase:
                errors.append("session lock status contradicts the lifecycle")
            if summary.get("candidate_tray_id") != lifecycle.get(
                "candidate_tray_id"
            ):
                errors.append("session candidate contradicts the lifecycle")
            if phase in {"candidate", "accepted"}:
                locked_id = lifecycle.get("accepted_tray_id")
                if locked_id is None:
                    locked_id = lifecycle.get("candidate_tray_id")
                if locked_id != summary.get("active_tray_id"):
                    errors.append(
                        "the committed tray must remain the active tray"
                    )
            expected_lock_phases = {
                "open": {"uncommitted"},
                "candidate": {"candidate"},
                "accepted": {"accepted"},
            }.get(phase, set())
            if phase == "accepted" and lifecycle.get("upgraded_from_candidate"):
                # The ledger still physically holds the candidate commitment;
                # only the derived quality-line upgrade reads as accepted.
                expected_lock_phases = {"accepted", "candidate"}
            active_lock = active.get("tray_lock")
            if not isinstance(active_lock, Mapping):
                errors.append("active tray lock is missing")
            elif active_lock.get("phase") not in expected_lock_phases:
                errors.append("active tray lock contradicts the lifecycle")
        recommendation = report.get("session_recommendation")
        if isinstance(recommendation, Mapping) and lifecycle is not None:
            phase = lifecycle.get("phase")
            if recommendation.get("release_required_before_switch") != (
                phase != "open"
            ):
                errors.append(
                    "session recommendation release flag contradicts the phase"
                )
            if phase == "open":
                expected_tray = summary.get("active_tray_id")
                expected_actions = {"follow_active_tray_report"}
            elif phase == "candidate":
                expected_tray = lifecycle.get("candidate_tray_id")
                expected_actions = {
                    "continue_with_candidate_tray",
                    "continue_with_candidate_tray_tool_plan",
                    "stop_or_release_candidate_tray",
                }
            else:
                expected_tray = lifecycle.get("accepted_tray_id")
                if expected_tray is None:
                    expected_tray = lifecycle.get("candidate_tray_id")
                expected_actions = {
                    "continue_with_accepted_tray",
                    "continue_with_accepted_tray_tool_plan",
                    "stop_or_release_accepted_tray",
                }
            if recommendation.get("action") not in expected_actions:
                errors.append(
                    "session recommendation action contradicts the phase"
                )
            if recommendation.get("tray_id") != expected_tray:
                errors.append(
                    "session recommendation tray contradicts the lifecycle"
                )

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
    if rule == "min_resale_ev":
        return f"¥{float(value):.2f}"
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
        preference_summary = report["preference_summary"]
        liked = preference_summary["liked"]
        score_groups = _score_derived_target_groups(
            {
                "liked": liked,
                "scores": preference_summary["scores"],
                "preference_sources": preference_summary["sources"],
            }
        )
        if (
            score_groups
            and float(preference_summary["scores"][score_groups[0][0]]) == 10.0
        ):
            label = "最爱款合计"
            first_value = 100.0 * float(first["p_favorite_any"])
            second_value = 100.0 * float(second["p_favorite_any"])
        elif score_groups:
            label = "最高评分款合计"
            first_value = 100.0 * sum(
                float(first["liked_probabilities"].get(design, 0.0))
                for design in score_groups[0]
            )
            second_value = 100.0 * sum(
                float(second["liked_probabilities"].get(design, 0.0))
                for design in score_groups[0]
            )
        else:
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
    failed_second_checks = [
        check for check in second.get("stop_rule_checks", []) if not check["passed"]
    ]
    if (
        all(check["passed"] for check in report["stop_rule_checks"])
        and failed_second_checks
    ):
        failed_labels = "、".join(
            REPORT_RULE_LABELS.get(check["rule"], check["rule"])
            for check in failed_second_checks
        )
        comparison = (
            f"首选通过全部停止线；{second['box_id']} 号未通过「{failed_labels}」，"
            "因此先选达线盒，再比较目标概率"
        )
    elif math.isclose(first_value, second_value, abs_tol=1e-12):
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
        max_draws = report["stop_rules"].get("max_draws")
        opened_count = report["draw_decision"]["opened_count"]
        if max_draws is not None and opened_count + 1 >= max_draws:
            return (
                f"建议抽 {box_id} 号。",
                f"抽 {box_id} 号；开盒后达到本轮最多 {max_draws} 盒，记录结果并结束，不追抽。",
            )
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
        elif unit == "cny":
            actual = f"¥{float(check['actual']):.2f}"
            threshold = (
                f"{check['operator']} ¥{float(check['threshold']):.2f}"
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


def _report_lifecycle(
    report: Mapping[str, Any]
) -> Optional[Mapping[str, Any]]:
    """Read the commitment ladder from a session or legacy solo report."""
    summary = report.get("session_summary")
    if isinstance(summary, Mapping):
        lifecycle = summary.get("tray_lifecycle")
    else:
        lifecycle = report.get("tray_lifecycle")
    return lifecycle if isinstance(lifecycle, Mapping) else None


def _lifecycle_sentence(lifecycle: Optional[Mapping[str, Any]]) -> str:
    """One user-state sentence for the commitment ladder.

    A missing block (legacy uncommitted solo reports) stays silent so the
    legacy output is byte-compatible; every session report states its phase.
    """
    if lifecycle is None:
        return ""
    if lifecycle.get("phase") == "open":
        return "当前端状态：未承诺（可自由换端；用卡或开盒会自动记录候选承诺）。"
    if lifecycle.get("phase") == "accepted":
        if lifecycle.get("upgraded_from_candidate"):
            return (
                "当前端状态：已接受（真实线索已通过全部质量线，由候选承诺自动"
                "升级）；保持锁定，换端前需先说明理由并记录释放事件。"
            )
        return (
            "当前端状态：已接受（锁定）；换端前需先说明理由并记录释放事件。"
        )
    source = lifecycle.get("commitment_source")
    basis = (
        "经多端横比或用户选定记录"
        if source == "explicit_event"
        else "首次真实用卡或开盒已自动承诺"
    )
    return (
        f"当前端状态：候选承诺（{basis}，尚未达到全部质量线）；"
        "达到全部质量线后自动升级为已接受，换端前需先说明理由并记录"
        "释放事件。"
    )


def _attach_solo_lifecycle(
    session: Mapping[str, Any],
    report: MutableMapping[str, Any],
) -> None:
    """Attach the commitment ladder to a legacy single-tray report.

    Uncommitted legacy inputs keep their exact prior output; only inputs
    whose real card/open history implies a commitment gain the block.
    """
    candidate_tray_id = session.get("candidate_tray_id")
    if candidate_tray_id is None and session.get("accepted_tray_id") is None:
        return
    state = session["_tray_states"][session["active_tray_id"]]
    if "tray_screening" in report:
        profile = report["tray_screening"]["acceptance_profile"]
        decision = report["tray_screening"]["direct_draw_decision"]
    else:
        profile = _tray_acceptance_profile(state, report["ranking"][0])
        decision = report["draw_decision"]
    candidate_qualified = (
        bool(profile)
        and decision["should_draw"]
        and all(check["passed"] for check in profile)
    )
    report["tray_lifecycle"] = _session_lifecycle_summary(
        accepted_tray_id=session.get("accepted_tray_id"),
        candidate_tray_id=candidate_tray_id,
        commitment_source=session.get("_candidate_source"),
        candidate_qualified=candidate_qualified,
        accepted_source=session.get("_accepted_source"),
        accepted_commitment_source=session.get(
            "_accepted_commitment_source"
        ),
    )


def _render_screening_markdown(
    report: Mapping[str, Any],
    tray_id: Optional[str],
    lifecycle: Optional[Mapping[str, Any]] = None,
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
        "candidate_review": (
            "当前端为候选承诺（已选定但未达线）；若要换端，先确认释放原因。"
        ),
    }
    recommendation = screening["recommendation"]
    if recommendation == "release_before_switch":
        if status == "candidate_review":
            conclusion = (
                "当前端为候选承诺（已选定但未达线）；若要换端，先确认释放"
                "原因。"
            )
        else:
            conclusion = "当前端已锁定；若要换端，先确认释放原因。"
    else:
        conclusion = conclusions.get(status, f"当前状态：{status}。")
    lines = [
        f"# {_markdown_cell(report.get('series') or '盲盒')}｜端筛选快报",
        "",
    ]
    if tray_id is not None:
        lines.extend([f"当前端：{_markdown_cell(tray_id)}", ""])
    lifecycle_text = _lifecycle_sentence(lifecycle)
    if lifecycle_text:
        lines.extend([lifecycle_text, ""])
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


def _calibration_rules_text(rules: Mapping[str, Any]) -> str:
    parts: List[str] = []
    rule_order = list(QUALITY_STOP_RULE_KEYS)
    if "min_resale_ev" in rules:
        rule_order.remove("min_resale_ev")
        rule_order.insert(0, "min_resale_ev")
    for rule in rule_order:
        if rule not in rules:
            continue
        value = rules[rule]
        if rule == "min_expected_score":
            rendered = f"{float(value):.1f}"
        elif rule == "min_resale_ev":
            rendered = f"¥{float(value):.0f}"
        else:
            rendered = f"{float(value):.0f}%"
        operator = "≥" if rule.startswith("min_") else "≤"
        parts.append(
            f"{REPORT_RULE_LABELS[rule].replace('至少', '').replace('不超过', '').strip()}"
            f"{operator}{rendered}"
        )
    return "；".join(parts) if parts else "无"


def render_preference_calibration_markdown(
    report: Mapping[str, Any],
) -> str:
    """Render score tiers and current-tray trade-offs without recommending a draw."""
    validate_preference_calibration_report(report)
    lines = [
        f"# {_markdown_cell(report.get('series') or '盲盒')}｜偏好校准",
        "",
    ]
    if report.get("tray_id") is not None:
        lines.extend(
            [f"当前端：{_markdown_cell(report['tray_id'])}", ""]
        )
    if report["status"] == "target_unreachable":
        conclusion = (
            f"当前端所有可选盒的{report['primary_metric']['label']}均为 0，"
            "无法生成有效候选边界；"
            "本报告未推荐抽盒，也未改写停止线。"
        )
    else:
        conclusion = (
            "已按逐款评分自动分档，并算出当前端的真实可达取舍；"
            "本报告未推荐抽盒，也未改写停止线。"
        )
    lines.extend(["## 结论", "", conclusion, "", "## 自动分档", ""])
    lines.extend(
        [
            "| 档位 | 分数 | 款式 |",
            "|---|---:|---|",
        ]
    )
    for tier in SCORE_TIER_KEYS:
        designs = report["score_tiers"][tier]
        design_text = "、".join(_markdown_cell(item) for item in designs) or "—"
        lines.append(
            f"| {SCORE_TIER_LABELS[tier]} | {SCORE_TIER_RANGES[tier]} | "
            f"{design_text} |"
        )
    coverage = report["score_coverage"]
    lines.extend(
        [
            "",
            (
                f"评分覆盖：{coverage['scored_design_count']}/"
                f"{coverage['total_designs']} 款。"
            ),
        ]
    )
    if coverage["score_default_used"]:
        lines.append(
            f"`score_default={float(coverage['score_default']):g}` 已补齐 "
            f"{len(coverage['filled_by_score_default'])} 款，且状态标记为用户已确认。"
        )

    ranges = report["attainable_ranges"]
    lines.extend(
        [
            "",
            "## 当前端可达区间",
            "",
            "以下区间基于当前直接可选盒；卡片在边界确认后另行规划。",
            "",
            "| 指标 | 最低 | 最高 |",
            "|---|---:|---:|",
            (
                "| 最爱款合计 | "
                f"{_pp(ranges['p_favorite_any_pp']['min'])} | "
                f"{_pp(ranges['p_favorite_any_pp']['max'])} |"
            ),
            (
                "| 喜欢款合计 | "
                f"{_pp(ranges['p_like_any_pp']['min'])} | "
                f"{_pp(ranges['p_like_any_pp']['max'])} |"
            ),
            (
                "| 不喜欢款合计 | "
                f"{_pp(ranges['p_dislike_any_pp']['min'])} | "
                f"{_pp(ranges['p_dislike_any_pp']['max'])} |"
            ),
            (
                "| 硬雷合计 | "
                f"{_pp(ranges['p_hard_avoid_pp']['min'])} | "
                f"{_pp(ranges['p_hard_avoid_pp']['max'])} |"
            ),
            (
                "| 期望评分 | "
                f"{float(ranges['expected_score']['min']):.2f} | "
                f"{float(ranges['expected_score']['max']):.2f} |"
            ),
        ]
    )
    if "resale_ev" in ranges:
        lines.append(
            "| 预期二手价值 | "
            f"¥{float(ranges['resale_ev']['min']):.2f} | "
            f"¥{float(ranges['resale_ev']['max']):.2f} |"
        )

    resale_active = "resale_ev" in ranges
    lines.extend(["", "## 全部可选盒", ""])
    if resale_active:
        lines.extend(
            [
                "| 当前策略排名 | 盒号 | 最爱 | 喜欢 | 不喜欢 | 硬雷 | 期望评分 | 预期二手价值 | 未被全面压过 |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
    else:
        lines.extend(
            [
                "| 当前策略排名 | 盒号 | 最爱 | 喜欢 | 不喜欢 | 硬雷 | 期望评分 | 未被全面压过 |",
                "|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
    for row in report["all_boxes"]:
        cells = [
            str(int(row["rank"])),
            f"{_markdown_cell(row['box_id'])}号",
            _pp(row["p_favorite_any_pp"]),
            _pp(row["p_like_any_pp"]),
            _pp(row["p_dislike_any_pp"]),
            _pp(row["p_hard_avoid_pp"]),
            f"{float(row['expected_score']):.2f}",
        ]
        if resale_active:
            cells.append(f"¥{float(row['resale_ev']):.2f}")
        cells.append("是" if row["pareto_frontier"] else "否")
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend(["", "## 候选边界", ""])
    if report["status"] == "target_unreachable":
        lines.append(
            "当前端目标不可达；请换端或停止，不生成数值为 0 的伪门槛。"
        )
    else:
        header = (
            "| 方案 | 取向 | 参考盒 | 最爱 | 喜欢 | 不喜欢 | 硬雷 | "
            "期望评分 | "
            + ("预期二手价值 | " if resale_active else "")
            + "确认后写入 |"
        )
        separator = (
            "|---|---|---:|---:|---:|---:|---:|---:|"
            + ("---:|" if resale_active else "")
            + "---|"
        )
        lines.extend([header, separator])
        for choice in report["choices"]:
            actual = choice["actual"]
            cells = [
                f"方案{choice['choice']}",
                str(choice["orientation"]),
                f"{_markdown_cell(choice['box_id'])}号",
                _pp(actual["p_favorite_any_pp"]),
                _pp(actual["p_like_any_pp"]),
                _pp(actual["p_dislike_any_pp"]),
                _pp(actual["p_hard_avoid_pp"]),
                f"{float(actual['expected_score']):.2f}",
            ]
            if resale_active:
                cells.append(f"¥{float(actual['resale_ev']):.2f}")
            cells.append(
                _calibration_rules_text(choice["suggested_stop_rules"])
            )
            lines.append("| " + " | ".join(cells) + " |")
        lines.extend(
            [
                "",
                "候选边界按当前参考盒向“仍可通过”的方向取整；它们是待确认建议，"
                "不是由评分机械推导出的事实。",
            ]
        )

    existing = report["existing_stop_rules"]
    if existing:
        existing_parts: List[str] = []
        quality_text = _calibration_rules_text(existing)
        if quality_text != "无":
            existing_parts.append(quality_text)
        if "max_draws" in existing:
            existing_parts.append(
                f"最多抽 {int(existing['max_draws'])} 盒"
            )
        lines.extend(
            [
                "",
                f"已有停止条件：{'；'.join(existing_parts)}。本报告未改写。",
            ]
        )
    lines.extend(["", "## 下一步", ""])
    if report["status"] == "target_unreachable":
        lines.append("换端后用同一评分重新校准；同系列评分不变时无需重填。")
    else:
        choice_ids = "/".join(
            f"方案{choice['choice']}" for choice in report["choices"]
        )
        lines.append(
            f"回复 {choice_ids} 即确认该组边界用于本系列当前会话；"
            "也可直接修改数字。确认后才写入 `stop_rules` 并生成正式抽盒建议。"
        )
    lines.extend(["", "## 模型口径", ""])
    lines.extend(_render_model_notes(report))
    return "\n".join(lines).rstrip() + "\n"


def render_preference_briefing_markdown(
    report: Mapping[str, Any],
) -> str:
    """Render the zero-tray briefing without any current-tray claims."""
    validate_preference_briefing_report(report)
    baseline = report["blind_baseline"]
    lines = [
        f"# {_markdown_cell(report.get('series') or '盲盒')}｜偏好简报（无端参考线）",
        "",
    ]
    if report["status"] == "needs_confirmation":
        conclusion = (
            "尚未观察任何一端盒位。已按完整端型算出盲抽基线，并为"
            "「随便中个喜欢」给出参考线；本报告未推荐抽盒，也未改写停止线。"
        )
    elif report["status"] == "preference_conflict":
        conclusion = (
            "尚未观察任何一端盒位。偏好分组与评分存在未解决冲突；盲抽基线"
            "仅供核对，本报告不生成参考线、不推荐抽盒，也不改写停止线。"
        )
    else:
        conclusion = (
            "尚未观察任何一端盒位。以下盲抽基线仅用于对照；当前策略无端不生成"
            "参考线，本报告未推荐抽盒，也未改写停止线。"
        )
    lines.extend(["## 结论", "", conclusion, "", "## 盲抽基线", ""])
    lines.extend(
        [
            "假设整端常规款无重复、每款一个；这是先验均匀分布，不是当前端校准。",
            "",
            "| 指标 | 盲抽值 |",
            "|---|---:|",
            f"| 常规款数 | {int(baseline['regular_count'])} |",
            f"| 最爱款合计 | {_pp(baseline['p_favorite_any_pp'])} |",
            f"| 喜欢款合计 | {_pp(baseline['p_like_any_pp'])} |",
            f"| 不喜欢款合计 | {_pp(baseline['p_dislike_any_pp'])} |",
            f"| 硬雷合计 | {_pp(baseline['p_hard_avoid_pp'])} |",
        ]
    )
    if baseline.get("expected_score") is not None:
        lines.append(
            f"| 期望评分 | {float(baseline['expected_score']):.2f} |"
        )
    if baseline.get("resale_ev") is not None:
        lines.append(
            f"| 预期二手价值 | ¥{float(baseline['resale_ev']):.2f} |"
        )
    lines.extend(
        [
            "",
            f"适用策略：「{report['strategy_name']}」——{report['strategy_rule']}",
        ]
    )

    lines.extend(["", "## 偏好一致性", ""])
    conflicts = report["preference_conflicts"]
    if conflicts:
        lines.extend(
            [
                "| 款式 | 显式字段 | 评分 | 评分档位 | 处理 |",
                "|---|---|---:|---|---|",
            ]
        )
        for conflict in conflicts:
            lines.append(
                "| {} | {} | {} | {} | 待确认 |".format(
                    _markdown_cell(conflict["design"]),
                    conflict["explicit_field"],
                    f"{float(conflict['score']):g}",
                    conflict["score_tier_label"],
                )
            )
        lines.extend(
            [
                "",
                "以上款式的显式分组与评分档位矛盾。旧状态读取时仍保留显式"
                "字段，但不能据此生成参考线；请修改分组或评分后重跑。",
            ]
        )
    elif report["status"] == "preference_conflict":
        lines.append("偏好冲突未解决，参考线已阻塞。先统一分组与评分。")
    else:
        lines.append("显式偏好字段与逐款评分未发现方向矛盾。")

    lines.extend(["", "## 参考线", ""])
    reference = report["reference_lines"]
    if report["status"] == "needs_confirmation":
        lines.extend(
            [
                "以下参考线仅适用于「随便中个喜欢」：按五个百分点一档严格改善"
                "盲抽基线，并使用 40% / 35% / 20% 平衡锚点。它们是判断性建议，确认前不写入 "
                "`stop_rules`。",
                "",
                "| 规则 | 盲抽基线 | 建议线 | 相差 |",
                "|---|---:|---:|---:|",
            ]
        )
        for rule in reference["rules"]:
            delta = float(rule["delta_vs_baseline_pp"])
            lines.append(
                f"| {rule['label']} | {_pp(rule['baseline_pp'])} | "
                f"{float(rule['suggested_value']):.0f}% | {delta:+.2f}pp |"
            )
    elif report["status"] == "preference_conflict":
        lines.append(
            "先确认冲突款应以显式分组还是评分档位为准，并修改输入；"
            "冲突消失后重新生成无端参考线。"
        )
    else:
        lines.append(reference["redirect_other_strategies"])

    existing = report["existing_stop_rules"]
    if existing:
        existing_parts: List[str] = []
        quality_text = _calibration_rules_text(existing)
        if quality_text != "无":
            existing_parts.append(quality_text)
        if "max_draws" in existing:
            existing_parts.append(
                f"最多抽 {int(existing['max_draws'])} 盒"
            )
        lines.extend(
            [
                "",
                f"已有停止条件：{'；'.join(existing_parts)}。本报告未改写。",
            ]
        )
    lines.extend(["", "## 下一步", ""])
    if report["status"] == "needs_confirmation":
        lines.append(
            "确认后参考线写入既有 `stop_rules`（不新增第二套入场线）；"
            "随后进入任一端补充盒位信息，先用 --screen-tray 判断端型。"
        )
    elif report["status"] == "preference_conflict":
        lines.append(
            "先统一冲突款的显式分组与评分；修改输入后重新生成简报。"
        )
    else:
        lines.append(
            "进入任一端补充盒位信息后，运行 --calibrate-preferences，"
            "用当前端真实可达取舍确认边界；判断端型用 --screen-tray。"
        )
    lines.extend(["", "## 模型口径", ""])
    lines.extend(_render_model_notes(report))
    return "\n".join(lines).rstrip() + "\n"


def _comparison_action_cell(action: Mapping[str, Any]) -> str:
    tool = action.get("tool")
    if tool == "hint":
        return f"提示卡@{action.get('box_id')}"
    if tool == "display":
        return f"显示卡@{action.get('box_id')}"
    return "免卡"


def render_tray_comparison_markdown(report: Mapping[str, Any]) -> str:
    """Render the multi-tray comparison in the fixed section order."""
    validate_tray_comparison_report(report)
    comparison = report["comparison"]
    rows = comparison["rows"]
    excluded = comparison["excluded_trays"]
    recommendation = report["recommendation"]
    session = report["session_summary"]
    lines: List[str] = [
        f"# {_markdown_cell(report.get('series') or '盲盒')}｜多端横比",
        "",
    ]

    lines.extend(["## 结论", ""])
    if rows:
        top = rows[0]
        horizon = (
            "两步时域（仅头部候选端扩展）"
            if recommendation["planning_horizon"] == "two_card"
            else "一步时域"
        )
        if recommendation["first_action"] is None:
            conclusion = (
                f"共 {len(rows)} 个端参与排序，但排名靠前的端状态为"
                f"「{top['status_label']}」，没有可直接执行的首步动作；"
                f"本结论基于{horizon}。建议停止本轮或先补齐质量线/端信息。"
            )
        else:
            first_cell = _comparison_action_cell(recommendation["first_action"])
            conclusion = (
                f"共 {len(rows)} 个可操作端完成独立求解。综合既有策略指标与"
                f"质量线，推荐端为「{top['tray_id']}」（{top['status_label']}），"
                f"首步动作：{first_cell}；本结论基于{horizon}。"
            )
        if recommendation.get("release_required_before_switch"):
            accepted_id = session.get("accepted_tray_id")
            candidate_id = session.get("candidate_tray_id")
            if accepted_id is not None:
                conclusion += (
                    f" 当前已接受端为「{accepted_id}」；换端前"
                    "需先说明理由并记录释放事件。"
                )
            else:
                conclusion += (
                    f" 当前候选承诺端为「{candidate_id}」（尚未达到全部"
                    "质量线）；换端前需先说明理由并记录释放事件。"
                )
        conclusion += " 换端或等下一端不保证更好。"
    else:
        conclusion = (
            "本会话没有仍可操作的端参与排序；已释放、历史只读或无可用盒的"
            "端只进入复盘。建议停止本轮或先补齐端信息。"
        )
    lines.extend([conclusion, ""])

    lines.extend(["## 逐端比较", ""])
    if rows:
        lines.extend(
            [
                "每端独立求解；盒号、排除、已售未知与后验不跨端共享。",
                "",
                "| 排名 | 端 | 直接最佳盒 | 最爱合计 | 喜欢合计 | 总雷 | 硬雷 | 直接状态 | 首张道具 | 道具后仍可抽 |",
                "|---:|---|---|---:|---:|---:|---:|---|---|---:|",
            ]
        )
        for row in rows:
            metrics = row["metrics"]
            markers = []
            if row.get("is_accepted_tray"):
                markers.append("已接受")
            elif row.get("is_candidate_tray"):
                markers.append("候选承诺")
            if row.get("is_active_tray"):
                markers.append("当前端")
            tray_cell = row["tray_id"] + (
                f"（{'、'.join(markers)}）" if markers else ""
            )
            lines.append(
                "| {} | {} | {}号 | {} | {} | {} | {} | {} | {} | {} |".format(
                    int(row["rank"]),
                    _markdown_cell(tray_cell),
                    row["direct_best_box_id"],
                    _pp(metrics["p_favorite_any_pp"]),
                    _pp(metrics["p_like_any_pp"]),
                    _pp(metrics["p_dislike_any_pp"]),
                    _pp(metrics["p_hard_avoid_pp"]),
                    row["status_label"],
                    _comparison_action_cell(row["first_tool_action"]),
                    _pp(row["post_tool_draw_probability_pp"]),
                )
            )
        lines.extend(
            [
                "",
                f"{comparison['rescue_probability_semantics']}"
                " 排序沿用既有策略与停止线；直接合格端先比策略指标，"
                "实用容差内等价时优先不用卡。",
            ]
        )
        depth_two = comparison.get("depth_two")
        if depth_two:
            lines.extend(
                [
                    "",
                    "### 两步时域（仅头部候选端）",
                    "",
                ]
            )
            for tray_id in depth_two["head_candidate_tray_ids"]:
                row = next(
                    item for item in rows if item["tray_id"] == tray_id
                )
                detail = row["depth_two_detail"]
                changed = "变化" if detail[
                    "first_action_changed_vs_depth_1"
                ] else "不变"
                equivalent = (
                    "实用容差内等价"
                    if detail[
                        "terminal_value_practically_equivalent_to_depth_1"
                    ]
                    else "不等价"
                )
                gain = detail.get("primary_gain_vs_one_card_pp")
                gain_text = (
                    f"两步较一步终局主指标增益 {float(gain):+.2f}pp"
                    if gain is not None
                    else "两步较一步终局主指标增益未定义"
                )
                lines.append(
                    f"- 「{tray_id}」：两步推荐首步 "
                    f"{_comparison_action_cell(detail['recommended_action'])}"
                    f"（相对一步：首步{changed}，终局{equivalent}；{gain_text}）。"
                )
            lines.append(
                f"- 其余 {len(rows) - len(depth_two['head_candidate_tray_ids'])} "
                "个端保持一步时域结果。"
            )
    else:
        lines.append("没有可排序的端。")
    if excluded:
        lines.extend(
            [
                "",
                "### 未参与排序的端（仅复盘）",
                "",
                "| 端 | 原因 | 可抽盒数 | 已开盒数 |",
                "|---|---|---:|---:|",
            ]
        )
        for item in excluded:
            lines.append(
                "| {} | {} | {} | {} |".format(
                    _markdown_cell(item["tray_id"]),
                    item["reason_label"],
                    int(item["drawable_box_count"]),
                    int(item["opened_box_count"]),
                )
            )

    lines.extend(["", "## 下一步（推荐端与首步动作）", ""])
    if rows and recommendation["first_action"] is not None:
        first_cell = _comparison_action_cell(recommendation["first_action"])
        lines.append(
            f"在推荐端「{recommendation['recommended_tray_id']}」执行："
            f"{first_cell}。"
        )
        commitment = recommendation.get("commitment_after_action")
        if isinstance(commitment, Mapping):
            lines.append(
                "该端当前依赖道具，不是直接合格：选定后先记录候选承诺"
                "（tray_committed 事件）；真实线索通过全部质量线后自动升级"
                "为已接受，换端前需先说明理由并记录释放事件。"
            )
        if recommendation["planning_horizon"] == "two_card":
            lines.append(
                "时域：两步规划仅扩展了头部候选端；请以推荐端标注的首步"
                "动作为准，第二步在第一步结果后重算。"
            )
        else:
            lines.append(
                "时域：默认一步规划；需要比较两步时域时再显式运行 "
                "--compare-depth 2。"
            )
        lines.append(
            "多端横比不重置偏好、质量线、卡数、抽数和历史证据；单端路径"
            "仍以 --screen-tray 或正式报告为准。"
        )
    else:
        lines.append("本轮建议停止或先补齐端信息；没有可执行的首步动作。")

    lines.extend(["", "## 质量线", ""])
    rules_text = _calibration_rules_text(report["stop_rules"])
    lines.extend(
        [
            f"适用策略：「{report['strategy_name']}」——{report['strategy_rule']}",
            f"质量线：{rules_text}。",
        ]
    )
    if "max_draws" in report["stop_rules"]:
        lines.append(
            f"最多抽 {int(report['stop_rules']['max_draws'])} 盒"
            f"（全会话已用 {int(session['draws_used'])} 盒）。"
        )
    lines.append("质量线与预算为会话共享；本报告未改写任何停止条件。")

    lines.extend(["", "## 模型口径", ""])
    model_lines = _render_model_notes(report)
    independent = report["model_summary"].get("independent_trays")
    if independent:
        model_lines.append(f"- {independent}")
    lines.extend(model_lines)
    return "\n".join(lines).rstrip() + "\n"


def _format_review_pp(value: Optional[float]) -> str:
    if value is None:
        return "不可恢复"
    return f"{float(value):.2f}%"


def _render_review_opening(
    opening: Mapping[str, Any], index: int
) -> List[str]:
    probabilities = opening["outcome_class_probabilities_pp"]
    ranked = "、".join(
        f"{item['design']} {_format_review_pp(item['probability_pp'])}"
        for item in opening["possible_designs_ranked"]
    )
    rank = opening["actual_design_rank"]
    rank_text = f"第 {int(rank)} 位" if rank is not None else "不在可能款列表中"
    lines = [
        f"### 第 {index} 次开盒（事件 {opening['seq']}，"
        f"端 {opening['tray_id']}，盒 {opening['box_id']} → "
        f"{opening['design']}）",
        "",
        f"- 实际款事前概率：{_format_review_pp(opening['actual_design_prior_pp'])}"
        f"（可能款排名 {rank_text}：{ranked}）",
        f"- 结果类概率：喜欢 {_format_review_pp(probabilities['liked'])}、"
        f"中性 {_format_review_pp(probabilities['neutral'])}、"
        f"不喜欢 {_format_review_pp(probabilities['disliked'])}、"
        f"硬雷 {_format_review_pp(probabilities['hard_avoid'])}",
        f"- 当时接受的失败概率："
        f"{_format_review_pp(opening['accepted_failure_pp'])}"
        f"（{opening['failure_semantics']}）",
    ]
    checks = opening["quality_lines_at_draw"]
    if checks:
        rendered = "；".join(
            f"{check['rule']} {check['actual']:.2f}"
            f"{'≥' if check['operator'] == '>=' else '≤'}"
            f"{check['threshold']:.2f}"
            f"（{'过' if check['passed'] else '未过'}）"
            for check in checks
        )
        lines.append(f"- 当时质量线：{rendered}")
    else:
        lines.append("- 当时质量线：未配置")
    if opening["chosen_was_optimal"]:
        lines.append("- 所选盒为决策时点最优盒")
    else:
        alternative = opening["strongest_alternative"]
        lines.append(
            f"- 所选盒非最优；当时最强备选：盒 {alternative['box_id']}"
            f"（喜欢 {_format_review_pp(alternative['p_like_any_pp'])}，"
            f"喜欢概率差 {alternative['p_like_any_delta_pp']:+.2f}pp）"
        )
    return lines


def _render_review_tool_card(card: Mapping[str, Any]) -> List[str]:
    result = card["real_result"]
    result_text = (
        f"排除了 {result['excluded']}"
        if "excluded" in result
        else f"显示为 {result['revealed']}"
    )
    branch = card["ex_ante_drawable_branch_pp"]
    branch_text = (
        _format_review_pp(branch)
        if card["ex_ante_branch_available"]
        else "不可恢复"
    )
    change = card["primary_metric_change"]
    metric_labels = {
        "severity_weighted_dislike": "严重度加权雷款风险",
        "p_like_any": "喜欢款概率",
        "p_favorite_any": "最爱款概率",
        "p_top_score_group": "最高分组概率",
        "p_top_liked": "第一目标概率",
        "expected_score": "期望评分",
        "legacy_utility": "综合效用",
        "resale_ev": "预期二手价值",
    }
    unit = change["unit"]
    if unit in {"percentage_points", "severity_weighted_probability_points"}:
        change_text = (
            f"{float(change['before']):.2f} → {float(change['after']):.2f}"
            f"（{float(change['delta']):+.2f}pp）"
        )
    elif unit == "CNY":
        change_text = (
            f"¥{float(change['before']):.2f} → ¥{float(change['after']):.2f}"
            f"（{float(change['delta']):+.2f} 元）"
        )
    else:
        change_text = (
            f"{float(change['before']):.2f} → {float(change['after']):.2f}"
            f"（{float(change['delta']):+.2f}）"
        )
    action_labels = {"draw": "抽", "stop": "停"}
    return [
        f"### 事件 {card['seq']}：{SESSION_REVIEW_TOOL_LABELS[card['tool']]}"
        f" → 端 {card['tray_id']} 盒 {card['box_id']}",
        "",
        f"- 真实结果：{result_text}",
        f"- 事前用卡后仍可抽分支概率：{branch_text}",
        f"- 策略主指标（{metric_labels.get(change['metric'], change['metric'])}）："
        f"{change_text}",
        f"- 最佳盒：{card['best_box_before']} → {card['best_box_after']}；"
        f"排序是否变化：{'是' if card['ranking_changed'] else '否'}",
        f"- 行动：{action_labels[card['action_before']]} → "
        f"{action_labels[card['action_after']]}；是否决定行动："
        f"{'是' if card['decision_changed'] else '否'}；"
        f"用后该类卡剩余 {int(card['cards_remaining_after'])} 张",
    ]


def render_session_review_markdown(report: Mapping[str, Any]) -> str:
    validate_session_review_report(report)
    summary = report["session_summary"]
    replay = report["replay"]
    counters = report["global_counters"]
    decision = report["decision_quality"]
    outcome = report["outcome_quality"]
    bias = report["bias_checks"]

    lines: List[str] = [
        f"# 盲盒整轮自动复盘：{report.get('series') or '合成会话'}",
        "",
        "## 复盘结论",
        "",
        f"- 停止结论：{counters['final_stop_conclusion_label']}",
        f"- 已抽 {int(counters['draws_used'])} 盒"
        + (
            f"（上限 {int(counters['max_draws'])} 盒）"
            if counters["max_draws"] is not None
            else "（未设上限）"
        )
        + f"；剩余提示卡 {int(summary['remaining_tools'].get('hint_cards', 0))} 张、"
        f"显示卡 {int(summary['remaining_tools'].get('display_cards', 0))} 张",
        f"- 决策质量："
        + (
            "历史不足，无法逐次判断"
            if not replay["recoverable"]
            else (
                "全部开盒均为决策时点最优"
                if decision["all_openings_optimal"]
                else "有 {} 次非最优开盒（事件 {}）".format(
                    len(decision["non_optimal_openings"]),
                    "、".join(
                        str(seq) for seq in decision["non_optimal_openings"]
                    ),
                )
            )
        ),
        f"- 结果质量："
        + (
            "历史不足，仅当前状态显示已抽 {} 盒，未逐次归类".format(
                int(counters["draws_used"])
            )
            if not replay["recoverable"]
            else "喜欢 {}、不喜欢 {}（含硬雷 {}）、中性 {}，共 {} 次".format(
                outcome["liked_hits"],
                outcome["disliked_hits"],
                outcome["hard_avoid_hits"],
                outcome["neutral_results"],
                outcome["openings_total"],
            )
        ),
        f"- 偏差检查：沉没成本风险"
        f"{'有' if bias['sunk_cost_risk'] else '无'}、赌徒谬误风险"
        f"{'有' if bias['gambler_fallacy_risk'] else '无'}（仅用事前信息判断）",
        f"- 数据可恢复性："
        + (
            "事件账本完整，整轮确定性回放"
            if replay["recoverable"]
            else f"存在不可恢复项（{replay['unrecoverable_reason']}），"
            "未用当前概率编造历史数字"
        ),
    ]

    lines.extend(["", "## 开盒逐次复盘", ""])
    if replay["openings"]:
        for index, opening in enumerate(replay["openings"], start=1):
            lines.extend(_render_review_opening(opening, index))
            lines.append("")
    else:
        if replay["recoverable"]:
            lines.append("本轮事件账本中没有开盒记录。")
        else:
            lines.append("无可回放的开盒记录；历史不足的逐次数据已标记不可恢复。")
        lines.append("")

    lines.extend(["## 道具卡逐张复盘", ""])
    if replay["tool_cards"]:
        for card in replay["tool_cards"]:
            lines.extend(_render_review_tool_card(card))
            lines.append("")
    else:
        lines.append("无可回放的用卡记录。")
        lines.append("")

    lines.extend(["## 承诺与止损线变更", ""])
    if replay["lifecycle"]:
        for entry in replay["lifecycle"]:
            label = SESSION_REVIEW_EVENT_LABELS.get(
                entry["type"], entry["type"]
            )
            source = entry.get("source")
            if source == "first_tool_or_open":
                label += "（自动）"
            elif source == "quality_lines_upgrade":
                label += "（达线升级）"
            text = f"- 事件 {entry['seq']}：{label}（端 {entry['tray_id']}）"
            if entry["type"] == "stop_rule_override":
                text += (
                    f"：{entry['rule']} {entry['old_value']} → "
                    f"{entry['new_value']}（{entry['reason']}）"
                )
            elif entry.get("reason"):
                text += f"：{entry['reason']}"
            lines.append(text)
    else:
        lines.append("- 无切端、承诺、接受、释放或停止线变更记录。")
    lines.append("")

    lines.extend(
        [
            "## 预算与停止结论",
            "",
            f"- 剩余提示卡 {int(counters['remaining_tools'].get('hint_cards', 0))} 张、"
            f"显示卡 {int(counters['remaining_tools'].get('display_cards', 0))} 张；"
            f"已抽 {int(counters['draws_used'])} 盒",
            f"- 最终停止结论：{counters['final_stop_conclusion_label']}",
            (
                "全局剩余卡、已抽盒数、上限与事件账本逐项核对一致。"
                if replay["recoverable"]
                else "剩余卡与已抽盒数来自当前状态；缺少事件账本，无法逐事件核对。"
            ),
            "",
            "## 决策、结果与模型质量",
            "",
            "- 决策质量只与决策时点的最强备选比较，不使用开盒后信息倒推。",
            f"- 模型质量："
            + (
                "无零概率矛盾事件"
                if not report["model_quality"]["zero_probability_openings"]
                else "事件 {} 的开盒结果在事前概率为零，模型口径需复核".format(
                    "、".join(
                        str(seq)
                        for seq in report["model_quality"][
                            "zero_probability_openings"
                        ]
                    )
                )
            ),
            "- 结果好坏不反推决策好坏：两者分开陈述。",
            "",
            "## 模型口径",
            "",
        ]
    )
    lines.extend(
        _render_model_notes(
            {
                "model_summary": report["model_quality"]["model_scope"],
                "model_warnings": report["model_quality"][
                    "model_warnings"
                ],
            }
        )
    )

    if not replay["recoverable"]:
        lines.extend(
            [
                "",
                "## 不可恢复项",
                "",
                "以下历史字段无法从当前状态唯一还原，已标记不可恢复，"
                "未用当前概率替代：",
            ]
        )
        for item in replay["unrecoverable_items"]:
            lines.append(f"- {item['field']}：{item['reason']}")
    return "\n".join(lines).rstrip() + "\n"


def render_user_markdown(
    report: Mapping[str, Any],
    *,
    screen_tray: bool = False,
) -> str:
    """Render the validated standard report; never fall back to a summary."""
    validate_user_report(report, screen_tray=screen_tray)
    active, tray_id = _active_user_report(report)
    lifecycle = _report_lifecycle(report)
    if screen_tray:
        return _render_screening_markdown(active, tray_id, lifecycle)

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
    lifecycle_text = _lifecycle_sentence(lifecycle)
    if lifecycle_text:
        lines.extend([lifecycle_text, ""])
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
    try:
        from scripts.blindbox_cli import _round_floats as round_output
    except ModuleNotFoundError:
        from blindbox_cli import _round_floats as round_output
    return round_output(obj, digits)


def _slim_report(
    report: Mapping[str, Any], top_actions: int, full_branches: bool
) -> Dict[str, Any]:
    try:
        from scripts.blindbox_cli import _slim_report as slim_output
    except ModuleNotFoundError:
        from blindbox_cli import _slim_report as slim_output
    return slim_output(
        report,
        top_actions,
        full_branches,
        _action_summary,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Delegate CLI parsing and mode routing to the focused CLI module."""
    try:
        from scripts.blindbox_cli import run_cli
    except ModuleNotFoundError:
        from blindbox_cli import run_cli
    return run_cli(sys.modules[__name__], argv)


if __name__ == "__main__":
    raise SystemExit(main())
