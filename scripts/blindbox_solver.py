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
    if preferences["tie_tolerance_pp"] < 0:
        raise StateError("tie_tolerance_pp must be non-negative")

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
    return copy.deepcopy({k: v for k, v in state.items() if not k.startswith("_")})


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
    ) -> Tuple[Dict[str, Any], float]:
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
                return branch, p_outcome
            return branch, 0.0

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
                    branch, branch_draw_probability = evaluate_branch(
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
                }
            )

    if state["tools"]["display_cards"] > 0:
        for box in eligible_boxes:
            box_id = box["id"]
            expected = _expected_metric_template(state)
            draw_probability = 0.0
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
                branch, branch_draw_probability = evaluate_branch(
                    next_state,
                    p_outcome,
                    actual,
                    expected,
                    beam is None or ("display", box_id) in beam,
                )
                branches.append(branch)
                draw_probability += branch_draw_probability
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
                }
            )

    tool_order = {"none": 0, "display": 1, "hint": 2}
    actions = sorted(
        actions,
        key=lambda action: (
            _action_metric_comparison_key(
                action["expected_terminal_metrics"], state
            ),
            tool_order[action["tool"]],
            _stable_box_sort_key(action["box_id"]),
        ),
    )
    for action in actions:
        uplift = _metric_delta(
            action["expected_terminal_metrics"],
            baseline_policy_metrics,
        )
        action["uplift_vs_no_card"] = uplift
        action["uplift_vs_direct_draw"] = uplift

    result = {
        "planning_depth": depth,
        "baseline_best_draw": baseline,
        "baseline_draw_decision": baseline_decision,
        "baseline_policy_metrics": baseline_policy_metrics,
        "recommended_action": actions[0],
        "action_ranking": actions,
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
        matching_depth_two_action = next(
            (
                action
                for action in actions
                if (
                    action["tool"],
                    action["action"],
                    action["box_id"],
                )
                == (
                    depth_one_action["tool"],
                    depth_one_action["action"],
                    depth_one_action["box_id"],
                )
            ),
            None,
        )
        result["depth_1_recommended_action"] = _action_summary(depth_one_action)
        result["gain_vs_one_card_horizon"] = _metric_delta(
            result["recommended_action"]["expected_terminal_metrics"],
            depth_one_action["expected_terminal_metrics"],
        )
        result["first_action_changed_vs_depth_1"] = (
            matching_depth_two_action is None
            or _action_metric_comparison_key(
                matching_depth_two_action["expected_terminal_metrics"],
                state,
            )
            != _action_metric_comparison_key(
                result["recommended_action"]["expected_terminal_metrics"],
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
    opened_count = sum(1 for box in state["boxes"] if box["status"] == "opened")

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
        "stop_rules_configured": bool(stop_rules),
        "reasons": reasons,
    }


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

    model_summary: Dict[str, Any] = {
        "type": state["model"].get("type", "unique_regular"),
        "exact_valid_assignments": posterior.exact_valid_assignments,
        "scenario_posteriors": posterior.scenario_posteriors,
        "scenario_valid_assignments": {
            r.name: r.valid_assignments for r in posterior.scenario_results
        },
        "assumption": (
            "Each scenario is a complete no-duplicate case; sold-but-unknown boxes "
            "remain latent and continue to constrain the other boxes."
        ),
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

    report = {
        "series": state.get("series"),
        "objective_mode": state["preferences"]["objective_mode"],
        "strategy_name": state["preferences"]["strategy"],
        "strategy_rule": STRATEGY_RULES[state["preferences"]["objective_mode"]],
        "preference_summary": {
            "liked": state["preferences"]["liked"],
            "disliked": state["preferences"]["disliked"],
            "hard_avoid": state["preferences"]["hard_avoid"],
            "score_tiers": state["preferences"]["score_tiers"],
            "sources": state["preferences"]["preference_sources"],
        },
        "model_summary": model_summary,
        "ranking": all_box_rows,
        "top_3": [row["box_id"] for row in all_box_rows[:3]],
        "draw_decision": evaluate_draw_decision(state, all_box_rows[0]),
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
                    "model_summary",
                    "tray_screening",
                )
            }
            if warnings:
                trimmed["warnings"] = warnings
            return trimmed
        report["next_tool_plan"] = tool_plan
    return report


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
    parser.add_argument("--indent", type=int, default=0)
    parser.add_argument("--digits", type=int, default=8)
    args = parser.parse_args(argv)

    try:
        if args.top_actions < 0:
            raise StateError("--top-actions must be >= 0")
        if args.beam_width < 0:
            raise StateError("--beam-width must be >= 0")
        state = _normalize_state(_read_json(args.state))
        if args.plan_one and args.plan_depth not in {None, 1}:
            raise StateError("--plan-one cannot be combined with --plan-depth 2")
        requested_depth = 1 if args.plan_one else args.plan_depth
        report = build_report(
            state,
            plan_depth=requested_depth,
            screen_tray=args.screen_tray,
            beam_width=args.beam_width,
        )
        report = _slim_report(report, args.top_actions, args.full_branches)
    except (OSError, json.JSONDecodeError, StateError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

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
