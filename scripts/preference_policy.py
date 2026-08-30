"""Preference tiers and zero-tray reference-line policy."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SCORE_TIER_KEYS = (
    "favorite",
    "liked",
    "acceptable",
    "neutral",
    "neutral_disappointed",
    "light_dislike",
    "hard_avoid",
)

SCORE_TIER_LABELS = {
    "favorite": "最爱",
    "liked": "喜欢",
    "acceptable": "可接受",
    "neutral": "中性",
    "neutral_disappointed": "中性但失望",
    "light_dislike": "轻雷",
    "hard_avoid": "硬雷",
}

SCORE_TIER_RANGES = {
    "favorite": "+10",
    "liked": "+6～+9",
    "acceptable": "+1～+5",
    "neutral": "0",
    "neutral_disappointed": "-1～-4",
    "light_dislike": "-5～-8",
    "hard_avoid": "-9～-10",
}

BRIEFING_BASELINE_METRIC_BY_RULE = {
    "min_like_any_pp": "p_like_any_pp",
    "min_favorite_any_pp": "p_favorite_any_pp",
    "max_dislike_any_pp": "p_dislike_any_pp",
    "max_hard_avoid_pp": "p_hard_avoid_pp",
}

BRIEFING_RULE_LABELS = {
    "min_like_any_pp": "喜欢款至少",
    "min_favorite_any_pp": "最爱款至少",
    "max_dislike_any_pp": "不喜欢款不超过",
    "max_hard_avoid_pp": "硬雷不超过",
}

BRIEFING_REFERENCE_STEP_PP = 5.0
BRIEFING_REFERENCE_ANCHORS = {
    "min_like_any_pp": 40.0,
    "max_dislike_any_pp": 35.0,
    "max_hard_avoid_pp": 20.0,
}


class PreferencePolicyError(ValueError):
    """Raised when a score falls outside the supported seven tiers."""


def score_tier(score: float) -> str:
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
    raise PreferencePolicyError(
        "preference scores must be between -10 and 10"
    )


def build_score_tiers(scores: Mapping[str, float]) -> Dict[str, List[str]]:
    tiers = {key: [] for key in SCORE_TIER_KEYS}
    for label, score in scores.items():
        tiers[score_tier(score)].append(label)
    for key in ("favorite", "liked", "acceptable"):
        tiers[key].sort(key=lambda label: (-scores[label], label))
    tiers["neutral"].sort()
    for key in ("neutral_disappointed", "light_dislike", "hard_avoid"):
        tiers[key].sort(key=lambda label: (scores[label], label))
    return tiers


def preference_tier_conflicts(
    state: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    preferences = state["preferences"]
    scores = preferences["scores"]
    conflicts: List[Dict[str, Any]] = []
    if not scores:
        return conflicts
    explicit_fields = {
        key: [str(value) for value in preferences.get(key, [])]
        for key in ("liked", "disliked", "hard_avoid")
        if preferences["preference_sources"][key] == "explicit"
    }
    compatible_tiers = {
        "liked": {"favorite", "liked"},
        "disliked": {"light_dislike", "hard_avoid"},
        "hard_avoid": {"hard_avoid"},
    }
    for field, members in explicit_fields.items():
        for design in members:
            score = scores.get(design)
            if score is None:
                continue
            tier = score_tier(float(score))
            if tier not in compatible_tiers[field]:
                conflicts.append(
                    {
                        "design": design,
                        "explicit_field": field,
                        "score": float(score),
                        "score_tier": tier,
                        "score_tier_label": SCORE_TIER_LABELS[tier],
                        "resolution": "confirmation_required",
                        "current_effective_source": "explicit_field",
                    }
                )
    for explicit_tier, members in preferences.get(
        "explicit_score_tiers", {}
    ).items():
        for design in members:
            score = scores.get(design)
            if score is None:
                continue
            derived_tier = score_tier(float(score))
            if derived_tier != explicit_tier:
                conflicts.append(
                    {
                        "design": design,
                        "explicit_field": (
                            f"explicit_score_tiers.{explicit_tier}"
                        ),
                        "explicit_score_tier": explicit_tier,
                        "score": float(score),
                        "score_tier": derived_tier,
                        "score_tier_label": SCORE_TIER_LABELS[derived_tier],
                        "resolution": "confirmation_required",
                        "current_effective_source": "scores",
                    }
                )
    conflicts.sort(key=lambda item: (item["design"], item["explicit_field"]))
    return conflicts


def briefing_reference_value(rule: str, baseline_pp: float) -> float:
    step = BRIEFING_REFERENCE_STEP_PP
    if rule.startswith("min_"):
        next_step = step * (math.floor(baseline_pp / step) + 1)
        return min(
            100.0,
            max(float(BRIEFING_REFERENCE_ANCHORS[rule]), next_step),
        )
    previous_step = step * (math.ceil(baseline_pp / step) - 1)
    return max(
        0.0,
        min(float(BRIEFING_REFERENCE_ANCHORS[rule]), previous_step),
    )


def briefing_reference_rules(
    state: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    preferences = state["preferences"]
    if not preferences["liked"]:
        return []
    candidates: Sequence[Tuple[str, bool]] = (
        ("min_like_any_pp", bool(preferences["liked"])),
        ("max_dislike_any_pp", bool(preferences["disliked"])),
        ("max_hard_avoid_pp", bool(preferences["hard_avoid"])),
    )
    rules: List[Dict[str, Any]] = []
    for rule, applicable in candidates:
        if not applicable:
            continue
        baseline_pp = float(baseline[BRIEFING_BASELINE_METRIC_BY_RULE[rule]])
        if baseline_pp <= 0:
            continue
        suggested = briefing_reference_value(rule, baseline_pp)
        strictly_improved = (
            suggested > baseline_pp + 1e-12
            if rule.startswith("min_")
            else suggested < baseline_pp - 1e-12
        )
        if not strictly_improved:
            continue
        rules.append(
            {
                "rule": rule,
                "label": BRIEFING_RULE_LABELS[rule],
                "basis": "blind_baseline_strictly_improved",
                "baseline_pp": baseline_pp,
                "suggested_value": suggested,
                "delta_vs_baseline_pp": suggested - baseline_pp,
                "judgment": (
                    "判断性建议：严格改善盲抽基线；确认前不写入 stop_rules。"
                ),
            }
        )
    return rules
