# Changelog

## Unreleased

- Added whole-session automatic review: `--review-session` deterministically
  replays the append-only event ledger — rewinding the final trays (un-opening
  boxes, un-using cards, restoring pre-override stop rules and the consumed
  card inventory) and re-applying every event in order with an ex-ante
  snapshot before each card and opening. The JSON report and reader-facing
  Markdown are program-generated; nothing is reconstructed by hand.
- Per opening, the review reports the actual design's ex-ante probability and
  rank among possible designs, the ex-ante liked / neutral / disliked
  (hard-avoid) class probabilities, the accepted failure probability under the
  declared objective, the quality lines as they stood at the decision
  (including pre-override thresholds), and the decision-time comparison with
  the strongest alternative box — post-opening information never judges a
  choice.
- Per hint/display card, the review reports the real result, the ex-ante
  still-drawable branch probability, the declared strategy's primary metric
  before/after, ranking change, draw/stop decision change, and remaining count.
- The lifecycle section lists every switch, commitment (explicit or
  single-tray `first_tool_or_open`), persisted `quality_lines_upgrade`
  acceptance, explicit acceptance, release, and stop-rule override with old
  value, new value, contiguous order, and reason.
- The report separates decision quality, outcome quality, and model quality,
  flags sunk-cost and gambler-fallacy patterns using ex-ante information only,
  cross-checks remaining cards, draws used, and the draw cap against the
  ledger, and closes with the declared model scope and a final stop verdict
  (budget exhausted / stopped below quality lines / still recommend drawing /
  no drawable box remains).
- Legacy states without an event ledger produce an honest review: openings,
  cards, per-draw quality lines, and overrides are marked
  `legacy_state_without_event_ledger` 不可恢复, and current-state probabilities
  are never substituted for the missing history; the validator rejects any
  fabricated legacy number.
- `--review-session` is mutually exclusive with planning flags,
  `--screen-tray`, `--calibrate-preferences`, `--compare-trays`, and
  `--brief-preferences`, and refuses zero-tray briefings; existing single-tray
  and multi-tray paths are untouched.
- Added the candidate-tray commitment ladder: session trays move through
  open (未承诺) → candidate (候选承诺) → accepted (已接受), surfaced in plain
  language in standard reports, screening (`candidate_review` /
  `accepted_review` plus `release_before_switch`), and the session review.
- Added `tray_committed` and `candidate_tray_id`: selecting a tool-dependent
  tray in a comparison records a candidate commitment instead of posing as
  directly qualified. Multi-tray actions now require that explicit event;
  only a single-tray session auto-inserts it before the first real card/open
  (`first_tool_or_open`).
- Added the quality-line upgrade: when real clues push every quality line past
  its threshold, the ledger gains `tray_accepted`, `accepted_tray_id` is
  persisted, and the candidate is cleared (`quality_lines_upgrade`). Later
  recommendations stay locked; explicit acceptance remains `explicit_event`.
- Hardened the release gate: switching away from a candidate or accepted tray
  requires a reasoned `tray_released` event first; silent switches, releases
  without a reason, re-committing the locked tray, and actions on other trays
  while locked all fail closed. Global tools, draws, stop lines, and event
  order stay identical across commitment, upgrade, release, and switching,
  and counterfactual planning never mutates the real commitment.
- Preserved lossless reads: legacy single-tray states with used tools or
  opened boxes normalize straight to candidate commitments, and existing
  `accepted_tray_id` sessions load unchanged; the validators reject tampered
  lifecycle blocks, mismatched locks, and dropped comparison commitment steps.
- Added optional multi-tray comparison: `--compare-trays` ranks every operable
  tray in a same-series session envelope with at least two observed trays. Each tray is
  solved independently, the shared strategy and quality lines are reused, and
  released, history-read-only, and inoperable trays stay out of the action
  ranking.
- Added tray participation: trays accept `participation: "history"` to keep
  them read-only for review; released trays are derived from `tray_released`
  events and trays without drawable boxes are reported as inoperable.
- Made the comparison report a validated decision surface: per-tray rows show
  best box, liked / any-disliked / hard-avoid metrics, status, first tool
  action, and the qualifying-branch probability explicitly labeled as not a
  win rate; the recommendation names the top tray's first action or an
  explicit stop-or-review verdict and never guarantees a better future tray.
- Added opt-in two-step comparison: `--compare-depth 2` requires at least two
  remaining cards, expands only the top-ranked head candidates, and reports
  whether the first action changed versus the one-step horizon. The default
  comparison stays one-step.
- Kept every existing path unchanged: legacy single-tray JSON, `--screen-tray`,
  formal reports, and calibration reject or ignore comparison mode, and the
  comparison itself never mutates preferences, quality lines, tools, draws, or
  events.
- Added zero-tray preference briefings: `regular_count` plus preferences (and
  optionally an explicit design table) normalize without any tray, and
  `--brief-preferences` renders a validated blind-baseline report — favorite,
  liked, disliked, and hard-avoid probabilities plus expected score under the
  complete no-duplicate uniform prior — while warning
  `current_tray_not_observed` and never claiming current-tray attainability.
- Added stable no-tray reference lines for `随便中个喜欢`: five-point steps
  strictly improve the blind baseline, with 40% liked / 35% disliked / 20%
  hard-avoid anchors for weak baselines, labeled as judgment suggestions with
  `stop_rules_mutated: false`; other strategies receive baselines only and are
  redirected to `--calibrate-preferences` after entering a real tray.
- Added preference-conflict gates: any duplicate JSON key now fails closed,
  zero-tray score coverage must be complete, unconfirmed defaults do not fill
  omitted designs, `explicit_score_tiers` represents all seven declared tiers,
  and explicit `liked`/`disliked`/
  `hard_avoid` entries that contradict score-derived tiers are listed in the
  briefing with their override source instead of being silently resolved.
- Split preference policy, lifecycle reduction, strategy-review metrics, and
  CLI routing into focused modules; shared synthetic session builders remove
  repeated fixtures.
- Added score-first preference calibration: complete per-design scores now
  derive all seven tiers, while `--calibrate-preferences` reports current-tray
  attainable ranges, every box's metrics, and non-dominated numbered boundary
  bundles without issuing a draw recommendation or mutating stop rules.
- Applied the calibration gate to all guided A–E goals. `保值优先` now requires
  complete current market values and calibrates expected resale value together
  with personal-score and risk boundaries.
- Required explicit confirmation when `score_default` fills omitted designs;
  confirmed boundary bundles apply within the current series session and are
  recalibrated after series or material score changes.
- Made score-derived `只冲最爱` maximize the combined probability of equal
  highest-score designs, while preserving explicit ordered-liked behavior.
- Added a validated reader-facing Markdown report with a concise conclusion,
  quantified TOP 3 comparison, full design-by-box probability matrix, every
  configured stop line, next action, and model warnings; JSON remains the
  default compatible interface and timed tray screening remains compact.
- Made the Skill relay that validated report unchanged for initial decisions,
  kept trays, real clue updates, and post-open follow-ups; added synthetic
  short-turn regressions for preserved requirements, exclusions, leader
  changes, and no-qualified-box stops.
- Added GitHub Actions checks for the full test suite, public-package
  validation, history validation, and whitespace errors on pull requests and
  `main`.
- Added `min_tool_uplift_pp`, defaulting to `tie_tolerance_pp`, so a
  direct-ready tray does not spend cards on immaterial primary uplift.
- Preserved rescue cards when direct draw fails but a card can create a
  fully qualifying branch.
- Added expected card consumption and separate depth-two action-identity and
  terminal-equivalence fields; equivalent policies now prefer fewer cards.
- Added report-level synthetic regressions around 0.063pp, 0.396pp, and
  0.589pp uplift, plus a redundant-first-card regression.
- Added accepted-tray locking, explicit release and stop-rule override events,
  and lock-aware session recommendations/review output.
- Added machine-readable regular-only and assumed-hint warnings, plus explicit
  conditional-probability metadata in every report.
- Added a backward-compatible multi-tray session envelope with stable tray
  IDs, global tool/draw counters, and an append-only actual-event ledger.
- Added one report-level session output that preserves independent posterior
  reports for every retained tray while planning only on the active tray.
- Added a synthetic three-tray regression covering cross-tray state retention,
  global draw limits, event consistency, and legacy one-tray normalization.
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
