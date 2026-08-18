# Changelog

## Unreleased

- Replaced session-derived regression fixtures with explicitly marked
  synthetic examples while preserving their probability invariants.
- Removed internal research notes from the public tree.
- Added a default-deny public-file allowlist, sensitive-content checker, and
  local pre-commit/pre-push protection.
- Documented the local-data boundary and mandatory history-validation gate.

## 1.9.0 — 2026-08-19

- Added screenshot-first guided intake: users may upload one tray screenshot
  and answer one decision question at a time instead of preparing a full brief.
- Added a bounded state machine that absorbs multi-part replies, avoids repeated
  questions, and separates screenshot facts from subjective user choices.
- Added a three-minute fast lane that collects only the minimum required
  choices in one turn and skips unnecessary market research.
- Added strategy-specific minimum inputs, preliminary risk calculation, and a
  confirmed decision contract. Hard-risk limits are never silently defaulted.
- Limited full resale research during guidance to resale-first decisions,
  explicit market questions, or unresolved series facts.
- Documented optional `meta.guidance` state and user-facing guided response
  templates without changing the probability solver.

## 1.8.0 — 2026-08-18

- Moved the standalone Skill to the repository root for direct GitHub
  installation and ZIP download.
- Made market research capability-aware and API-key-free: native host search,
  direct public fetches, and user-supplied materials are supported without a
  required search provider.
- Added explicit no-network and no-browser fallbacks. Missing market access no
  longer blocks screenshot parsing or preference/probability analysis, while
  resale ranking is disabled instead of guessed.
- Generalized the Xianyu/Xiaohongshu workflow from a specific Chrome connector
  to any safe interactive browser that reuses the user's existing session.
- Rewrote the README in user-facing Chinese with installation, examples,
  capability limits, privacy boundaries, and a concise English summary.
- Removed generated machine-local Skillshare metadata from the public tree.
- Added the MIT License for public reuse and redistribution.

## 1.7.0 — 2026-08-18

- Slimmed the plan output by default: branches keep only the decision summary,
  and `action_ranking` keeps the top 3 actions with the rest collapsed into
  `other_actions_ranked`. Use `--full-branches --top-actions 0` for the audit
  payload; `--indent` now defaults to 0.
- Deduplicated report fields: `top_3` lists box-id references and
  `next_tool_plan.baseline_best_draw` is a box-id string.
- Added beam truncation to depth-two planning: the second layer expands only
  the top 3 depth-1 card actions, others keep their depth-1 evaluation and are
  labelled `depth_evaluated: 1` with a `beam_note`. `beam_width=0` restores the
  untruncated exact pass (`--beam-width 0`). On the 13-box regression fixture the
  default depth-2 run drops from 4.65 s / 113.7 KB (v1.6.0 exact) to
  3.29 s / 40.7 KB, and the beam also bounds worst-case growth on larger trays
  where the exact pass scales multiplicatively with card actions.
- Coverage-gate errors from the QianDao snapshot now list the actual record
  names and suggest `--category` / `--include-secret` fixes.
- Partial `scores` now emit a Chinese warning in `report.warnings` instead of
  silently scoring unlisted designs as 0; the warning also reaches the
  `--screen-tray` payload.
- Fixed documentation drift: snapshot command templates include `--category`,
  the state-schema minimal example is runnable and self-consistent, the README
  structure listing matches the package, and genericized a leftover
  series-specific instruction.
- Added `tests/test_docs_contract.py`: documented commands must run/parse, and
  every example state plus the schema's minimal example must normalize.

## 1.6.0 — 2026-08-18

- Added `--screen-tray` as a timer-safe one-step assessment before committing
  cards or a purchase.
- Added direct-ready, tool-dependent, switch, session-stop, and missing-rule
  outcomes with reusable next-tray acceptance margins.
- Based tray quality on exact posterior metrics rather than raw exclusion
  counts, without claiming an unseen next tray will be better.

## 1.5.0 — 2026-08-18

- Added `min_favorite_any_pp` for the combined probability of all `+10`
  designs, independent from the broader liked threshold.
- Exposed favorite probabilities and planner uplift in public JSON.
- Enforced the favorite stop line in direct, hint, display, and depth-two
  branches without changing the selected strategy's box ranking.

## 1.4.0 — 2026-08-18

- Added seven default score tiers and score-derived preference groups.
- Made regular-only analysis the default and filtered hidden designs from the market fast path unless explicitly requested.
- Added direct draw or stop to tool ranking, including stable no-card wins for numerical zero-uplift ties.
- Added optional exact depth-two adaptive planning with first-action and one-card-horizon comparisons.
- Added staged solver regression fixtures for before-tools, after-hint, and
  after-open states.

## 1.3.0 — 2026-08-13

- Added six plain-language strategy names with backward-compatible aliases.
- Added per-design satisfaction scores and defaults for unlisted designs.
- Added hard-avoid probability limits and explicit stopping conditions.
- Added expected score, hard-avoid risk, and continue/stop decisions to solver output.
- Enforced hard limits separately in every hint/display outcome branch.

## 1.2.0 — 2026-08-09

- Added a fixed, read-only Chrome search protocol for logged-in Xianyu and Xiaohongshu.
- Replaced field-by-field browser reading with one batched DOM extraction per query.
- Added cross-query ID deduplication, Xiaohongshu relevance filtering, and bounded detail checks.
- Restricted evidence to POP MART official facts, QianDao, Xianyu, and Xiaohongshu; excluded Taobao, JD, other marketplaces, and overseas evidence.
- Added explicit credential, temporary-token, challenge, and confidence fallback rules.

## 1.1.0 — 2026-08-09

- Added a no-Browser QianDao batch extractor for average sold price, live bids/asks, and wish counts.
- Added an optional official-lineup coverage gate.
- Limited Xianyu and Xiaohongshu research to one series search plus at most three diagnostic designs.
- Capped single-platform confidence at medium and excluded overseas market evidence.
- Updated the initial market output contract and added extractor regression tests.

## 1.0.0 — 2026-08-09

- Added current-market research workflow.
- Added screenshot-to-state protocol and counterfactual branch isolation.
- Added exact perfect-matching posterior solver.
- Added complete-case mixture support for secret sensitivity analysis.
- Added ranked risk-first, target-only, top-target-first, balanced, and resale-EV objectives.
- Added one-step adaptive hint/display card planning.
- Added complete top-three option-distribution output contract.
- Added final-draw review and skill-improvement protocol.
- Added regression tests and runnable examples.
