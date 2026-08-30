import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest

from session_test_helpers import make_session_payload, make_unique_tray


ROOT = pathlib.Path(__file__).parents[1]
SOLVER_PATH = ROOT / "scripts" / "blindbox_solver.py"
spec = importlib.util.spec_from_file_location(
    "blindbox_solver_comparison",
    SOLVER_PATH,
)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
assert spec.loader is not None
spec.loader.exec_module(solver)

EXAMPLE = ROOT / "examples" / "synthetic-tray-comparison.json"
BRIEFING_EXAMPLE = ROOT / "examples" / "synthetic-preference-briefing.json"
LEGACY_EXAMPLE = ROOT / "examples" / "minimal-demo.json"

PREFS = {
    "liked": ["A"],
    "disliked": ["C"],
    "objective_mode": "target_only",
    "tie_tolerance_pp": 0.0,
    "stop_rules": {"min_like_any_pp": 55, "max_draws": 3},
}


def make_tray(tray_id, exclusions, *, participation=None):
    return make_unique_tray(
        tray_id,
        exclusions,
        designs=["A", "B", "C", "D"],
        participation=participation,
    )


def make_session(trays, *, prefs=None, tools=None, events=None,
                 accepted=None, draws_used=0):
    event_list = events or [
        {"seq": 1, "type": "tray_switch", "tray_id": trays[0]["id"]}
    ]
    return solver._normalize_session(
        make_session_payload(
            trays,
            event_list,
            series="synthetic-comparison-tests",
            preferences=prefs or PREFS,
            tools=tools or {"hint_cards": 2, "display_cards": 1},
            accepted=accepted,
            draws_used=draws_used,
        )
    )


def ready_tray(tray_id):
    # Best box has P(A)=1/2 with zero dislike risk; passes a 50% like line.
    return make_tray(tray_id, [["C", "D"], ["C", "D"], ["A", "B"], []])


def pair_tray(tray_id):
    # A lives in exactly two boxes; direct P(A)=1/2 fails a 55% line, but a
    # display card always leaves a qualifying box (rescue = 100%).
    return make_tray(tray_id, [["C", "D"], ["C", "D"], ["A", "B"], ["A", "B"]])


def spread_tray(tray_id):
    # A can sit in three boxes; direct P(A)=1/3 and only the branch where the
    # display card reveals A qualifies (rescue = 1/3).
    return make_tray(tray_id, [["D"], ["D"], ["D"], ["A"]])


def comparison_rows(report):
    return report["comparison"]["rows"]


class TrayParticipationNormalizationTests(unittest.TestCase):
    def test_participation_defaults_to_active(self):
        session = make_session([ready_tray("t-a"), pair_tray("t-b")])
        self.assertEqual(
            session["_tray_participations"], {"t-a": "active", "t-b": "active"}
        )

    def test_history_participation_is_recorded(self):
        session = make_session(
            [ready_tray("t-a"), make_tray("t-old", [[]] * 4, participation="history")]
        )
        self.assertEqual(session["_tray_participations"]["t-old"], "history")

    def test_invalid_participation_fails_closed(self):
        with self.assertRaises(solver.StateError):
            make_session(
                [ready_tray("t-a"), make_tray("t-bad", [[]] * 4, participation="archived")]
            )

    def test_cross_series_trays_fail_closed(self):
        trays = [ready_tray("t-a"), pair_tray("t-b")]
        trays[1]["series"] = "another-series"
        with self.assertRaisesRegex(solver.StateError, r"same series"):
            make_session(trays)


class TrayComparisonReportTests(unittest.TestCase):
    def build_example_report(self, **kwargs):
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        session = solver._normalize_session(payload)
        return build_validated(session, **kwargs)

    def test_example_ranks_operable_trays_and_excludes_the_rest(self):
        report = self.build_example_report()
        rows = comparison_rows(report)
        self.assertEqual(
            [row["tray_id"] for row in rows], ["tray-1", "tray-2"]
        )
        self.assertEqual(
            [(item["tray_id"], item["reason"]) for item in report["comparison"]["excluded_trays"]],
            [("tray-3", "history"), ("tray-4", "released"), ("tray-5", "inoperable")],
        )
        self.assertEqual([row["rank"] for row in rows], [1, 2])
        statuses = [row["status"] for row in rows]
        self.assertEqual(
            statuses, sorted(statuses, key=solver.TRAY_COMPARISON_STATUS_RANK.get)
        )
        self.assertEqual(rows[0]["status"], "ready")
        self.assertEqual(rows[1]["status"], "tool_dependent")
        for row in rows:
            for key in ("p_like_any_pp", "p_dislike_any_pp", "p_hard_avoid_pp"):
                self.assertTrue(0.0 <= row["metrics"][key] <= 100.0)
            self.assertTrue(0.0 <= row["post_tool_draw_probability_pp"] <= 100.0)

    def test_rescue_probability_is_labeled_not_a_win_rate(self):
        report = self.build_example_report()
        self.assertIn("不是中奖率", report["comparison"]["rescue_probability_semantics"])

    def test_rows_match_solo_tray_computation(self):
        report = self.build_example_report()
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        tray_payload = copy.deepcopy(payload["trays"][1])
        tray_payload.update(
            {
                "preferences": copy.deepcopy(payload["preferences"]),
                "tools": copy.deepcopy(payload["tools"]),
            }
        )
        solo = solver._normalize_state(tray_payload)
        solo_best = solver.available_box_metrics(solo, solver.analyze_posterior(solo))[0]
        row = comparison_rows(report)[1]
        self.assertEqual(row["direct_best_box_id"], solo_best["box_id"])
        self.assertAlmostEqual(
            row["metrics"]["p_like_any_pp"], 100.0 * solo_best["p_like_any"], places=9
        )
        self.assertAlmostEqual(
            row["metrics"]["p_dislike_any_pp"], 100.0 * solo_best["p_dislike_any"], places=9
        )

    def test_report_preserves_session_level_contract(self):
        report = self.build_example_report()
        summary = report["session_summary"]
        self.assertEqual(summary["tools"]["hint_cards"], 3)
        self.assertEqual(summary["tools"]["display_cards"], 1)
        self.assertEqual(summary["draws_used"], 1)
        self.assertEqual(summary["accepted_tray_id"], None)
        self.assertEqual(
            sorted(summary["tray_ids"]), ["tray-1", "tray-2", "tray-3", "tray-4", "tray-5"]
        )
        self.assertEqual(
            report["stop_rules"], {"min_like_any_pp": 50, "max_draws": 3}
        )

    def test_recommendation_points_at_top_ranked_tray(self):
        report = self.build_example_report()
        recommendation = report["recommendation"]
        self.assertEqual(
            recommendation["recommended_tray_id"], comparison_rows(report)[0]["tray_id"]
        )
        self.assertEqual(recommendation["action"], "execute_first_action")
        self.assertIsNotNone(recommendation["first_action"])
        self.assertFalse(recommendation["future_tray_improvement_guaranteed"])
        self.assertFalse(recommendation["release_required_before_switch"])

    def test_accepted_session_flags_release_before_switch(self):
        report = self.build_example_report()
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        events = payload["events"] + [
            {
                "seq": len(payload["events"]) + 1,
                "type": "tray_switch",
                "tray_id": "tray-2",
            },
            {
                "seq": len(payload["events"]) + 2,
                "type": "tray_accepted",
                "tray_id": "tray-2",
            },
        ]
        payload["events"] = events
        payload["active_tray_id"] = "tray-2"
        payload["accepted_tray_id"] = "tray-2"
        session = solver._normalize_session(payload)
        accepted_report = solver.build_tray_comparison_report(session)
        self.assertTrue(
            accepted_report["recommendation"]["release_required_before_switch"]
        )
        markdown = solver.render_tray_comparison_markdown(accepted_report)
        self.assertIn("记录释放", markdown)

    def test_single_tray_session_fails_closed(self):
        session = make_session([ready_tray("t-only")])
        with self.assertRaises(solver.StateError):
            solver.build_tray_comparison_report(session)

    def test_tool_dependent_rows_order_by_qualifying_branch(self):
        report = build_validated(
            make_session([pair_tray("t-pair"), spread_tray("t-spread")])
        )
        rows = comparison_rows(report)
        self.assertEqual(
            [row["status"] for row in rows], ["tool_dependent", "tool_dependent"]
        )
        rescues = [row["post_tool_draw_probability_pp"] for row in rows]
        self.assertGreater(rescues[0], rescues[1])
        self.assertEqual([row["tray_id"] for row in rows], ["t-pair", "t-spread"])
        for row in rows:
            self.assertNotEqual(row["first_tool_action"]["tool"], "none")

    def test_no_card_preferred_within_practical_tolerance(self):
        prefs = copy.deepcopy(PREFS)
        prefs["tie_tolerance_pp"] = 60.0
        prefs["stop_rules"] = {"min_like_any_pp": 50, "max_draws": 3}
        report = build_validated(
            make_session([ready_tray("t-ready"), pair_tray("t-pair")], prefs=prefs)
        )
        for row in comparison_rows(report):
            self.assertEqual(row["status"], "ready")
            self.assertEqual(row["first_tool_action"]["tool"], "none")

    def test_switch_status_without_cards_or_rescue(self):
        report = build_validated(
            make_session(
                [spread_tray("t-spread"), pair_tray("t-pair")],
                tools={"hint_cards": 0, "display_cards": 0},
            )
        )
        rows = comparison_rows(report)
        self.assertEqual({row["status"] for row in rows}, {"switch"})
        recommendation = report["recommendation"]
        self.assertIsNone(recommendation["first_action"])
        self.assertEqual(recommendation["action"], "stop_or_review")
        markdown = solver.render_tray_comparison_markdown(report)
        self.assertIn("没有可直接执行的首步动作", markdown)

    def test_needs_acceptance_rules_ranks_after_ready(self):
        prefs = copy.deepcopy(PREFS)
        prefs["stop_rules"] = {"max_draws": 3}
        report = build_validated(
            make_session([ready_tray("t-ready"), pair_tray("t-pair")], prefs=prefs)
        )
        statuses = [row["status"] for row in comparison_rows(report)]
        self.assertEqual(statuses, ["needs_acceptance_rules", "needs_acceptance_rules"])

    def test_depth_two_requires_two_cards(self):
        session = make_session(
            [pair_tray("t-pair"), spread_tray("t-spread")],
            tools={"hint_cards": 1, "display_cards": 0},
        )
        with self.assertRaises(solver.StateError):
            solver.build_tray_comparison_report(session, compare_depth=2)
        report = solver.build_tray_comparison_report(session, compare_depth=1)
        solver.validate_tray_comparison_report(report)

    def test_depth_two_expands_only_head_candidates(self):
        report = build_validated(
            make_session(
                [
                    pair_tray("t-pair"),
                    ready_tray("t-ready"),
                    spread_tray("t-spread"),
                ],
                prefs={
                    **copy.deepcopy(PREFS),
                    "stop_rules": {"min_like_any_pp": 50, "max_draws": 3},
                },
            ),
            compare_depth=2,
        )
        depth_two = report["comparison"]["depth_two"]
        self.assertEqual(
            depth_two["head_candidate_tray_ids"], ["t-pair", "t-ready"]
        )
        self.assertGreaterEqual(depth_two["cards_available"], 2)
        rows = comparison_rows(report)
        head_ids = set(depth_two["head_candidate_tray_ids"])
        for row in rows:
            if row["tray_id"] in head_ids:
                detail = row["depth_two_detail"]
                self.assertIsInstance(detail["first_action_changed_vs_depth_1"], bool)
                self.assertIsInstance(
                    detail["terminal_value_practically_equivalent_to_depth_1"], bool
                )
                self.assertIn("primary_gain_vs_one_card_pp", detail)
            else:
                self.assertIn("depth_two_note", row)
                self.assertNotIn("depth_two_detail", row)
        self.assertEqual(report["recommendation"]["planning_horizon"], "two_card")

    def test_validator_rejects_tampered_reports(self):
        report = self.build_example_report()

        def tampered(mutate):
            broken = copy.deepcopy(report)
            mutate(broken)
            return broken

        def drop_row(broken):
            broken["comparison"]["rows"] = broken["comparison"]["rows"][1:]

        def duplicate_rank(broken):
            broken["comparison"]["rows"][1]["rank"] = 1

        def break_status_order(broken):
            broken["comparison"]["rows"].reverse()
            for rank, row in enumerate(broken["comparison"]["rows"], start=1):
                row["rank"] = rank

        def wrong_recommendation(broken):
            broken["recommendation"]["recommended_tray_id"] = "tray-2"

        def guarantee_future_tray(broken):
            broken["comparison"]["future_tray_improvement_guaranteed"] = True

        def strip_rescue_semantics(broken):
            broken["comparison"]["rescue_probability_semantics"] = "中奖率"

        def no_card_tool_dependent(broken):
            row = broken["comparison"]["rows"][1]
            row["first_tool_action"] = dict(row["first_tool_action"], tool="none")

        def out_of_range_metric(broken):
            broken["comparison"]["rows"][0]["metrics"]["p_like_any_pp"] = 250.0

        def drop_depth_two_detail(broken):
            del broken["comparison"]["rows"][0]["depth_two_detail"]

        for mutate in (
            drop_row,
            duplicate_rank,
            break_status_order,
            wrong_recommendation,
            guarantee_future_tray,
            strip_rescue_semantics,
            no_card_tool_dependent,
            out_of_range_metric,
        ):
            with self.assertRaises(solver.StateError, msg=mutate.__name__):
                solver.validate_tray_comparison_report(tampered(mutate))

        depth_two_report = self.build_example_report(compare_depth=2)
        broken = copy.deepcopy(depth_two_report)
        drop_depth_two_detail(broken)
        with self.assertRaises(solver.StateError):
            solver.validate_tray_comparison_report(broken)

    def test_validator_rejects_reordered_ready_trays(self):
        prefs = copy.deepcopy(PREFS)
        prefs["stop_rules"] = {"min_like_any_pp": 30, "max_draws": 3}
        report = build_validated(
            make_session(
                [pair_tray("t-strong"), spread_tray("t-weak")],
                prefs=prefs,
            )
        )
        rows = report["comparison"]["rows"]
        self.assertEqual([row["status"] for row in rows], ["ready", "ready"])
        self.assertGreater(
            rows[0]["metrics"]["p_like_any_pp"],
            rows[1]["metrics"]["p_like_any_pp"],
        )

        rows.reverse()
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        report["recommendation"]["recommended_tray_id"] = rows[0]["tray_id"]
        with self.assertRaisesRegex(solver.StateError, r"strategy ranking"):
            solver.validate_tray_comparison_report(report)

    def test_lossless_second_tray_addition(self):
        single = make_session([ready_tray("t-a")])
        first_row_key, first_row = solver._tray_comparison_row(
            "t-a", single["_tray_states"]["t-a"], beam_width=3
        )
        dual = make_session([ready_tray("t-a"), pair_tray("t-b")])
        report = build_validated(dual)
        single_prefs = single["_tray_states"]["t-a"]["preferences"]
        dual_prefs = dual["_tray_states"]["t-a"]["preferences"]
        self.assertEqual(dual_prefs["stop_rules"], single_prefs["stop_rules"])
        self.assertEqual(dual_prefs["liked"], single_prefs["liked"])
        self.assertEqual(dual_prefs["objective_mode"], single_prefs["objective_mode"])
        self.assertEqual(dual["tools"], single["tools"])
        self.assertEqual(dual["draws_used"], single["draws_used"])
        self.assertEqual(len(dual["events"]), len(single["events"]))
        dual_row = comparison_rows(report)[0]
        self.assertEqual(dual_row["tray_id"], "t-a")
        self.assertEqual(dual_row["direct_best_box_id"], first_row["direct_best_box_id"])
        self.assertAlmostEqual(
            dual_row["metrics"]["p_like_any_pp"],
            first_row["metrics"]["p_like_any_pp"],
            places=9,
        )
        self.assertAlmostEqual(
            dual_row["metrics"]["p_dislike_any_pp"],
            first_row["metrics"]["p_dislike_any_pp"],
            places=9,
        )


def build_validated(session, **kwargs):
    report = solver.build_tray_comparison_report(session, **kwargs)
    solver.validate_tray_comparison_report(report)
    solver.render_tray_comparison_markdown(report)
    return report


class TrayComparisonRendererTests(unittest.TestCase):
    def render_example(self, **kwargs):
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        session = solver._normalize_session(payload)
        report = solver.build_tray_comparison_report(session, **kwargs)
        return solver.render_tray_comparison_markdown(report)

    def test_markdown_uses_fixed_section_order(self):
        markdown = self.render_example()
        self.assertTrue(markdown.startswith("# "))
        positions = [
            markdown.index("## 结论"),
            markdown.index("## 逐端比较"),
            markdown.index("## 下一步"),
            markdown.index("## 质量线"),
            markdown.index("## 模型口径"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_markdown_carries_required_content(self):
        markdown = self.render_example()
        self.assertIn("不是中奖率", markdown)
        self.assertIn("每端独立求解", markdown)
        self.assertIn("未参与排序的端", markdown)
        self.assertIn("多端横比不重置偏好、质量线、卡数、抽数和历史证据", markdown)
        self.assertIn("直接可做", markdown)
        self.assertIn("依赖道具", markdown)
        for label in ("已释放", "历史只读", "无可用盒"):
            self.assertIn(label, markdown)

    def test_markdown_labels_depth_two_horizon(self):
        markdown = self.render_example(compare_depth=2)
        self.assertIn("两步时域（仅头部候选端）", markdown)
        self.assertIn("首步不变", markdown)
        self.assertIn("保持一步时域结果", markdown)


class TrayComparisonCLITests(unittest.TestCase):
    def run_cli(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = solver.main(argv)
        return exit_code, stdout.getvalue() + stderr.getvalue()

    def test_markdown_round_trip(self):
        exit_code, markdown = self.run_cli(
            [str(EXAMPLE), "--compare-trays", "--format", "markdown"]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(markdown.startswith("# "))
        self.assertIn("## 结论", markdown)
        self.assertIn("## 下一步", markdown)

    def test_json_round_trip(self):
        exit_code, output = self.run_cli([str(EXAMPLE), "--compare-trays"])
        self.assertEqual(exit_code, 0)
        report = json.loads(output)
        self.assertEqual(report["report_type"], "tray_comparison")
        solver.validate_tray_comparison_report(report)

    def test_depth_two_cli_round_trip(self):
        exit_code, markdown = self.run_cli(
            [str(EXAMPLE), "--compare-trays", "--compare-depth", "2", "--format", "markdown"]
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("两步时域（仅头部候选端）", markdown)

    def test_compare_depth_requires_compare_trays(self):
        exit_code, output = self.run_cli(
            [str(EXAMPLE), "--compare-depth", "2"]
        )
        self.assertNotEqual(exit_code, 0)
        self.assertIn("--compare-depth requires --compare-trays", output)

    def test_compare_trays_rejects_other_modes(self):
        for extra in (
            ["--screen-tray"],
            ["--plan-depth", "2"],
            ["--calibrate-preferences"],
        ):
            with self.subTest(extra=extra):
                exit_code, output = self.run_cli(
                    [str(EXAMPLE), "--compare-trays"] + extra
                )
                self.assertNotEqual(exit_code, 0)
                self.assertIn("cannot be combined", output)

    def test_legacy_single_tray_state_is_rejected(self):
        exit_code, output = self.run_cli([str(LEGACY_EXAMPLE), "--compare-trays"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("at least two trays", output)

    def test_briefing_state_is_rejected(self):
        exit_code, _ = self.run_cli([str(BRIEFING_EXAMPLE), "--compare-trays"])
        self.assertNotEqual(exit_code, 0)

    def test_depth_two_with_too_few_cards_fails_closed(self):
        payload = {
            "session_schema_version": 1,
            "series": "synthetic-comparison-tests",
            "active_tray_id": "t-pair",
            "accepted_tray_id": None,
            "draws_used": 0,
            "preferences": copy.deepcopy(PREFS),
            "tools": {"hint_cards": 1, "display_cards": 0},
            "trays": [pair_tray("t-pair"), spread_tray("t-spread")],
            "events": [{"seq": 1, "type": "tray_switch", "tray_id": "t-pair"}],
            "meta": {"provenance": "synthetic"},
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            path = handle.name
        try:
            exit_code, output = self.run_cli(
                [path, "--compare-trays", "--compare-depth", "2"]
            )
            self.assertNotEqual(exit_code, 0)
            self.assertIn("at least two available cards", output)
        finally:
            pathlib.Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
