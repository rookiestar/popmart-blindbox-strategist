import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SOLVER_PATH = ROOT / "scripts" / "blindbox_solver.py"
spec = importlib.util.spec_from_file_location(
    "blindbox_solver_briefing",
    SOLVER_PATH,
)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
assert spec.loader is not None
spec.loader.exec_module(solver)

EXAMPLE = ROOT / "examples" / "synthetic-preference-briefing.json"

BASE_SCORES = {
    "A": 10,
    "B": 8,
    "C": 7,
    "D": 6,
    "E": 5,
    "F": 1,
    "G": 0,
    "H": 0,
    "I": -2,
    "J": -3,
    "K": -6,
    "L": -10,
}


def briefing_payload():
    return {
        "session_schema_version": 1,
        "series": "合成系列",
        "regular_count": 12,
        "designs": ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"],
        "preferences": {
            "strategy": "随便中个喜欢",
            "scores": dict(BASE_SCORES),
            "stop_rules": {"max_draws": 2},
        },
        "tools": {"hint_cards": 2, "display_cards": 1},
        "meta": {"provenance": "synthetic"},
    }


def normalized_briefing(payload=None):
    return solver._normalize_session(payload or briefing_payload())


class PreferenceBriefingNormalizationTests(unittest.TestCase):
    def test_briefing_normalizes_without_trays_or_boxes(self):
        session = normalized_briefing()
        self.assertTrue(session["_briefing_input"])
        state = session["_briefing_state"]
        self.assertEqual(len(state["boxes"]), 12)
        self.assertTrue(
            all(box["status"] == "available" for box in state["boxes"])
        )
        self.assertTrue(
            all(not box["excluded"] for box in state["boxes"])
        )
        self.assertEqual(len(state["_union_designs"]), 12)
        self.assertEqual(session["draws_used"], 0)

    def test_legacy_and_envelope_inputs_are_not_briefings(self):
        legacy = {
            "model": {"type": "unique_regular", "designs": ["A", "B", "C"]},
            "boxes": [
                {"id": "1", "status": "available"},
                {"id": "2", "status": "available"},
                {"id": "3", "status": "available"},
            ],
        }
        session = solver._normalize_session(legacy)
        self.assertFalse(session.get("_briefing_input"))
        self.assertTrue(session["_legacy_input"])

    def test_incomplete_design_coverage_fails_closed(self):
        payload = briefing_payload()
        payload.pop("designs")
        partial = dict(BASE_SCORES)
        partial.pop("L")
        payload["preferences"]["scores"] = partial
        with self.assertRaisesRegex(
            solver.StateError,
            r"refusing to guess",
        ):
            solver._normalize_session(payload)

    def test_design_count_mismatch_fails_closed(self):
        payload = briefing_payload()
        payload["designs"] = payload["designs"][:11]
        with self.assertRaisesRegex(
            solver.StateError,
            r"regular_count is 12",
        ):
            solver._normalize_session(payload)

    def test_derived_design_count_mismatch_fails_closed(self):
        payload = briefing_payload()
        payload.pop("designs")
        payload["regular_count"] = 11
        with self.assertRaisesRegex(
            solver.StateError,
            r"design coverage is incomplete",
        ):
            solver._normalize_session(payload)

    def test_mixture_models_are_rejected_without_a_real_tray(self):
        payload = briefing_payload()
        payload["model"] = {
            "type": "mixture",
            "scenarios": [{"name": "s", "prior": 1, "designs": list("ABCDEFGHIJKL")}],
        }
        with self.assertRaisesRegex(
            solver.StateError,
            r"mixture models need a real tray",
        ):
            solver._normalize_session(payload)

    def test_conflicting_dual_score_tables_fail_closed(self):
        payload = briefing_payload()
        payload["preferences"]["utility_scores"] = dict(BASE_SCORES)
        payload["preferences"]["utility_scores"]["A"] = 9
        with self.assertRaisesRegex(
            solver.StateError,
            r"cannot carry two different scores",
        ):
            solver._normalize_session(payload)

    def test_equal_dual_score_tables_still_normalize(self):
        payload = briefing_payload()
        payload["preferences"]["utility_scores"] = dict(BASE_SCORES)
        session = solver._normalize_session(payload)
        self.assertTrue(session["_briefing_input"])

    def test_scoring_strategy_with_partial_scores_fails_closed(self):
        payload = briefing_payload()
        payload["preferences"]["strategy"] = "守住底线"
        payload["preferences"]["hard_avoid_max_pp"] = 10
        partial = {"A": 10, "B": 8}
        payload["preferences"]["scores"] = partial
        payload["preferences"]["hard_avoid"] = ["L"]
        with self.assertRaisesRegex(
            solver.StateError,
            r"scoring strategies require every design",
        ):
            solver._normalize_session(payload)

    def test_target_strategy_with_partial_scores_fails_closed(self):
        payload = briefing_payload()
        payload["preferences"]["scores"].pop("L")
        with self.assertRaisesRegex(
            solver.StateError,
            r"complete scores|score coverage",
        ):
            solver._normalize_session(payload)

    def test_unconfirmed_score_default_does_not_fill_missing_designs(self):
        payload = briefing_payload()
        payload["preferences"]["scores"].pop("L")
        payload["preferences"]["score_default"] = 0
        payload["preferences"]["score_default_confirmed"] = False
        with self.assertRaisesRegex(
            solver.StateError,
            r"score_default_confirmed=true",
        ):
            solver._normalize_session(payload)


class PreferenceBriefingReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = normalized_briefing()
        cls.report = solver.build_preference_briefing_report(
            cls.session["_briefing_state"]
        )

    def test_blind_baseline_matches_uniform_marginals(self):
        baseline = self.report["blind_baseline"]
        self.assertEqual(baseline["regular_count"], 12)
        self.assertAlmostEqual(
            baseline["p_favorite_any_pp"], 100.0 / 12.0, places=9
        )
        self.assertAlmostEqual(
            baseline["p_like_any_pp"], 400.0 / 12.0, places=9
        )
        self.assertAlmostEqual(
            baseline["p_dislike_any_pp"], 200.0 / 12.0, places=9
        )
        self.assertAlmostEqual(
            baseline["p_hard_avoid_pp"], 100.0 / 12.0, places=9
        )
        self.assertAlmostEqual(
            baseline["expected_score"], 16.0 / 12.0, places=9
        )

    def test_reference_rules_strictly_improve_the_blind_baseline(self):
        rules = {
            rule["rule"]: rule
            for rule in self.report["reference_lines"]["rules"]
        }
        self.assertEqual(
            set(rules),
            {"min_like_any_pp", "max_dislike_any_pp", "max_hard_avoid_pp"},
        )
        self.assertAlmostEqual(
            rules["min_like_any_pp"]["suggested_value"], 40.0
        )
        self.assertAlmostEqual(
            rules["min_like_any_pp"]["delta_vs_baseline_pp"],
            40.0 - 100.0 * 4.0 / 12.0,
            places=9,
        )
        self.assertAlmostEqual(
            rules["max_dislike_any_pp"]["suggested_value"], 15.0
        )
        self.assertAlmostEqual(
            rules["max_hard_avoid_pp"]["suggested_value"], 5.0
        )
        for rule in rules.values():
            self.assertEqual(rule["basis"], "blind_baseline_strictly_improved")
            self.assertNotEqual(rule["delta_vs_baseline_pp"], 0.0)
        self.assertEqual(self.report["status"], "needs_confirmation")

    def test_stop_rules_stay_unmutated(self):
        self.assertTrue(self.report["stop_rules_mutated"] is False)
        self.assertTrue(self.report["confirmation_required"] is True)
        self.assertEqual(
            self.report["existing_stop_rules"], {"max_draws": 2}
        )

    def test_validator_accepts_the_built_report(self):
        solver.validate_preference_briefing_report(self.report)

    def test_report_builder_rechecks_complete_score_coverage(self):
        state = copy.deepcopy(self.session["_briefing_state"])
        state["_score_coverage"].update(
            {"complete": False, "missing_scores": ["L"]}
        )
        with self.assertRaisesRegex(solver.StateError, r"complete scores"):
            solver.build_preference_briefing_report(state)

    def test_whole_number_baseline_still_requires_strict_improvement(self):
        payload = briefing_payload()
        payload["regular_count"] = 10
        designs = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
        payload["designs"] = designs
        payload["preferences"]["scores"] = {
            "A": 10, "B": 9, "C": 8, "D": 7, "E": 6,
            "F": 0, "G": 0, "H": 0, "I": -2, "J": -10,
        }
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        rules = {
            rule["rule"]: rule
            for rule in report["reference_lines"]["rules"]
        }
        self.assertAlmostEqual(
            rules["min_like_any_pp"]["baseline_pp"], 50.0
        )
        self.assertAlmostEqual(
            rules["min_like_any_pp"]["suggested_value"], 55.0
        )
        self.assertAlmostEqual(
            rules["min_like_any_pp"]["delta_vs_baseline_pp"], 5.0
        )

    def test_confirmed_balanced_reference_matches_issue_16_example(self):
        payload = briefing_payload()
        payload["preferences"]["scores"] = {
            "A": 10,
            "B": 10,
            "C": 8,
            "D": -10,
            "E": -10,
            "F": -10,
            "G": -7,
            "H": -7,
            "I": 0,
            "J": 0,
            "K": 0,
            "L": 0,
        }
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        rules = {
            rule["rule"]: rule["suggested_value"]
            for rule in report["reference_lines"]["rules"]
        }
        self.assertEqual(
            rules,
            {
                "min_like_any_pp": 40.0,
                "max_dislike_any_pp": 35.0,
                "max_hard_avoid_pp": 20.0,
            },
        )

    def test_other_strategies_get_baseline_only(self):
        payload = briefing_payload()
        payload["preferences"]["strategy"] = "稳妥避雷"
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        self.assertEqual(report["status"], "calibration_required")
        self.assertEqual(report["reference_lines"]["rules"], [])
        self.assertIn(
            "--calibrate-preferences",
            report["reference_lines"]["redirect_other_strategies"],
        )
        solver.validate_preference_briefing_report(report)

    def test_target_only_without_complete_scores_fails_closed(self):
        payload = briefing_payload()
        payload["preferences"] = {
            "strategy": "随便中个喜欢",
            "liked": [],
            "disliked": ["L"],
        }
        with self.assertRaisesRegex(solver.StateError, r"complete scores"):
            normalized_briefing(payload)

    def test_explicit_hard_avoid_tier_conflicts_are_listed(self):
        payload = briefing_payload()
        payload["preferences"]["hard_avoid"] = ["K", "I"]
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        conflicts = {
            item["design"]: item
            for item in report["preference_conflicts"]
        }
        self.assertEqual(set(conflicts), {"I", "K"})
        for item in conflicts.values():
            self.assertEqual(item["explicit_field"], "hard_avoid")
            self.assertEqual(
                item["resolution"], "confirmation_required"
            )
            self.assertEqual(item["current_effective_source"], "explicit_field")
        self.assertEqual(conflicts["K"]["score_tier"], "light_dislike")
        self.assertEqual(
            conflicts["I"]["score_tier"], "neutral_disappointed"
        )
        self.assertEqual(report["status"], "preference_conflict")
        self.assertEqual(report["reference_lines"]["rules"], [])
        solver.validate_preference_briefing_report(report)

    def test_explicit_liked_negative_score_conflict_is_listed(self):
        payload = briefing_payload()
        payload["preferences"]["liked"] = ["A", "B", "C", "D", "K"]
        payload["preferences"]["disliked"] = ["L"]
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        conflicts = report["preference_conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["design"], "K")
        self.assertEqual(conflicts[0]["explicit_field"], "liked")
        self.assertEqual(conflicts[0]["score_tier"], "light_dislike")
        self.assertEqual(report["status"], "preference_conflict")

    def test_explicit_groups_require_exact_compatible_score_tiers(self):
        payload = briefing_payload()
        payload["preferences"]["liked"] = ["A", "E"]
        payload["preferences"]["disliked"] = ["I", "L"]
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        conflicts = {
            (item["design"], item["explicit_field"]): item
            for item in report["preference_conflicts"]
        }
        self.assertEqual(
            set(conflicts),
            {("E", "liked"), ("I", "disliked")},
        )
        self.assertEqual(conflicts[("E", "liked")]["score_tier"], "acceptable")
        self.assertEqual(
            conflicts[("I", "disliked")]["score_tier"],
            "neutral_disappointed",
        )
        self.assertEqual(report["status"], "preference_conflict")

    def test_explicit_seven_tier_input_is_machine_checked(self):
        payload = briefing_payload()
        payload["preferences"]["explicit_score_tiers"] = {
            "favorite": ["A"],
            "neutral_disappointed": ["K"],
        }
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        self.assertEqual(report["status"], "preference_conflict")
        self.assertEqual(len(report["preference_conflicts"]), 1)
        conflict = report["preference_conflicts"][0]
        self.assertEqual(conflict["design"], "K")
        self.assertEqual(
            conflict["explicit_field"],
            "explicit_score_tiers.neutral_disappointed",
        )
        self.assertEqual(conflict["score_tier"], "light_dislike")
        self.assertEqual(conflict["current_effective_source"], "scores")

    def test_design_cannot_appear_in_two_explicit_score_tiers(self):
        payload = briefing_payload()
        payload["preferences"]["explicit_score_tiers"] = {
            "favorite": ["A"],
            "liked": ["A"],
        }
        with self.assertRaisesRegex(
            solver.StateError,
            r"only one explicit score tier",
        ):
            normalized_briefing(payload)

    def test_validator_rejects_tampered_reports(self):
        mutated = copy.deepcopy(self.report)
        mutated["stop_rules_mutated"] = True
        with self.assertRaisesRegex(
            solver.StateError, r"stop rules were mutated"
        ):
            solver.validate_preference_briefing_report(mutated)

        unobserved = copy.deepcopy(self.report)
        unobserved["model_warnings"] = [
            warning
            for warning in unobserved["model_warnings"]
            if warning["code"] != "current_tray_not_observed"
        ]
        with self.assertRaisesRegex(
            solver.StateError, r"current tray not observed warning"
        ):
            solver.validate_preference_briefing_report(unobserved)

        loosened = copy.deepcopy(self.report)
        loosened["reference_lines"]["rules"][0]["suggested_value"] = 33.0
        with self.assertRaisesRegex(
            solver.StateError, r"reference line rounding"
        ):
            solver.validate_preference_briefing_report(loosened)

        recommendation = copy.deepcopy(self.report)
        recommendation["top_3"] = []
        with self.assertRaisesRegex(
            solver.StateError, r"formal recommendation leaked"
        ):
            solver.validate_preference_briefing_report(recommendation)

        wrong_strategy = copy.deepcopy(self.report)
        wrong_strategy["strategy_name"] = "稳妥避雷"
        with self.assertRaisesRegex(
            solver.StateError, r"reference line strategy scope"
        ):
            solver.validate_preference_briefing_report(wrong_strategy)

        incomplete_scores = copy.deepcopy(self.report)
        incomplete_scores["score_coverage"]["complete"] = False
        with self.assertRaisesRegex(
            solver.StateError,
            r"score coverage",
        ):
            solver.validate_preference_briefing_report(incomplete_scores)


class PreferenceBriefingRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = solver.build_preference_briefing_report(
            normalized_briefing()["_briefing_state"]
        )
        cls.markdown = solver.render_preference_briefing_markdown(
            cls.report
        )

    def test_markdown_contract(self):
        self.assertTrue(self.markdown.startswith("# "))
        for heading in (
            "## 结论",
            "## 盲抽基线",
            "## 偏好一致性",
            "## 参考线",
            "## 下一步",
            "## 模型口径",
        ):
            self.assertIn(heading, self.markdown)
        self.assertEqual(self.markdown.count("隐藏款：默认未计入"), 1)
        self.assertIn("| 喜欢款合计 | 33.33% |", self.markdown)
        self.assertIn("| 喜欢款至少 | 33.33% | 40% | +6.67pp |", self.markdown)
        self.assertIn("已有停止条件：最多抽 2 盒。本报告未改写。", self.markdown)

    def test_markdown_never_claims_current_tray_attainability(self):
        self.assertNotIn("当前端可达", self.markdown)
        self.assertNotIn("当前端校准结果\n", self.markdown)
        self.assertIn("不是当前端校准", self.markdown)

    def test_markdown_lists_tier_conflicts(self):
        payload = briefing_payload()
        payload["preferences"]["hard_avoid"] = ["K", "I"]
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        markdown = solver.render_preference_briefing_markdown(report)
        self.assertIn("## 偏好一致性", markdown)
        self.assertIn("| K | hard_avoid | -6 | 轻雷 | 待确认 |", markdown)
        self.assertIn("| I | hard_avoid | -2 | 中性但失望 | 待确认 |", markdown)
        self.assertIn("不生成参考线", markdown)
        self.assertNotIn("| 规则 | 盲抽基线 | 建议线 | 相差 |", markdown)

    def test_markdown_redirects_without_target_only(self):
        payload = briefing_payload()
        payload["preferences"]["strategy"] = "稳妥避雷"
        report = solver.build_preference_briefing_report(
            normalized_briefing(payload)["_briefing_state"]
        )
        markdown = solver.render_preference_briefing_markdown(report)
        self.assertIn("--calibrate-preferences", markdown)
        self.assertNotIn("| 规则 | 盲抽基线 | 建议线 | 相差 |", markdown)


class PreferenceBriefingCLITests(unittest.TestCase):
    def run_cli(self, payload, *flags):
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", encoding="utf-8"
        ) as state_file:
            json.dump(payload, state_file, ensure_ascii=False)
            state_file.flush()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = solver.main([state_file.name, *flags])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_example_file_normalizes_and_reports(self):
        session = solver._normalize_session(
            json.loads(EXAMPLE.read_text(encoding="utf-8"))
        )
        self.assertTrue(session["_briefing_input"])

    def test_cli_json_and_markdown_reports(self):
        payload = briefing_payload()
        exit_code, stdout, stderr = self.run_cli(
            payload, "--brief-preferences", "--digits", "6"
        )
        self.assertEqual(exit_code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["report_type"], "preference_briefing")
        self.assertEqual(report["status"], "needs_confirmation")

        exit_code, stdout, stderr = self.run_cli(
            payload, "--brief-preferences", "--format", "markdown"
        )
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("## 结论", stdout)
        self.assertIn("## 下一步", stdout)

    def test_cli_requires_the_flag_on_briefing_inputs(self):
        exit_code, stdout, stderr = self.run_cli(briefing_payload())
        self.assertEqual(exit_code, 2)
        self.assertIn("--brief-preferences", stderr)

    def test_cli_flag_rejects_states_with_boxes(self):
        legacy = {
            "model": {"type": "unique_regular", "designs": ["A", "B", "C"]},
            "boxes": [
                {"id": "1", "status": "available"},
                {"id": "2", "status": "available"},
                {"id": "3", "status": "available"},
            ],
        }
        exit_code, stdout, stderr = self.run_cli(
            legacy, "--brief-preferences"
        )
        self.assertEqual(exit_code, 2)
        self.assertIn("briefing state", stderr)

    def test_cli_flag_rejects_combined_modes(self):
        exit_code, stdout, stderr = self.run_cli(
            briefing_payload(), "--brief-preferences", "--screen-tray"
        )
        self.assertEqual(exit_code, 2)
        self.assertIn("cannot be combined", stderr)

    def test_cli_rejects_duplicate_json_score_keys(self):
        raw = json.dumps(briefing_payload(), ensure_ascii=False)
        raw = raw.replace(
            '"A": 10',
            '"A": 10, "A": -7',
            1,
        )
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", encoding="utf-8"
        ) as state_file:
            state_file.write(raw)
            state_file.flush()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = solver.main(
                    [state_file.name, "--brief-preferences"]
                )
        self.assertEqual(exit_code, 2)
        self.assertIn("duplicate JSON key", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
