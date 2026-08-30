# Final draw review and skill evaluation

Use this file after the user reports the purchased/opened result, or when validating a change to this skill.

For a whole-session closing review, run `--review-session` first (template N
in `references/output-templates.md`): the program replays the event ledger and
produces every per-event number in this file's structure. Use the sections
below to interpret it and to decide preference/skill updates; never recompute
the numbers by hand.

## 1. Separate three judgments

Never collapse these into one verdict.

### Outcome quality

Was the realized design personally good, neutral, or bad for the user?

### Decision quality

Given only the information available before opening, did the selected box and tool policy follow the declared objective and beat the available alternatives?

### Model quality

Were the tray rules, clue semantics, screenshot extraction, market data, and preference representation correct enough for the reported precision?

A good decision can produce a bad outcome. A lucky result can come from a poor decision.

## 2. Required review structure

### Executive conclusion

The first paragraph must state:

- whether the decision was reasonable under the declared objective;
- whether the realized result was good, neutral, or bad;
- the pre-draw probability of the actual design;
- whether any systematic skill issue was found.

### Ex-ante probability of the actual result

Report:

- `P(actual design in chosen box)` before opening;
- its rank among all possible designs in that box;
- probability of the user's broader outcome class:
  - any liked design;
  - any disliked design;
  - neutral design;
- failure probability that was explicitly accepted before drawing.

Use calibrated language:

- common outcome: roughly 10% or above;
- moderately unlikely: roughly 3%–10%;
- rare: roughly 1%–3%;
- very rare: below roughly 1%.

These labels are descriptive, not hard statistical laws. Always show the exact probability.

### Decision comparison

Compare the chosen box with the strongest alternatives at decision time:

| Choice | Top-liked | Any liked | Disliked 1 | Disliked 2 | Any disliked | Why it ranked here |
|---|---:|---:|---:|---:|---:|---|

State whether the chosen box was:

- uniquely optimal;
- practically tied within the equivalence band;
- defensible but not optimal;
- inconsistent with the declared objective.

Do not judge using information learned only after opening.

### Tool-by-tool review

For every hint/display card:

- box targeted;
- observed result;
- expected information value before use, when available;
- actual change in ranking or probabilities;
- whether the card was decisive, useful but non-decisive, redundant, or harmful only because the user changed objectives afterward.

A clue cannot be called a bad use merely because its random output was unhelpful. Review the ex-ante target choice.

### Stop-rule review

Check:

- planned maximum number of draws;
- planned tool budget;
- accepted and released tray events;
- every stop-rule override, including old/new values and confirmed reason;
- whether the user changed the objective after a loss;
- whether another draw was justified by updated information or driven by sunk cost;
- whether the reported success/failure probability was understood.

Explicitly flag gambler's-fallacy reasoning such as “the previous box missed, so the next is due.”

## 3. Preference-model update

The most common real failure is treating unspecified designs as neutral when they are actually disappointing.

After the result, ask or infer only when evidence is clear:

- Did a nominally neutral design feel like a mild dislike?
- Are some liked designs much more valuable than the rest?
- Does the user care about resale value only for neutral/disliked outcomes?
- Is avoiding the top disliked design a hard constraint or merely a strong preference?

For future runs, capture complete per-design scores first and derive
`硬雷 / 轻雷 / 中性但失望 / 可接受 / 喜欢 / 最爱`. Re-run current-tray
preference calibration when scores materially change. Do not infer a new
probability boundary from one realized result. Read
`references/preference-strategies.md`.

## 4. Skill-change decision

### Change the skill when

- a reproducible calculation error exists;
- the platform's actual tray or tool rule contradicts the modeled rule;
- screenshot parsing repeatedly misses the same layout pattern;
- the output omitted a decision-relevant probability or full option list;
- the objective-mode mapping systematically contradicts the user's stated intent;
- tool planning is biased by an unsupported hint-generation assumption;
- a test fixture reproduces the failure.

### Do not change the skill merely because

- one reasonable draw missed;
- a low-probability item occurred;
- the best box only had a modest edge;
- a randomly generated hint was unhelpful;
- a counterfactual alternative happened to contain the desired item.

## 5. Proposed change format

When a change is justified, provide:

| Field | Required content |
|---|---|
| Observed failure | Exact behavior that was wrong or misleading |
| Root cause | Parsing, state, model, objective, market data, output, or user input |
| Proposed change | Smallest concrete modification |
| Expected benefit | Which future decision improves |
| Regression risk | What could become worse |
| Test | A reproducible state and expected result |
| Version | Suggested semantic version change |

Prefer a narrow patch over rewriting the whole skill.

## 6. Regression evaluation suite

Run these checks after modifying the skill or solver.

### E1 — Screenshot semantics

A grid headed by “不是” must be parsed as exclusions, never positive candidates.

### E2 — Sold unknown remains latent

A sold box is absent from the purchase ranking but remains in the complete-case matching count.

### E3 — Known result updates globally

When one box is confirmed as design A, every other box has posterior probability 0 for A in a no-duplicate scenario.

### E4 — Risk-first ranking

A box with lower severity-weighted disliked risk should beat a box with higher liked probability but materially worse disliked risk, unless the user switches modes. A zero probability for a high-ranked dislike should materially improve that risk score.

### E5 — Objective switch

The same tray may produce a different recommendation under `risk_first`, `target_only`, and `top_target_first`. The response must state that the objective changed.

### E6 — One-tool-per-box

No hint or display action may target a box with `tool_used: true`.

### E7 — Counterfactual isolation

A hypothetical clue must not mutate the confirmed state.

### E8 — Complete TOP 3 distributions

Each top-three table includes every not-explicitly-excluded label; globally impossible labels are shown at 0% with a note, and probabilities sum to approximately 100%.

### E9 — Secret-model honesty

Default reviews use the one-line regular-only note. Only when the user explicitly requested a hidden mixture, exact hint-card value planning must be blocked or modeled with the correct observation likelihood.

### E10 — Market-data honesty

Listing prices are labeled as listings, current claims are cited, sparse samples have low confidence, and no price is fabricated.

### E11 — Conversation regression fixture

Run `tests/test_solver.py`. The included synthetic fixture must retain its known exact assignment counts and posterior probabilities unless a deliberate model change explains the difference.

### E12 — Plain-language strategy mapping

All six Chinese strategy names map to the intended internal objective. Legacy
English state files and prior Chinese aliases remain accepted.

### E13 — Scores and hard limits

Changing explicit scores can change the selected box. `守住底线` never
recommends a direct draw when every box exceeds the hard-avoid limit, and tool
planning enforces the limit separately in every outcome branch.

### E14 — Stopping conditions

Every configured condition is checked against the current best box after each
real event. Reaching `max_draws` or failing any probability/score threshold
returns a clear stop recommendation.

### E15 — No-card action

Direct draw or stop participates in the same ranking as hint/display actions.
The no-card action wins numerical zero-uplift ties, and it remains the sole
recommendation when no cards are available.

### E16 — Optional two-step planning

Depth two keeps direct draw or stop at both layers, never targets a used box,
returns only the first executable action, reports action identity separately
from terminal equivalence, prefers fewer expected cards for equivalent
terminal policies, and labels terminal gain as two-card versus one-card
horizon rather than as proof against rolling one-step replanning.

### E17 — Favorite stop line

`min_favorite_any_pp` checks the combined probability of every `+10` design
independently from the broader liked threshold. It never changes box ranking,
requires at least one `+10` score, and is enforced in direct, tool, and
depth-two branches.

### E18 — Timed tray screening

`--screen-tray` uses exact posterior metrics and configured quality lines,
never raw exclusion count. It distinguishes direct-ready, tool-dependent,
switch, session-stop, and missing-rule states; `max_draws` cannot be fixed by
switching trays. The fast path uses depth one and does not claim the next tray
will be better without empirical tray-state data.

### E19 — Practical card spending

When direct draw passes every stop rule, a card below
`min_tool_uplift_pp` cannot beat no card. When direct draw fails, a card with a
nonzero fully qualifying branch remains eligible as a rescue route. Report
primary uplift, threshold result, and expected cards used.

### E20 — Accepted-tray lock and overrides

An accepted tray remains active until `tray_released` records a concise
reason. Switching without release fails closed. The complete session report
shows the current lock, acceptance lifecycle, and every stop-rule override;
the latest override value matches session preferences.

### E21 — Conditional-probability warnings

Every report exposes model scope, conditional assumptions, and hint-mechanism
status. Regular-only analysis emits `regular_only_scope`. A card plan using an
unconfirmed uniform wrong-label mechanism also emits
`hint_mechanism_assumed`; confirmed mechanisms clear only that warning.

### E22 — Standard report handoff

Initial recommendations, kept trays, real clue updates, and post-open
continue/stop decisions run `blindbox_solver.py --format markdown` and return
its validated stdout unchanged. The final reply contains the conclusion,
three concise explanations, TOP 3 full probability matrix, all stop lines,
next action, and model scope without a second manual summary.

Run `tests/test_agent_report_contract.py` with
`tests/fixtures/agent-report-regressions.json`. Its synthetic short turns cover
“重来，需求不变”, a new exclusion, a changed leading box, and no qualifying
box. The regression must consume each short message, verify the resulting
state transition or preserved requirements, then reach the same complete user
report without requiring the user to ask for “所有选项”. Before release, also
give an independent Agent only this Skill, a synthetic prior state, and one
short update; its reply must be the validated report without a manual wrapper.

### E23 — Score-first preference calibration

`--calibrate-preferences` requires complete scores, derives all seven tiers,
and requires `score_default_confirmed: true` when a default fills designs. It
shows current-tray attainable ranges, every drawable box, the non-dominated
frontier, and concrete candidate boundary bundles. The calibration output has
no draw/card recommendation, leaves `stop_rules` unchanged, and requires user
confirmation before a bundle is written. Score-derived `只冲最爱` uses the
combined probability of equal `+10` designs; explicit ordered targets remain a
backward-compatible non-calibration path. Exercise every guided A–E goal.
`保值优先` must fail closed on incomplete market coverage and, when complete,
add expected resale value plus `min_resale_ev`, personal-score, and risk
boundaries. Numbered calibration schemes must not reuse the A–E goal letters.

### E24 — Zero-tray preference briefing

`--brief-preferences` accepts only zero-tray briefing states (`regular_count`
plus preferences, no trays or boxes) and fails closed on incomplete design or
score coverage, unconfirmed defaults, mixture models, or any duplicate JSON
key. The report is the uniform complete no-duplicate blind baseline, carries
`current_tray_not_observed`, and never claims current-tray attainability.
Reference lines appear only for `随便中个喜欢` with a non-empty liked group and
use five-point steps that strictly improve the baseline, with 40% / 35% / 20%
balanced anchors for weak baselines; stop rules stay unmutated with
`confirmation_required: true`, and
explicit preference fields (including `explicit_score_tiers`) contradicting
score tiers are listed with
`confirmation_required`, and reference lines stay blocked. Run
`tests/test_preference_briefing.py`.

### E25 — Multi-tray comparison

`--compare-trays` requires a session envelope with at least two observed trays
from the same declared series and refuses planning, screening, calibration,
and briefing modes. Each
tray is solved independently — box ids, exclusions, sold-unknown boxes, and
posteriors never cross trays — and the row metrics equal a solo run of the
same tray. Released, `participation: "history"`, and zero-drawable trays are
excluded from ranking and listed for review only. The table shows tray ID,
best box, liked / any-disliked / hard-avoid metrics, status, first tool
action, and the qualifying-branch probability labeled as not a win rate.
Ranking reuses the existing strategy ordering and status precedence
(ready < tool_dependent < switch < needs_acceptance_rules < session_stop)
with tool-dependent trays ordered by qualifying-branch probability and
fewer expected cards inside the practical tolerance; no new implicit
strategy appears. Adding a second tray mid-session preserves preferences,
quality lines, global tools, draws used, and prior tray evidence. Depth two
is gated on at least two remaining cards, expands only the head candidates,
and reports whether the first action changed. The validator rejects dropped
or reordered rows (including strategy-order violations inside `ready`), rank
gaps, non-top recommendations, guaranteed-improvement
claims, win-rate semantics, and malformed depth-two sections. Run
`tests/test_tray_comparison.py`.

### E26 — Candidate-tray commitment and lossless upgrade

Session trays move through a three-state ladder — open (未承诺) → candidate
(候选承诺) → accepted (已接受) — and both locks live above the append-only
ledger. Selecting a tool-dependent tray in a comparison records a candidate
commitment (`tray_committed`) instead of pretending it directly qualifies.
Multi-tray actions require that explicit event. Only a single-tray session may
auto-insert `tray_committed` immediately before its first real action
(`first_tool_or_open`). Once real clues push every quality line past its
threshold, `tray_accepted` is added, `accepted_tray_id` is persisted, and the
candidate is cleared (`quality_lines_upgrade`). Explicit acceptance remains
the `explicit_event` path.
Both locks gate switching — a reasoned `tray_released` event must precede any
`tray_switch` away, and silent switches, releases without a reason,
re-committing the locked tray, and actions on other trays while locked all
fail closed. Preferences, quality lines, tool inventory, draw counts, and
event order stay identical across commitment, upgrade, release, and
switching, and counterfactual planning never mutates the real commitment.
Legacy single-tray states with used tools or opened boxes normalize straight
to candidate commitments without migration, and existing `accepted_tray_id`
sessions keep reading unchanged. Standard reports and screening surface the
phase in plain language (screening uses `candidate_review` /
`accepted_review` with `release_before_switch`), and the validators reject
tampered lifecycle blocks, mismatched locks, and dropped comparison
commitment steps. Run `tests/test_tray_commitment.py`.

### E27 — Whole-session automatic review

`--review-session` generates the closing review by deterministically
replaying the append-only event ledger — rewinding the final trays
(un-opening boxes, un-using cards, restoring pre-override stop rules and the
consumed card inventory), then re-applying every event in `seq` order with an
ex-ante snapshot before each card and opening. It cannot be combined with
planning flags, `--screen-tray`, `--calibrate-preferences`, `--compare-trays`,
or `--brief-preferences`, and a zero-tray briefing is rejected. Every opening
reports the actual design's ex-ante probability and rank among possible
designs, the ex-ante liked / neutral / disliked (hard-avoid) class
probabilities, the accepted failure probability under the declared objective,
the quality lines as they stood (including pre-override thresholds), and the
decision-time comparison with the strongest alternative — post-opening
information never judges a choice. Every card reports its real result, the
ex-ante still-drawable branch probability, the declared strategy's real primary
metric before/after, ranking change, draw/stop decision change, and the
remaining count. The lifecycle section lists every switch, commitment
(explicit or single-tray `first_tool_or_open`), persisted
`quality_lines_upgrade` acceptance, explicit acceptance, release, and
stop-rule override with old value, new value, contiguous order, and reason.
The report separates decision, outcome,
and model quality, flags sunk-cost and gambler-fallacy patterns from ex-ante
information only, cross-checks remaining cards / draws used / draw cap against
the ledger, and ends with the declared model scope. Legacy states without an
event ledger mark openings, cards, per-draw quality lines, and overrides as
`legacy_state_without_event_ledger` 不可恢复 — no number is fabricated. The
validator rejects fabricated legacy numbers, broken class partitions,
rank/prior disagreements, missing strongest alternatives, branch-null
mismatches, counter divergence, unknown verdicts, descending or duplicated
lifecycle entries, and decision-quality divergence. Run
`tests/test_session_review.py`.

## 7. Review output template

For a single-draw follow-up review, fill in this shape from the standard
report. For the whole-session closing review, relay the `--review-session`
renderer output (template N) unchanged instead of this hand-filled skeleton.

```markdown
## 复盘结论

**决策：合理 / 基本合理 / 有偏差。结果：喜欢 / 中性 / 不喜欢。**
实际款在开盒前的概率为 X%，在该盒所有可能款中排第 N。

## 事前概率

| 指标 | 概率 |
|---|---:|
| 实际款 | ... |
| 任一喜欢款 | ... |
| 任一不喜欢款 | ... |
| 中性款 | ... |

## 决策与备选

[比较所选盒和最强备选，不使用开盒后的信息。]

## 道具使用

[逐张区分 ex-ante 价值和随机结果。]

## 承诺与止损线变更

[列承诺/接受/释放端及其理由（含候选承诺的自动记录与达线升级），以及每次 stop_rule_override 的旧值、新值和理由。]

## 真正需要更新的地方

- 偏好：...
- 模型：...
- 输出：...
- Skill：保持 / 小改 / 版本升级，理由...
```
