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

## B. First tray strategy

```markdown
## 结论

**当前首选：X号。**

- 最讨厌款1：...%
- 最讨厌款2：...%
- 任一不喜欢款：...%
- 任一喜欢款：...%
- 最爱合计：...%（若设置最爱停止线）
- 最喜欢款：...%
- 硬雷合计：...%（若设置）
- 平均评分：...（若设置）

一句话解释 why X beats Y under the declared objective.

## 模型与识别

- 策略：稳妥避雷 / 守住底线 / 整体最满意 / 随便中个喜欢 / 只冲最爱 / 保值优先
- 规则：用一句话说明本轮如何选盒
- 整盒假设：...
- 已售未知盒：保留为潜变量的盒号
- 合法整盒排列：N（若为混合模型则列情景后验）
- 关键不确定性：...
- 隐藏款：默认未计入

## 当前 TOP 3

| 排名 | 盒号 | 喜欢1 | 喜欢2 | 喜欢3 | 喜欢合计 | 不喜欢1 | 不喜欢2 | 不喜欢3 | 不喜欢合计 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|

### 1）X号：所有未显式排除选项
| 概率排序 | 款式 | 概率 | 标签 |
|---:|---|---:|---|

### 2）Y号：所有未显式排除选项
...

### 3）Z号：所有未显式排除选项
...

> 0% 的未显式排除项保留在表尾，并标记“被全局整盒约束排除”。

## 是否继续

**继续抽 X号 / 停止。**

- 喜欢款概率：...（门槛：...）
- 最爱款合计概率：...（门槛：...）
- 不喜欢款概率：...（上限：...）
- 硬雷概率：...（上限：...）
- 平均评分：...（门槛：...）
- 已抽：...盒（最多：...盒）

只列用户实际设置的条件；任一条件失败就明确建议停止。

## 下一步动作

**下一步：直接抽 / 停止 / 使用提示卡 / 使用显示卡。**

- 不用卡基线：直接抽 X号 / 停止
- 若推荐卡片：使用后自适应选择的期望为喜欢合计 ...%，不喜欢合计 ...%
- 若推荐卡片：结果后仍建议抽盒的概率为 ...%
- 若推荐卡片：相对不用卡，喜欢 +...pp；不喜欢 -...pp
- 若推荐卡片：列关键分支，并在真实结果后重算
- 若启用两步：只执行首步；说明单步首选是否仍为两步最优，并列“两卡终局相对一卡终局”的增益
```

The “all options” tables must be complete for each top-three box. Do not show only liked and disliked subsets.

## C. Update after a hint/display result

```markdown
## 更新结论

真实新增信息：X号不是/显示为 A。

**更新后首选：Y号。**

| 指标 | 更新前 | 更新后 | 变化 |
|---|---:|---:|---:|
| 喜欢合计 | ... | ... | ...pp |
| 最喜欢款 | ... | ... | ...pp |
| 不喜欢合计 | ... | ... | ...pp |

## 更新后的 TOP 3

[Repeat the full top-three contract, including complete option lists.]

## 下一步动作

- 剩余提示卡：N
- 剩余显示卡：M
- 动作排名：直接抽/停止、提示卡、显示卡
- 当前建议：...
- 两步规划（若启用）：首步...；单步首选仍最优/已改变；两卡终局相对一卡终局...pp
```

Every update must recompute the whole tray. Do not only update the affected box.

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

```markdown
## 端筛选

**结论：直接可做 / 依赖道具 / 建议换端 / 本轮停止 / 需先设入场线。**

- 当前最佳盒：X号
- 喜欢合计：...%（门槛 ...%，差 ...pp）
- 最爱合计：...%（门槛 ...%，差 ...pp）
- 硬雷合计：...%（上限 ...%，差 ...pp）
- 若依赖道具：用提示卡/显示卡于 X号；结果后仍可抽的概率 ...%
- 下一端入场线：默认摇盒后，当前最佳盒需同时满足以上全部质量线
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

Use after preliminary calculation and any contextual risk question, before the
final exact recommendation:

```markdown
## 请确认本轮决策

- 系列与当前端：……
- 关键识别：……（含无法排除的不确定性）
- 策略：……（一句话规则）
- 偏好：最爱……；喜欢……；中性但失望……；轻雷……；硬雷……
- 行动边界：必须抽 / 可换端 / 可停止；最多……盒
- 道具与预算：提示卡……；显示卡……；额外付费……
- 风险与停止线：……
- 采用的建议默认值：无 / ……

请回复“确认”，或直接修改任何一项。
```

List only preference tiers and boundaries relevant to the chosen strategy.
Never insert an unconfirmed hard-risk cap. In guided mode, do not present the
final box recommendation until this contract is explicitly confirmed. After
confirmation, set `meta.guidance.confirmed` to `true`, run the current global
state, and use template B or G.
