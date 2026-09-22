# POP MART 盲盒决策助手

一个面向泡泡玛特在线抽盒的 Skill：结合当前市场情况，并参考摇盒线索、个人喜好和道具，计算整端概率并给出选择建议；抽盒前后可通过市场热度与交易价值辅助判断。

## 它能做什么

- 查询系列常规款、官方信息和大陆二手市场参考；
- 读取在线抽盒截图中的“不是”线索；
- 从一张截图开始，逐步询问目标和逐款评分，自动分成七档；
- 也可在进抽盒机前先给完整偏好，计算盲抽基线和可确认的参考线；
- 用当前端真实可达概率生成边界选项，不要求你凭空估上限；
- 按整端无重复模型计算每个盒位的精确概率；
- 比较直接抽、停止、提示卡和显示卡；
- 过滤低于实用收益门槛的卡片动作，避免为小数噪声浪费卡；
- 跨多端保留各自线索，并统一计算剩余卡数和抽盒上限；
- 可选地横向比较同系列多端；只给一端时直接进入原单端流程；
- 锁定已达标的端，并审计释放与止损线变更；
- 明示常规款范围和提示机制假设，不把条件概率写成确定事实；
- 按你的喜好、硬雷和止损线推荐盒位；
- 正式建议自动给出量化理由、TOP 3 全款概率矩阵、停止线和下一步；
- 每得到一条新线索后重新计算；
- 抽完后复盘“决策是否合理”和“结果是否走运”；
- 收官时回放整轮事件账本，逐次还原事前概率、用卡价值、承诺与止损线变更，
  区分决策质量、结果质量和模型质量。

支持六种直观策略：

`稳妥避雷 / 守住底线 / 整体最满意 / 随便中个喜欢 / 只冲最爱 / 保值优先`

## 30 秒开始

### Codex：推荐

在 Codex 中调用 `$skill-installer`，并让它从以下仓库安装：

```text
https://github.com/rookiestar/popmart-blindbox-strategist
```

也可以手动安装到用户级 Skill 目录：

```bash
git clone https://github.com/rookiestar/popmart-blindbox-strategist \
  "$HOME/.agents/skills/popmart-blindbox-strategist"
```

安装后直接输入：

```text
$popmart-blindbox-strategist
帮我分析这端应该抽哪个盒位。
```

Codex 会自动发现新安装的 Skill；若未出现，重启 Codex。官方安装与 Skill 说明见 [OpenAI 文档](https://learn.chatgpt.com/docs/build-skills)。

### ChatGPT 桌面版

独立 Skill 可在 ChatGPT 桌面版使用；可在侧栏的 **Skills** 入口查看。GitHub 仓库的导入入口取决于客户端版本和账号能力，本 README 不承诺所有账号都能直接导入 ZIP。

如果需要面向 ChatGPT 网页端、移动端和团队用户的一键分发，后续应再封装为 Plugin；本仓库当前是独立 Skill。

## 第一次怎么问

### 只发一张截图也可以

不需要先整理款式、策略或概率门槛。上传当前抽盒机截图，然后说：

```text
帮我选一个，你一步一步问我就行。
```

Skill 会先读取系列、盒位、“不是”、已售位置、卡片和倒计时，再每次只问
一个当前最关键的问题。你一次回答了多项，它会全部记住，不会重复问。
评分完整后，它会先展示当前端全部盒位的最爱、喜欢、不喜欢和硬雷概率，
让你从具体方案1/2/3中确认边界；之后才确认“本轮决策”并正式推荐。
五个目标都走这套评分校准；选择 E 时会先补齐当前市场价值，再同时校准
保值、个人满意度和雷区边界。

第一问通常是：

```text
这次你最看重什么？
A. 尽量别踩雷
B. 任意喜欢款都可以
C. 只冲最想要的那款
D. 综合下来最满意
E. 优先保值
```

倒计时不足约 3 分钟时，直接说“时间紧”，Skill 会把最少必要问题合并成
一次询问，并跳过不必要的市场研究。

只有系列名：

```text
研究一下“漫威穿越无限”现在的热款、普通款和弱势款。
```

已有抽盒截图：

```text
这是当前端的截图，策略只冲最爱。
A +10，B +7，C 0，D -6，E -10。
我有 2 张提示卡，最多抽 2 盒。概率边界请按当前端帮我校准。
```

想快速筛端：

```text
我只有 3 分钟，这一端值得继续还是换端？
```

想先定偏好和参考线：

```text
这是一端 12 盒的标准配置。策略随便中个喜欢；下面是完整逐款评分。
最多抽 2 盒，提示卡 4 张。先给我盲抽基线和参考线。
```


## 搜索与隐私

使用核心选盒功能**不需要** Tavily、搜索 API Key 或额外账号配置。没有搜索
能力时，截图解析、偏好引导和概率计算仍可继续；只有依赖市场价格的
`保值优先`会暂停，避免编造二手排行。

市场研究固定使用：

- 泡泡玛特官方；
- 千岛；
- 闲鱼；
- 小红书。

不会因为某个来源不可用而扩展到淘宝、京东或海外市场。

闲鱼和小红书只复用用户自己的已登录浏览器，执行只读查询。Skill 不要求提供账号密码，不导出 Cookie、Storage State 或浏览器配置；遇到验证码、异常流量或登录失效会停止该来源。

## 本地命令

概率计算：

```bash
python3 scripts/blindbox_solver.py examples/minimal-demo.json
```

评分校准（只展示可达区间和候选边界，不推荐抽盒）：

```bash
python3 scripts/blindbox_solver.py \
  examples/synthetic-score-first-calibration.json \
  --calibrate-preferences --format markdown
```

无端偏好简报（先验基线与待确认参考线）：

```bash
python3 scripts/blindbox_solver.py \
  examples/synthetic-preference-briefing.json \
  --brief-preferences --format markdown
```

同系列多端横比（可选步骤）：

```bash
python3 scripts/blindbox_solver.py \
  examples/synthetic-tray-comparison.json \
  --compare-trays --format markdown
```

面向用户的完整决策报告（自动执行一道具规划并校验完整性）：

```bash
python3 scripts/blindbox_solver.py examples/minimal-demo.json --format markdown
```

需要解释选盒或用卡原因时，使用同一计算结果的简明视图：

```bash
python3 scripts/blindbox_solver.py examples/minimal-demo.json --explain --format markdown
```

从第一端开始保存会话与事件。将已有盘面转为会话，或在更新后导出包含自动承诺/
接受事件的完整状态（输出 JSON，不覆盖输入）：

```bash
python3 scripts/blindbox_solver.py examples/minimal-demo.json --session-state
```

历史不全时保留初始快照，后续事件仍可复盘。实际购买超过上限会被明确标记，
不会为生成复盘而改写原上限。排除条数上限与“提示后能否显示”是独立的平台规则。

快速筛端：

```bash
python3 scripts/blindbox_solver.py examples/minimal-demo.json \
  --screen-tray --format markdown
```

整轮自动复盘（回放事件账本，逐次给出事前概率与决策质量）：

```bash
python3 scripts/blindbox_solver.py \
  examples/synthetic-session-review.json \
  --review-session --format markdown
```

千岛公开市场快照：

```bash
python3 scripts/qiandao_market_snapshot.py "漫威穿越无限" \
  --category "穿越无限系列" --retail-price 69 \
  --expected-count 13 --format markdown
```

Python 环境：求解器和市场快照脚本支持 Python 3.9 及以上版本，只使用标准库；不需要安装
第三方包。Skill 结构校验的 `quick_validate.py` 随 Codex `skill-creator` 提供，另需
PyYAML；建议放在项目外的独立校验环境，不要加入本项目运行依赖。

运行测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

## 参与开发与隐私

本仓库只接受公开白名单内的文件，示例数据必须为明确标注的合成数据。首次
clone 后运行：

```bash
git config core.hooksPath .githooks
```

内部截图、会话和研究数据请放在仓库外，或放入已忽略的 `.private/`。完整
规则见 [公开发布策略](PUBLICATION_POLICY.md)。

## 重要限制

- 精确计算要求平台符合受支持的整端模型。
- 评分可自动确定偏好档位，但概率边界仍是风险选择，必须结合当前端确认。
- `保值优先`还要求每个常规款具有当前、同口径的市场价值；数据不足时暂停。
- 截图看不清或缺少完整常规款信息时，Skill 会只追问阻塞计算的部分。
- 已售但未知的盒位仍会影响剩余盒位。
- 市场挂牌价不等于成交价，社交热度也不等于保值能力。
- 千岛单一来源最高只能给出中等置信度。
- 公开网页结构变化可能使市场脚本暂时失效。
- 本项目与 POP MART、千岛、闲鱼、小红书无官方关联。

## 项目结构

```text
.
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── LICENSE
├── scripts/
├── references/
├── assets/
├── examples/
└── tests/
```

## 开源许可

[MIT License](LICENSE)

## English summary

A skill for POP MART online blind-box decisions. Core screenshot parsing and exact tray-level probability calculations use only Python's standard library. Live mainland-market research degrades gracefully when native search or a logged-in browser is unavailable.
