"""Synthetic end-to-end coverage for partial history, real overruns and card rules."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workflow_solver", ROOT / "scripts/blindbox_solver.py")
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
spec.loader.exec_module(solver)


def state():
    return {
        "series": "Synthetic workflow",
        "model": {"type": "unique_regular", "designs": ["A", "B", "C", "D"]},
        "boxes": [{"id": str(i), "excluded": []} for i in range(1, 5)],
        "preferences": {"strategy": "只冲最爱", "scores": {"A": 10, "B": 10, "C": 8, "D": -10},
                        "stop_rules": {"max_draws": 1}},
        "tools": {"hint_cards": 2, "display_cards": 1},
    }


def session(raw):
    return solver.export_session_state(solver._normalize_session(raw))


def add_event(payload, kind, **fields):
    payload["events"].append(dict(seq=len(payload["events"]) + 1, type=kind,
                                  tray_id=payload["active_tray_id"], **fields))


def cli(raw, *flags):
    return subprocess.run([sys.executable, str(ROOT / "scripts/blindbox_solver.py"), "-", *flags],
                          input=json.dumps(raw), text=True, capture_output=True)


class WorkflowTests(unittest.TestCase):
    def test_real_overrun_is_reported_without_raising_or_relaxing_budget(self):
        raw = session(state())
        for i, design in enumerate(["C", "A"]):
            raw["trays"][0]["boxes"][i].update(status="opened", known=design)
            add_event(raw, "opened_result", box_id=str(i + 1), design=design)
        raw["draws_used"] = 2
        report = solver.build_session_review_report(solver._normalize_session(raw))
        solver.validate_session_review_report(report)
        self.assertEqual(report["global_counters"]["max_draws"], 1)
        self.assertEqual(report["global_counters"]["draws_over_budget"], 1)
        self.assertFalse(report["replay"]["openings"][1]["should_draw_at_decision"])
        checks = report["replay"]["openings"][1]["quality_lines_at_draw"]
        self.assertFalse(next(c for c in checks if c["rule"] == "max_draws")["passed"])
        result = cli(raw, "--review-session", "--format", "markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("实际超出原抽盒上限 1 盒", result.stdout)

    def test_initial_used_card_does_not_erase_later_history(self):
        raw = state()
        raw["boxes"][0].update(excluded=["D"], tool_used=True, tools_used=["hint"])
        payload = session(raw)
        payload["trays"][0]["boxes"][1].update(status="opened", known="C")
        add_event(payload, "opened_result", box_id="2", design="C")
        payload["draws_used"] = 1
        report = solver.build_session_review_report(solver._normalize_session(payload))
        solver.validate_session_review_report(report)
        self.assertTrue(report["replay"]["recoverable"])
        self.assertEqual(len(report["replay"]["openings"]), 1)
        self.assertEqual(report["replay"]["tool_cards"], [])
        self.assertEqual(len(report["replay"]["unrecoverable_items"]), 1)
        self.assertIn("不可恢复项", solver.render_session_review_markdown(report))

    def test_initial_openings_count_towards_budget_without_invented_events(self):
        raw = state()
        raw["boxes"][0].update(status="opened", known="C")
        payload = session(raw)
        payload["trays"][0]["boxes"][1].update(status="opened", known="A")
        payload["draws_used"] = 2
        add_event(payload, "opened_result", box_id="2", design="A")
        report = solver.build_session_review_report(solver._normalize_session(payload))
        solver.validate_session_review_report(report)
        self.assertEqual(report["replay"]["baseline"]["draws_used"], 1)
        self.assertEqual(len(report["replay"]["openings"]), 1)
        self.assertFalse(report["replay"]["openings"][0]["should_draw_at_decision"])

    def test_exported_acceptance_survives_a_later_exhausted_budget(self):
        raw = state()
        raw["preferences"]["stop_rules"]["min_favorite_any_pp"] = 20
        payload = session(raw)
        payload["trays"][0]["boxes"][0].update(known="A", tool_used=True, tools_used=["display"])
        payload["tools"]["display_cards"] = 0
        add_event(payload, "display_used", box_id="1", design="A")
        normalized = solver._normalize_session(payload)
        self.assertEqual(normalized["accepted_tray_id"], payload["active_tray_id"])
        saved = solver.export_session_state(normalized)
        saved["trays"][0]["boxes"][0]["status"] = "opened"
        saved["draws_used"] = 1
        add_event(saved, "opened_result", box_id="1", design="A")
        again = solver._normalize_session(saved)
        self.assertEqual(again["accepted_tray_id"], payload["active_tray_id"])
        solver.validate_session_review_report(solver.build_session_review_report(again))

    def test_conflicting_initial_snapshot_is_rejected(self):
        raw = session(state())
        raw["trays"][0]["boxes"][0]["excluded"] = ["A"]
        with self.assertRaisesRegex(solver.StateError, "initial_boxes contradict"):
            solver._normalize_session(raw)

    def test_exclusion_cap_does_not_disable_display(self):
        raw = state()
        raw["model"]["tool_rules"] = {"max_exclusions": 2, "display_after_hint": True}
        raw["boxes"][0].update(excluded=["C", "D"], tool_used=True, tools_used=["hint"])
        n = solver._normalize_state(raw)
        plan = solver.plan_tools(n, solver.analyze_posterior(n))
        actions = {(a["tool"], a["box_id"]) for a in plan["action_ranking"]}
        self.assertNotIn(("hint", "1"), actions)
        self.assertIn(("display", "1"), actions)
        raw["model"]["tool_rules"]["display_after_hint"] = False
        n = solver._normalize_state(raw)
        self.assertNotIn(("display", "1"), {(a["tool"], a["box_id"]) for a in
                         solver.plan_tools(n, solver.analyze_posterior(n))["action_ranking"]})

    def test_hint_then_display_replays_both_cards_when_confirmed(self):
        raw = state()
        raw["model"]["tool_rules"] = {"max_exclusions": 2, "display_after_hint": True}
        payload = session(raw)
        payload["trays"][0]["boxes"][0].update(excluded=["D"], tool_used=True,
                                               tools_used=["hint", "display"], known="A")
        payload["tools"].update(hint_cards=1, display_cards=0)
        add_event(payload, "hint_used", box_id="1", excluded="D")
        add_event(payload, "display_used", box_id="1", design="A")
        report = solver.build_session_review_report(solver._normalize_session(payload))
        solver.validate_session_review_report(report)
        self.assertEqual(len(report["replay"]["tool_cards"]), 2)
        self.assertTrue(report["replay"]["tool_cards"][1]["ex_ante_branch_available"])

    def test_unknown_stacking_rule_changes_decision_and_requests_confirmation(self):
        raw = state()
        raw["boxes"][0].update(excluded=["C"], tool_used=True, tools_used=["hint"])
        raw["model"]["tool_rules"] = {"display_after_hint": None}
        raw["preferences"]["stop_rules"].update(min_favorite_any_pp=60, max_hard_avoid_pp=0)
        report = solver.build_report(solver._normalize_state(raw), include_plan=True)
        self.assertIn("tool_rule_confirmation", report)
        self.assertIsNone(raw["model"]["tool_rules"]["display_after_hint"])
        text = solver.render_explanation_markdown(report)
        self.assertIn(report["tool_rule_confirmation"]["question"], text)
        self.assertNotIn("建议先对", text)
        result = cli(raw, "--screen-tray", "--format", "markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(report["tool_rule_confirmation"]["question"], result.stdout)

    def test_equivalent_stacking_policy_does_not_ask_redundant_question(self):
        raw = state()
        raw["boxes"][0].update(tool_used=True, tools_used=["hint"])
        report = solver.build_report(solver._normalize_state(raw), include_plan=True)
        self.assertNotIn("tool_rule_confirmation", report)

    def test_budget_overrun_counter_cannot_be_forged(self):
        raw = state()
        raw["boxes"][0].update(status="opened", known="A")
        raw["boxes"][1].update(status="opened", known="C")
        report = solver.build_session_review_report(solver._normalize_session(raw))
        self.assertEqual(report["global_counters"]["draws_over_budget"], 1)
        report["global_counters"]["draws_over_budget"] = 0
        with self.assertRaisesRegex(solver.StateError, "budget overrun"):
            solver.validate_session_review_report(report)

    def test_inventory_unavailable_is_not_fabricated_card_usage(self):
        raw = session(state())
        add_event(raw, "tools_updated", before={"hint_cards": 2, "display_cards": 1},
                  after={"hint_cards": 0, "display_cards": 0}, reason="用户报告本场卡片已不可用")
        raw["tools"] = {"hint_cards": 0, "display_cards": 0}
        report = solver.build_session_review_report(solver._normalize_session(raw))
        solver.validate_session_review_report(report)
        self.assertEqual(report["replay"]["baseline"]["tools"]["hint_cards"], 2)
        self.assertEqual(report["replay"]["tool_cards"], [])

    def test_explanation_uses_the_same_action_and_branch_probabilities(self):
        raw = state()
        n = solver._normalize_state(raw)
        report = solver.build_report(n, include_plan=True)
        text = solver.render_explanation_markdown(report)
        self.assertIn(solver._conclusion_and_next_action(report)[0], text)
        action = report["next_tool_plan"]["recommended_action"]
        self.assertEqual(action["tool"], "display")
        self.assertAlmostEqual(sum(b["probability"] for b in action["branches"]), 1)
        for branch in action["branches"]:
            self.assertIn(solver._percent(branch["probability"]), text)
        result = cli(raw, "--explain", "--format", "markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, text)

    def test_single_tray_export_cli_can_be_read_back(self):
        result = cli(state(), "--session-state")
        self.assertEqual(result.returncode, 0, result.stderr)
        n = solver._normalize_session(json.loads(result.stdout))
        self.assertFalse(n["_legacy_input"])
        self.assertEqual(len(n["_tray_states"]), 1)


if __name__ == "__main__":
    unittest.main()
