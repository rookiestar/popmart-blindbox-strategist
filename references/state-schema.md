# Session state schema

The solver accepts one JSON object. Preserve this state across turns and mutate it only for confirmed real-world events. The solver ignores optional metadata fields, so they may be used for audit history and counterfactual branches.

## Minimal structure

```json
{
  "series": "系列名称",
  "model": {
    "type": "unique_regular",
    "designs": ["款A", "款B", "款C"],
    "hint_labels": ["款A", "款B", "款C"]
  },
  "boxes": [
    {
      "id": "1",
      "excluded": ["款C"],
      "known": null,
      "status": "available",
      "tool_used": false
    },
    {
      "id": "2",
      "excluded": [],
      "known": null,
      "status": "available",
      "tool_used": false
    },
    {
      "id": "3",
      "excluded": [],
      "known": null,
      "status": "sold_unknown",
      "tool_used": false
    }
  ],
  "preferences": {
    "liked": ["款A", "款B"],
    "disliked": ["款C"],
    "strategy": "守住底线",
    "scores": {
      "款A": 10,
      "款B": 7,
      "款C": -10
    },
    "hard_avoid": ["款C"],
    "hard_avoid_max_pp": 15,
    "stop_rules": {
      "min_like_any_pp": 50,
      "min_favorite_any_pp": 15,
      "max_draws": 2
    },
    "tie_tolerance_pp": 0.5
  },
  "tools": {
    "hint_cards": 3,
    "display_cards": 1
  },
  "market_values": {
    "款A": 89,
    "款B": 52
  },
  "meta": {
    "branch": "actual",
    "updated_at": "ISO-8601 timestamp",
    "notes": [],
    "guidance": {
      "mode": "guided",
      "phase": "goal",
      "settled": ["intent", "series", "tray"],
      "pending_question": "goal",
      "defaults_applied": [],
      "confirmed": false
    }
  }
}
```

## `model`

### Complete regular case

Use when there are exactly as many regular designs as tray positions and every regular design appears once:

```json
{
  "type": "unique_regular",
  "designs": ["A", "B", "C"],
  "hint_labels": ["A", "B", "C"]
}
```

The order of `designs` has no statistical meaning.

### Scenario mixture

The default state is `unique_regular`. Use a mixture only when the user explicitly requests hidden-design modeling and the mechanics and priors are known well enough to specify complete-case scenarios:

```json
{
  "type": "mixture",
  "hint_labels": ["A", "B", "C"],
  "scenarios": [
    {"name": "no-secret", "prior": 0.916667, "designs": ["A", "B", "C"]},
    {"name": "secret-replaces-A", "prior": 0.027778, "designs": ["S", "B", "C"]},
    {"name": "secret-replaces-B", "prior": 0.027778, "designs": ["A", "S", "C"]},
    {"name": "secret-replaces-C", "prior": 0.027778, "designs": ["A", "B", "S"]}
  ]
}
```

Every scenario must contain exactly one design per tray position and no duplicate design names. Priors are normalized by the solver.

`hint_labels` means labels that a hint card can explicitly rule out. A secret design often is not a hint label. Exact hint-card planning is deliberately blocked when a mixture has design sets different from `hint_labels`, because the observation mechanism then requires a richer likelihood model.

## `boxes`

Every tray position must be represented, including sold positions.

| Field | Meaning |
|---|---|
| `id` | Box number shown in the UI. Store as a string. |
| `excluded` | Every design explicitly shown as “not”, including baseline clues and hint-card results. |
| `known` | Confirmed true design from a display card or completed opening; otherwise `null`. |
| `status` | Availability state listed below. |
| `tool_used` | `true` only after the user used an extra hint or display card on this box. Baseline UI exclusions do not count. |

Supported statuses:

- `available`: can still be selected.
- `sold_unknown`: sold, with unknown content; keep it latent.
- `opened`: purchased/opened and its `known` design is available.
- `unavailable_unknown`: unavailable for another reason, content unknown; keep it latent.
- `reserved_unknown`: temporarily unavailable, content unknown; keep it latent.

A known design may not also appear in that box's exclusion list. Two boxes cannot both be known as the same design in a no-duplicate scenario.

## `preferences`

`liked` and `disliked` are ordered from strongest to weakest when supplied.
When omitted, the solver derives them from scores using the seven default
tiers below. Read `references/preference-strategies.md` for plain-language
input and strategy selection.

### User-facing strategies

| `strategy` | Internal `objective_mode` | Required fields |
|---|---|---|
| `稳妥避雷` | `risk_first` | Ordered `disliked`; default |
| `守住底线` | `guardrail` | `scores`, `hard_avoid_max_pp`; `hard_avoid` may derive from scores |
| `整体最满意` | `balanced` | `scores` |
| `随便中个喜欢` | `target_only` | `liked` or scores that derive it |
| `只冲最爱` | `top_target_first` | Ordered `liked` or scores that derive it |
| `保值优先` | `resale_ev` | `market_values` |

Prefer `strategy` in new state files. `objective_mode` remains supported for
backward compatibility. The solver rejects conflicting values.

### Scores and hard limits

- `scores`: satisfaction score from `-10` through `+10` by design.
- `score_default`: score assigned to every unlisted design once at least one
  explicit score exists.
- `hard_avoid`: designs treated as hard failures.
- `hard_avoid_max_pp`: maximum combined hard-avoid probability for
  `守住底线`, in percentage points.

The seven default tiers are:

| Score | Normalized `score_tiers` key |
|---|---|
| `+10` | `favorite` |
| `+6` through `+9` | `liked` |
| `+1` through `+5` | `acceptable` |
| `0` | `neutral` |
| `-1` through `-4` | `neutral_disappointed` |
| `-5` through `-8` | `light_dislike` |
| `-9` through `-10` | `hard_avoid` |

When the corresponding field is absent, scores derive:

- `liked` from `favorite` plus `liked`;
- `disliked` from `hard_avoid` plus `light_dislike`;
- `hard_avoid` from the `hard_avoid` tier.

An explicitly supplied field, including an empty array, overrides only its
corresponding derivation. `score_default` participates after it fills unlisted
designs. The normalized solver output exposes all seven groups in
`preference_summary.score_tiers` and records explicit versus score-derived
sources.

`scores` and `hard_avoid` solve different problems: scores rank trade-offs;
the hard limit blocks compensation beyond the user's stated boundary. A hard
score does not create a probability limit; `hard_avoid_max_pp` remains explicit.

### Stopping conditions

`stop_rules` may contain:

- `min_like_any_pp`: stop if the best box's any-liked probability is lower.
- `min_favorite_any_pp`: stop if the best box's combined probability across
  all designs scored exactly `+10` is lower. Requires at least one `+10` score.
- `max_dislike_any_pp`: stop if its any-disliked probability is higher.
- `max_hard_avoid_pp`: stop if its hard-avoid probability is higher.
- `min_expected_score`: stop if its expected score is lower.
- `max_draws`: stop once this many boxes are already opened.

Every configured condition must pass. Re-evaluate after every clue, display,
or opening. A favorite threshold is a stop rule, not a ranking objective; use
`只冲最爱` when the box ranking itself should prioritize favorites.

### Timed tray screening

`--screen-tray` reuses the quality stopping conditions as the next-tray
acceptance profile. It adds no state field. `max_draws` remains a session-wide
cap and does not qualify a tray by itself. `守住底线` also contributes its
`hard_avoid_max_pp` limit.

The public report adds:

```json
{
  "tray_screening": {
    "status": "ready",
    "recommendation": "keep",
    "direct_best_box_id": "1",
    "acceptance_profile": [
      {
        "rule": "min_like_any_pp",
        "operator": ">=",
        "threshold": 55,
        "actual": 63.2,
        "margin": 8.2,
        "unit": "pp",
        "passed": true
      }
    ],
    "failed_acceptance_rules": [],
    "default_start_rule": "all_acceptance_rules_pass_after_default_shake",
    "comparison_basis": "posterior_metrics",
    "future_tray_improvement_guaranteed": false,
    "planning_depth": 1,
    "one_card_action": {
      "tool": "none",
      "action": "direct_draw",
      "box_id": "1",
      "expected_draw_probability": 1
    }
  }
}
```

The screening report is compact: it omits `ranking`, `top_3`, and the full
tool branch tree. Run the ordinary report after keeping the tray.

Read `references/tray-screening.md` for status meanings and the timed workflow.

`tie_tolerance_pp` is the practical-comparison bucket size in percentage points. Default `0.5` prevents tiny numeric differences from dominating weighted-risk or target comparisons. Set to `0` for strict unbucketed ordering.

## `tools`

- `hint_cards`: remaining hint cards.
- `display_cards`: remaining display cards. `reveal_cards` is accepted as a backward-compatible alias.

One user tool total may be used per box. Once a tool result is applied, set `tool_used: true`.

## `meta.guidance`

`meta.guidance` is optional conversation metadata. The solver ignores it; it
does not change the probability model or create preference defaults.

| Field | Meaning |
|---|---|
| `mode` | `guided` when the assistant is collecting a decision brief step by step. |
| `phase` | Next incomplete phase: `parse`, `clarify`, `goal`, `preferences`, `commitment`, `tools`, `risk`, `contract`, or `ready`. |
| `settled` | Semantic facts already answered or reliably read from the screenshot. |
| `pending_question` | The one current question in normal mode; `null` when none. |
| `defaults_applied` | Suggested defaults the user explicitly accepted. Keep unconfirmed suggestions out of calculation fields. |
| `confirmed` | `true` only after the user explicitly confirms the complete decision contract. |

When one reply answers multiple later questions, add every answered topic to
`settled`, advance to the first incomplete phase, and do not ask those
questions again. If the user changes the goal, preferences, action boundary,
tool budget, or risk limit, set `confirmed` back to `false` until the revised
contract is confirmed.

Read `references/guided-intake.md` for the question scheduler, fast lane, and
completion gate.

## Event updates

### Actual hint result

Before:

```json
{"id": "8", "excluded": ["A", "B", "C"], "tool_used": false}
```

After “8号不是D”:

```json
{"id": "8", "excluded": ["A", "B", "C", "D"], "tool_used": true}
```

Decrease `tools.hint_cards` by one.

### Actual display result

After “11号显示为A”:

```json
{"id": "11", "excluded": ["B", "C"], "known": "A", "status": "available", "tool_used": true}
```

Decrease `tools.display_cards` by one. Keep the box available if the platform still allows purchase.

### Completed purchase/opening

After the user buys box 11 and confirms A:

```json
{"id": "11", "known": "A", "status": "opened", "tool_used": true}
```

The known item continues to constrain all remaining boxes.

## Counterfactual branches

Never mutate the real state for a hypothetical statement.

```json
{
  "meta": {
    "branch": "hypothetical-8-not-roadblock",
    "parent": "actual",
    "assumption": "8号提示卡排除路障"
  }
}
```

Copy the actual state, apply the hypothetical event to the copy, compute the branch, and label the response as counterfactual. Merge only when the user explicitly confirms the event occurred.
