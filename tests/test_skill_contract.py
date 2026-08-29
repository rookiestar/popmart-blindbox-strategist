import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]


class SkillContractTests(unittest.TestCase):
    def test_market_sources_are_strictly_bounded(self):
        skill = (ROOT / "SKILL.md").read_text()
        market = (ROOT / "references" / "market-research.md").read_text()

        self.assertIn(
            "Use exactly POP MART official facts, 千岛、闲鱼、小红书.",
            skill,
        )
        self.assertIn("Exclude 淘宝、京东", market)
        self.assertIn("Keep the allowlist fixed.", market)

    def test_logged_in_search_is_batched_and_read_only(self):
        browser = (
            ROOT / "references" / "market-browser-search.md"
        ).read_text()

        self.assertIn("not a required dependency", browser)
        self.assertIn("whatever browser tools the host actually provides", browser)
        self.assertIn("one DOM extraction", browser)
        self.assertIn("section.note-item", browser)
        self.assertIn("Do not export Cookie, Storage State", browser)
        self.assertIn("remove it from returned records", browser)
        self.assertIn("Never ask the user to share", browser)

    def test_market_research_is_api_key_free_and_degrades_gracefully(self):
        skill = (ROOT / "SKILL.md").read_text()
        market = (ROOT / "references" / "market-research.md").read_text()
        readme = (ROOT / "README.md").read_text()

        self.assertIn("Stay API-key-free", skill)
        self.assertIn("Do not ask the user to create an API key", skill)
        self.assertIn("## Capability gate", market)
        self.assertIn("disable resale-based ranking", market)
        self.assertIn("**不需要**", readme)
        tavily_key_name = "TAVILY" + "_API_KEY"
        self.assertNotIn(tavily_key_name, skill + market + readme)

    def test_public_repo_root_is_a_self_contained_skill(self):
        for path in (
            "SKILL.md",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "scripts/blindbox_solver.py",
            "scripts/qiandao_market_snapshot.py",
            "references/market-research.md",
        ):
            self.assertTrue((ROOT / path).is_file(), path)

        self.assertFalse(
            (ROOT / ".skillshare" / "skills" / ".metadata.json").exists()
        )
        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and "__pycache__" not in path.parts
            and path.suffix in {".md", ".json", ".py"}
        )
        machine_home_prefix = "/" + "Users" + "/"
        self.assertNotIn(machine_home_prefix, public_text)

    def test_public_examples_are_explicitly_synthetic(self):
        examples = sorted((ROOT / "examples").glob("*.json"))
        self.assertGreaterEqual(len(examples), 5)
        for path in examples:
            with self.subTest(example=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload.get("meta", {}).get("provenance"),
                    "synthetic",
                )

    def test_browser_reference_pointer_exists(self):
        skill = (ROOT / "SKILL.md").read_text()
        reference = ROOT / "references" / "market-browser-search.md"

        self.assertTrue(reference.is_file())
        self.assertIn(
            "`references/market-browser-search.md`",
            skill,
        )

    def test_plain_language_strategy_contract_is_discoverable(self):
        skill = (ROOT / "SKILL.md").read_text()
        strategies = (
            ROOT / "references" / "preference-strategies.md"
        ).read_text()

        self.assertIn("`references/preference-strategies.md`", skill)
        for name in (
            "稳妥避雷",
            "守住底线",
            "整体最满意",
            "随便中个喜欢",
            "只冲最爱",
            "保值优先",
        ):
            self.assertIn(name, skill)
            self.assertIn(name, strategies)

        for tier in ("硬雷", "轻雷", "中性但失望"):
            self.assertIn(tier, strategies)

        for score_range in (
            "`+10`",
            "`+6` 至 `+9`",
            "`+1` 至 `+5`",
            "`0`",
            "`-1` 至 `-4`",
            "`-5` 至 `-8`",
            "`-9` 至 `-10`",
        ):
            self.assertIn(score_range, strategies)

        self.assertIn("七档默认规则", strategies)
        self.assertIn("显式空数组也覆盖自动推导", strategies)
        self.assertIn("其余款 0", strategies)
        self.assertIn("score_default_confirmed", strategies)
        self.assertIn("--calibrate-preferences", strategies)
        self.assertIn("min_favorite_any_pp", strategies)
        self.assertIn("所有 `+10` 最爱款的合计概率", strategies)

    def test_hidden_designs_are_ignored_by_default(self):
        skill = (ROOT / "SKILL.md").read_text()
        market = (ROOT / "references" / "market-research.md").read_text()
        browser = (
            ROOT / "references" / "market-browser-search.md"
        ).read_text()
        output = (ROOT / "references" / "output-templates.md").read_text()
        probability = (
            ROOT / "references" / "probability-model.md"
        ).read_text()
        schema = (ROOT / "references" / "state-schema.md").read_text()

        self.assertIn("隐藏款：默认未计入", skill)
        self.assertIn("隐藏款：默认未计入", output)
        for document in (skill, market, browser, probability, schema):
            self.assertIn("only when the user explicitly requests", document)

        self.assertNotIn("<regulars-plus-secrets>", skill)
        self.assertNotIn("<regulars-plus-secrets>", market)
        self.assertNotIn("proposed hot, proposed weak, and secret", market)
        self.assertNotIn("proposed hot, proposed weak, and secret", browser)
        self.assertNotIn(
            "regular-only main analysis plus a clearly labeled sensitivity note",
            skill,
        )

    def test_tool_planner_compares_the_no_card_action(self):
        skill = (ROOT / "SKILL.md").read_text()
        probability = (
            ROOT / "references" / "probability-model.md"
        ).read_text()
        output = (ROOT / "references" / "output-templates.md").read_text()
        evals = (ROOT / "references" / "review-and-evals.md").read_text()

        self.assertIn("direct draw or stop", skill)
        self.assertIn("no-card action wins numerical zero-uplift ties", probability)
        self.assertIn("直接抽 / 停止 / 使用提示卡 / 使用显示卡", output)
        self.assertIn("No-card action", evals)

    def test_session_lock_and_model_warning_contracts_are_discoverable(self):
        skill = (ROOT / "SKILL.md").read_text()
        schema = (ROOT / "references" / "state-schema.md").read_text()
        probability = (
            ROOT / "references" / "probability-model.md"
        ).read_text()
        output = (ROOT / "references" / "output-templates.md").read_text()
        evals = (ROOT / "references" / "review-and-evals.md").read_text()

        for token in (
            "accepted_tray_id",
            "tray_accepted",
            "tray_released",
            "stop_rule_override",
        ):
            self.assertIn(token, skill)
            self.assertIn(token, schema)
        for token in (
            "regular_only_scope",
            "hint_mechanism_assumed",
            "条件概率",
        ):
            self.assertIn(token, skill)
            self.assertIn(token, probability)
            self.assertIn(token, output)
        self.assertIn("Accepted-tray lock and overrides", evals)
        self.assertIn("Conditional-probability warnings", evals)

    def test_timed_tray_screening_fast_path_is_discoverable(self):
        skill = (ROOT / "SKILL.md").read_text()
        screening_path = ROOT / "references" / "tray-screening.md"

        self.assertTrue(screening_path.is_file())
        screening = screening_path.read_text()
        self.assertIn("`references/tray-screening.md`", skill)
        self.assertIn("--screen-tray", skill)
        self.assertIn("3–5", screening)
        self.assertIn("提示条数", screening)
        for status in (
            "ready",
            "tool_dependent",
            "switch",
            "accepted_review",
            "session_stop",
            "needs_acceptance_rules",
        ):
            self.assertIn(f"`{status}`", screening)

    def test_screenshot_only_guided_intake_is_discoverable(self):
        skill = (ROOT / "SKILL.md").read_text()
        readme = (ROOT / "README.md").read_text()
        guided_path = ROOT / "references" / "guided-intake.md"

        self.assertTrue(guided_path.is_file())
        guided = guided_path.read_text()
        self.assertIn("`references/guided-intake.md`", skill)
        self.assertIn("Stage 0 — Guided intake", skill)
        self.assertIn("截图单独输入", guided)
        self.assertIn("每轮只问一个", guided)
        self.assertIn("一次回答多个信息时全部吸收", guided)
        self.assertIn("不要重复询问", guided)
        self.assertIn("AI 负责识别", guided)
        self.assertIn("用户只回答主观决策", guided)
        self.assertIn("只发一张截图也可以", readme)

    def test_guided_intake_has_a_bounded_decision_flow(self):
        guided = (ROOT / "references" / "guided-intake.md").read_text()

        ordered_steps = (
            "截图解析",
            "阻塞信息澄清",
            "目标",
            "逐款评分",
            "必须抽 / 可换端或停止",
            "卡片与预算",
            "当前端校准",
            "边界确认",
            "决策合同",
            "精确计算",
            "新线索后全局重算",
        )
        positions = [guided.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("约 3 分钟", guided)
        self.assertIn("一次收集最少必要信息", guided)
        self.assertIn("不得套用固定 `15%`", guided)
        self.assertIn("--calibrate-preferences", guided)
        self.assertIn("明确确认", guided)
        self.assertIn("保值优先", guided)
        self.assertIn("用户主动询问市场", guided)

    def test_score_first_calibration_is_a_confirmation_gate(self):
        skill = (ROOT / "SKILL.md").read_text()
        strategies = (
            ROOT / "references" / "preference-strategies.md"
        ).read_text()
        schema = (ROOT / "references" / "state-schema.md").read_text()
        probability = (
            ROOT / "references" / "probability-model.md"
        ).read_text()
        output = (ROOT / "references" / "output-templates.md").read_text()
        evals = (ROOT / "references" / "review-and-evals.md").read_text()
        template = json.loads(
            (ROOT / "assets" / "session-state-template.json").read_text()
        )

        for document in (skill, strategies, schema, probability, output):
            self.assertIn("--calibrate-preferences", document)
        for document in (skill, strategies, schema, output):
            self.assertIn("score_default_confirmed", document)
        self.assertIn("stop_rules_mutated: false", schema)
        self.assertIn("confirmation_required: true", schema)
        self.assertIn("scheme 1/2/3", output)
        self.assertIn("保值优先", strategies)
        self.assertIn("min_resale_ev", schema)
        self.assertIn("min_resale_ev", probability)
        self.assertIn("Numbered calibration schemes", evals)
        self.assertIn("not a draw recommendation", skill)
        self.assertIn("current series session", schema)
        self.assertIn("E23 — Score-first preference calibration", evals)
        self.assertNotIn("liked", template["preferences"])
        self.assertNotIn("disliked", template["preferences"])
        self.assertNotIn("hard_avoid", template["preferences"])
        self.assertNotIn("score_default", template["preferences"])

    def test_guidance_state_and_response_contract_are_documented(self):
        schema = (ROOT / "references" / "state-schema.md").read_text()
        output = (ROOT / "references" / "output-templates.md").read_text()

        for field in (
            '"mode": "guided"',
            '"phase": "goal"',
            '"settled"',
            '"pending_question"',
            '"defaults_applied"',
            '"confirmed": false',
        ):
            self.assertIn(field, schema)

        self.assertIn("## I. Guided intake — single question", output)
        self.assertIn("第1步：这次你最看重什么？", output)
        self.assertIn("## J. Guided intake — decision contract", output)
        self.assertIn("请回复“确认”", output)
        self.assertNotIn(
            "截图 + 偏好分档/评分 + 提示卡/显示卡数量 + 本轮策略。",
            output,
        )
        self.assertIn("直接上传当前端截图即可", output)

    def test_formal_decisions_relay_one_validated_markdown_contract(self):
        skill = (ROOT / "SKILL.md").read_text()
        output = (ROOT / "references" / "output-templates.md").read_text()
        evals = (ROOT / "references" / "review-and-evals.md").read_text()

        self.assertIn(
            "python3 scripts/blindbox_solver.py <state.json> --format markdown",
            skill,
        )
        self.assertIn("原样转交 stdout", skill)
        self.assertIn("不得手工摘要", skill)
        for trigger in (
            "初次正式建议",
            "保留当前端",
            "真实提示或显示结果",
            "开盒后的追抽判断",
            "重来，需求不变",
            "排除了",
        ):
            self.assertIn(trigger, skill)

        self.assertIn("机器契约的唯一事实来源", output)
        self.assertIn("--format markdown", output)
        self.assertNotIn("### 1）X号：所有未显式排除选项", output)

        self.assertIn("E22 — Standard report handoff", evals)
        self.assertIn("agent-report-regressions.json", evals)


if __name__ == "__main__":
    unittest.main()
