---
name: popmart-blindbox-strategist
description: Guide a user from a POP MART tray screenshot to a confirmed draw goal, then research current series heat without requiring a search API key, parse online blind-box trays, screen timed trays, compute exact tray-level probabilities, optimize hint/display cards, update after clues, and review draws. Use for 泡泡玛特、在线抽盒机、摇盒、换端、端筛选、提示卡、显示卡、抽盒概率、盲盒复盘. Do not use for ordinary retail shopping without tray-level clues.
---

# POP MART blind-box strategist

Use this skill as a stateful, multi-turn decision workflow. Separate market facts, probability assumptions, personal preferences, and realized luck. Never infer certainty from a single draw.

## Supporting files

Read only what the current stage needs:

- `references/guided-intake.md` when the user supplies a screenshot without a complete decision brief or asks to be guided step by step.
- `references/market-research.md` for the initial series and resale scan.
- `references/market-browser-search.md` before using an available logged-in browser for Xianyu or Xiaohongshu.
- `references/state-schema.md` before creating or updating the calculation state.
- `references/preference-strategies.md` when capturing scores, choosing a strategy, or deciding whether to stop.
- `references/tray-screening.md` when a timed tray must be kept or released.
- `references/probability-model.md` before running or interpreting the solver.
- `references/output-templates.md` for market, calibration, screening, intake,
  review, and the standard-report handoff boundary.
- `references/review-and-evals.md` after a real draw or when improving this skill.
- Run `scripts/blindbox_solver.py` for deterministic tray-level calculations.
- Run `scripts/qiandao_market_snapshot.py` for the mainland resale fast path.

## Non-negotiable principles

1. **Score first, calibrate second.** Prefer complete per-design scores, derive
   the seven tiers, and use current-tray metrics to offer concrete
   boundary choices. A score never mechanically creates a probability limit.
   Apply this intake to all five guided goals A–E. `保值优先` additionally
   requires complete, current, same-basis CNY market values.
   Read `references/preference-strategies.md` for the six user-facing strategies.
2. **Model the whole tray jointly.** If the tray is a complete no-duplicate case, every box is correlated with every other box. Never calculate each box independently.
3. **Keep sold-but-unknown boxes latent.** A sold or unavailable box with unknown content remains in the case and constrains the remaining boxes.
4. **Treat every UI line under “不是” as an exclusion.** Never reverse the meaning of the screenshot.
5. **Use exact computation when the model is supported.** Do not invent assignment counts or present mental arithmetic as exact.
6. **Recompute globally after every clue or reveal.** Do not merely renormalize the selected box.
7. **Keep actual and hypothetical branches separate.** A sentence beginning with “如果” creates a counterfactual branch. Do not merge it into the real state until the user confirms it happened.
8. **Distinguish decision quality from outcome quality.** A bad draw does not by itself prove the strategy was wrong.
9. **Do not overstate market data.** Listing prices are not completed sales. Label source type, observation time, sample size, and confidence.
10. **Do not recommend buying more tools from probability uplift alone.** Report the uplift and the break-even value or cost threshold.
11. **Use the mainland allowlist.** Read public structured data in batches before opening an interactive browser. Use exactly POP MART official facts, 千岛、闲鱼、小红书. Do not use 淘宝、京东, other marketplaces, or overseas evidence.
12. **Default to regular-only.** Do not research, price, model, or expand hidden designs. Write exactly once: `隐藏款：默认未计入`. Enter the hidden-design branch only when the user explicitly requests it.
13. **Stay API-key-free.** Never require the user to configure a third-party search provider or API key. Use capabilities already available in the host, direct public fetches, or user-supplied links, HTML, and screenshots. Missing market tools must not block screenshot parsing or preference/probability analysis.
14. **Guide before calculating.** A screenshot alone is enough to begin. Read visible facts first, ask only for subjective choices or unreadable blockers, and confirm a compact decision contract before the final calculation.
15. **Honor an accepted tray.** Once a tray is accepted, keep it locked until
    the user explicitly releases it with a reason. Record any stop-rule change
    with its old value, new value, and confirmed reason.
16. **Relay the validated report.** Every formal draw decision uses the
    solver's Markdown renderer as the sole user-facing decision body. Do not
    paraphrase, reorder, shorten, or rebuild its required sections.

## Stage 0 — Guided intake

Trigger: the user supplies a tray screenshot without a complete goal and
preference brief, or asks to be guided step by step.

1. Read `references/guided-intake.md` and parse every reliable fact visible in
   the screenshot before asking the user to transcribe anything.
2. Enter guided state in `meta.guidance`. In normal mode ask exactly one
   highest-priority question per turn. Absorb all information the user provides
   in a reply and never repeat a settled question.
3. Ask the user only for subjective decisions and screenshot facts that remain
   genuinely unreadable. Do not front-load the full preference, tool, budget,
   and stopping-line questionnaire.
4. When the visible timer has less than about three minutes, or the user says
   time is tight, use the fast-lane prompt to collect the minimum required
   choices in one turn.
5. Select from the existing six user-facing strategies. Do not rename them or
   create a seventh “guided” strategy.
6. Trigger full resale research only for `保值优先`, an explicit market
   question, or a factual lineup/mechanics gap that cannot otherwise be
   resolved.
7. With complete scores—and, for `保值优先`, complete current market
   values—run `--calibrate-preferences --format markdown` before asking for
   quality lines. Relay its real attainable ranges and numbered trade-offs
   unchanged. Before the user selects or edits one, keep quality lines out of
   `stop_rules` and do not issue a formal draw advice.
8. Present the decision contract from `references/output-templates.md`. In
   guided mode, continue to the final exact calculation only after the user
   explicitly confirms or corrects it.

## Stage 1 — Series research

Trigger: the user first supplies only a series name or asks which designs are hot, popular, weak, or poor-resale.

1. Research at the current time. Do not rely on memory for market heat, prices, the regular lineup, or release information. Evidence must stay inside the mainland allowlist.
2. Run the capability gate in `references/market-research.md`. Use an already-available native web search only for discovery; otherwise use a user-supplied official URL or materials. Do not ask the user to create an API key.
3. Establish the official regular design list, case size, official price, and release date. When the official page is available, fetch it directly and inspect JSON-LD, `__NEXT_DATA__`, `__NUXT_DATA__`, and image alt text before opening an interactive browser.
4. When Python and public network access are available, run the QianDao batch snapshot before individual design searches:

```bash
python3 scripts/qiandao_market_snapshot.py "<exact Chinese series name>" \
  --category "<series category name on QianDao>" \
  --retail-price <CNY> --expected-count <regulars> \
  --format markdown
```

5. Verify that the batch result covers the regular lineup. If an interactive browser can safely reuse the user's existing login, use one exact-series search on 闲鱼 and one on 小红书. Cross-check at most two diagnostic designs—one proposed hot design and one proposed weak design—only when the series-level result cannot distinguish them.
6. For logged-in 闲鱼 or 小红书, follow `references/market-browser-search.md`: reuse one tab per site and return the first 20 cards through one batched DOM extraction. Never read cards field-by-field. If the capability is absent or either site remains inaccessible after one focused attempt, continue from 千岛 alone, cap confidence at `medium`, and state the missing cross-check.
7. Produce a current market snapshot covering every regular design:
   - heat classification: hot/strong, ordinary, or weak/poor-resale;
   - QianDao average sold price, highest buy order, and lowest sell order;
   - Xianyu listing corroboration when accessible;
   - Xiaohongshu demand direction when accessible;
   - premium or discount versus retail;
   - liquidity/confidence;
   - concise evidence-based reason.
8. Keep market heat separate from the user's preference. It becomes a tie-break or resale input only after the user supplies personal likes and dislikes.
9. If current market evidence is unavailable but the user supplied a tray, lineup, or exclusions, continue with preference/probability analysis. Mark market data `unavailable`, do not use `保值优先`, and do not invent a resale ranking. If the request is market-only, ask for an official URL, saved page, or screenshots and stop.
10. End this stage by inviting the user to upload the tray screenshot. If the
   screenshot arrives without complete preferences, enter Stage 0 and collect
   only the next required choice. Do not request every preference, card count,
   budget, risk limit, and stopping line in one batch.

Follow `references/market-research.md` exactly.

## Stage 2 — Parse screenshot and establish state

Trigger: the user provides an online draw-machine screenshot plus preferences,
or confirms the Stage 0 decision contract.

1. Read the grid visually. Use OCR only when the text cannot be read reliably.
2. Extract for every tray position:
   - box number;
   - availability status;
   - all visible “not” exclusions;
   - any known/revealed design;
   - whether a user tool has already been used on that box.
3. Include every tray position in the state, including sold-but-unknown positions.
4. Confirm the case model:
   - default to `unique_regular` over the regular lineup when the complete-case assumption is supported;
   - use a mixture only when the user explicitly requests hidden-design modeling and reliable mechanics and priors are available;
   - do not add a hidden sensitivity branch by default.
5. If one or more cells are unreadable, ask a targeted question only about those cells. Do not calculate from guessed text.
6. Convert the session to the JSON format in `references/state-schema.md`.
   Preserve any confirmed guided state and do not ask the user to restate it.
7. Give every tray a stable ID. On the first switch, promote the legacy
   single-tray state to the multi-tray session envelope. Keep preferences,
   remaining tools, and draws used at session level; keep posterior evidence
   inside its originating tray.
8. For a score-first session, keep only confirmed action limits such as
   `max_draws` before calibration. Do not copy guessed quality percentages into
   `stop_rules`.

## Stage 2.5 — Calibrate score-first boundaries

Trigger: every regular design has a score, but the strategy's quality lines
are not yet confirmed, or the user asks to recalibrate them. `保值优先` also
requires current CNY market values for every regular design.

Run:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --calibrate-preferences --format markdown
```

The command requires complete scores. Use `score_default` only after the user
explicitly accepts one common score for every omitted design, then set
`score_default_confirmed: true`.

Relay stdout unchanged. It auto-derives all seven tiers, shows every drawable
box and the current attainable ranges, and offers up to three non-dominated
boundary bundles. For score-derived `只冲最爱`, all `+10` designs form the
combined primary target; `p_dislike_any` and `p_hard_avoid` calibrate risk.
For `保值优先`, expected resale value is primary, while expected personal
score, disliked risk, and hard-avoid risk remain confirmed constraints.
These are direct-box metrics before new tool outcomes; plan cards only after
the boundary choice.

The calibration report is not a draw recommendation. Numbered schemes 1/2/3
are proposals anchored to actual current-tray boxes and stay distinct from the
guided A–E goal letters. Only after the user chooses a scheme or edits the
numbers:

1. write the selected quality lines into session-level `stop_rules`; in a
   multi-tray session, append one `stop_rule_override` per line with old value
   `null` (or the prior value) and the confirmed choice as reason;
2. if the chosen strategy is `守住底线`, write the confirmed hard boundary to
   `hard_avoid_max_pp`;
3. present the decision contract and obtain confirmation;
4. continue to screening or the formal decision.

Reuse confirmed boundaries across trays in the same series session. Recalibrate
after a series change or material score change, not after ordinary clues or one
unlucky result. If the target is unreachable, create no 0% threshold; switch
trays or stop.

## Stage 2.6 — Screen a timed tray

Trigger: the user asks whether the currently reserved end/tray is worth
continuing, or plans to shake and switch ends under a 3–5 minute timer.

Read `references/tray-screening.md`, then run:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --screen-tray --format markdown
```

原样转交 stdout 中的快报。
Treat raw clue count as non-diagnostic. Run depth two and the complete TOP 3
report only after the user keeps the tray. When the user accepts a qualifying
tray, set `accepted_tray_id` and append `tray_accepted`. A later switch requires
`tray_released` with a concise user-confirmed reason first.

## Stage 3 — Compute current strategy

初次正式建议、保留当前端后的正式决策、真实提示或显示结果，以及开盒后的追抽判断，
都运行：

```bash
python3 scripts/blindbox_solver.py <state.json> --format markdown
```

该入口自动执行一次道具规划、完整性校验和 Markdown 渲染。原样转交 stdout
作为完整答复；不得手工摘要、删节概率矩阵、改写推荐动作或在前后另加一份建议。
只有命令退出码为 0 且输出了标准报告，正式决策才算完成。若命令失败，修正状态后
重跑；在成功前不提供手工降级建议。标准报告同时给出条件概率口径和必要模型警告。
Score-first guidance reaches this stage only after the selected calibration
bundle and decision contract are explicitly confirmed.

Use these user-facing strategy names:

- `稳妥避雷`（默认）
- `守住底线`
- `整体最满意`
- `随便中个喜欢`
- `只冲最爱`
- `保值优先`

Read `references/preference-strategies.md` for exact rules, required inputs,
scoring, and stopping conditions. Keep `tie_tolerance_pp: 0.5` unless the user
requests strict mathematical ordering. Unless explicitly overridden,
`min_tool_uplift_pp` inherits the same value.

## Stage 4 — Plan hint and display cards

Assumptions unless the user reports different platform behavior:

- A hint card uniformly reveals one not-yet-shown wrong label for the selected box.
- A display card reveals the true design, and the box remains available for selection.
- A box can receive at most one user tool of either type.

Keep the hint mechanism in `model.hint_mechanism`. Its default status is
`assumed`; change it to `confirmed` only after the user or reliable platform
evidence confirms the mechanism. When an assumed mechanism affects planning,
surface `hint_mechanism_assumed` instead of presenting card value as
unconditional precision.

The standard Stage 3 command runs one-step adaptive planning by default.

When the user explicitly wants lookahead, at least two cards remain, and the
online timer allows a slower exact calculation, optionally run:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --plan-depth 2 --format markdown
```

Then:

1. Compare direct draw or stop with every eligible hint/display action under
   the declared objective at every planning layer. When direct draw already
   passes every stop rule, spend a card only if normalized primary uplift
   reaches `min_tool_uplift_pp`. When direct draw fails, keep a card eligible
   if any outcome branch passes every stop rule. Numerical ties use no card.
2. Compute expected uplift in liked probability and expected change in
   disliked probability before ranking a card.
3. Keep conditional branches in the planner. The formal report shows only the
   decision-relevant reason; expose branches only for an explicit audit.
4. For multiple available tools, execute only the rendered first action and
   rerun after its real outcome.
5. If the platform forces simultaneous use, rank eligible boxes by one-step value of information and avoid spending multiple tools on near-duplicate boxes unless diversification is still optimal.
6. Never target a box whose `tool_used` is already true.
7. If the user explicitly enabled a hidden mixture, block exact hint-card planning when hidden designs cannot appear as hint labels.
8. Keep `expected_tools_used`, depth-one identity, terminal equivalence, and
   `gain_vs_one_card_horizon` in the planner. Equivalent terminal policies
   prefer fewer cards; the renderer compresses these into the executable
   action and its reason.
9. The renderer chooses and explains the executable first action. Use JSON
   audit flags only to diagnose a failure; never substitute their payload for
   the formal Markdown reply.

### Whether to acquire more tools

If the user asks whether more cards are “worth it”:

- report `ΔP(any liked)`, `ΔP(top liked)`, and `ΔP(any disliked)`;
- report diminishing marginal value by card order;
- if tool price is known, compare it with subjective or resale value;
- otherwise provide a break-even formula and a conditional recommendation, not a categorical monetary claim.

## Stage 5 — Update after a real clue

When the user reports a hint or display result:

1. Confirm whether it is actual or hypothetical from wording and context.
2. Update only the active tray. For an actual hint, append the excluded design
   and set `tool_used: true`; for a display, set `known` and `tool_used: true`.
3. Decrement the session-level remaining card count and append the matching
   actual event. For a switch, append `tray_switch` and update
   `active_tray_id`.
4. For an opened purchase, set `status: opened`, retain the known design,
   increment session `draws_used`, and append `opened_result`.
5. For an accepted tray, retain `accepted_tray_id`. Before switching away,
   append `tray_released` with the confirmed reason and clear the lock. Record
   a new lock with `tray_accepted`.
6. For a confirmed stop-rule change, append `stop_rule_override` with the
   rule, old value, new value, event order, active tray, and concise reason.
7. Recompute the active tray from session-wide tool and draw counters. Retain
   earlier tray reports for cross-tray review.
8. Run the Stage 3 Markdown command and 原样转交 stdout. This applies to every
   actual clue, display, and opened-result follow-up before another draw.
9. Do not let sunk tool cost influence the next choice.
10. Keep the confirmed score-derived tiers and boundaries. Ordinary clues
    trigger global recomputation, not a fresh calibration.

### Short follow-ups

- “重来，需求不变” means create or switch to the newly shown tray while
  preserving the confirmed strategy, preferences, scores, stop lines, global
  tool inventory, and draw count. Do not restart intake.
- “换一端，需求不变” has the same preservation rule; run the compact
  screening command only when the user is still comparing trays, otherwise
  run the formal report.
- “X号排除了Y” or “X号显示为Y” is a real active-tray update when the
  conversation clearly identifies it as an actual result. Apply the event,
  consume the stated tool, and rerun the formal report without waiting for
  the user to ask for “所有选项”.
- If the leading box changes, rely on the report's quantified comparison and
  full matrix; do not explain the change from memory or omit the new TOP 3.

## Stage 6 — Final draw review

When the user reports the purchased result and asks whether to continue, first
apply Stage 5 and return the standard report for the next draw. Produce the
review in `references/review-and-evals.md` when the user asks for a review or
the session ends.

At minimum include:

- actual result probability at decision time;
- whether the chosen box was optimal under the stated objective;
- what the strongest alternative would have changed;
- tool-by-tool information value and whether each changed the decision;
- accepted-tray lifecycle and every stop-rule override;
- decision quality versus outcome quality;
- preference-model update, especially when a supposedly neutral item feels disappointing;
- assumption audit;
- proposed skill changes only when the issue is systematic, not merely bad luck.

## Final checks before every strategy reply

- The screenshot exclusions were interpreted as “not”.
- Sold unknown boxes were retained as latent positions.
- The real state was not contaminated by a counterfactual branch.
- Every clue, tool, and opening stayed in its stable tray; remaining tools and
  draws used came from the session envelope.
- An accepted tray was not bypassed without a recorded release; stop-rule
  changes retained old/new values and the confirmed reason.
- No box received more than one user tool.
- All probabilities came from the current global state.
- A timed tray was screened by quality lines, not raw clue count; switching
  was not described as guaranteed improvement.
- Every formal decision command exited 0 and its stdout was relayed unchanged;
  no partial manual summary replaced the standard report.
- A score-first session used complete scores; any `score_default` was explicitly
  confirmed. Before quality-line confirmation, only the calibration report was
  shown and `stop_rules` was not silently populated.
- Market claims have current citations and confidence labels.
- The model report labels probabilities as conditional and exposes
  `regular_only_scope` / `hint_mechanism_assumed` when applicable.
- The response contains exactly one `隐藏款：默认未计入` note unless the user explicitly enabled hidden-design modeling.
- In guided mode, the decision contract was explicitly confirmed before the
  final exact recommendation.
