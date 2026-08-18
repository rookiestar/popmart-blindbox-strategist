# Final draw review and skill evaluation

Use this file after the user reports the purchased/opened result, or when validating a change to this skill.

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

For future runs, capture `硬雷 / 轻雷 / 中性但失望 / 可接受 / 喜欢 /
最爱`, then add per-design scores when a boundary decision is sensitive to
how far apart the designs feel. Read `references/preference-strategies.md`.

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
returns only the first executable action, reports whether depth one's first
action remains optimal, and labels terminal gain as two-card versus one-card
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

## 7. Review output template

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

## 真正需要更新的地方

- 偏好：...
- 模型：...
- 输出：...
- Skill：保持 / 小改 / 版本升级，理由...
```
