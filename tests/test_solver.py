import contextlib
import copy
import importlib.util
import io
import json
import math
import pathlib
import sys
import tempfile
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


def multi_tray_session():
    fixture = (
        MODULE_PATH.parents[1]
        / "examples"
        / "synthetic-multi-tray-session.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


def tool_planning_fixture():
    fixture = (
        MODULE_PATH.parents[1]
        / "tests"
        / "fixtures"
        / "tool-planning-boundaries.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


def session_lock_fixture():
    fixture = (
        MODULE_PATH.parents[1]
        / "tests"
        / "fixtures"
        / "session-lock-and-warnings.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


def score_calibration_fixture():
    fixture = (
        MODULE_PATH.parents[1]
        / "examples"
        / "synthetic-score-first-calibration.json"
    )
    return json.loads(fixture.read_text(encoding="utf-8"))


class SolverTests(unittest.TestCase):
    def normalized(self, state=None):
        return solver._normalize_state(state or base_state())

    def cli_output(self, raw, *args):
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".json",
            encoding="utf-8",
        ) as state_file:
            json.dump(raw, state_file, ensure_ascii=False)
            state_file.flush()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = solver.main([state_file.name, *args])
        return exit_code, stdout.getvalue(), stderr.getvalue()

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

    def test_legacy_state_normalizes_to_an_implicit_session(self):
        session = solver._normalize_session(base_state())

        self.assertTrue(session["_legacy_input"])
        self.assertEqual(session["active_tray_id"], "tray-1")
        self.assertEqual(list(session["_tray_states"]), ["tray-1"])
        self.assertEqual(session["draws_used"], 0)

    def test_multi_tray_report_keeps_posteriors_isolated_and_counters_global(self):
        session = solver._normalize_session(multi_tray_session())
        report = solver.build_session_report(session)

        self.assertFalse(session["_legacy_input"])
        self.assertEqual(report["session_summary"]["active_tray_id"], "tray-c")
        self.assertEqual(
            report["session_summary"]["tray_ids"],
            ["tray-a", "tray-b", "tray-c"],
        )
        self.assertEqual(
            report["session_summary"]["tools"],
            {"hint_cards": 4, "display_cards": 1, "reveal_cards": 1},
        )
        self.assertEqual(report["session_summary"]["draws_used"], 1)
        self.assertEqual(report["session_summary"]["event_count"], 9)
        self.assertEqual(len(report["actual_events"]), 9)

        tray_b = report["tray_reports"]["tray-b"]
        tray_c = report["tray_reports"]["tray-c"]
        tray_b_box_1 = next(
            row for row in tray_b["ranking"] if row["box_id"] == "1"
        )
        tray_c_box_1 = next(
            row for row in tray_c["ranking"] if row["box_id"] == "1"
        )
        self.assertNotEqual(
            tray_b_box_1["liked_probabilities"]["A"],
            tray_c_box_1["liked_probabilities"]["A"],
        )
        self.assertEqual(tray_c["draw_decision"]["opened_count"], 1)
        self.assertEqual(tray_c["draw_decision"]["tray_opened_count"], 0)
        self.assertFalse(tray_c["draw_decision"]["should_draw"])
        self.assertIn("达到最多 1 盒", tray_c["draw_decision"]["reasons"][0])

    def test_switching_back_does_not_restore_global_tool_inventory(self):
        raw = multi_tray_session()
        raw["active_tray_id"] = "tray-a"
        raw["events"].append(
            {"seq": 10, "type": "tray_switch", "tray_id": "tray-a"}
        )

        report = solver.build_session_report(
            solver._normalize_session(raw),
            plan_depth=1,
        )

        self.assertEqual(
            report["session_summary"]["tools"],
            {"hint_cards": 4, "display_cards": 1, "reveal_cards": 1},
        )
        self.assertEqual(report["session_summary"]["active_tray_id"], "tray-a")
        self.assertEqual(report["session_summary"]["event_count"], 10)
        tray_b_box_1 = next(
            row
            for row in report["tray_reports"]["tray-b"]["ranking"]
            if row["box_id"] == "1"
        )
        self.assertTrue(tray_b_box_1["tool_used"])
        self.assertNotIn(
            "next_tool_plan",
            report["tray_reports"]["tray-c"],
        )
        self.assertIn(
            "next_tool_plan",
            report["tray_reports"]["tray-a"],
        )

    def test_counterfactual_tool_planning_does_not_mutate_actual_session(self):
        raw = multi_tray_session()
        raw["preferences"]["stop_rules"]["max_draws"] = 2
        session = solver._normalize_session(raw)
        before = copy.deepcopy(session)

        report = solver.build_session_report(
            session,
            plan_depth=2,
            beam_width=0,
        )

        plan = report["tray_reports"]["tray-c"]["next_tool_plan"]
        self.assertTrue(
            any(
                action["tool"] != "none"
                for action in plan["action_ranking"]
            )
        )
        self.assertEqual(session, before)
        self.assertEqual(report["actual_events"], before["events"])
        self.assertEqual(report["session_summary"]["event_count"], 9)

    def test_accepted_tray_lock_reaches_the_complete_session_report(self):
        raw = session_lock_fixture()["accepted_session"]
        session = solver._normalize_session(raw)
        report = solver.build_session_report(session, plan_depth=1)

        self.assertEqual(session["accepted_tray_id"], "tray-a")
        self.assertEqual(
            report["session_summary"]["accepted_tray_id"],
            "tray-a",
        )
        self.assertEqual(report["session_summary"]["lock_status"], "accepted")
        self.assertEqual(
            report["session_recommendation"],
            {
                "action": "continue_with_accepted_tray",
                "tray_id": "tray-a",
                "release_required_before_switch": True,
            },
        )
        self.assertTrue(
            report["tray_reports"]["tray-a"]["tray_lock"]["is_accepted"]
        )
        self.assertTrue(
            report["tray_reports"]["tray-a"]["tray_lock"][
                "currently_qualified"
            ]
        )
        self.assertFalse(
            report["tray_reports"]["tray-b"]["tray_lock"]["is_accepted"]
        )
        model_summary = report["tray_reports"]["tray-a"]["model_summary"]
        self.assertEqual(model_summary["scope"], "regular_only")
        self.assertEqual(model_summary["probability_kind"], "conditional")
        self.assertIn("条件概率", model_summary["probability_statement"])
        self.assertEqual(
            model_summary["hint_mechanism"],
            {
                "type": "uniform_wrong_label",
                "status": "assumed",
            },
        )
        self.assertEqual(
            {
                warning["code"]
                for warning in report["tray_reports"]["tray-a"][
                    "model_warnings"
                ]
            },
            {"regular_only_scope", "hint_mechanism_assumed"},
        )

    def test_accepted_tray_requires_release_before_switching(self):
        raw = session_lock_fixture()["accepted_session"]
        raw["events"].pop()
        with self.assertRaisesRegex(
            solver.StateError, "must match the tray acceptance"
        ):
            solver._normalize_session(raw)

        raw = session_lock_fixture()["accepted_session"]
        raw["active_tray_id"] = "tray-b"
        raw["events"].append(
            {"seq": 3, "type": "tray_switch", "tray_id": "tray-b"}
        )
        with self.assertRaisesRegex(
            solver.StateError, "release the accepted tray"
        ):
            solver._normalize_session(raw)

        raw = session_lock_fixture()["accepted_session"]
        raw["accepted_tray_id"] = None
        raw["active_tray_id"] = "tray-b"
        raw["events"].extend(
            [
                {
                    "seq": 3,
                    "type": "tray_released",
                    "tray_id": "tray-a",
                    "reason": "用户确认继续比较其他端",
                },
                {
                    "seq": 4,
                    "type": "tray_switch",
                    "tray_id": "tray-b",
                },
            ]
        )
        report = solver.build_session_report(solver._normalize_session(raw))

        self.assertEqual(report["session_summary"]["lock_status"], "open")
        self.assertEqual(
            [
                event["type"]
                for event in report["session_review"][
                    "acceptance_lifecycle"
                ]
            ],
            ["tray_accepted", "tray_released"],
        )
        self.assertEqual(
            report["session_recommendation"]["action"],
            "follow_active_tray_report",
        )

    def test_locked_tray_screening_requires_release_before_switch(self):
        raw = session_lock_fixture()["accepted_session"]
        raw["preferences"]["stop_rules"]["min_like_any_pp"] = 80
        raw["tools"]["hint_cards"] = 0
        raw["events"].append(
            {
                "seq": 3,
                "type": "stop_rule_override",
                "tray_id": "tray-a",
                "rule": "min_like_any_pp",
                "old_value": 60,
                "new_value": 80,
                "reason": "用户确认提高本端最低喜欢率",
            }
        )
        report = solver.build_session_report(
            solver._normalize_session(raw),
            screen_tray=True,
        )
        screening = report["tray_reports"]["tray-a"]["tray_screening"]

        self.assertEqual(screening["status"], "accepted_review")
        self.assertEqual(
            screening["recommendation"],
            "release_before_switch",
        )
        self.assertEqual(screening["unlocked_recommendation"], "switch")
        self.assertTrue(screening["release_required_before_switch"])
        self.assertFalse(
            report["tray_reports"]["tray-a"]["tray_lock"][
                "currently_qualified"
            ]
        )

    def test_stop_rule_override_is_auditable_and_matches_final_state(self):
        raw = session_lock_fixture()["override_session"]
        report = solver.build_session_report(solver._normalize_session(raw))
        ledger_event = next(
            event
            for event in report["actual_events"]
            if event["type"] == "stop_rule_override"
        )
        review_event = report["session_review"]["stop_rule_overrides"][0]

        self.assertEqual(report["session_summary"]["max_draws"], 2)
        self.assertEqual(
            report["session_summary"]["stop_rule_override_count"],
            1,
        )
        self.assertEqual(
            (ledger_event["old_value"], ledger_event["new_value"]),
            (1, 2),
        )
        self.assertEqual(review_event, ledger_event)
        self.assertTrue(review_event["reason"])

        raw = session_lock_fixture()["override_session"]
        raw["preferences"]["stop_rules"]["max_draws"] = 3
        with self.assertRaisesRegex(
            solver.StateError, "must match session preferences"
        ):
            solver._normalize_session(raw)

        raw = session_lock_fixture()["override_session"]
        override_event = next(
            event
            for event in raw["events"]
            if event["type"] == "stop_rule_override"
        )
        override_event["tray_id"] = "tray-b"
        with self.assertRaisesRegex(
            solver.StateError, "must target the current event tray"
        ):
            solver._normalize_session(raw)

    def test_confirmed_hint_mechanism_clears_only_its_warning(self):
        raw = base_state()
        raw["model"]["hint_mechanism"] = {
            "type": "uniform_wrong_label",
            "status": "confirmed",
        }
        report = solver.build_report(
            self.normalized(raw),
            plan_depth=1,
        )

        self.assertEqual(
            {warning["code"] for warning in report["model_warnings"]},
            {"regular_only_scope"},
        )
        self.assertEqual(
            report["model_summary"]["hint_mechanism"]["status"],
            "confirmed",
        )

    def test_hint_mechanism_metadata_rejects_unsupported_claims(self):
        raw = base_state()
        raw["model"]["hint_mechanism"] = {
            "type": "uniform_wrong_label",
            "status": "verified_elsewhere",
        }
        with self.assertRaisesRegex(
            solver.StateError, "status must be assumed or confirmed"
        ):
            self.normalized(raw)

    def test_session_event_ledger_must_match_real_tray_state(self):
        raw = multi_tray_session()
        raw["events"][6]["excluded"] = "C"

        with self.assertRaisesRegex(
            solver.StateError, "hint result must match"
        ):
            solver._normalize_session(raw)

        raw = multi_tray_session()
        # Drop the tray-b hint and its release so the tray holds a
        # tool_used box with no matching ledger event and no lock.
        for index in sorted((5, 6, 7), reverse=True):
            raw["events"].pop(index)
        for seq, event in enumerate(raw["events"], start=1):
            event["seq"] = seq
        with self.assertRaisesRegex(
            solver.StateError, "tool events must exactly match"
        ):
            solver._normalize_session(raw)

    def test_multi_tray_cli_returns_one_report_with_every_tray(self):
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".json",
            encoding="utf-8",
        ) as state_file:
            json.dump(multi_tray_session(), state_file, ensure_ascii=False)
            state_file.flush()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = solver.main(
                    [state_file.name, "--plan-depth", "1", "--digits", "10"]
                )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["session_summary"]["active_tray_id"], "tray-c")
        self.assertEqual(
            list(payload["tray_reports"]),
            ["tray-a", "tray-b", "tray-c"],
        )
        self.assertNotIn("next_tool_plan", payload["tray_reports"]["tray-a"])
        self.assertNotIn("next_tool_plan", payload["tray_reports"]["tray-b"])
        self.assertEqual(
            payload["tray_reports"]["tray-c"]["next_tool_plan"][
                "recommended_action"
            ]["action"],
            "stop",
        )

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
            "score_default_confirmed": True,
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
            "score_default_confirmed": True,
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

    def test_score_derived_top_target_combines_equal_highest_scores(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "只冲最爱",
            "scores": {"A": 10, "B": 10, "C": 0},
            "tie_tolerance_pp": 0,
        }
        state = self.normalized(raw)
        concentrated = {
            "p_like_any": 0.4,
            "p_favorite_any": 0.4,
            "p_dislike_any": 0.0,
            "liked_probabilities": {"A": 0.4, "B": 0.0},
            "favorite_probabilities": {"A": 0.4, "B": 0.0},
            "disliked_probabilities": {},
            "p_hard_avoid": 0.0,
            "hard_avoid_probabilities": {},
            "expected_score": 4.0,
            "liked_weighted_score": 0.8,
            "disliked_weighted_loss": 0.0,
            "resale_ev": None,
        }
        combined = copy.deepcopy(concentrated)
        combined.update(
            {
                "p_like_any": 0.6,
                "p_favorite_any": 0.6,
                "liked_probabilities": {"A": 0.3, "B": 0.3},
                "favorite_probabilities": {"A": 0.3, "B": 0.3},
                "expected_score": 6.0,
                "liked_weighted_score": 0.9,
            }
        )

        self.assertLess(
            solver.compare_metrics(combined, concentrated, state),
            0,
        )
        self.assertEqual(
            solver._primary_tool_metric(combined, state),
            (0.6, "p_favorite_any"),
        )

        raw["preferences"]["liked"] = ["A", "B"]
        explicit_state = self.normalized(raw)
        self.assertLess(
            solver.compare_metrics(concentrated, combined, explicit_state),
            0,
        )
        with self.assertRaisesRegex(
            solver.StateError,
            "requires score-derived liked designs",
        ):
            solver.build_preference_calibration_report(explicit_state)

    def test_preference_calibration_builds_real_tradeoff_choices(self):
        raw = score_calibration_fixture()
        state = self.normalized(raw)
        original_stop_rules = copy.deepcopy(state["preferences"]["stop_rules"])

        report = solver.build_preference_calibration_report(state)
        solver.validate_preference_calibration_report(report)

        self.assertEqual(report["status"], "needs_confirmation")
        self.assertEqual(report["primary_metric"]["metric"], "p_favorite_any_pp")
        self.assertEqual(report["score_coverage"]["scored_design_count"], 5)
        self.assertEqual(
            report["score_tiers"]["hard_avoid"],
            ["E"],
        )
        self.assertEqual(
            [(choice["choice"], choice["box_id"]) for choice in report["choices"]],
            [("1", "4"), ("2", "5"), ("3", "3")],
        )
        self.assertEqual(
            report["choices"][0]["suggested_stop_rules"],
            {
                "min_favorite_any_pp": 29.0,
                "max_dislike_any_pp": 42.0,
                "max_hard_avoid_pp": 42.0,
            },
        )
        self.assertFalse(report["stop_rules_mutated"])
        self.assertTrue(report["confirmation_required"])
        self.assertNotIn("draw_decision", report)
        self.assertNotIn("next_tool_plan", report)
        self.assertEqual(state["preferences"]["stop_rules"], original_stop_rules)

    def test_preference_calibration_preserves_existing_quality_rules(self):
        raw = score_calibration_fixture()
        raw["preferences"]["stop_rules"].update(
            {
                "min_favorite_any_pp": 18,
                "max_dislike_any_pp": 25,
                "max_hard_avoid_pp": 8,
            }
        )
        state = self.normalized(raw)
        existing = copy.deepcopy(state["preferences"]["stop_rules"])

        report = solver.build_preference_calibration_report(state)

        self.assertEqual(report["existing_stop_rules"], existing)
        self.assertEqual(state["preferences"]["stop_rules"], existing)
        self.assertTrue(report["confirmation_required"])

    def test_preference_calibration_supports_score_based_strategies(self):
        cases = {
            "稳妥避雷": "p_dislike_any_pp",
            "整体最满意": "expected_score",
            "随便中个喜欢": "p_like_any_pp",
            "只冲最爱": "p_favorite_any_pp",
            "守住底线": "expected_score",
            "保值优先": "resale_ev",
        }
        for strategy, primary_metric in cases.items():
            with self.subTest(strategy=strategy):
                raw = base_state()
                raw["preferences"] = {
                    "strategy": strategy,
                    "scores": {"A": 10, "B": 0, "C": -10},
                    "stop_rules": {"max_draws": 2},
                }
                if strategy == "守住底线":
                    raw["preferences"]["hard_avoid_max_pp"] = 100
                if strategy == "保值优先":
                    raw["market_values"] = {"A": 100, "B": 60, "C": 30}
                report = solver.build_preference_calibration_report(
                    self.normalized(raw)
                )
                self.assertEqual(
                    report["primary_metric"]["metric"],
                    primary_metric,
                )
                solver.validate_preference_calibration_report(report)

    def test_resale_calibration_adds_market_and_personal_boundaries(self):
        raw = score_calibration_fixture()
        raw["preferences"]["strategy"] = "保值优先"
        raw["market_values"] = {
            "A": 120,
            "B": 90,
            "C": 60,
            "D": 45,
            "E": 25,
        }
        report = solver.build_preference_calibration_report(
            self.normalized(raw)
        )

        self.assertEqual(report["primary_metric"]["metric"], "resale_ev")
        self.assertTrue(report["market_value_coverage"]["complete"])
        self.assertEqual(report["market_value_coverage"]["currency"], "CNY")
        self.assertIn("resale_ev", report["attainable_ranges"])
        self.assertTrue(report["choices"])
        for choice in report["choices"]:
            self.assertIn("resale_ev", choice["actual"])
            self.assertIn("min_resale_ev", choice["suggested_stop_rules"])
            self.assertIn(
                "min_expected_score",
                choice["suggested_stop_rules"],
            )
        solver.validate_preference_calibration_report(report)

        markdown = solver.render_preference_calibration_markdown(report)
        self.assertIn("预期二手价值", markdown)
        self.assertIn("方案1", markdown)
        self.assertIn("¥", markdown)
        self.assertNotIn("回复 A/B/C", markdown)

    def test_resale_calibration_requires_complete_market_values(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "保值优先",
            "scores": {"A": 10, "B": 0, "C": -10},
        }
        raw["market_values"] = {"A": 100, "B": 60}

        with self.assertRaisesRegex(
            solver.StateError,
            "requires current market_values for every design",
        ):
            self.normalized(raw)

    def test_resale_calibration_zero_values_create_no_pseudo_threshold(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "保值优先",
            "scores": {"A": 10, "B": 0, "C": -10},
        }
        raw["market_values"] = {"A": 0, "B": 0, "C": 0}

        report = solver.build_preference_calibration_report(
            self.normalized(raw)
        )

        self.assertEqual(report["status"], "target_unreachable")
        self.assertEqual(report["choices"], [])
        solver.validate_preference_calibration_report(report)

    def test_preference_calibration_requires_complete_confirmed_scores(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "只冲最爱",
            "scores": {"A": 10, "C": -10},
        }
        state = self.normalized(raw)
        with self.assertRaisesRegex(
            solver.StateError,
            "requires every design to have a score",
        ):
            solver.build_preference_calibration_report(state)

        raw["preferences"]["score_default"] = 0
        state = self.normalized(raw)
        with self.assertRaisesRegex(
            solver.StateError,
            "score_default_confirmed=true",
        ):
            solver.build_preference_calibration_report(state)

        raw["preferences"]["score_default_confirmed"] = True
        report = solver.build_preference_calibration_report(
            self.normalized(raw)
        )
        self.assertTrue(report["score_coverage"]["score_default_used"])
        self.assertTrue(report["score_coverage"]["score_default_confirmed"])

    def test_preference_calibration_reports_unreachable_favorite(self):
        raw = {
            "series": "unreachable-favorite",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C"],
            },
            "boxes": [
                {"id": "1", "excluded": ["A"], "status": "available"},
                {"id": "2", "excluded": ["A"], "status": "available"},
                {"id": "3", "excluded": [], "status": "sold_unknown"},
            ],
            "preferences": {
                "strategy": "只冲最爱",
                "scores": {"A": 10, "B": 0, "C": -10},
                "stop_rules": {"max_draws": 2},
            },
            "tools": {},
        }
        report = solver.build_preference_calibration_report(
            self.normalized(raw)
        )

        self.assertEqual(report["status"], "target_unreachable")
        self.assertEqual(
            report["attainable_ranges"]["p_favorite_any_pp"]["max"],
            0.0,
        )
        self.assertEqual(report["choices"], [])
        solver.validate_preference_calibration_report(report)

    def test_preference_calibration_markdown_stops_before_a_draw_advice(self):
        exit_code, output, error = self.cli_output(
            score_calibration_fixture(),
            "--calibrate-preferences",
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("｜偏好校准", output)
        self.assertIn("## 自动分档", output)
        self.assertIn("## 当前端可达区间", output)
        self.assertIn("卡片在边界确认后另行规划", output)
        self.assertIn("回复 方案1/方案2/方案3 即确认", output)
        self.assertIn("未推荐抽盒，也未改写停止线", output)
        self.assertNotIn("建议抽 ", output)
        self.assertNotIn("## 全款概率矩阵", output)

    def test_preference_calibration_validation_rejects_advice_or_drift(self):
        report = solver.build_preference_calibration_report(
            self.normalized(score_calibration_fixture())
        )
        mutations = []

        leaked_advice = copy.deepcopy(report)
        leaked_advice["draw_decision"] = {"should_draw": True}
        mutations.append(("formal recommendation leaked", leaked_advice))

        mutated_rules = copy.deepcopy(report)
        mutated_rules["stop_rules_mutated"] = True
        mutations.append(("stop rules were mutated", mutated_rules))

        drifted_actual = copy.deepcopy(report)
        drifted_actual["choices"][0]["actual"]["p_favorite_any_pp"] += 1
        mutations.append(("candidate actual metrics", drifted_actual))

        for expected, malformed in mutations:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(solver.StateError, expected):
                    solver.validate_preference_calibration_report(malformed)

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

    def test_resale_stop_rule_fails_closed_and_reports_cny(self):
        raw = base_state()
        raw["preferences"] = {
            "strategy": "保值优先",
            "scores": {"A": 10, "B": 0, "C": -10},
            "stop_rules": {"min_resale_ev": 90},
        }
        raw["market_values"] = {"A": 100, "B": 60, "C": 30}
        raw["tools"] = {}

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("建议停止", output)
        self.assertIn("预期二手价值", output)
        self.assertIn("≥ ¥90.00", output)

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
                "min_tool_uplift_pp": 50,
            },
            "tools": {"display_cards": 1},
        }
        report = solver.build_report(self.normalized(raw), screen_tray=True)
        screening = report["tray_screening"]

        self.assertEqual(screening["status"], "tool_dependent")
        self.assertEqual(screening["recommendation"], "keep_if_using_tool")
        self.assertEqual(screening["one_card_action"]["tool"], "display")
        self.assertTrue(
            screening["one_card_action"]["passes_tool_uplift_gate"]
        )
        self.assertEqual(
            screening["one_card_action"]["tool_gate_reason"],
            "rescue_route",
        )
        self.assertAlmostEqual(
            screening["one_card_action"]["expected_draw_probability"],
            1 / 3,
        )
        self.assertEqual(
            [check["rule"] for check in screening["failed_acceptance_rules"]],
            ["min_favorite_any_pp"],
        )

    def test_practical_tool_gate_uses_report_level_synthetic_boundaries(self):
        fixture = tool_planning_fixture()
        base_path = (
            MODULE_PATH.parents[1]
            / "examples"
            / fixture["base_example"]
        )
        base = json.loads(base_path.read_text(encoding="utf-8"))

        for case in fixture["direct_ready_cases"]:
            with self.subTest(case=case["name"]):
                raw = copy.deepcopy(base)
                liked = list(case["liked"])
                hard_avoid = case["hard_avoid"]
                raw["preferences"] = {
                    "liked": liked,
                    "disliked": [hard_avoid],
                    "hard_avoid": [hard_avoid],
                    "strategy": "随便中个喜欢",
                    "scores": {
                        **{design: 10 for design in liked},
                        hard_avoid: -10,
                    },
                    "score_default": 0,
                    "tie_tolerance_pp": 0.5,
                }
                disabled = set(case["disable_tool_boxes"])
                for box in raw["boxes"]:
                    if box["id"] in disabled:
                        box["tool_used"] = True

                report = solver.build_report(
                    self.normalized(raw),
                    plan_depth=1,
                )
                plan = report["next_tool_plan"]
                target = next(
                    action
                    for action in plan["action_ranking"]
                    if action["tool"] == "hint"
                    and action["box_id"] == case["target_box_id"]
                )

                self.assertEqual(
                    report["tool_policy"],
                    {
                        "min_tool_uplift_pp": 0.5,
                        "source": "tie_tolerance_pp",
                    },
                )
                self.assertAlmostEqual(
                    target["primary_uplift_pp"],
                    case["expected_primary_uplift_pp"],
                    places=10,
                )
                self.assertEqual(
                    plan["recommended_action"]["tool"],
                    case["expected_recommended_tool"],
                )
                expected_gate = case["expected_recommended_tool"] != "none"
                self.assertEqual(
                    target["passes_tool_uplift_gate"],
                    expected_gate,
                )
                hard_delta = target["uplift_vs_no_card"][
                    "p_hard_avoid_pp"
                ]
                score_delta = target["uplift_vs_no_card"][
                    "expected_score"
                ]
                self.assertEqual(
                    hard_delta > 0,
                    case["expected_hard_avoid_delta"] == "positive",
                )
                self.assertEqual(
                    score_delta > 0,
                    case["expected_score_delta"] == "positive",
                )

    def test_explicit_zero_tool_gate_restores_strict_maximization(self):
        fixture = tool_planning_fixture()
        case = fixture["direct_ready_cases"][0]
        base_path = (
            MODULE_PATH.parents[1]
            / "examples"
            / fixture["base_example"]
        )
        raw = json.loads(base_path.read_text(encoding="utf-8"))
        raw["preferences"] = {
            "liked": list(case["liked"]),
            "disliked": [],
            "strategy": "随便中个喜欢",
            "tie_tolerance_pp": 0.5,
            "min_tool_uplift_pp": 0,
        }
        disabled = set(case["disable_tool_boxes"])
        for box in raw["boxes"]:
            if box["id"] in disabled:
                box["tool_used"] = True
        state = self.normalized(raw)
        plan = solver.plan_one_tool(
            state,
            solver.analyze_posterior(state),
        )

        self.assertEqual(state["preferences"]["min_tool_uplift_pp"], 0)
        self.assertEqual(
            state["preferences"]["min_tool_uplift_source"],
            "explicit",
        )
        self.assertEqual(plan["recommended_action"]["tool"], "hint")
        self.assertEqual(
            plan["recommended_action"]["box_id"],
            case["target_box_id"],
        )

    def test_explicit_zero_tool_gate_preserves_secondary_strategy_order(self):
        raw = {
            "series": "strict-secondary-order",
            "model": {
                "type": "unique_regular",
                "designs": ["A", "B", "C", "D"],
                "hint_labels": ["A", "B", "C", "D"],
            },
            "boxes": [
                {"id": "1", "excluded": [], "status": "available"},
                {
                    "id": "2",
                    "excluded": [],
                    "status": "available",
                    "tool_used": True,
                },
                {
                    "id": "3",
                    "excluded": ["A", "B"],
                    "status": "available",
                    "tool_used": True,
                },
                {
                    "id": "4",
                    "excluded": ["A", "B"],
                    "status": "available",
                    "tool_used": True,
                },
            ],
            "preferences": {
                "liked": ["A", "B"],
                "disliked": [],
                "strategy": "随便中个喜欢",
                "tie_tolerance_pp": 0.5,
                "min_tool_uplift_pp": 0,
            },
            "tools": {"hint_cards": 1},
            "meta": {"provenance": "synthetic"},
        }
        report = solver.build_report(self.normalized(raw), plan_depth=1)
        plan = report["next_tool_plan"]
        direct = next(
            action
            for action in plan["action_ranking"]
            if action["tool"] == "none"
        )
        recommended = plan["recommended_action"]

        self.assertEqual(recommended["tool"], "hint")
        self.assertEqual(recommended["box_id"], "1")
        self.assertAlmostEqual(recommended["primary_uplift_pp"], 0.0)
        self.assertGreater(
            recommended["expected_terminal_metrics"][
                "liked_probabilities"
            ]["A"],
            direct["expected_terminal_metrics"]["liked_probabilities"]["A"],
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

    def test_two_step_skips_a_redundant_setup_card(self):
        raw = tool_planning_fixture()["redundant_first_state"]
        state = self.normalized(raw)
        posterior = solver.analyze_posterior(state)
        one_step = solver.plan_tools(state, posterior, depth=1)
        two_step = solver.plan_tools(
            state,
            posterior,
            depth=2,
            beam_width=0,
        )

        self.assertEqual(one_step["recommended_action"]["box_id"], "2")
        self.assertEqual(two_step["recommended_action"]["box_id"], "2")
        self.assertEqual(
            two_step["recommended_action"]["expected_tools_used"],
            1.0,
        )
        redundant = next(
            action
            for action in two_step["action_ranking"]
            if action["tool"] == "hint" and action["box_id"] == "1"
        )
        self.assertGreater(redundant["expected_tools_used"], 1.0)
        self.assertFalse(two_step["first_action_changed_vs_depth_1"])
        self.assertTrue(
            two_step[
                "terminal_value_practically_equivalent_to_depth_1"
            ]
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
        self.assertFalse(
            plan["terminal_value_practically_equivalent_to_depth_1"]
        )
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
        report = solver.build_report(state)
        self.assertEqual(
            report["model_summary"]["scope"],
            "declared_mixture",
        )
        self.assertTrue(
            report["model_summary"]["hidden_designs_included"]
        )
        self.assertNotIn(
            "regular_only_scope",
            {warning["code"] for warning in report["model_warnings"]},
        )
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

    def test_markdown_cli_delivers_the_complete_initial_report(self):
        fixture = (
            MODULE_PATH.parents[1]
            / "examples"
            / "synthetic-series-a-before-tools.json"
        )
        raw = json.loads(fixture.read_text(encoding="utf-8"))

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
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
        explanation = output.split("## 决策依据", 1)[1].split("## TOP 3", 1)[0]
        explanation_lines = [
            line for line in explanation.splitlines() if line.startswith("- ")
        ]
        self.assertEqual(len(explanation_lines), 3)
        self.assertIn("| 款式 | 2号 | 8号 | 12号 |", output)
        for design in raw["model"]["designs"]:
            self.assertIn(f"| {design} |", output)

    def test_markdown_matrix_distinguishes_explicit_and_global_zeroes(self):
        raw = base_state()
        raw["boxes"][2].update({"status": "opened", "known": "C"})

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("| C | 已排除 | 0.00%（全局约束） |", output)
        self.assertIn("| B | 0.00%（全局约束） | 100.00% |", output)

    def test_markdown_report_remains_complete_after_a_real_hint(self):
        fixture = (
            MODULE_PATH.parents[1]
            / "examples"
            / "synthetic-series-a-after-hint.json"
        )
        raw = json.loads(fixture.read_text(encoding="utf-8"))

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("## 决策依据", output)
        self.assertIn("## 全款概率矩阵", output)
        self.assertIn("## 停止线", output)
        for design in raw["model"]["designs"]:
            self.assertIn(f"| {design} |", output)

    def test_markdown_cli_plans_a_card_without_an_extra_plan_flag(self):
        fixture = (
            MODULE_PATH.parents[1]
            / "examples"
            / "synthetic-series-b-after-open.json"
        )
        raw = json.loads(fixture.read_text(encoding="utf-8"))

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("建议先对 11 号使用提示卡", output)
        self.assertIn("主指标提升", output)

    def test_markdown_report_explains_why_no_card_is_needed(self):
        raw = json.loads(
            (
                MODULE_PATH.parents[1] / "examples" / "minimal-demo.json"
            ).read_text(encoding="utf-8")
        )

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("建议抽 1 号", output)
        self.assertIn("不用卡", output)

    def test_markdown_report_lists_every_failed_stop_line(self):
        raw = base_state()
        raw["preferences"]["stop_rules"] = {
            "min_like_any_pp": 90,
            "max_dislike_any_pp": 80,
            "max_draws": 2,
        }
        raw["tools"] = {}

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("建议停止", output)
        self.assertIn("| 喜欢款至少 | 66.67% | ≥ 90.00% | 未通过 |", output)
        self.assertIn("| 不喜欢款不超过 | 0.00% | ≤ 80.00% | 通过 |", output)
        self.assertIn("| 最多抽盒数 | 0盒 | < 2盒 | 通过 |", output)

    def test_markdown_report_lists_every_supported_stop_rule(self):
        raw = base_state()
        raw["preferences"].pop("objective_mode")
        raw["preferences"].update(
            {
                "strategy": "守住底线",
                "scores": {"A": 10, "B": 0, "C": -10},
                "hard_avoid": ["C"],
                "hard_avoid_max_pp": 100,
                "stop_rules": {
                    "min_like_any_pp": 60,
                    "min_favorite_any_pp": 60,
                    "max_dislike_any_pp": 10,
                    "max_hard_avoid_pp": 10,
                    "min_expected_score": 5,
                    "max_draws": 2,
                },
            }
        )

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        for label in (
            "喜欢款至少",
            "最爱款至少",
            "不喜欢款不超过",
            "硬雷不超过",
            "期望评分至少",
            "最多抽盒数",
            "策略硬雷上限",
        ):
            self.assertIn(f"| {label} |", output)

    def test_markdown_session_report_enforces_the_global_draw_cap(self):
        raw = multi_tray_session()

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("当前端：tray-c", output)
        self.assertIn("建议停止", output)
        self.assertIn("| 最多抽盒数 | 1盒 | < 1盒 | 未通过 |", output)

    def test_markdown_session_report_shows_a_confirmed_draw_cap_override(self):
        raw = session_lock_fixture()["override_session"]

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("## 全款概率矩阵", output)
        self.assertIn("| 最多抽盒数 | 1盒 | < 2盒 | 通过 |", output)
        self.assertIn("### 已确认变更", output)
        self.assertIn("最多抽盒数：1盒 → 2盒", output)

    def test_markdown_explains_only_the_latest_actual_event(self):
        accepted = session_lock_fixture()["accepted_session"]

        exit_code, output, error = self.cli_output(
            accepted,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("最新信息：已保留 tray-a", output)
        self.assertNotIn("最新信息：已切换至 tray-a", output)

        overridden = session_lock_fixture()["accepted_session"]
        overridden["preferences"]["stop_rules"]["min_like_any_pp"] = 55
        overridden["events"].append(
            {
                "seq": 3,
                "type": "stop_rule_override",
                "tray_id": "tray-a",
                "rule": "min_like_any_pp",
                "old_value": 60,
                "new_value": 55,
                "reason": "用户确认调整喜欢率门槛",
            }
        )

        exit_code, output, error = self.cli_output(
            overridden,
            "--format",
            "markdown",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn(
            "最新信息：已将喜欢款至少从 60.00% 调整为 55.00%",
            output,
        )
        self.assertNotIn("最新信息：已保留 tray-a", output)

    def test_markdown_screening_stays_compact(self):
        raw = base_state()
        raw["preferences"]["stop_rules"] = {
            "min_like_any_pp": 60,
            "max_dislike_any_pp": 10,
        }

        exit_code, output, error = self.cli_output(
            raw,
            "--format",
            "markdown",
            "--screen-tray",
        )

        self.assertEqual(exit_code, 0, error)
        self.assertIn("端筛选快报", output)
        self.assertIn("## 质量线", output)
        self.assertNotIn("## TOP 3 汇总", output)
        self.assertNotIn("## 全款概率矩阵", output)

    def test_accepted_tray_returns_to_the_full_report_after_screening(self):
        raw = session_lock_fixture()["accepted_session"]

        screen_code, screening, screen_error = self.cli_output(
            raw,
            "--format",
            "markdown",
            "--screen-tray",
        )
        report_code, report, report_error = self.cli_output(
            raw,
            "--format",
            "markdown",
        )

        self.assertEqual(screen_code, 0, screen_error)
        self.assertEqual(report_code, 0, report_error)
        self.assertNotIn("## 全款概率矩阵", screening)
        self.assertIn("## TOP 3 汇总", report)
        self.assertIn("## 全款概率矩阵", report)

    def test_user_report_validation_rejects_incomplete_or_contradictory_data(self):
        raw = base_state()
        raw["boxes"][2].update({"status": "opened", "known": "C"})
        report = solver.build_report(self.normalized(raw), plan_depth=1)

        mutations = {}

        missing_option = copy.deepcopy(report)
        missing_option["ranking"][0]["remaining_options_desc"].pop()
        mutations["option coverage"] = missing_option

        candidate_mismatch = copy.deepcopy(report)
        candidate_mismatch["top_3"] = list(reversed(candidate_mismatch["top_3"]))
        mutations["top_3"] = candidate_mismatch

        probability_mismatch = copy.deepcopy(report)
        probability_mismatch["ranking"][0]["remaining_options_desc"][0][
            "probability"
        ] += 0.1
        mutations["probability sum"] = probability_mismatch

        action_mismatch = copy.deepcopy(report)
        action_mismatch["next_tool_plan"]["recommended_action"][
            "action"
        ] = "stop"
        action_mismatch["next_tool_plan"]["recommended_action"]["box_id"] = None
        action_mismatch["next_tool_plan"]["action_ranking"][0] = copy.deepcopy(
            action_mismatch["next_tool_plan"]["recommended_action"]
        )
        mutations["action"] = action_mismatch

        for expected_error, malformed in mutations.items():
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(solver.StateError, expected_error):
                    solver.validate_user_report(malformed)

    def test_explicit_json_format_preserves_the_default_cli_contract(self):
        raw = base_state()

        default_code, default_output, default_error = self.cli_output(raw)
        json_code, json_output, json_error = self.cli_output(
            raw,
            "--format",
            "json",
        )

        self.assertEqual(default_code, 0, default_error)
        self.assertEqual(json_code, 0, json_error)
        self.assertEqual(json.loads(default_output), json.loads(json_output))

    def test_markdown_report_supports_all_six_strategy_names(self):
        cases = {}
        for strategy in ("稳妥避雷", "随便中个喜欢", "只冲最爱"):
            raw = base_state()
            raw["preferences"].pop("objective_mode")
            raw["preferences"]["strategy"] = strategy
            cases[strategy] = raw

        for strategy in ("守住底线", "整体最满意"):
            raw = base_state()
            raw["preferences"].pop("objective_mode")
            raw["preferences"].update(
                {
                    "strategy": strategy,
                    "scores": {"A": 10, "B": 0, "C": -10},
                }
            )
            if strategy == "守住底线":
                raw["preferences"].update(
                    {
                        "hard_avoid": ["C"],
                        "hard_avoid_max_pp": 100,
                    }
                )
            cases[strategy] = raw

        resale = base_state()
        resale["preferences"].pop("objective_mode")
        resale["preferences"]["strategy"] = "保值优先"
        resale["market_values"] = {"A": 100, "B": 60, "C": 30}
        cases["保值优先"] = resale

        for strategy, raw in cases.items():
            with self.subTest(strategy=strategy):
                exit_code, output, error = self.cli_output(
                    raw,
                    "--format",
                    "markdown",
                )
                self.assertEqual(exit_code, 0, error)
                self.assertIn(f"本轮采用「{strategy}」", output)
                self.assertIn("## 全款概率矩阵", output)

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
