import contextlib
import importlib.util
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SOLVER_PATH = ROOT / "scripts" / "blindbox_solver.py"
spec = importlib.util.spec_from_file_location(
    "blindbox_solver_agent_contract",
    SOLVER_PATH,
)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
assert spec.loader is not None
spec.loader.exec_module(solver)

FIXTURE = (
    ROOT / "tests" / "fixtures" / "agent-report-regressions.json"
)


class AgentReportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def render(self, state):
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".json",
            encoding="utf-8",
        ) as state_file:
            json.dump(state, state_file, ensure_ascii=False)
            state_file.flush()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = solver.main(
                    [state_file.name, "--format", "markdown"]
                )
        self.assertEqual(exit_code, 0, stderr.getvalue())
        return stdout.getvalue()

    def active_tray(self, state):
        if "session_schema_version" not in state:
            return state
        return next(
            tray
            for tray in state["trays"]
            if tray["id"] == state["active_tray_id"]
        )

    def draws_used(self, state):
        if "draws_used" in state:
            return state["draws_used"]
        return sum(
            box.get("status") == "opened"
            for box in self.active_tray(state)["boxes"]
        )

    def assert_short_turn_transition(self, scenario):
        message = scenario["message"]
        before = scenario.get("previous_state")
        after = scenario["state"]

        if scenario["name"] == "demand-unchanged-new-tray":
            self.assertRegex(
                message,
                r"^(重来|换一端)，?需求不变$",
            )
            self.assertIsNotNone(before)
            self.assertEqual(before["preferences"], after["preferences"])
            self.assertEqual(before["tools"], after["tools"])
            self.assertEqual(
                self.draws_used(before),
                self.draws_used(after),
            )
            previous_trays = {
                tray["id"]: tray for tray in before["trays"]
            }
            current_trays = {
                tray["id"]: tray for tray in after["trays"]
            }
            for tray_id, tray in previous_trays.items():
                self.assertEqual(current_trays[tray_id], tray)
            self.assertNotEqual(
                before["active_tray_id"],
                after["active_tray_id"],
            )
            self.assertEqual(
                after["events"][:-1],
                before["events"],
            )
            self.assertEqual(
                after["events"][-1],
                {
                    "seq": len(after["events"]),
                    "type": "tray_switch",
                    "tray_id": after["active_tray_id"],
                },
            )
            return

        if scenario["name"] in {"new-exclusion", "leader-change"}:
            match = re.fullmatch(r"(.+?)号排除了(.+)", message)
            self.assertIsNotNone(match)
            self.assertIsNotNone(before)
            box_id, design = match.groups()
            before_tray = self.active_tray(before)
            after_tray = self.active_tray(after)
            before_boxes = {
                box["id"]: box for box in before_tray["boxes"]
            }
            after_boxes = {
                box["id"]: box for box in after_tray["boxes"]
            }
            self.assertNotIn(design, before_boxes[box_id]["excluded"])
            self.assertIn(design, after_boxes[box_id]["excluded"])
            self.assertTrue(after_boxes[box_id]["tool_used"])
            for other_id, box in before_boxes.items():
                if other_id != box_id:
                    self.assertEqual(after_boxes[other_id], box)
            self.assertEqual(
                before["preferences"],
                after["preferences"],
            )
            self.assertEqual(
                before["tools"]["hint_cards"] - 1,
                after["tools"]["hint_cards"],
            )
            self.assertEqual(
                self.draws_used(before),
                self.draws_used(after),
            )
            self.assertEqual(
                after["events"][-1],
                {
                    "seq": len(after["events"]),
                    "type": "hint_used",
                    "tray_id": after["active_tray_id"],
                    "box_id": box_id,
                    "excluded": design,
                },
            )
            return

        if scenario["name"] == "no-qualified-box":
            self.assertRegex(message, r"(值得|继续).*抽")
            return

        self.fail(f"uncovered short-turn scenario: {scenario['name']}")

    def test_short_turns_reach_the_same_complete_user_report(self):
        self.assertEqual(
            self.payload["meta"]["provenance"],
            "synthetic",
        )
        for scenario in self.payload["scenarios"]:
            with self.subTest(scenario=scenario["name"]):
                self.assert_short_turn_transition(scenario)
                output = self.render(scenario["state"])
                for heading in (
                    "## 结论",
                    "## 决策依据",
                    "## TOP 3 汇总",
                    "## 全款概率矩阵",
                    "## 停止线",
                    "## 下一步",
                    "## 模型口径",
                ):
                    self.assertIn(heading, output)
                self.assertEqual(
                    output.count("隐藏款：默认未计入"),
                    1,
                )
                explanation = output.split(
                    "## 决策依据", 1
                )[1].split("## TOP 3", 1)[0]
                self.assertEqual(
                    sum(
                        line.startswith("- ")
                        for line in explanation.splitlines()
                    ),
                    3,
                )
                for design in scenario["state"].get(
                    "model",
                    scenario["state"].get("trays", [{}])[-1].get(
                        "model", {}
                    ),
                ).get("designs", []):
                    self.assertIn(f"| {design} |", output)

                expected = scenario["expected"]
                self.assertIn(expected["conclusion"], output)
                self.assertIn(
                    f"| 1 | {expected['top_box_id']}号 |",
                    output,
                )
                if "active_tray_id" in expected:
                    self.assertIn(
                        f"当前端：{expected['active_tray_id']}",
                        output,
                    )
                if "strategy" in expected:
                    self.assertIn(
                        f"本轮采用「{expected['strategy']}」",
                        output,
                    )
                if "matrix_contains" in expected:
                    self.assertIn(expected["matrix_contains"], output)
                if "event_contains" in expected:
                    self.assertIn(expected["event_contains"], output)
                if "stop_status" in expected:
                    self.assertIn(expected["stop_status"], output)

    def test_leader_change_fixture_proves_the_recommendation_changed(self):
        scenario = next(
            item
            for item in self.payload["scenarios"]
            if item["name"] == "leader-change"
        )
        before = solver.build_report(
            solver._normalize_state(scenario["previous_state"])
        )
        after_session = solver._normalize_session(scenario["state"])
        after = solver.build_session_report(
            after_session
        )

        self.assertEqual(
            before["top_3"][0],
            scenario["expected"]["previous_top_box_id"],
        )
        self.assertEqual(
            after["tray_reports"][after_session["active_tray_id"]]["top_3"][0],
            scenario["expected"]["top_box_id"],
        )
        self.assertNotEqual(
            before["top_3"][0],
            after["tray_reports"][after_session["active_tray_id"]]["top_3"][0],
        )


if __name__ == "__main__":
    unittest.main()
