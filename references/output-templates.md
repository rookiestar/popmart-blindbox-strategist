# Required response templates

Use these structures as output contracts. Keep the prose direct; the probability tables carry the detail.

## A. Initial market scan

```markdown
## 当前市场结论（截至 YYYY-MM-DD HH:mm，市场：大陆/CNY）

一句话说明热款、普通款、弱势款，以及整体证据置信度。

数据源状态：千岛（可用/不可用）；闲鱼（可用/不可用）；小红书（可用/不可用）。
海外数据：未使用。

### 系列基础信息
| 项目 | 结果 |
|---|---|
| 常规款 / 整盒数 | ... |
| 官方单盒价 | ... |
| 发售日 | ... |

隐藏款：默认未计入

### 二手现值与热度
| 款式 | 热度 | 千岛成交均价 | 最高求购 | 最低挂牌 | 闲鱼补证 | 小红书需求 | 相对官价 | 流动性 | 置信度 |
|---|---|---:|---:|---:|---|---|---:|---|---|

> 千岛“成交均价”、当前求购和当前挂牌是不同口径；闲鱼挂牌不写成成交。

### 需要你下一步提供
直接上传当前端截图即可；若没有整理偏好，接下来每次只问一个关键问题。
```

Cover every regular design. Cite current claims inline. If only QianDao is usable, cap confidence at medium and name the missing checks.

## B. Standard formal decision report

Use for the first confirmed recommendation, a kept tray, every real
hint/display update, and every post-open decision about another draw.

机器契约的唯一事实来源是经过校验的 renderer：

```bash
python3 scripts/blindbox_solver.py <state.json> --format markdown
```

Return stdout unchanged. Do not handwrite, paraphrase, reorder, shorten, or
append a second recommendation. The renderer itself guarantees:

- a clear conclusion and exactly three concise decision explanations;
- the quantified first-versus-second trade-off;
- TOP 3 summary plus one design-by-box matrix covering every design;
- distinct `已排除` and `0.00%（全局约束）` cells;
- every configured stop line and its pass/fail state;
- the tray lifecycle sentence (未承诺 / 候选承诺 / 已接受 — a tool-dependent
  selection is never faked as directly qualified), including the auto-upgrade
  note once real clues pass every quality line and the reasoned-release
  requirement before any switch;
- one executable next action: 直接抽 / 停止 / 使用提示卡 / 使用显示卡;
- the `条件概率` scope and applicable `regular_only_scope` /
  `hint_mechanism_assumed` warnings.

Completion means the command exits 0 after report validation. On failure,
correct the state and rerun; never fall back to a partial manual report.

## C. Update after a hint/display/open result

1. Decide whether the message is an actual event or a counterfactual.
2. Apply the actual event only to the active tray; update the session-level
   tool and draw counters.
3. Preserve the confirmed strategy, preference scores, stop lines, candidate
   or accepted tray lock, and prior trays. A real card or opening on a new
   tray records its candidate commitment automatically; reaching every
   quality line upgrades it to accepted without touching earlier events.
4. Run template B's command and return stdout unchanged.

“重来，需求不变” and “换一端，需求不变” preserve the decision contract.
“X号排除了Y” and “X号显示为Y” trigger the same complete report after the
state update. The user never needs to ask again for “所有选项”.

## D. Counterfactual branch

Begin with an explicit label:

```markdown
## 反事实分支：假设“8号提示卡排除路障”

该分支没有写入真实状态。以下结果仅用于比较。
```

At the end, restate the last confirmed actual state in one line so the branches cannot be confused.

## E. Whether additional tools are worth acquiring

```markdown
## 判断

**免费/低成本：值得；需要明显付费：暂不建议。**

| 新增工具数 | 最优使用方式 | 喜欢合计 | 最喜欢款 | 不喜欢合计 | 相对无工具提升 |
|---:|---|---:|---:|---:|---:|

### 边际价值
- 第1张：+...pp
- 第2张：+...pp
- 第3张：+...pp

### 盈亏平衡
工具总成本应低于：
`ΔP(喜欢) × 喜欢相对普通款的主观增值 + 避免不喜欢款的期望损失`

仍有 ...% 概率不中任何喜欢款。不要把信息增益写成高确定性。
```

If the user has not supplied tool cost or subjective values, give a conditional recommendation rather than a fabricated monetary EV.

## F. Pure target mode

When the user changes the goal to “只冲喜欢款”:

1. set the strategy to `随便中个喜欢`;
2. if the user says “尤其是第一款” but still accepts the other liked items, keep `随便中个喜欢` and use rank as tie-break;
3. switch to `只冲最爱` only when the first liked item genuinely dominates the other targets;
4. state that the objective changed, so the new recommendation need not match the earlier risk-first recommendation.

## G. Timed tray screening

Use before committing cards or a purchase to a 3–5 minute tray:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --screen-tray --format markdown
```

Return stdout unchanged. This is the only compact decision surface.

```markdown
## 端筛选

**结论：直接可做 / 依赖道具 / 建议换端 / 本轮停止 / 需先设入场线。**
**已锁定结论：候选复盘（候选承诺，未达线）/ 接受复盘（已接受）。**

- 当前最佳盒：X号
- 喜欢合计：...%（门槛 ...%，差 ...pp）
- 最爱合计：...%（门槛 ...%，差 ...pp）
- 硬雷合计：...%（上限 ...%，差 ...pp）
- 若依赖道具：用提示卡/显示卡于 X号；结果后仍可抽的概率 ...%
- 下一端入场线：默认摇盒后，当前最佳盒需同时满足以上全部质量线
- 若本端为候选承诺：已达线后自动升级为已接受；如要换端，先说明理由并记录释放
- 若本端已接受：保持锁定；如要换端，先说明理由并记录释放
- 下一端不保证更好；本结论未使用固定提示条数
- 隐藏款：默认未计入
```

List only configured checks. Omit the full TOP 3 during this fast path; provide
it after the user keeps the tray. Read `references/tray-screening.md`.

## H. Final draw review

Use `references/review-and-evals.md`. The first paragraph must answer:

- Was the decision reasonable under the declared objective?
- Was the realized result good, neutral, or bad?
- How probable was the actual result?

Never use “手气差” as a substitute for the actual probability.

The review must list the committed/accepted/released tray lifecycle and every
`stop_rule_override` with its old value, new value, and confirmed reason.

## I. Guided intake — single question

Use after a screenshot-only input. Mention only facts that reassure the user the
image was read correctly, then ask one current question:

```markdown
我已读到：系列……；共……个盒位；已售……；倒计时……。
有一处需要确认：……（仅在真正阻塞时显示）

### 第1步：这次你最看重什么？

A. 尽量别踩雷
B. 任意喜欢款都可以
C. 只冲最想要的那款
D. 综合下来最满意
E. 优先保值

没有明确目标时默认建议 A。回复字母或自然语言即可。
```

If a screenshot fact is unreadable, ask only that targeted clarification and
defer the goal question. On later turns, replace the heading and choices with
the single next question from `references/guided-intake.md`. Do not append a
second question. If the user answers several future topics at once, acknowledge
and retain all of them.

For the under-three-minute fast lane, one compact message may request goal,
minimum preference groups, draw/switch/stop boundary, and current card counts
together. Remove every item already known.

## J. Guided intake — decision contract

Use after the score-first calibration choice, before the final exact
recommendation:

```markdown
## 请确认本轮决策

- 系列与当前端：……
- 关键识别：……（含无法排除的不确定性）
- 策略：……（一句话规则）
- 偏好：最爱……；喜欢……；中性但失望……；轻雷……；硬雷……
- 行动边界：必须抽 / 可换端 / 可停止；最多……盒
- 道具与预算：提示卡……；显示卡……；额外付费……
- 校准选择：方案1/2/3；参考盒……
- 风险与停止线：……
- 采用的建议默认值：无 / ……

请回复“确认”，或直接修改任何一项。
```

List only preference tiers and boundaries relevant to the chosen strategy.
Never insert an unconfirmed hard-risk cap. In guided mode, do not present the
final box recommendation until this contract is explicitly confirmed. After
confirmation, set `meta.guidance.confirmed` to `true`, run the current global
state, and use template B or G.

## K. Score-first preference calibration

Use after complete per-design scores, action boundaries, and current card
counts are known, but before quality probability lines are confirmed:

```bash
python3 scripts/blindbox_solver.py <state.json> \
  --calibrate-preferences --format markdown
```

Return stdout unchanged. This report is intentionally not template B: it
contains no draw/card recommendation and does not mutate `stop_rules`. It must
show:

- all seven score-derived tiers and complete score coverage;
- current-tray attainable ranges;
- every drawable box's favorite, liked, disliked, hard-avoid, and expected
  score metrics;
- for `保值优先`, complete market-value coverage and expected resale value;
- the non-dominated frontier;
- up to three concrete numbered boundary bundles.

State that these are direct-box metrics before new tool outcomes. Plan cards
only after the boundary bundle is confirmed.

Tell the user that replying with scheme 1/2/3 confirms that bundle for the
current series session; edited numbers are also valid. Only after that reply may the
assistant write the selected quality lines into `stop_rules`. In a multi-tray
session, append matching `stop_rule_override` events with old/new values and
the confirmed choice as reason. Then present template J for confirmation and
run template B or G.

If the report says `target_unreachable`, do not create a 0% target threshold.
Switch trays or stop. If `score_default` filled any design, calibration is
valid only when `score_default_confirmed: true`.

## L. Zero-tray preference briefing

Use when the user supplies preferences before any tray is observed — no
screenshot, no boxes, only `regular_count` plus preferences:

```bash
python3 scripts/blindbox_solver.py examples/synthetic-preference-briefing.json \
  --brief-preferences --format markdown
```

Return stdout unchanged. This report is intentionally not template B or K: it
contains no draw/card recommendation, no current-tray attainability claim, and
no `stop_rules` mutation. It must show:

- the blind baseline under the complete no-duplicate uniform prior: favorite,
  liked, disliked, and hard-avoid probabilities plus expected score (and
  expected resale value for `保值优先` with complete market values);
- the `current_tray_not_observed` warning stating the numbers are prior
  baselines, not any observed tray's real level;
- for `随便中个喜欢` with a non-empty liked group: five-point reference lines
  that strictly improve the baseline, using 40% / 35% / 20% balanced anchors
  for weak baselines, labeled as judgment suggestions; every other strategy
  gets baselines only and a redirect to
  `--calibrate-preferences` after entering a real tray;
- explicit `liked`/`disliked`/`hard_avoid` or `explicit_score_tiers` entries
  contradicting score-derived tiers, each marked `confirmation_required`;
  these block reference lines
  until the input is reconciled;
- the existing stop rules plus `stop_rules_mutated: false` and
  `confirmation_required: true`.

Tell the user that confirming a reference line writes it into the existing
`stop_rules` (recording a `stop_rule_override` event in a session); there is no
second entry-line set. When a real tray is later observed, keep the confirmed
scores, lines, budgets, and history, then continue with template K or B.

## M. Multi-tray comparison

Use only when the session state already carries at least two observed trays
from the same series and the user explicitly asks which end to play. A single tray stays on
template G or B; adding a second tray mid-session must not reset preferences,
quality lines, global card counts, draw counts, or prior tray evidence.

```bash
python3 scripts/blindbox_solver.py examples/synthetic-tray-comparison.json \
  --compare-trays --format markdown
```

Return stdout unchanged. Do not hand-assemble, reorder, or extend the
comparison table. The renderer itself guarantees, in fixed order:

1. `## 结论` — the overall verdict: comparable tray count, recommended tray
   with its status and first action, the planning horizon used, whether a
   recorded release is required before switching away from a candidate or
   accepted tray, and that a future tray is not guaranteed to be better;
2. `## 逐端比较` — one row per operable tray with rank, tray ID, direct best
   box, the three quality metrics (liked / any disliked / hard avoid), direct
   status, first tool action, and the qualifying-branch probability, plus the
   statement that this probability is not a win rate. Rows mark the accepted
   and candidate trays. Released, history read-only, and inoperable trays
   appear only in the review sub-table, in that precedence order;
3. `## 下一步（推荐端与首步动作）` — exactly one executable first action on
   the recommended tray, or an explicit stop-or-review verdict when the
   leading tray has no executable action. When the recommended tray is
   tool-dependent and not yet locked, the step directs recording a candidate
   commitment (`tray_committed`) after selection — never a direct acceptance —
   with the auto-upgrade and reasoned-release rules restated;
4. `## 质量线` — the shared session strategy, quality lines, and draw budget,
   with the note that the comparison mutated nothing;
5. `## 模型口径` — the conditional-probability scope, applicable model
   warnings, and the independent-trays statement.

Two-step horizon: `--compare-depth 2` is opt-in only, requires at least two
remaining cards, expands only the top-ranked head candidates, and must state
per expanded tray whether the first action changed versus one step and the
terminal gain versus the one-card horizon.

## N. Whole-session automatic review

Use when the user asks for a session review or the session ends. The report is
generated by replaying the append-only event ledger; never reconstruct it by
hand, and never fill a missing historical number with a current probability:

```bash
python3 scripts/blindbox_solver.py examples/synthetic-session-review.json \
  --review-session --format markdown
```

Return stdout unchanged. `--review-session` cannot be combined with planning
flags, `--screen-tray`, `--calibrate-preferences`, `--compare-trays`, or
`--brief-preferences`. The renderer itself guarantees, in fixed order:

1. `# 盲盒整轮自动复盘：<系列>` and `## 复盘结论` — the stop conclusion,
   draws used versus the cap, remaining cards, decision quality (optimal
   openings or the non-optimal event list), outcome tallies, ex-ante-only
   sunk-cost / gambler-fallacy flags, and data recoverability;
2. `## 开盒逐次复盘` — per opening: the actual design with its ex-ante
   probability and rank among possible designs, the ex-ante liked / neutral /
   disliked (hard-avoid) class probabilities, the accepted failure probability
   under the declared objective, the quality lines as they stood at the
   decision (pre-override thresholds included), and either decision-time
   optimality or the strongest alternative box with its liked-probability
   delta;
3. `## 道具卡逐张复盘` — per card: the real result, the ex-ante
   still-drawable branch probability, the best box and liked probability
   before/after with the metric change, whether the decision changed, and the
   remaining count of that card type;
4. `## 承诺与止损线变更` — every switch, commitment (marked 自动 when derived
   from the first card/opening), lossless 达线升级 acceptance, acceptance,
   release, and stop-rule override with old value, new value, and reason;
5. `## 预算与停止结论` — remaining cards, draws used, the final stop
   conclusion, and the cross-check against the ledger;
6. `## 决策、结果与模型质量` — decision quality judged only against the
   decision-time alternative, outcome quality stated separately, and model
   zero-probability contradictions;
7. `## 模型口径` — the conditional-probability scope and model warnings;
8. `## 不可恢复项` — only for legacy states without an event ledger: each
   unrecoverable field with its reason and the no-fabrication statement.
