"""Synthetic regressions for constrained selection and adaptive target groups."""

import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "solver_decision_boundaries", ROOT / "scripts" / "blindbox_solver.py"
)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
spec.loader.exec_module(solver)


def synthetic_state(tied=False):
    designs = ["A", "B", "C"] if tied else ["A", "B", "C", "D"]
    boxes = [{"id": str(i + 1), "excluded": []} for i in range(len(designs))]
    if not tied:
        boxes[0]["excluded"] = ["B", "D"]
        boxes[1]["excluded"] = ["C"]
        boxes[2]["status"] = boxes[3]["status"] = "sold_unknown"
    scores = {"A": 10, "B": 10 if tied else 8, "C": -10}
    if not tied:
        scores["D"] = 0
    return {
        "series": "Synthetic boundary regression",
        "model": {"type": "unique_regular", "designs": designs},
        "boxes": boxes,
        "preferences": {
            "strategy": "只冲最爱",
            "scores": scores,
            "stop_rules": {"max_hard_avoid_pp": 0, "max_draws": 1},
        },
        "tools": {"display_cards": 1 if tied else 0, "hint_cards": 0},
        "meta": {"provenance": "synthetic"},
    }


class DecisionBoundaryTests(unittest.TestCase):
    def test_renormalization_preserves_derived_groups_and_explicit_order(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                raw = synthetic_state(tied=True)
                if explicit:
                    raw["preferences"]["liked"] = ["B", "A"]
                first = solver._normalize_state(raw)
                second = solver._normalize_state(solver._public_state_copy(first))
                self.assertEqual(
                    first["preferences"]["preference_sources"],
                    second["preferences"]["preference_sources"],
                )
                self.assertEqual(
                    solver._score_derived_target_groups(first["preferences"]),
                    solver._score_derived_target_groups(second["preferences"]),
                )
                self.assertEqual(second["preferences"]["liked"], ["B", "A"] if explicit else ["A", "B"])

    def test_explicit_edit_to_normalized_list_is_respected(self):
        state = solver._normalize_state(synthetic_state(tied=True))
        state["preferences"]["liked"] = ["B", "A"]
        state = solver._normalize_state(state)
        self.assertEqual(state["preferences"]["preference_sources"]["liked"], "explicit")
        self.assertEqual(state["preferences"]["liked"], ["B", "A"])

    def test_lower_target_box_is_selected_when_it_alone_meets_risk_lines(self):
        state = solver._normalize_state(synthetic_state())
        posterior = solver.analyze_posterior(state)
        self.assertGreater(
            solver.metrics_for_box(state, posterior, "1")["p_favorite_any"],
            solver.metrics_for_box(state, posterior, "2")["p_favorite_any"],
        )
        report = solver.build_report(state, include_plan=True)
        self.assertEqual(report["draw_decision"]["best_box_id"], "2")
        self.assertTrue(report["draw_decision"]["should_draw"])
        self.assertEqual(report["next_tool_plan"]["recommended_action"]["action"], "direct_draw")
        screen = solver.build_report(state, screen_tray=True)
        self.assertEqual(screen["tray_screening"]["status"], "ready")

    def test_no_qualifying_box_still_stops_and_budget_still_applies(self):
        for rules in ({"min_favorite_any_pp": 100}, {"max_draws": 0}):
            raw = synthetic_state()
            raw["preferences"]["stop_rules"].update(rules)
            report = solver.build_report(solver._normalize_state(raw), include_plan=True)
            self.assertFalse(report["draw_decision"]["should_draw"])
            self.assertEqual(report["next_tool_plan"]["recommended_action"]["action"], "stop")

    def test_both_equal_favorites_are_selected_after_display_at_each_depth(self):
        raw = synthetic_state(tied=True)
        raw["preferences"]["stop_rules"]["min_favorite_any_pp"] = 100
        raw["tools"]["display_cards"] = 2
        state = solver._normalize_state(raw)
        posterior = solver.analyze_posterior(state)
        for depth in (1, 2):
            plan = solver.plan_tools(state, posterior, depth=depth, beam_width=0)
            for action in plan["action_ranking"]:
                if action["tool"] != "display":
                    continue
                for branch in action["branches"]:
                    if branch["outcome"] in {"A", "B"}:
                        with self.subTest(depth=depth, box=action["box_id"], result=branch["outcome"]):
                            self.assertEqual(branch["recommended_draw_after_outcome"], action["box_id"])
                            self.assertEqual(branch["best_metrics_after_outcome"]["p_favorite_any"], 1)
            if depth == 1:
                # C reveals a safe remaining box too: all branches get a favorite.
                self.assertAlmostEqual(plan["recommended_action"]["expected_terminal_metrics"]["p_favorite_any"], 1)

    def test_report_explains_constraint_order_instead_of_tolerance(self):
        state = solver._normalize_state(synthetic_state())
        report = solver.build_report(state, include_plan=True)
        text = solver._strategy_comparison_sentence(report)
        self.assertIn("停止线", text)
        self.assertNotIn("排序容差", text)

    def test_last_draw_next_action_ends_session_using_global_budget(self):
        for cap, used in ((1, 0), (2, 1)):
            state = solver._normalize_state(synthetic_state())
            state["preferences"]["stop_rules"]["max_draws"] = cap
            state["_session_draws_used"] = used
            report = solver.build_report(state, include_plan=True)
            conclusion, action = solver._conclusion_and_next_action(report)
            self.assertIn("建议抽", conclusion)
            self.assertIn("结束", action)
            self.assertNotIn("再判断是否继续", action)

    def test_more_draws_remain_does_not_claim_session_over(self):
        raw = synthetic_state(tied=True)
        raw["tools"]["display_cards"] = 0
        raw["preferences"]["stop_rules"] = {"max_draws": 2}
        report = solver.build_report(solver._normalize_state(raw), include_plan=True)
        _, action = solver._conclusion_and_next_action(report)
        self.assertNotIn("结束", action)


if __name__ == "__main__":
    unittest.main()
