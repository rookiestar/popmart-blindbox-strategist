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

## Multi-tray session envelope

Use the session envelope as soon as the user switches trays. Preferences,
remaining tools, and the draw cap are session-wide; each tray keeps its own
model, boxes, clues, known results, and `tool_used` flags.

```json
{
  "session_schema_version": 1,
  "series": "合成系列",
  "active_tray_id": "tray-b",
  "accepted_tray_id": null,
  "draws_used": 1,
  "preferences": {
    "liked": ["A"],
    "disliked": ["C"],
    "strategy": "随便中个喜欢",
    "stop_rules": {"max_draws": 2}
  },
  "tools": {"hint_cards": 4, "display_cards": 1},
  "trays": [
    {
      "id": "tray-a",
      "model": {"type": "unique_regular", "designs": ["A", "B", "C"]},
      "boxes": [
        {"id": "1", "excluded": ["C"], "status": "available"},
        {"id": "2", "excluded": [], "status": "available"},
        {"id": "3", "excluded": [], "known": "B", "status": "opened"}
      ]
    },
    {
      "id": "tray-b",
      "model": {"type": "unique_regular", "designs": ["A", "B", "C"]},
      "boxes": [
        {"id": "1", "excluded": ["B"], "status": "available"},
        {"id": "2", "excluded": [], "status": "available"},
        {"id": "3", "excluded": [], "status": "sold_unknown"}
      ]
    }
  ],
  "events": [
    {"seq": 1, "type": "tray_switch", "tray_id": "tray-a"},
    {
      "seq": 2,
      "type": "opened_result",
      "tray_id": "tray-a",
      "box_id": "3",
      "design": "B"
    },
    {"seq": 3, "type": "tray_switch", "tray_id": "tray-b"}
  ],
  "meta": {"provenance": "synthetic"}
}
```

- `active_tray_id` must identify one stable tray ID.
- `draws_used` must equal the opened-box total across all retained trays.
- `tools` is the single remaining inventory used by every tray report.
- `events` is append-only and uses contiguous `seq` values from `1`.
- Supported events are `tray_switch`, `hint_used`, `display_used`,
  `opened_result`, `tray_accepted`, `tray_released`, and
  `stop_rule_override`; result fields must match the retained state.
- Tool and opening events must exactly cover the trays' `tool_used` and
  `opened` boxes; one box may have at most one tool event before opening.
- `accepted_tray_id` is `null` or the currently locked tray. Its acceptance
  lifecycle must match `tray_accepted` / `tray_released`; switching to another
  tray while locked is invalid.
- `tray_released` requires a concise reason. `stop_rule_override` requires the
  rule, old value, new value, active tray, and a user-supplied or confirmed
  reason; the latest new value must match session preferences.
- A tray may not repeat session-level `preferences`, `tools`, or
  `market_values`.

The CLI returns `session_summary`, `tray_reports` keyed by stable tray ID, and
`actual_events`. `session_recommendation` and each report's `tray_lock` prevent
an accepted tray from being bypassed silently. `session_review` repeats only
the acceptance lifecycle and stop-rule overrides needed for final review.
Only the active tray receives a requested card plan or timed screening; every
retained tray remains available for posterior review.

```bash
python3 scripts/blindbox_solver.py \
  examples/synthetic-multi-tray-session.json --digits 10
```

Legacy single-tray JSON remains valid. It is normalized internally as one
implicit `tray-1` session while preserving the legacy report shape.

### Accepted-tray lifecycle

Accept only after the configured quality lines pass:

```json
{"seq": 2, "type": "tray_accepted", "tray_id": "tray-a", "reason": "all_acceptance_rules_passed"}
```

To compare another tray, release before the switch:

```json
{"seq": 3, "type": "tray_released", "tray_id": "tray-a", "reason": "用户确认继续比较其他端"}
{"seq": 4, "type": "tray_switch", "tray_id": "tray-b"}
```

Set `accepted_tray_id` to `tray-a` while locked and to `null` after release.

### Stop-rule override

```json
{
  "seq": 5,
  "type": "stop_rule_override",
  "tray_id": "tray-b",
  "rule": "max_draws",
  "old_value": 1,
  "new_value": 2,
  "reason": "首抽未命中，用户确认再抽一盒"
}
```

Use `null` for an absent old or new value. Multiple changes to the same rule
must form a contiguous old-to-new chain.

## `model`

### Complete regular case

Use when there are exactly as many regular designs as tray positions and every regular design appears once:

```json
{
  "type": "unique_regular",
  "designs": ["A", "B", "C"],
  "hint_labels": ["A", "B", "C"],
  "hint_mechanism": {
    "type": "uniform_wrong_label",
    "status": "assumed"
  }
}
```

The order of `designs` has no statistical meaning.
`hint_mechanism.status` defaults to `assumed`; use `confirmed` only when the
platform behavior has been reliably confirmed. Unsupported mechanisms are
rejected rather than approximated silently.

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
| `只冲最爱` | `top_target_first` | Ordered `liked`, or complete scores with at least one `+10` for calibration |
| `保值优先` | `resale_ev` | Complete current `market_values`; complete `scores` for calibration |

Prefer `strategy` in new state files. `objective_mode` remains supported for
backward compatibility. The solver rejects conflicting values.

### Scores and hard limits

- `scores`: satisfaction score from `-10` through `+10` by design.
- `score_default`: score assigned to every unlisted design once at least one
  explicit score exists.
- `score_default_confirmed`: set to `true` only after the user explicitly
  confirms that every unlisted design shares `score_default`. Required by
  `--calibrate-preferences` when the default actually fills designs.
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
corresponding derivation for backward compatibility. `score_default`
participates after it fills unlisted designs. New score-first sessions should
prefer complete `scores`; never set a default merely because the user omitted
items. The normalized solver output exposes all seven groups in
`preference_summary.score_tiers` and records explicit versus score-derived
sources.

`scores` and `hard_avoid` solve different problems: scores rank trade-offs;
the hard limit blocks compensation beyond the user's stated boundary. A hard
score does not create a probability limit; `hard_avoid_max_pp` remains explicit.

For score-derived `只冲最爱`, equal highest-score designs are one target group:
all `+10` designs are maximized by combined probability. Supplying `liked`
explicitly preserves its ordered, one-design-at-a-time legacy meaning and is
therefore rejected by the score-first calibration entry point.

### Preference calibration

Before asking the user to invent probability thresholds, run the active tray
with complete scores:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --calibrate-preferences --format markdown
```

The calibration output contains:

- score coverage and all seven derived tiers;
- current-tray attainable ranges for favorite, liked, disliked, hard-avoid,
  and expected score;
- every drawable box's metrics and the non-dominated frontier;
- up to three candidate boundary bundles anchored to actual boxes;
- `stop_rules_mutated: false` and `confirmation_required: true`.

All guided A–E goals use this score-first gate. `保值优先` additionally
requires finite, non-negative, same-basis CNY `market_values` for every regular
design. Its report adds attainable expected resale value and proposes
`min_resale_ev` together with personal-score and risk boundaries.

These are direct drawable-box metrics before new tool outcomes. Tool planning
runs only after the user confirms boundaries; a random card branch does not
define the user's risk preference.

Candidate probability lines are rounded outward to whole percentage points so
their reference box still passes. They are proposals, not score-derived facts.
Expected scores round down to one decimal and expected resale values down to
whole CNY for the same reason. Do not copy a bundle into `stop_rules` until the
user selects scheme 1/2/3 or edits its numbers. A selected bundle applies to
the current series session;
recalibrate after a series change or material score change.

Calibration requires every design to be scored. A partial score map fails
closed. `score_default` may fill the rest only with
`score_default_confirmed: true`. Existing explicit stop rules remain valid and
are shown without being overwritten.

### Stopping conditions

`stop_rules` may contain:

- `min_like_any_pp`: stop if the best box's any-liked probability is lower.
- `min_favorite_any_pp`: stop if the best box's combined probability across
  all designs scored exactly `+10` is lower. Requires at least one `+10` score.
- `max_dislike_any_pp`: stop if its any-disliked probability is higher.
- `max_hard_avoid_pp`: stop if its hard-avoid probability is higher.
- `min_expected_score`: stop if its expected score is lower.
- `min_resale_ev`: stop if its probability-weighted expected resale value is
  lower; requires complete `market_values`.
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

`min_tool_uplift_pp` is the minimum normalized primary-strategy improvement
required to spend a card when direct draw already passes every stop rule. It
defaults to `tie_tolerance_pp`; set it explicitly to `0` for strict
mathematical maximization. Rescue cards that create a nonzero qualifying draw
branch remain eligible when direct draw fails.

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
| `phase` | Next incomplete phase: `parse`, `clarify`, `goal`, `preferences`, `commitment`, `tools`, `calibration`, `contract`, or `ready`. Legacy `risk` means the same pending calibration step. |
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

### Accept or release a tray

When every configured quality line passes and the user chooses to keep the
tray, set `accepted_tray_id` and append `tray_accepted`. Before changing
`active_tray_id` to another tray, append `tray_released` with the confirmed
reason and clear `accepted_tray_id`.

### Change a stopping condition

Update the session-level `preferences.stop_rules`, then append
`stop_rule_override` with the exact old/new values and reason. The event is
part of the actual ledger and is repeated in `session_review`.

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
