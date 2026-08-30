"""Pure strategy metrics used by deterministic session reviews."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class ReviewMetricError(ValueError):
    """Raised when a review metric cannot be derived consistently."""


def _score_derived_target_groups(
    preferences: Mapping[str, Any],
) -> List[List[str]]:
    if preferences.get("preference_sources", {}).get("liked") != "scores":
        return []
    scores = preferences["scores"]
    grouped: Dict[float, List[str]] = {}
    for design in preferences["liked"]:
        grouped.setdefault(float(scores[design]), []).append(design)
    return [
        sorted(grouped[score])
        for score in sorted(grouped, reverse=True)
    ]


def outcome_class_probabilities(
    box_probs: Mapping[str, float],
    preferences: Mapping[str, Any],
) -> Dict[str, float]:
    liked = set(preferences["liked"])
    disliked = set(preferences["disliked"])
    hard_avoid = set(preferences["hard_avoid"])
    probabilities = {
        "liked": 0.0,
        "neutral": 0.0,
        "disliked": 0.0,
        "hard_avoid": 0.0,
    }
    for design, probability in box_probs.items():
        weight = float(probability)
        if weight <= 0:
            continue
        if design in liked:
            probabilities["liked"] += weight
        elif design in hard_avoid:
            probabilities["hard_avoid"] += weight
            probabilities["disliked"] += weight
        elif design in disliked:
            probabilities["disliked"] += weight
        else:
            probabilities["neutral"] += weight
    return probabilities


def failure_probability(
    box_probs: Mapping[str, float],
    preferences: Mapping[str, Any],
    class_probabilities: Mapping[str, float],
) -> Tuple[float, str]:
    objective_mode = preferences["objective_mode"]
    if objective_mode == "target_only":
        return (
            1.0 - float(class_probabilities["liked"]),
            "开盒前未落入喜欢款的概率（目标导向口径）",
        )
    if objective_mode == "top_target_first":
        score_groups = _score_derived_target_groups(preferences)
        targets = score_groups[0] if score_groups else preferences["liked"][:1]
        target_probability = sum(
            float(box_probs.get(design, 0.0)) for design in targets
        )
        return (
            1.0 - target_probability,
            "开盒前未落入最高优先目标的概率（最爱优先口径）",
        )
    return (
        float(class_probabilities["disliked"]),
        "开盒前落入不喜欢款（含硬雷）的概率（防守导向口径）",
    )


def _primary_metric_value(
    metrics: Mapping[str, Any],
    state: Mapping[str, Any],
) -> Tuple[str, str, str, float]:
    preferences = state["preferences"]
    mode = preferences["objective_mode"]
    if mode == "risk_first":
        return (
            "severity_weighted_dislike",
            "severity_weighted_probability_points",
            "lower_is_better",
            100.0 * float(metrics["disliked_weighted_loss"]),
        )
    if mode == "target_only":
        return (
            "p_like_any",
            "percentage_points",
            "higher_is_better",
            100.0 * float(metrics["p_like_any"]),
        )
    if mode == "top_target_first":
        score_groups = _score_derived_target_groups(preferences)
        if score_groups:
            targets = score_groups[0]
            metric = (
                "p_favorite_any"
                if float(preferences["scores"][targets[0]]) == 10.0
                else "p_top_score_group"
            )
        else:
            targets = preferences["liked"][:1]
            metric = "p_top_liked"
        value = sum(
            float(metrics["liked_probabilities"].get(design, 0.0))
            for design in targets
        )
        return metric, "percentage_points", "higher_is_better", 100.0 * value
    if mode in {"guardrail", "balanced"}:
        expected_score = metrics.get("expected_score")
        if expected_score is not None:
            return (
                "expected_score",
                "score_points",
                "higher_is_better",
                float(expected_score),
            )
        legacy_utility = float(metrics["liked_weighted_score"]) - float(
            metrics["disliked_weighted_loss"]
        )
        return (
            "legacy_utility",
            "utility_points",
            "higher_is_better",
            legacy_utility,
        )
    if mode == "resale_ev":
        return (
            "resale_ev",
            "CNY",
            "higher_is_better",
            float(metrics["resale_ev"]),
        )
    raise ReviewMetricError(f"unsupported objective mode: {mode}")


def primary_metric_change(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    before_metric, unit, direction, before_value = _primary_metric_value(
        before,
        state,
    )
    after_metric, after_unit, after_direction, after_value = (
        _primary_metric_value(after, state)
    )
    if (before_metric, unit, direction) != (
        after_metric,
        after_unit,
        after_direction,
    ):
        raise ReviewMetricError(
            "review primary metric changed identity during replay"
        )
    delta = after_value - before_value
    improvement = delta if direction == "higher_is_better" else -delta
    return {
        "metric": before_metric,
        "unit": unit,
        "direction": direction,
        "before": before_value,
        "after": after_value,
        "delta": delta,
        "improvement": improvement,
    }


def quality_lines(
    checks: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        {
            "rule": check["rule"],
            "operator": check["operator"],
            "threshold": float(check["threshold"]),
            "actual": float(check["actual"]),
            "passed": bool(check["passed"]),
        }
        for check in checks
    ]


def strongest_alternative(
    rows: Sequence[Mapping[str, Any]],
    chosen_index: int,
) -> Optional[Dict[str, Any]]:
    if chosen_index == 0:
        if len(rows) < 2:
            return None
        alternative = rows[1]
    else:
        alternative = rows[0]
    chosen = rows[chosen_index]
    return {
        "box_id": alternative["box_id"],
        "p_like_any_pp": 100.0 * float(alternative["p_like_any"]),
        "p_dislike_any_pp": 100.0 * float(alternative["p_dislike_any"]),
        "p_like_any_delta_pp": 100.0
        * (float(chosen["p_like_any"]) - float(alternative["p_like_any"])),
    }
