# Probability model and solver interpretation

## 1. Core model: constrained complete-case assignment

For a tray with `N` positions and `N` distinct designs, define an allowed matrix:

- row `i`: tray position `i`;
- column `j`: design `j`;
- an edge exists when design `j` has not been excluded from box `i` and does not contradict a known reveal.

A valid tray is a perfect matching: each box receives exactly one design and each design appears exactly once.

The posterior probability is:

\[
P(\text{box }i=j\mid E)
=\frac{\#\{\text{valid matchings satisfying }i\to j\}}
{\#\{\text{all valid matchings satisfying evidence }E\}}.
\]

This is why the boxes are not independent. An exclusion on one box can change every other box.

`scripts/blindbox_solver.py` counts valid matchings with a bitmask dynamic program and computes exact edge marginals. For ordinary 12-box cases, this is much faster and more reliable than enumerating all `12!` permutations.

## 2. Sold and unavailable boxes

A sold-but-unknown box is not removed from the probabilistic tray. It remains a latent row in the matching problem. It is excluded only from the purchase ranking.

A known opened box remains in the model as a fixed row-to-design assignment. The fixed design becomes impossible for every other position in a no-duplicate case.

## 3. Prior assumptions

The regular complete-case model assumes all tray permutations are equally likely before clues. Use it only when:

- the UI tray corresponds to one complete factory case;
- each regular design appears once in a no-secret case;
- the platform has not mixed loose boxes from multiple cases;
- clues are truthful.

This assumption is more consequential than decimal precision. If it cannot be supported, state the limitation instead of presenting exact tray-level numbers.

## 4. Secret designs

The default analysis is regular-only and adds only `隐藏款：默认未计入`.
Use the rest of this section only when the user explicitly requests hidden-design modeling.

A secret can be represented as a mixture of complete-case scenarios. For example:

- no-secret scenario;
- one scenario for each regular design that the secret could replace.

For scenario `s` with prior `π_s` and `M_s` valid matchings:

\[
P(s\mid E) \propto \pi_s M_s.
\]

Marginals are averaged using these posterior scenario weights.

Do not derive a case-level secret prior from an ambiguous “1/x” label without checking whether it is per box, per case, or a marketing simplification. When the explicitly requested replacement rule is unknown, stop the hidden branch rather than inventing a mixture or sensitivity analysis.

## 5. Preference objectives

Read `references/preference-strategies.md` for user-facing selection and input.
The solver implements:

- `稳妥避雷`: lexicographically minimize severity-weighted disliked risk and
  total disliked probability before maximizing liked probability.
- `守住底线`: split boxes into hard-avoid probability within/outside the
  stated limit. Rank within-limit boxes by expected score. If none qualify,
  recommend stopping rather than silently choosing an over-limit box.
- `整体最满意`: maximize
  \(\sum_d P(d\mid E)\times score(d)\). Positive and negative outcomes may
  compensate each other.
- `随便中个喜欢`: maximize total liked probability.
- `只冲最爱`: compare liked-design probabilities in stated order.
- `保值优先`: maximize probability-weighted resale value.

`tie_tolerance_pp` prevents immaterial probability differences from
dominating rank-based objectives. Scoring objectives compare expected scores
directly.

## 6. Hint-card model

Default mechanism:

- the card selects uniformly from not-yet-shown labels that are not the true design;
- the result adds one hard exclusion to the selected box;
- the box cannot receive another user tool.

If a box has `K` currently revealable labels and the true design is among them, then for label `y`:

\[
P(\text{hint says not }y)=\frac{1-P(\text{box}=y)}{K-1}.
\]

After observing “not y”, the solver conditions the entire tray on that new exclusion.

When hidden designs cannot appear as hint labels, the denominator differs depending on whether the true design is hidden. The current script blocks exact hint-value planning in that mixture case rather than silently applying the wrong likelihood.

## 7. Display-card model

Default mechanism:

- the card reveals the true design;
- the box remains selectable;
- the known design globally updates the rest of the tray;
- the box cannot receive another user tool.

For every possible reveal result, the planner recomputes the full posterior and chooses the best final box under the declared objective. The expected terminal probability is the probability-weighted average over these adaptive branches.

Each branch also runs the stopping conditions. A branch that violates a hard
limit or stopping condition recommends no draw and contributes zero draw
utility; the planner reports the probability that a tool outcome still leads
to a purchase. This prevents averaging safe and unsafe branches into a false
“safe on average” recommendation.

## 8. Adaptive planning horizons

The default one-step planner ranks direct draw or stop together with every
eligible next card. The no-card action wins numerical zero-uplift ties, so
floating-point dust cannot spend a card. After an actual outcome, rerun.

Optional depth-two planning expands one more adaptive layer. Every first-card
outcome chooses its own best second action, including direct draw or stop. It
returns only the first action for execution; the real outcome must still be
written into state before replanning.

To keep depth two inside the online timer, the second layer expands only the
top `beam_width` (default 3) depth-1 card actions; every other card action
keeps its depth-1 evaluation and is labelled `depth_evaluated: 1`, with a
`beam_note` in the plan when truncation dropped anything. Direct draw or stop
competes untruncated at every layer. Pass `--beam-width 0` (plan argument `beam_width=0`) for the
untruncated exact pass when auditing.

Use `--plan-depth 2` only when at least two cards remain and the online timer
allows the slower exact calculation. Keep depth one as the default.

`gain_vs_one_card_horizon` compares the best policy allowed up to two cards
with drawing or stopping after at most one card. If the one-step first action
remains optimal under the two-step horizon, rolling one-step replanning has no
demonstrated first-decision loss even when the two-card terminal probability
is higher.

## 9. Tool value versus tool price

The planner reports probability uplift, not automatic monetary value.

A simple break-even expression is:

\[
\text{tool value}
\approx \Delta P(\text{liked})\times V_{\text{liked uplift}}
+[-\Delta P(\text{disliked})]\times C_{\text{disliked loss avoided}}.
\]

Use resale values only when they reflect the user's actual fallback behavior. A highly priced design the user will keep is not automatically liquid cash value.

## 10. Validation rules

Before presenting results, verify:

- at least one valid complete-case assignment exists;
- every box distribution sums to 1 within numerical tolerance;
- in a single no-secret scenario, each design's probabilities across all tray positions sum to 1;
- top-three option tables omit explicit exclusions and retain globally impossible zero-probability labels with a note;
- probabilities shown to the user are derived from the latest state, not a previous branch;
- direct draw or stop participates at every planning layer, and the no-card action wins numerical zero-uplift ties;
- no user tool is planned on a box with `tool_used: true`.

## 11. Unsupported or ambiguous situations

Stop or qualify rather than fabricate precision when:

- the screenshot is unreadable;
- the tray may combine multiple factory cases;
- duplicate regular designs are possible;
- the series has a nonstandard case composition not expressible as complete distinct-design scenarios;
- explicitly requested secret priors or replacement mechanics are unknown;
- platform hint/display behavior differs from the stated assumptions.
