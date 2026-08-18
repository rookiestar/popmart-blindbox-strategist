import copy
import importlib.util
import math
import pathlib
import sys
import unittest

MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "blindbox_solver.py"
spec = importlib.util.spec_from_file_location("blindbox_solver", MODULE_PATH)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
assert spec.loader is not None
spec.loader.exec_module(solver)


def base_state():
    return {
        "series": "demo",
        "model": {
            "type": "unique_regular",
            "designs": ["A", "B", "C"],
            "hint_labels": ["A", "B", "C"],
        },
        "boxes": [
            {"id": "1", "excluded": ["C"], "status": "available", "tool_used": False},
            {"id": "2", "excluded": ["A"], "status": "available", "tool_used": False},
            {"id": "3", "excluded": [], "status": "sold_unknown", "tool_used": False},
        ],
        "preferences": {
            "liked": ["A"],
            "disliked": ["C"],
            "objective_mode": "risk_first",
            "tie_tolerance_pp": 0.0,
        },
        "tools": {"hint_cards": 1, "display_cards": 1},
    }


class SolverTests(unittest.TestCase):
    def normalized(self, state=None):
        return solver._normalize_state(state or base_state())

    def test_exact_matching_and_marginals(self):
        state = self.normalized()
        posterior = solver.analyze_posterior(state)
        self.assertEqual(posterior.exact_valid_assignments, 3)
        self.assertAlmostEqual(posterior.marginals["1"]["A"], 2 / 3)
        self.assertAlmostEqual(posterior.marginals["1"]["B"], 1 / 3)
        self.assertAlmostEqual(posterior.marginals["1"]["C"], 0.0)
        self.assertAlmostEqual(posterior.marginals["2"]["B"], 1 / 3)
        self.assertAlmostEqual(posterior.marginals["2"]["C"], 2 / 3)
        self.assertAlmostEqual(sum(posterior.marginals["3"].values()), 1.0)

    def test_sold_unknown_box_stays_in_joint_model(self):
        state = self.normalized()
        report = solver.build_report(state)
        self.assertEqual(report["model_summary"]["exact_valid_assignments"], 3)
        # Only drawable boxes appear in the ranking, but the sold box affected the count.
        self.assertEqual([r["box_id"] for r in report["ranking"]], ["1", "2"])

    def test_known_opened_item_updates_every_box(self):
        raw = base_state()
        raw["boxes"][2].update({"status": "opened", "known": "C"})
        state = self.normalized(raw)
        posterior = solver.analyze_posterior(state)
        self.assertEqual(posterior.exact_valid_assignments, 1)
        self.assertAlmostEqual(posterior.marginals["1"]["A"], 1.0)
        self.assertAlmostEqual(posterior.marginals["2"]["B"], 1.0)

    def test_risk_first_prefers_avoiding_highest_ranked_dislike(self):
        raw = {
            "series": "risk",
            "model": {"type": "unique_regular", "designs": ["L", "D1", "D2"]},
            "boxes": [
                {"id": "1", "excluded": ["D1"], "status": "available"},
                {"id": "2", "excluded": ["D2"], "status": "available"},
                {"id": "3", "excluded": [], "status": "sold_unknown"},
            ],
            "preferences": {
                "liked": ["L"],
                "disliked": ["D1", "D2"],
                "objective_mode": "risk_first",
                "tie_tolerance_pp": 0,
            },
            "tools": {},
        }
        report = solver.build_report(self.normalized(raw))
        self.assertEqual(report["ranking"][0]["box_id"], "1")

    def test_top_target_first_mode(self):
        raw = base_state()
        raw["preferences"].update(
            {"liked": ["A", "B"], "disliked": [], "objective_mode": "top_target_first"}
        )
        report = solver.build_report(self.normalized(raw))
        self.assertEqual(report["ranking"][0]["box_id"], "1")

    def test_plain_language_strategy_names_map_to_expected_modes(self):
        expected = {
            "稳妥避雷": "risk_first",
            "守住底线": "guardrail",
            "整体最满意": "balanced",
            "随便中个喜欢": "target_only",
            "只冲最爱": "top_target_first",
            "保值优先": "resale_ev",
        }
        for strategy, mode in expected.items():
            raw = base_state()
            raw["preferences"].pop("objective_mode", None)
            raw["preferences"]["strategy"] = strategy
            if mode in {"guardrail", "balanced"}:
                raw["preferences"].update(
                    {
                        "scores": {"A": 10, "B": 0, "C": -10},
                        "hard_avoid": ["C"],
                        "hard_avoid_max_pp": 80,
                    }
                )
            if mode == "resale_ev":
                raw["market_values"] = {"A": 100, "B": 50, "C": 0}
            state = self.normalized(raw)
            self.assertEqual(state["preferences"]["objective_mode"], mode)
            self.assertEqual(state["preferences"]["strategy"], strategy)

    def test_legacy_strategy_aliases_remain_supported(self):
        expected = {
            "先避雷": "risk_first",
            "守底线": "guardrail",
            "总体最满意": "balanced",
            "喜欢就行": "target_only",
            "优先保值": "resale_ev",
        }
        for strategy, mode in expected.items():
            raw = base_state()
            raw["preferences"].pop("objective_mode", None)
            raw["preferences"]["strategy"] = strategy
            if mode in {"guardrail", "balanced"}:
                raw["preferences"].update(
                    {
                        "scores": {"A": 10, "B": 0, "C": -10},
                        "hard_avoid": ["C"],
                        "hard_avoid_max_pp": 80,
                    }
                )
            if mode == "resale_ev":
                raw["market_values"] = {"A": 100, "B": 50, "C": 0}
            self.assertEqual(
                self.normalized(raw)["preferences"]["objective_mode"], mode
            )

    def test_custom_scores_can_change_balanced_ranking(self):
        raw = {
            "series": "scores",
            "model": {"type": "unique_regular", "designs": ["A", "B", "C", "D"]},
            "boxes": [
                {"id": "1", "excluded": ["B", "D"], "status": "available"},
                {"id": "2", "excluded": ["A", "C"], "status": "available"},
                {"id": "3", "excluded": ["B", "D"], "status": "sold_unknown"},
                {"id": "4", "excluded": ["A", "C"], "status": "sold_unknown"},
            ],
            "preferences": {
                "liked": ["A", "B"],
                "disliked": [],
                "strategy": "整体最满意",
                "scores": {"A": 10, "B": 8, "C": 0, "D": 0},
                "tie_tolerance_pp": 0,
            },
            "tools": {},
        }
        report = solver.build_report(self.normalized(raw))
        self.assertEqual(report["ranking"][0]["box_id"], "1")
        self.assertAlmostEqual(report["ranking"][0]["expected_score"], 5)

        raw["preferences"] = {
            "liked": ["A", "B"],
            "disliked": [],
            "strategy": "整体最满意",
            "scores": {"A": 6, "B": 10, "C": 0, "D": 0},
            "tie_tolerance_pp": 0,
        }
        report = solver.build_report(self.normalized(raw))
        self.assertEqual(report["ranking"][0]["box_id"], "2")
        self.assertAlmostEqual(report["ranking"][0]["expected_score"], 5)

    def test_score_default_fills_unlisted_designs_only_with_explicit_scores(self):
        raw = base_state()
        raw["preferences"] = {
            "liked": ["A"],
            "disliked": ["C"],
            "strategy": "整体最满意",
            "scores": {"A": 10, "C": -10},
            "score_default": -2,
        }
        state = self.normalized(raw)
        self.assertEqual(state["preferences"]["scores"]["B"], -2)

        old = base_state()
        old["preferences"]["score_default"] = 0
        state = self.normalized(old)
        self.assertEqual(state["preferences"]["scores"], {})
        self.assertEqual(state["preferences"]["score_source"], "legacy_rank_weights")

    def test_scores_only_derive_all_seven_default_tiers(self):
        raw = {
            "series": "seven-tiers",
            "model": {
                "type": "unique_regular",
                "designs": ["F", "L", "A", "N", "U", "D", "H"],
            },
            "boxes": [
                {"id": str(index), "excluded": [], "status": "available"}
                for index in range(1, 8)
            ],
            "preferences": {
                "scores": {
                    "F": 10,
                    "L": 9,
                    "A": 5,
                    "N": 0,
                    "U": -4,
                    "D": -8,
                    "H": -10,
                }
            },
            "tools": {},
        }
        state = self.normalized(raw)
        preferences = state["preferences"]

        self.assertEqual(
            preferences["score_tiers"],
            {
                "favorite": ["F"],
                "liked": ["L"],
                "acceptable": ["A"],
                "neutral": ["N"],
                "neutral_disappointed": ["U"],
                "light_dislike": ["D"],
                "hard_avoid": ["H"],
            },
        )
        self.assertEqual(preferences["liked"], ["F", "L"])
        self.assertEqual(preferences["disliked"], ["H", "D"])
        self.assertEqual(preferences["hard_avoid"], ["H"])

    def test_score_tier_boundaries_match_the_default_rule(self):
        expected = {
            10: "favorite",
            9: "liked",
            6: "liked",
            5: "acceptable",
            1: "acceptable",
            0: "neutral",
            -1: "neutral_disappointed",
            -4: "neutral_disappointed",
            -5: "light_dislike",
            -8: "light_dislike",
            -9: "hard_avoid",
            -10: "hard_avoid",
        }
        for score, tier in expected.items():
            with self.subTest(score=score):
                self.assertEqual(solver._score_tier(score), tier)

    def test_explicit_preference_lists_override_score_defaults_including_empty(self):
        raw = {
            "series": "explicit-overrides",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C", "D"],
            },
            "boxes": [
                {"id": str(index), "excluded": [], "status": "available"}
                for index in range(1, 5)
            ],
            "preferences": {
                "liked": [],
                "disliked": ["B"],
                "hard_avoid": [],
                "scores": {"A": 10, "B": 7, "C": -6, "D": -10},
            },
            "tools": {},
        }
        preferences = self.normalized(raw)["preferences"]

        self.assertEqual(preferences["liked"], [])
        self.assertEqual(preferences["disliked"], ["B"])
        self.assertEqual(preferences["hard_avoid"], [])
        self.assertEqual(
            preferences["preference_sources"],
            {
                "liked": "explicit",
                "disliked": "explicit",
                "hard_avoid": "explicit",
            },
        )

    def test_score_default_participates_in_default_tiers(self):
        raw = base_state()
        raw["preferences"] = {
            "scores": {"A": 10},
            "score_default": -6,
        }
        preferences = self.normalized(raw)["preferences"]

        self.assertEqual(preferences["liked"], ["A"])
        self.assertEqual(preferences["disliked"], ["B", "C"])
        self.assertEqual(preferences["hard_avoid"], [])
        self.assertEqual(preferences["score_tiers"]["light_dislike"], ["B", "C"])

    def test_target_only_can_run_from_scores_without_explicit_liked(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "随便中个喜欢",
            "scores": {"A": 10, "B": 6, "C": 0},
        }
        state = self.normalized(raw)
        report = solver.build_report(state)

        self.assertEqual(state["preferences"]["liked"], ["A", "B"])
        self.assertEqual(report["preference_summary"]["liked"], ["A", "B"])
        self.assertEqual(report["preference_summary"]["score_tiers"]["favorite"], ["A"])
        self.assertTrue(report["draw_decision"]["should_draw"])

    def test_guardrail_prefers_best_score_within_hard_limit(self):
        raw = base_state()
        raw["preferences"] = {
            "liked": ["A"],
            "disliked": ["C"],
            "strategy": "守住底线",
            "scores": {"A": 10, "B": 1, "C": -10},
            "hard_avoid": ["C"],
            "hard_avoid_max_pp": 50,
        }
        report = solver.build_report(self.normalized(raw))
        self.assertEqual(report["ranking"][0]["box_id"], "1")
        self.assertTrue(report["draw_decision"]["should_draw"])

    def test_guardrail_stops_when_every_box_exceeds_hard_limit(self):
        raw = {
            "series": "guardrail",
            "model": {"type": "unique_regular", "designs": ["A", "D"]},
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": ["D"],
                "strategy": "守住底线",
                "scores": {"A": 10, "D": -10},
                "hard_avoid": ["D"],
                "hard_avoid_max_pp": 40,
            },
            "tools": {},
        }
        report = solver.build_report(self.normalized(raw))
        self.assertFalse(report["draw_decision"]["should_draw"])
        self.assertIn("没有盒子满足硬雷不超过", report["draw_decision"]["reasons"][0])

    def test_stop_rules_fail_closed_if_any_condition_fails(self):
        raw = base_state()
        raw["preferences"].update(
            {
                "stop_rules": {
                    "min_like_any_pp": 70,
                    "max_dislike_any_pp": 80,
                    "max_draws": 2,
                }
            }
        )
        report = solver.build_report(self.normalized(raw))
        self.assertFalse(report["draw_decision"]["should_draw"])
        self.assertEqual(len(report["draw_decision"]["reasons"]), 1)
        self.assertIn("喜欢款概率", report["draw_decision"]["reasons"][0])

        raw["boxes"][2].update({"status": "opened", "known": "B"})
        raw["preferences"]["stop_rules"] = {"max_draws": 1}
        report = solver.build_report(self.normalized(raw))
        self.assertFalse(report["draw_decision"]["should_draw"])
        self.assertIn("达到最多 1 盒", report["draw_decision"]["reasons"][0])

    def test_tray_screening_marks_a_directly_qualified_tray_ready(self):
        raw = base_state()
        raw["preferences"]["stop_rules"] = {
            "min_like_any_pp": 60,
            "max_dislike_any_pp": 10,
        }
        report = solver.build_report(self.normalized(raw), screen_tray=True)
        screening = report["tray_screening"]

        self.assertEqual(screening["status"], "ready")
        self.assertEqual(screening["recommendation"], "keep")
        self.assertEqual(screening["direct_best_box_id"], "1")
        self.assertEqual(screening["planning_depth"], 1)
        self.assertEqual(
            screening["default_start_rule"],
            "all_acceptance_rules_pass_after_default_shake",
        )
        self.assertEqual(screening["comparison_basis"], "posterior_metrics")
        self.assertFalse(screening["future_tray_improvement_guaranteed"])
        checks = {check["rule"]: check for check in screening["acceptance_profile"]}
        self.assertTrue(checks["min_like_any_pp"]["passed"])
        self.assertTrue(checks["max_dislike_any_pp"]["passed"])
        self.assertAlmostEqual(checks["min_like_any_pp"]["actual"], 200 / 3)
        self.assertAlmostEqual(checks["min_like_any_pp"]["margin"], 20 / 3)

    def test_tray_screening_marks_a_card_rescuable_tray_tool_dependent(self):
        raw = {
            "series": "tool-dependent-tray",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C"],
            },
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
                {"id": "3", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "strategy": "随便中个喜欢",
                "scores": {"A": 10, "B": 9, "C": 0},
                "stop_rules": {"min_favorite_any_pp": 60},
                "tie_tolerance_pp": 0,
            },
            "tools": {"display_cards": 1},
        }
        report = solver.build_report(self.normalized(raw), screen_tray=True)
        screening = report["tray_screening"]

        self.assertEqual(screening["status"], "tool_dependent")
        self.assertEqual(screening["recommendation"], "keep_if_using_tool")
        self.assertEqual(screening["one_card_action"]["tool"], "display")
        self.assertAlmostEqual(
            screening["one_card_action"]["expected_draw_probability"],
            1 / 3,
        )
        self.assertEqual(
            [check["rule"] for check in screening["failed_acceptance_rules"]],
            ["min_favorite_any_pp"],
        )

    def test_tray_screening_recommends_switch_when_no_route_meets_the_lines(self):
        raw = base_state()
        raw["preferences"]["stop_rules"] = {"min_like_any_pp": 70}
        raw["tools"] = {}
        report = solver.build_report(self.normalized(raw), screen_tray=True)
        screening = report["tray_screening"]

        self.assertEqual(screening["status"], "switch")
        self.assertEqual(screening["recommendation"], "switch")
        self.assertEqual(screening["one_card_action"]["tool"], "none")
        self.assertEqual(screening["one_card_action"]["action"], "stop")
        self.assertAlmostEqual(
            screening["failed_acceptance_rules"][0]["margin"],
            -10 / 3,
        )

    def test_tray_screening_stops_the_session_when_draw_cap_is_reached(self):
        raw = base_state()
        raw["boxes"][2].update({"status": "opened", "known": "B"})
        raw["preferences"]["stop_rules"] = {
            "min_like_any_pp": 60,
            "max_draws": 1,
        }
        report = solver.build_report(self.normalized(raw), screen_tray=True)
        screening = report["tray_screening"]

        self.assertEqual(screening["status"], "session_stop")
        self.assertEqual(screening["recommendation"], "stop")
        self.assertTrue(screening["acceptance_profile"][0]["passed"])
        self.assertIn(
            "达到最多 1 盒",
            screening["direct_draw_decision"]["reasons"][0],
        )

    def test_tray_screening_requires_a_quality_acceptance_rule(self):
        report = solver.build_report(self.normalized(), screen_tray=True)
        screening = report["tray_screening"]

        self.assertEqual(screening["status"], "needs_acceptance_rules")
        self.assertEqual(screening["recommendation"], "configure_rules")
        self.assertEqual(screening["acceptance_profile"], [])

    def test_screen_tray_cli_exposes_the_fast_assessment(self):
        import contextlib
        import io
        import json
        import tempfile

        raw = base_state()
        raw["preferences"]["stop_rules"] = {"min_like_any_pp": 60}
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".json",
            encoding="utf-8",
        ) as state_file:
            json.dump(raw, state_file, ensure_ascii=False)
            state_file.flush()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = solver.main(
                    [state_file.name, "--screen-tray", "--digits", "10"]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["tray_screening"]["status"], "ready")
        self.assertEqual(payload["tray_screening"]["planning_depth"], 1)
        self.assertNotIn("ranking", payload)
        self.assertNotIn("top_3", payload)
        self.assertNotIn("next_tool_plan", payload)

    def test_tray_screening_keeps_the_timer_safe_one_step_horizon(self):
        raw = base_state()
        raw["preferences"]["stop_rules"] = {"min_like_any_pp": 60}

        with self.assertRaisesRegex(
            solver.StateError,
            "tray screening uses planning depth 1",
        ):
            solver.build_report(
                self.normalized(raw),
                plan_depth=2,
                screen_tray=True,
            )

    def test_favorite_stop_rule_is_independent_from_like_stop_rule(self):
        raw = {
            "series": "favorite-stop",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C"],
            },
            "boxes": [
                {
                    "id": "1",
                    "excluded": ["A", "C"],
                    "status": "available",
                },
                {"id": "2", "excluded": ["B"], "status": "available"},
                {"id": "3", "excluded": [], "status": "sold_unknown"},
            ],
            "preferences": {
                "strategy": "随便中个喜欢",
                "scores": {"A": 10, "B": 9, "C": 0},
                "stop_rules": {
                    "min_like_any_pp": 55,
                    "min_favorite_any_pp": 15,
                },
            },
            "tools": {},
        }
        report = solver.build_report(self.normalized(raw))
        best = report["ranking"][0]

        self.assertEqual(best["box_id"], "1")
        self.assertAlmostEqual(best["p_like_any"], 1.0)
        self.assertAlmostEqual(best["p_favorite_any"], 0.0)
        self.assertEqual(best["favorite_probabilities"], {"A": 0.0})
        self.assertFalse(report["draw_decision"]["should_draw"])
        self.assertEqual(len(report["draw_decision"]["reasons"]), 1)
        self.assertIn("最爱款概率", report["draw_decision"]["reasons"][0])

    def test_favorite_stop_rule_requires_a_score_10_design(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "随便中个喜欢",
            "scores": {"A": 9, "B": 8, "C": 0},
            "stop_rules": {"min_favorite_any_pp": 15},
        }

        with self.assertRaisesRegex(
            solver.StateError,
            r"min_favorite_any_pp requires at least one design scored \+10",
        ):
            self.normalized(raw)

    def test_guardrail_tool_plan_applies_limit_per_branch(self):
        raw = {
            "series": "guardrail-tool",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "D"],
                "hint_labels": ["A", "B", "D"],
            },
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
                {"id": "3", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": ["D"],
                "strategy": "守住底线",
                "scores": {"A": 10, "B": 0, "D": -10},
                "hard_avoid": ["D"],
                "hard_avoid_max_pp": 20,
            },
            "tools": {"hint_cards": 1},
        }
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))
        branches = [
            branch
            for action in plan["action_ranking"]
            for branch in action["branches"]
        ]
        self.assertTrue(any(not b["draw_decision_after_outcome"]["should_draw"] for b in branches))
        for branch in branches:
            if not branch["draw_decision_after_outcome"]["should_draw"]:
                self.assertIsNone(branch["recommended_draw_after_outcome"])

    def test_remaining_options_include_global_zero_but_not_explicit_exclusion(self):
        raw = base_state()
        raw["boxes"][2].update({"status": "opened", "known": "C"})
        report = solver.build_report(self.normalized(raw))
        row = next(r for r in report["ranking"] if r["box_id"] == "1")
        options = {x["design"]: x for x in row["remaining_options_desc"]}
        self.assertNotIn("C", options)  # explicitly excluded
        self.assertIn("B", options)
        self.assertTrue(options["B"]["globally_impossible"])
        self.assertEqual(options["B"]["probability"], 0.0)

    def test_tool_used_box_is_not_recommended_for_another_tool(self):
        raw = base_state()
        raw["boxes"][0]["tool_used"] = True
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))
        acted_boxes = {
            action["box_id"]
            for action in plan["action_ranking"]
            if action["tool"] != "none"
        }
        self.assertNotIn("1", acted_boxes)

    def test_display_cards_alias_is_normalized(self):
        state = self.normalized()
        self.assertEqual(state["tools"]["display_cards"], 1)
        self.assertEqual(state["tools"]["reveal_cards"], 1)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))
        self.assertTrue(
            any(action["tool"] == "display" for action in plan["action_ranking"])
        )

    def test_no_card_wins_when_a_card_has_zero_uplift(self):
        raw = {
            "series": "no-card-tie",
            "model": {"type": "unique_regular", "designs": ["A"]},
            "boxes": [{"id": "1", "excluded": [], "status": "available"}],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
            },
            "tools": {"display_cards": 1},
        }
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))

        self.assertEqual(plan["recommended_action"]["tool"], "none")
        self.assertEqual(plan["recommended_action"]["action"], "direct_draw")
        self.assertEqual(plan["recommended_action"]["box_id"], "1")
        self.assertEqual(
            [action["tool"] for action in plan["action_ranking"]],
            ["none", "display"],
        )
        self.assertEqual(
            plan["action_ranking"][1]["uplift_vs_no_card"]["p_like_any_pp"],
            0.0,
        )

    def test_no_card_wins_real_world_floating_point_tie(self):
        import json

        fixture = (
            MODULE_PATH.parents[1]
            / "examples"
            / "synthetic-series-a-after-hint.json"
        )
        with open(fixture, encoding="utf-8") as f:
            state = self.normalized(json.load(f))
        report = solver.build_report(state, include_plan=True)

        self.assertEqual(
            report["model_summary"]["exact_valid_assignments"],
            20_411_262,
        )
        self.assertEqual(report["next_tool_plan"]["recommended_action"]["tool"], "none")
        self.assertEqual(
            report["next_tool_plan"]["recommended_action"]["action"],
            "direct_draw",
        )
        self.assertEqual(
            report["next_tool_plan"]["recommended_action"]["box_id"],
            "2",
        )

    def test_no_card_action_is_stop_when_current_stop_rules_fail(self):
        raw = base_state()
        raw["boxes"][2].update({"status": "opened", "known": "B"})
        raw["preferences"]["stop_rules"] = {"max_draws": 1}
        raw["tools"] = {"display_cards": 1}
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))

        self.assertEqual(plan["recommended_action"]["tool"], "none")
        self.assertEqual(plan["recommended_action"]["action"], "stop")
        self.assertIsNone(plan["recommended_action"]["box_id"])
        self.assertFalse(plan["recommended_action"]["draw_decision"]["should_draw"])

    def test_no_card_action_exists_when_no_cards_remain(self):
        raw = base_state()
        raw["tools"] = {}
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))

        self.assertEqual(plan["recommended_action"]["tool"], "none")
        self.assertEqual(plan["recommended_action"]["action"], "direct_draw")
        self.assertEqual(len(plan["action_ranking"]), 1)

    def test_display_card_beats_direct_draw_when_it_improves_the_objective(self):
        raw = {
            "series": "card-uplift",
            "model": {"type": "unique_regular", "designs": ["A", "B"]},
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"display_cards": 1},
        }
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))

        self.assertEqual(plan["recommended_action"]["tool"], "display")
        self.assertGreater(
            plan["recommended_action"]["uplift_vs_no_card"]["p_like_any_pp"],
            0,
        )

    def test_resale_tool_uplift_is_exposed_in_public_json(self):
        raw = {
            "series": "resale-uplift",
            "model": {"type": "unique_regular", "designs": ["A", "B"]},
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "liked": [],
                "disliked": [],
                "strategy": "保值优先",
            },
            "tools": {"display_cards": 1},
            "market_values": {"A": 100, "B": 0},
        }
        state = self.normalized(raw)
        plan = solver.plan_one_tool(state, solver.analyze_posterior(state))

        self.assertEqual(plan["recommended_action"]["tool"], "display")
        self.assertAlmostEqual(
            plan["recommended_action"]["uplift_vs_no_card"]["resale_ev"],
            50.0,
        )

    def test_optional_two_step_planning_keeps_no_card_at_each_layer(self):
        raw = {
            "series": "two-step-no-card",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B"],
                "hint_labels": ["A", "B"],
            },
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"hint_cards": 2},
        }
        state = self.normalized(raw)
        report = solver.build_report(state, plan_depth=2)
        plan = report["next_tool_plan"]

        self.assertEqual(plan["planning_depth"], 2)
        self.assertEqual(plan["recommended_action"]["tool"], "hint")
        self.assertEqual(plan["recommended_action"]["box_id"], "1")
        self.assertFalse(plan["first_action_changed_vs_depth_1"])
        self.assertAlmostEqual(
            plan["gain_vs_one_card_horizon"]["p_like_any_pp"],
            0.0,
        )
        self.assertTrue(
            all(
                branch["next_action_after_outcome"]["tool"] == "none"
                for branch in plan["recommended_action"]["branches"]
            )
        )

    def test_favorite_stop_rule_applies_at_both_planning_layers(self):
        raw = {
            "series": "favorite-stop-two-step",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C"],
            },
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
                {"id": "3", "excluded": [], "status": "available"},
            ],
            "preferences": {
                "strategy": "随便中个喜欢",
                "scores": {"A": 10, "B": 9, "C": 0},
                "stop_rules": {"min_favorite_any_pp": 60},
                "tie_tolerance_pp": 0,
            },
            "tools": {"display_cards": 2},
        }
        state = self.normalized(raw)
        posterior = solver.analyze_posterior(state)
        one_step = solver.plan_tools(state, posterior, depth=1)
        two_step = solver.plan_tools(state, posterior, depth=2)

        self.assertEqual(one_step["baseline_draw_decision"]["should_draw"], False)
        self.assertEqual(one_step["recommended_action"]["tool"], "display")
        self.assertAlmostEqual(
            one_step["recommended_action"]["expected_terminal_metrics"][
                "p_favorite_any"
            ],
            1 / 3,
        )
        self.assertAlmostEqual(
            one_step["recommended_action"]["uplift_vs_no_card"][
                "p_favorite_any_pp"
            ],
            100 / 3,
        )
        stopped_outcomes = {
            branch["outcome"]
            for branch in one_step["recommended_action"]["branches"]
            if not branch["draw_decision_after_outcome"]["should_draw"]
        }
        self.assertEqual(stopped_outcomes, {"B", "C"})

        self.assertEqual(two_step["recommended_action"]["tool"], "display")
        self.assertAlmostEqual(
            two_step["recommended_action"]["expected_terminal_metrics"][
                "p_favorite_any"
            ],
            1.0,
        )
        next_actions = {
            branch["outcome"]: branch["next_action_after_outcome"]["tool"]
            for branch in two_step["recommended_action"]["branches"]
        }
        self.assertEqual(next_actions, {"A": "none", "B": "display", "C": "display"})
        self.assertAlmostEqual(
            two_step["gain_vs_one_card_horizon"]["p_favorite_any_pp"],
            200 / 3,
        )

    def test_two_step_lookahead_can_change_the_first_action(self):
        raw = {
            "series": "two-step-lookahead",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C", "D"],
                "hint_labels": ["A", "B", "C", "D"],
            },
            "boxes": [
                {"id": "1", "excluded": ["C"], "status": "available"},
                {"id": "2", "excluded": ["C"], "status": "available"},
                {"id": "3", "excluded": ["D"], "status": "available"},
                {"id": "4", "excluded": ["A", "B"], "status": "available"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"hint_cards": 2},
        }
        state = self.normalized(raw)
        report = solver.build_report(state, plan_depth=2)
        plan = report["next_tool_plan"]

        self.assertEqual(
            report["model_summary"]["exact_valid_assignments"],
            6,
        )
        self.assertEqual(plan["depth_1_recommended_action"]["box_id"], "1")
        self.assertEqual(plan["recommended_action"]["box_id"], "3")
        self.assertTrue(plan["first_action_changed_vs_depth_1"])
        self.assertAlmostEqual(
            plan["recommended_action"]["expected_terminal_metrics"]["p_like_any"],
            2 / 3,
        )
        self.assertAlmostEqual(
            plan["gain_vs_one_card_horizon"]["p_like_any_pp"],
            100 / 6,
        )
        for branch in plan["recommended_action"]["branches"]:
            if branch["next_action_after_outcome"]["tool"] != "none":
                self.assertIsNone(branch["recommended_draw_after_outcome"])

    def test_depth_two_beam_truncation_labels_and_note(self):
        raw = {
            "series": "beam-truncation",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C", "D"],
                "hint_labels": ["A", "B", "C", "D"],
            },
            "boxes": [
                {"id": "1", "excluded": ["C"], "status": "available"},
                {"id": "2", "excluded": ["C"], "status": "available"},
                {"id": "3", "excluded": ["D"], "status": "available"},
                {"id": "4", "excluded": ["A", "B"], "status": "available"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"hint_cards": 2},
        }
        state = self.normalized(raw)
        posterior = solver.analyze_posterior(state)
        plan = solver.plan_tools(state, posterior, depth=2)

        hint_actions = [
            action
            for action in plan["action_ranking"]
            if action["tool"] == "hint"
        ]
        self.assertEqual(len(hint_actions), 4)
        self.assertEqual(
            sorted(action["depth_evaluated"] for action in hint_actions),
            [1, 2, 2, 2],
        )
        truncated = [
            action for action in hint_actions if action["depth_evaluated"] == 1
        ]
        for action in truncated:
            self.assertTrue(
                all(
                    "next_action_after_outcome" not in branch
                    for branch in action["branches"]
                )
            )
        self.assertIn("beam_note", plan)
        self.assertEqual(plan["recommended_action"]["depth_evaluated"], 2)

        exact = solver.plan_tools(state, posterior, depth=2, beam_width=0)
        self.assertNotIn("beam_note", exact)
        self.assertTrue(
            all(
                action["depth_evaluated"] == 2
                for action in exact["action_ranking"]
                if action["tool"] == "hint"
            )
        )
        self.assertEqual(
            (
                exact["recommended_action"]["tool"],
                exact["recommended_action"]["box_id"],
            ),
            (
                plan["recommended_action"]["tool"],
                plan["recommended_action"]["box_id"],
            ),
        )

    def test_beam_width_participates_in_the_depth_two_plan_cache_key(self):
        raw = {
            "series": "beam-cache",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C", "D"],
                "hint_labels": ["A", "B", "C", "D"],
            },
            "boxes": [
                {"id": str(index), "excluded": [], "status": "available"}
                for index in range(1, 5)
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"hint_cards": 2},
        }
        state = self.normalized(raw)

        key_default = solver._state_signature_for_plan(state, 2, 3)
        key_wide = solver._state_signature_for_plan(state, 2, 0)
        key_depth_one = solver._state_signature_for_plan(state, 1, 3)
        key_depth_one_other_beam = solver._state_signature_for_plan(state, 1, 0)

        self.assertNotEqual(key_default, key_wide)
        self.assertEqual(key_depth_one, key_depth_one_other_beam)

    def test_slim_report_compacts_branches_and_truncates_actions(self):
        raw = {
            "series": "slim",
            "model": {"type": "unique_regular", "designs": ["A", "B", "C"]},
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
                {"id": "3", "excluded": [], "status": "sold_unknown"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"hint_cards": 1, "display_cards": 1},
        }
        state = self.normalized(raw)
        report = solver.build_report(state, plan_depth=1)
        full_plan = report["next_tool_plan"]
        self.assertEqual(len(full_plan["action_ranking"]), 5)

        slimmed = solver._slim_report(report, top_actions=3, full_branches=False)
        plan = slimmed["next_tool_plan"]
        self.assertEqual(len(plan["action_ranking"]), 3)
        self.assertEqual(len(plan["other_actions_ranked"]), 2)
        self.assertEqual(
            plan["other_actions_ranked"][0]["tool"],
            full_plan["action_ranking"][3]["tool"],
        )
        self.assertIs(plan["recommended_action"], plan["action_ranking"][0])
        self.assertIsInstance(plan["baseline_best_draw"], str)
        allowed = set(solver.COMPACT_BRANCH_KEYS) | {"stop_reasons"}
        for action in plan["action_ranking"]:
            for branch in action["branches"]:
                self.assertLessEqual(set(branch.keys()), allowed)

        untruncated = solver._slim_report(report, top_actions=0, full_branches=False)
        self.assertEqual(len(untruncated["next_tool_plan"]["action_ranking"]), 5)
        self.assertNotIn("other_actions_ranked", untruncated["next_tool_plan"])

        audited = solver._slim_report(report, top_actions=3, full_branches=True)
        self.assertIn(
            "best_metrics_after_outcome",
            audited["next_tool_plan"]["recommended_action"]["branches"][0],
        )

    def test_cli_slim_flags_shape_the_payload(self):
        import contextlib
        import io
        import json
        import tempfile

        raw = {
            "series": "slim-cli",
            "model": {"type": "unique_regular", "designs": ["A", "B", "C"]},
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
                {"id": "3", "excluded": [], "status": "sold_unknown"},
            ],
            "preferences": {
                "liked": ["A"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0,
            },
            "tools": {"hint_cards": 1, "display_cards": 1},
        }
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".json",
            encoding="utf-8",
        ) as state_file:
            json.dump(raw, state_file, ensure_ascii=False)
            state_file.flush()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = solver.main([state_file.name, "--plan-depth", "1"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        plan = payload["next_tool_plan"]
        self.assertEqual(len(plan["action_ranking"]), 3)
        self.assertEqual(len(plan["other_actions_ranked"]), 2)
        self.assertIsInstance(plan["baseline_best_draw"], str)

    def test_partial_scores_emit_a_warning_and_complete_scores_do_not(self):
        raw = base_state()
        raw["preferences"]["scores"] = {"A": 10, "C": -10}
        report = solver.build_report(self.normalized(raw))
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("B", report["warnings"][0])
        self.assertIn("2/3", report["warnings"][0])

        raw["preferences"]["scores"] = {"A": 10, "B": 0, "C": -10}
        report = solver.build_report(self.normalized(raw))
        self.assertNotIn("warnings", report)

    def test_infeasible_constraints_raise(self):
        raw = base_state()
        raw["boxes"][0]["known"] = "A"
        raw["boxes"][1]["excluded"] = []
        raw["boxes"][1]["known"] = "A"
        state = self.normalized(raw)
        with self.assertRaises(solver.StateError):
            solver.analyze_posterior(state)

    def test_simple_secret_mixture(self):
        raw = {
            "series": "secret-demo",
            "model": {
                "type": "mixture",
                "hint_labels": ["A", "B"],
                "scenarios": [
                    {"name": "regular", "prior": 0.8, "designs": ["A", "B"]},
                    {"name": "secret-misses-A", "prior": 0.1, "designs": ["S", "B"]},
                    {"name": "secret-misses-B", "prior": 0.1, "designs": ["A", "S"]},
                ],
            },
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {"id": "2", "excluded": [], "status": "available"},
            ],
            "preferences": {"liked": ["A"], "disliked": [], "objective_mode": "target_only"},
            "tools": {"hint_cards": 1, "display_cards": 1},
        }
        state = self.normalized(raw)
        posterior = solver.analyze_posterior(state)
        self.assertAlmostEqual(posterior.marginals["1"]["A"], 0.45)
        self.assertAlmostEqual(posterior.marginals["1"]["B"], 0.45)
        self.assertAlmostEqual(posterior.marginals["1"]["S"], 0.10)
        with self.assertRaises(solver.StateError):
            solver.plan_one_tool(state, posterior)

    def test_synthetic_series_b_fixture_matches_regression(self):
        import json

        fixture = (
            MODULE_PATH.parents[1]
            / "examples"
            / "synthetic-series-b-after-hints.json"
        )
        with open(fixture, encoding="utf-8") as f:
            state = self.normalized(json.load(f))
        report = solver.build_report(state)
        self.assertEqual(report["model_summary"]["exact_valid_assignments"], 11_325_784)
        self.assertEqual(report["top_3"], ["5", "7", "11"])
        by_id = {row["box_id"]: row for row in report["ranking"]}
        self.assertAlmostEqual(by_id["5"]["p_like_any"], 0.2957607173, places=9)
        self.assertAlmostEqual(by_id["7"]["p_dislike_any"], 0.1077566021, places=9)

    def test_synthetic_series_a_stages_match_regression(self):
        import json

        expected = {
            "synthetic-series-a-before-tools.json": 23_659_800,
            "synthetic-series-a-after-hint.json": 20_411_262,
            "synthetic-series-a-after-open.json": 2_436_300,
        }
        reports = {}
        for name, assignment_count in expected.items():
            fixture = MODULE_PATH.parents[1] / "examples" / name
            with open(fixture, encoding="utf-8") as f:
                state = self.normalized(json.load(f))
            reports[name] = solver.build_report(state)
            self.assertEqual(
                reports[name]["model_summary"]["exact_valid_assignments"],
                assignment_count,
            )

        after_open = reports["synthetic-series-a-after-open.json"]
        self.assertEqual(after_open["ranking"][0]["box_id"], "8")
        self.assertAlmostEqual(
            after_open["ranking"][0]["p_like_any"],
            0.6233,
            places=4,
        )

    def test_target_tie_order_is_deterministic(self):
        import json

        fixture = (
            MODULE_PATH.parents[1]
            / "examples"
            / "synthetic-series-b-after-open.json"
        )
        with open(fixture, encoding="utf-8") as f:
            state = self.normalized(json.load(f))
        report = solver.build_report(state)
        self.assertEqual(report["top_3"], ["1", "11", "8"])


if __name__ == "__main__":
    unittest.main()
