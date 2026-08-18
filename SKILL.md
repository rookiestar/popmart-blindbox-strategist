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
- `references/output-templates.md` before replying with a strategy or update.
- `references/review-and-evals.md` after a real draw or when improving this skill.
- Run `scripts/blindbox_solver.py` for deterministic tray-level calculations.
- Run `scripts/qiandao_market_snapshot.py` for the mainland resale fast path.

## Non-negotiable principles

1. **Make the user's trade-off explicit.** Default to `稳妥避雷`; use scores and hard limits only when supplied. Read `references/preference-strategies.md` for the six user-facing strategies.
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
6. Run a preliminary calculation before asking for a hard-risk limit. Show the
   real attainable risk and ask whether the user accepts it; never silently
   apply a fixed probability cap.
7. Trigger full resale research only for `保值优先`, an explicit market
   question, or a factual lineup/mechanics gap that cannot otherwise be
   resolved.
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

## Stage 2.5 — Screen a timed tray

Trigger: the user asks whether the currently reserved end/tray is worth
continuing, or plans to shake and switch ends under a 3–5 minute timer.

Read `references/tray-screening.md`, then run:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --screen-tray --digits 10
```

Return the compact screening result and reusable acceptance lines first.
Treat raw clue count as non-diagnostic. Run depth two and the complete TOP 3
report only after the user keeps the tray.

## Stage 3 — Compute current strategy

Run:

```bash
python3 scripts/blindbox_solver.py <state.json> --digits 10
```

Use these user-facing strategy names:

- `稳妥避雷`（默认）
- `守住底线`
- `整体最满意`
- `随便中个喜欢`
- `只冲最爱`
- `保值优先`

Read `references/preference-strategies.md` for exact rules, required inputs, scoring, and stopping conditions. Keep `tie_tolerance_pp: 0.5` unless the user requests strict mathematical ordering.

### Required calculation output

Always provide:

- model assumptions and unresolved uncertainty;
- exact valid-assignment count for the regular-only scenario; show mixture weights only when the user explicitly enabled hidden-design modeling;
- current top-three boxes;
- for each top-three box, **every not-explicitly-excluded design sorted by posterior probability descending**;
- liked-item probabilities by rank, total liked probability, disliked-item probabilities by rank, and total disliked probability;
- a direct recommendation and the exact trade-off against the next-best alternatives;
- the strategy name and one-sentence rule;
- hard-avoid probability and expected score when configured;
- combined `+10` favorite probability when its stop line is configured;
- whether to draw again, with every failed stopping condition.

Percentages shown to the user should normally use two decimals, but calculations must use unrounded values. Confirm that each displayed full distribution sums to approximately 100%; explain any rounding difference.

## Stage 4 — Plan hint and display cards

Assumptions unless the user reports different platform behavior:

- A hint card uniformly reveals one not-yet-shown wrong label for the selected box.
- A display card reveals the true design, and the box remains available for selection.
- A box can receive at most one user tool of either type.

Run one-step adaptive planning by default:

```bash
python3 scripts/blindbox_solver.py <state.json> --plan-one --digits 10
```

When the user explicitly wants lookahead, at least two cards remain, and the
online timer allows a slower exact calculation, optionally run:

```bash
python3 scripts/blindbox_solver.py <state.json> --plan-depth 2 --digits 10
```

Then:

1. Compare direct draw or stop with every eligible hint/display action under the declared objective at every planning layer. Recommend a card only when it ranks above the no-card action; numerical zero-uplift ties use no card.
2. Quantify expected uplift in liked probability and expected change in disliked probability.
3. Show the important conditional branches: which outcomes make another box overtake the current leader.
4. For multiple available tools, give a provisional second/third priority but instruct that the calculation must be rerun after the actual first outcome.
5. If the platform forces simultaneous use, rank eligible boxes by one-step value of information and avoid spending multiple tools on near-duplicate boxes unless diversification is still optimal.
6. Never target a box whose `tool_used` is already true.
7. If the user explicitly enabled a hidden mixture, block exact hint-card planning when hidden designs cannot appear as hint labels.
8. For depth two, report only the first action, whether one-step's first action remains two-step-optimal, and `gain_vs_one_card_horizon`. Explain that this gain compares two-card versus one-card horizons; it does not prove rolling one-step replanning is worse.
9. The plan payload is compact by default: branches carry only the decision
   summary and `action_ranking` keeps the top 3 actions (the rest appear as
   `other_actions_ranked` one-line summaries). Depth two expands the second
   layer only for the top 3 depth-1 card actions; truncated actions are
   labelled `depth_evaluated: 1`. For a full audit payload rerun with
   `--full-branches --top-actions 0 --beam-width 0`.

### Whether to acquire more tools

If the user asks whether more cards are “worth it”:

- report `ΔP(any liked)`, `ΔP(top liked)`, and `ΔP(any disliked)`;
- report diminishing marginal value by card order;
- if tool price is known, compare it with subjective or resale value;
- otherwise provide a break-even formula and a conditional recommendation, not a categorical monetary claim.

## Stage 5 — Update after a real clue

When the user reports a hint or display result:

1. Confirm whether it is actual or hypothetical from wording and context.
2. For an actual hint, append the excluded design and set `tool_used: true`.
3. For an actual display, set `known` to the revealed design and `tool_used: true`.
4. For an opened purchase, set `status: opened` and retain the known design.
5. Recompute the entire posterior and tool plan.
6. Return the same required top-three output, including full sorted option distributions.
7. Do not let sunk tool cost influence the next choice.

## Stage 6 — Final draw review

When the user reports the purchased result, produce the review in `references/review-and-evals.md`.

At minimum include:

- actual result probability at decision time;
- whether the chosen box was optimal under the stated objective;
- what the strongest alternative would have changed;
- tool-by-tool information value and whether each changed the decision;
- decision quality versus outcome quality;
- preference-model update, especially when a supposedly neutral item feels disappointing;
- assumption audit;
- proposed skill changes only when the issue is systematic, not merely bad luck.

## Final checks before every strategy reply

- The screenshot exclusions were interpreted as “not”.
- Sold unknown boxes were retained as latent positions.
- The real state was not contaminated by a counterfactual branch.
- No box received more than one user tool.
- All probabilities came from the current global state.
- A timed tray was screened by quality lines, not raw clue count; switching
  was not described as guaranteed improvement.
- The top three each include a complete descending option list.
- The recommendation follows the currently declared objective mode.
- The displayed strategy uses its user-facing Chinese name and one-sentence rule.
- Market claims have current citations and confidence labels.
- The response contains exactly one `隐藏款：默认未计入` note unless the user explicitly enabled hidden-design modeling.
- In guided mode, the decision contract was explicitly confirmed before the
  final exact recommendation.
