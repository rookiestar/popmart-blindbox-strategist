"""Regressions for issue #20: event-ledger automatic session review.

Synthetic sessions only. They cover deterministic ledger replay (baseline
inventory rewinding, per-opening ex-ante probabilities, rankings, accepted
failure semantics, and quality lines as they were at each decision point),
per-card ex-ante branch probabilities and decision impact, the full
commitment/acceptance/release/switch/override lifecycle including derived
auto-commitments and quality-line upgrades, bias checks that only use
ex-ante information, legacy states that mark blind spots instead of
fabricating numbers, validator tamper rejection, and the CLI surface.
"""

import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from session_test_helpers import make_box, make_session_payload


ROOT = pathlib.Path(__file__).parents[1]
SOLVER_PATH = ROOT / "scripts" / "blindbox_solver.py"
spec = importlib.util.spec_from_file_location(
    "blindbox_solver_review",
    SOLVER_PATH,
)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
assert spec.loader is not None
spec.loader.exec_module(solver)

FIXTURE = ROOT / "examples" / "synthetic-session-review.json"

PREFS = {
    "liked": ["A"],
    "disliked": ["C"],
    "hard_avoid": ["D"],
    "objective_mode": "target_only",
    "tie_tolerance_pp": 0.0,
    "stop_rules": {"min_like_any_pp": 55, "max_draws": 3},
}
TOOLS = {"hint_cards": 2, "display_cards": 1}
MODEL = {
    "type": "unique_regular",
    "designs": ["A", "B", "C", "D"],
    "hint_labels": ["A", "B", "C", "D"],
}


box = make_box


def pair_boxes():
    # A sits in boxes 1-2 only: direct P(A) = 50% fails a 55% quality line.
    return [
        box("1", ["C", "D"]),
        box("2", ["C", "D"]),
        box("3", ["A", "B"]),
        box("4", ["A", "B"]),
    ]


def payload(trays, events, *, active=None, tools=None, prefs=None,
            draws=None, accepted=None, candidate=None):
    kwargs = {}
    if candidate is not None:
        kwargs["candidate"] = candidate
    return make_session_payload(
        trays,
        events,
        series="synthetic-review-tests",
        preferences=prefs or PREFS,
        tools=tools or TOOLS,
        active=active,
        accepted=accepted,
        draws_used=draws,
        **kwargs,
    )


def session_of(trays, events, **kwargs):
    return solver._normalize_session(payload(trays, events, **kwargs))


def plain_tray(tray_id, boxes=None):
    return {
        "id": tray_id,
        "model": copy.deepcopy(MODEL),
        "boxes": pair_boxes() if boxes is None else boxes,
    }


def hint(seq, tray_id, box_id, excluded):
    return {
        "seq": seq,
        "type": "hint_used",
        "tray_id": tray_id,
        "box_id": box_id,
        "excluded": excluded,
    }


def display(seq, tray_id, box_id, design):
    return {
        "seq": seq,
        "type": "display_used",
        "tray_id": tray_id,
        "box_id": box_id,
        "design": design,
    }


def opened(seq, tray_id, box_id, design):
    return {
        "seq": seq,
        "type": "opened_result",
        "tray_id": tray_id,
        "box_id": box_id,
        "design": design,
    }


def override(seq, tray_id, rule, old_value, new_value, reason):
    return {
        "seq": seq,
        "type": "stop_rule_override",
        "tray_id": tray_id,
        "rule": rule,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
    }


def released(seq, tray_id, reason="合成测试释放原因"):
    return {
        "seq": seq,
        "type": "tray_released",
        "tray_id": tray_id,
        "reason": reason,
    }


def switch(seq, tray_id):
    return {"seq": seq, "type": "tray_switch", "tray_id": tray_id}


def review_fixture_session():
    """The richest synthetic narrative: cards, an opening, an override,
    a reasoned release, a switch, and a non-optimal second opening."""
    return solver._normalize_session(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


def run_cli(state, *flags):
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False
    ) as handle:
        json.dump(state, handle, ensure_ascii=False)
        path = handle.name
    return subprocess.run(
        [sys.executable, str(SOLVER_PATH), path, *flags],
        capture_output=True,
        text=True,
    )


class ReplayDeterminismTests(unittest.TestCase):
    def test_replayed_numbers_match_hand_computed_posteriors(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        openings = report["replay"]["openings"]
        self.assertEqual(len(openings), 2)

        first, second = openings
        # Display already revealed box 1 = B, so box 2 held A with certainty.
        self.assertAlmostEqual(first["actual_design_prior_pp"], 100.0)
        self.assertEqual(first["actual_design_rank"], 1)
        self.assertEqual(first["design"], "A")
        self.assertAlmostEqual(
            first["outcome_class_probabilities_pp"]["liked"], 100.0
        )
        self.assertAlmostEqual(first["accepted_failure_pp"], 0.0)
        self.assertTrue(first["chosen_was_optimal"])
        self.assertTrue(
            all(check["passed"] for check in first["quality_lines_at_draw"])
        )

        # Tray t-b excludes A from box 4: B/C/D split the remaining mass.
        self.assertAlmostEqual(second["actual_design_prior_pp"],
                               100.0 / 3.0, places=6)
        self.assertEqual(second["actual_design_rank"], 2)
        self.assertEqual(
            [item["design"] for item in second["possible_designs_ranked"]],
            ["B", "C", "D"],
        )
        probabilities = second["outcome_class_probabilities_pp"]
        self.assertAlmostEqual(probabilities["liked"], 0.0)
        self.assertAlmostEqual(probabilities["neutral"], 100.0 / 3.0,
                               places=6)
        self.assertAlmostEqual(probabilities["disliked"], 200.0 / 3.0,
                               places=6)
        self.assertAlmostEqual(probabilities["hard_avoid"], 100.0 / 3.0,
                               places=6)
        self.assertAlmostEqual(second["accepted_failure_pp"], 100.0)
        self.assertFalse(second["chosen_was_optimal"])
        alternative = second["strongest_alternative"]
        self.assertEqual(alternative["box_id"], "1")
        self.assertAlmostEqual(alternative["p_like_any_pp"], 100.0 / 3.0,
                               places=6)
        self.assertAlmostEqual(alternative["p_like_any_delta_pp"],
                               -100.0 / 3.0, places=6)
        self.assertFalse(
            all(check["passed"] for check in second["quality_lines_at_draw"])
        )

    def test_replay_is_deterministic_and_leaves_the_session_untouched(self):
        session = review_fixture_session()
        before = copy.deepcopy(session)
        first = solver.build_session_review_report(session)
        second = solver.build_session_review_report(session)
        self.assertEqual(session, before)
        self.assertEqual(first, second)

    def test_baseline_inventory_is_rewound_from_the_final_state(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        replay = report["replay"]
        self.assertTrue(replay["recoverable"])
        self.assertEqual(replay["baseline"]["draws_used"], 0)
        self.assertEqual(
            replay["baseline"]["tools"],
            {"hint_cards": 2, "display_cards": 1, "reveal_cards": 0},
        )
        counters = report["global_counters"]
        self.assertEqual(counters["draws_used"], 2)
        self.assertEqual(
            counters["draws_used"], len(replay["openings"])
        )
        self.assertEqual(counters["remaining_tools"],
                         {"hint_cards": 1, "display_cards": 0,
                          "reveal_cards": 0})
        self.assertEqual(counters["max_draws"], 3)

    def test_stop_rules_are_rewound_to_the_pre_override_line(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        session = review_fixture_session()
        final_line = session["_tray_states"]["t-a"]["preferences"][
            "stop_rules"
        ]["min_like_any_pp"]
        self.assertEqual(final_line, 45)
        first = report["replay"]["openings"][0]
        threshold = first["quality_lines_at_draw"][0]["threshold"]
        self.assertEqual(threshold, 55.0)

    def test_override_chains_apply_in_order(self):
        boxes = pair_boxes()
        boxes[2]["tool_used"] = True
        boxes[2]["excluded"] = ["B", "A"]
        boxes[0].update({"tool_used": True, "known": "B"})
        boxes[1].update({"status": "opened", "known": "A"})
        events = [
            hint(1, "t-a", "3", "A"),
            override(2, "t-a", "min_like_any_pp", 55, 50,
                     "合成校准：先降到 50"),
            display(3, "t-a", "1", "B"),
            opened(4, "t-a", "2", "A"),
            override(5, "t-a", "min_like_any_pp", 50, 45,
                     "合成校准：再降到 45"),
        ]
        session = session_of(
            [plain_tray("t-a", boxes)],
            events,
            prefs={
                **PREFS,
                "stop_rules": {"min_like_any_pp": 45, "max_draws": 3},
            },
            tools={"hint_cards": 1, "display_cards": 0},
        )
        report = solver.build_session_review_report(session)
        opening = report["replay"]["openings"][0]
        self.assertEqual(
            opening["quality_lines_at_draw"][0]["threshold"], 50.0
        )
        entries = [
            (entry["seq"], entry["old_value"], entry["new_value"])
            for entry in report["stop_rule_overrides"]
        ]
        self.assertEqual(entries, [(3, 55, 50), (7, 50, 45)])

    def test_new_stop_rule_with_null_old_value_replays_without_crashing(self):
        boxes = pair_boxes()
        boxes[2]["tool_used"] = True
        boxes[2]["excluded"] = ["A", "B"]
        session = session_of(
            [plain_tray("t-a", boxes)],
            [
                hint(1, "t-a", "3", "A"),
                override(
                    2,
                    "t-a",
                    "min_like_any_pp",
                    None,
                    55,
                    "合成校准：新增喜欢下限",
                ),
            ],
            prefs={
                **PREFS,
                "stop_rules": {"min_like_any_pp": 55, "max_draws": 3},
            },
            tools={"hint_cards": 0, "display_cards": 0},
        )
        report = solver.build_session_review_report(session)
        self.assertTrue(report["replay"]["recoverable"])
        self.assertEqual(
            report["stop_rule_overrides"][0]["old_value"],
            None,
        )

    def test_risk_first_reports_disliked_probability_as_failure(self):
        boxes = [box(str(index + 1), []) for index in range(4)]
        boxes[0].update({"status": "opened", "known": "B"})
        session = session_of(
            [plain_tray("t-a", boxes)],
            [opened(1, "t-a", "1", "B")],
            prefs={
                "liked": ["A"],
                "disliked": ["C"],
                "hard_avoid": ["C"],
                "objective_mode": "risk_first",
                "stop_rules": {"max_draws": 3},
            },
            tools={"hint_cards": 0, "display_cards": 0},
        )
        opening = solver.build_session_review_report(session)["replay"][
            "openings"
        ][0]
        self.assertAlmostEqual(
            opening["outcome_class_probabilities_pp"]["disliked"],
            25.0,
        )
        self.assertAlmostEqual(opening["accepted_failure_pp"], 25.0)
        self.assertIn("落入不喜欢款", opening["failure_semantics"])


class ToolCardReviewTests(unittest.TestCase):
    def test_hint_card_keeps_its_ex_ante_zero_uplift(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        card = report["replay"]["tool_cards"][0]
        self.assertEqual(card["tool"], "hint")
        self.assertEqual(card["real_result"], {"excluded": "A"})
        self.assertTrue(card["ex_ante_branch_available"])
        self.assertAlmostEqual(card["ex_ante_drawable_branch_pp"], 0.0)
        self.assertEqual(card["best_box_before"], "1")
        self.assertEqual(card["best_box_after"], "1")
        self.assertAlmostEqual(card["primary_metric_change_pp"], 0.0)
        self.assertEqual(card["primary_metric_change"]["metric"], "p_like_any")
        self.assertEqual(
            card["primary_metric_change"]["unit"],
            "percentage_points",
        )
        self.assertFalse(card["decision_changed"])
        self.assertEqual(card["cards_remaining_after"], 1)

    def test_display_card_records_the_decision_change(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        card = report["replay"]["tool_cards"][1]
        self.assertEqual(card["tool"], "display")
        self.assertEqual(card["real_result"], {"revealed": "B"})
        self.assertAlmostEqual(card["ex_ante_drawable_branch_pp"], 100.0)
        self.assertEqual(card["best_box_before"], "1")
        self.assertEqual(card["best_box_after"], "2")
        self.assertAlmostEqual(card["best_p_like_before_pp"], 50.0)
        self.assertAlmostEqual(card["best_p_like_after_pp"], 100.0)
        self.assertAlmostEqual(card["primary_metric_change_pp"], 50.0)
        self.assertEqual(card["primary_metric_change"]["delta"], 50.0)
        self.assertTrue(card["decision_changed"])
        self.assertEqual(card["cards_remaining_after"], 0)

    def test_primary_metric_change_supports_all_six_strategies(self):
        before = {
            "p_like_any": 0.2,
            "p_hard_avoid": 0.3,
            "p_dislike_any": 0.4,
            "liked_probabilities": {"A": 0.1, "B": 0.1},
            "disliked_weighted_loss": 0.8,
            "expected_score": 1.0,
            "resale_ev": 10.0,
        }
        after = {
            "p_like_any": 0.5,
            "p_hard_avoid": 0.1,
            "p_dislike_any": 0.2,
            "liked_probabilities": {"A": 0.4, "B": 0.1},
            "disliked_weighted_loss": 0.5,
            "expected_score": 3.0,
            "resale_ev": 14.0,
        }
        expected = {
            "risk_first": ("severity_weighted_dislike", -30.0),
            "guardrail": ("expected_score", 2.0),
            "balanced": ("expected_score", 2.0),
            "target_only": ("p_like_any", 30.0),
            "top_target_first": ("p_favorite_any", 30.0),
            "resale_ev": ("resale_ev", 4.0),
        }
        for mode, (metric, delta) in expected.items():
            with self.subTest(mode=mode):
                state = {
                    "preferences": {
                        "objective_mode": mode,
                        "liked": ["A", "B"],
                        "scores": {"A": 10.0, "B": 8.0},
                        "score_tiers": {
                            "favorite": ["A"],
                            "liked": ["B"],
                        },
                        "preference_sources": {"liked": "scores"},
                    },
                    "market_values": {"A": 20.0, "B": 10.0},
                }
                change = solver._review_primary_metric_change(
                    before,
                    after,
                    state,
                )
                self.assertEqual(change["metric"], metric)
                self.assertAlmostEqual(change["delta"], delta)


class LifecycleAndOverridesTests(unittest.TestCase):
    def test_lifecycle_lists_every_commitment_and_release_in_order(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        lifecycle = report["replay"]["lifecycle"]
        observed = [
            (entry["seq"], entry["type"], entry.get("source"))
            for entry in lifecycle
        ]
        self.assertEqual(
            observed,
            [
                (1, "tray_switch", None),
                (2, "tray_committed", "explicit_event"),
                (5, "tray_accepted", "quality_lines_upgrade"),
                (7, "stop_rule_override", None),
                (8, "tray_released", None),
                (9, "tray_switch", None),
                (10, "tray_committed", "explicit_event"),
            ],
        )
        override_entry = lifecycle[3]
        self.assertEqual(
            override_entry["rule"], "min_like_any_pp"
        )
        self.assertEqual(override_entry["old_value"], 55)
        self.assertEqual(override_entry["new_value"], 45)
        self.assertEqual(override_entry["reason"], "合成校准：边界下调")
        self.assertEqual(
            report["stop_rule_overrides"],
            [dict(override_entry)],
        )

    def test_explicit_commit_and_acceptance_appear_with_their_reasons(self):
        events = [
            {"seq": 1, "type": "tray_committed", "tray_id": "t-a"},
            {
                "seq": 2,
                "type": "tray_accepted",
                "tray_id": "t-a",
                "reason": "合成确认：接受此端",
            },
        ]
        session = session_of(
            [plain_tray("t-a"), plain_tray("t-b")],
            events,
            accepted="t-a",
        )
        report = solver.build_session_review_report(session)
        observed = [
            (entry["seq"], entry["type"], entry.get("source"),
             entry.get("reason"))
            for entry in report["replay"]["lifecycle"]
        ]
        self.assertEqual(
            observed,
            [
                (1, "tray_committed", "explicit_event", None),
                (2, "tray_accepted", "explicit_event", "合成确认：接受此端"),
            ],
        )


class BiasCheckTests(unittest.TestCase):
    def bias_session(self, *, max_draws=3):
        boxes = pair_boxes()
        boxes[2].update({"status": "opened", "known": "C"})
        boxes[3].update({"status": "opened", "known": "D"})
        events = [
            opened(1, "t-a", "3", "C"),
            opened(2, "t-a", "4", "D"),
        ]
        prefs = {
            **PREFS,
            "stop_rules": {"min_like_any_pp": 55, "max_draws": max_draws},
        }
        return session_of(
            [plain_tray("t-a", boxes)],
            events,
            prefs=prefs,
            draws=2,
        )

    def test_consecutive_zero_like_opens_flag_both_biases(self):
        report = solver.build_session_review_report(self.bias_session())
        bias = report["bias_checks"]
        self.assertTrue(bias["sunk_cost_risk"])
        self.assertEqual(
            bias["sunk_cost_evidence"]["openings_despite_failing_lines"],
            [2, 3],
        )
        self.assertTrue(bias["gambler_fallacy_risk"])
        chains = bias["gambler_fallacy_evidence"]["consecutive_miss_chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["seqs"], [2, 3])
        self.assertEqual(chains[0]["chosen_p_like_pp"], [0.0, 0.0])
        self.assertIn("事前", bias["semantics"])

    def test_a_liked_hit_breaks_the_miss_chain(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        bias = report["bias_checks"]
        # Opening 2 missed every line, but opening 1 was a liked hit, so no
        # consecutive-miss chain exists.
        self.assertTrue(bias["sunk_cost_risk"])
        self.assertFalse(bias["gambler_fallacy_risk"])

    def test_clean_openings_raise_no_bias_flags(self):
        boxes = pair_boxes()
        boxes[0].update({"tool_used": True, "known": "B"})
        boxes[1].update({"status": "opened", "known": "A"})
        session = session_of(
            [plain_tray("t-a", boxes)],
            [display(1, "t-a", "1", "B"), opened(2, "t-a", "2", "A")],
            tools={"hint_cards": 2, "display_cards": 0},
        )
        report = solver.build_session_review_report(session)
        bias = report["bias_checks"]
        self.assertFalse(bias["sunk_cost_risk"])
        self.assertFalse(bias["gambler_fallacy_risk"])
        self.assertEqual(
            report["outcome_quality"],
            {
                "liked_hits": 1,
                "disliked_hits": 0,
                "hard_avoid_hits": 0,
                "neutral_results": 0,
                "openings_total": 1,
            },
        )


class StopConclusionTests(unittest.TestCase):
    def conclusion_of(self, session):
        report = solver.build_session_review_report(session)
        return report["global_counters"]["final_stop_conclusion"]

    def test_budget_exhausted_beats_quality_lines(self):
        boxes = pair_boxes()
        boxes[2].update({"status": "opened", "known": "C"})
        boxes[3].update({"status": "opened", "known": "D"})
        prefs = {
            **PREFS,
            "stop_rules": {"min_like_any_pp": 55, "max_draws": 2},
        }
        session = session_of(
            [plain_tray("t-a", boxes)],
            [opened(1, "t-a", "3", "C"), opened(2, "t-a", "4", "D")],
            prefs=prefs,
            draws=2,
        )
        self.assertEqual(self.conclusion_of(session), "budget_exhausted")

    def test_no_drawable_box_remains(self):
        boxes = pair_boxes()
        boxes[0].update({"status": "opened", "known": "B"})
        boxes[1].update({"status": "opened", "known": "A"})
        boxes[2].update({"status": "opened", "known": "C"})
        boxes[3].update({"status": "opened", "known": "D"})
        prefs = {
            **PREFS,
            "stop_rules": {"min_like_any_pp": 55, "max_draws": 4},
        }
        session = session_of(
            [plain_tray("t-a", boxes)],
            [
                opened(1, "t-a", "3", "C"),
                opened(2, "t-a", "4", "D"),
                opened(3, "t-a", "1", "B"),
                opened(4, "t-a", "2", "A"),
            ],
            prefs=prefs,
            draws=4,
        )
        self.assertEqual(self.conclusion_of(session),
                         "no_drawable_box_remains")
        report = solver.build_session_review_report(session)
        self.assertEqual(len(report["replay"]["openings"]), 4)

    def test_still_recommend_drawing_when_lines_pass(self):
        boxes = pair_boxes()
        boxes[0].update({"tool_used": True, "known": "B"})
        session = session_of(
            [plain_tray("t-a", boxes)],
            [display(1, "t-a", "1", "B")],
            tools={"hint_cards": 2, "display_cards": 0},
        )
        self.assertEqual(self.conclusion_of(session),
                         "still_recommend_drawing")

    def test_stopped_below_quality_lines(self):
        self.assertEqual(
            self.conclusion_of(review_fixture_session()),
            "stopped_below_quality_lines",
        )


class LegacyUnrecoverableTests(unittest.TestCase):
    def legacy_state(self):
        return {
            "series": "synthetic-legacy-review",
            "model": copy.deepcopy(MODEL),
            "preferences": copy.deepcopy(PREFS),
            "tools": {"hint_cards": 1, "display_cards": 0},
            "boxes": [
                {"id": "1", "excluded": ["C"], "status": "available",
                 "tool_used": True},
                {"id": "2", "excluded": [], "status": "opened",
                 "known": "B"},
                {"id": "3", "excluded": [], "status": "available"},
                {"id": "4", "excluded": [], "status": "available"},
            ],
            "meta": {"provenance": "synthetic"},
        }

    def test_legacy_state_marks_blind_spots_without_fabricating(self):
        session = solver._normalize_session(self.legacy_state())
        report = solver.build_session_review_report(session)
        replay = report["replay"]
        self.assertFalse(replay["recoverable"])
        self.assertEqual(
            replay["unrecoverable_reason"],
            "legacy_state_without_event_ledger",
        )
        self.assertEqual(
            [item["field"] for item in replay["unrecoverable_items"]],
            [
                "openings",
                "tool_cards",
                "quality_lines_at_draw",
                "stop_rule_overrides",
            ],
        )
        self.assertEqual(replay["openings"], [])
        self.assertEqual(replay["tool_cards"], [])
        self.assertEqual(report["stop_rule_overrides"], [])
        # The draw count still comes from the current state (one opened box);
        # only a recoverable replay must match it to the opening records.
        self.assertEqual(report["global_counters"]["draws_used"], 1)
        self.assertEqual(report["global_counters"]["opened_boxes"], 1)

        markdown = solver.render_session_review_markdown(report)
        self.assertIn("## 不可恢复项", markdown)
        self.assertIn("legacy_state_without_event_ledger", markdown)
        self.assertNotIn("### 第 1 次开盒", markdown)
        self.assertIn("未逐次归类", markdown)

    def test_validator_rejects_fabricated_legacy_numbers(self):
        session = solver._normalize_session(self.legacy_state())
        report = solver.build_session_review_report(session)
        report["replay"]["openings"].append(
            {
                "seq": 1,
                "tray_id": "tray-1",
                "box_id": "2",
                "design": "B",
                "actual_design_prior_pp": 25.0,
                "actual_design_rank": 1,
                "possible_designs_ranked": [
                    {"design": "B", "probability_pp": 25.0}
                ],
                "outcome_class_probabilities_pp": {
                    "liked": 25.0,
                    "neutral": 75.0,
                    "disliked": 0.0,
                    "hard_avoid": 0.0,
                },
                "accepted_failure_pp": 75.0,
                "failure_semantics": "fabricated",
                "quality_lines_at_draw": [],
                "should_draw_at_decision": True,
                "stop_reasons_at_decision": [],
                "chosen_was_optimal": True,
                "strongest_alternative": None,
                "liked_hit": False,
                "hard_avoid_hit": False,
                "disliked_hit": False,
            }
        )
        with self.assertRaisesRegex(
            solver.StateError, "must not fabricate per-event numbers"
        ):
            solver.validate_session_review_report(report)


class ValidatorTamperTests(unittest.TestCase):
    def report(self):
        return solver.build_session_review_report(
            review_fixture_session()
        )

    def assert_tampered(self, mutate, message):
        report = self.report()
        mutate(report)
        with self.assertRaisesRegex(solver.StateError, message):
            solver.validate_session_review_report(report)

    def test_partition_violation_is_rejected(self):
        def mutate(report):
            report["replay"]["openings"][0][
                "outcome_class_probabilities_pp"
            ]["liked"] = 90.0

        self.assert_tampered(mutate, "partition to 100")

    def test_rank_prior_disagreement_is_rejected(self):
        def mutate(report):
            report["replay"]["openings"][1]["actual_design_prior_pp"] = 90.0

        self.assert_tampered(mutate, "rank and prior disagree")

    def test_missing_alternative_for_non_optimal_choice_is_rejected(self):
        def mutate(report):
            report["replay"]["openings"][1]["strongest_alternative"] = None

        self.assert_tampered(mutate, "needs its alternative")

    def test_branch_null_mismatch_is_rejected(self):
        def mutate(report):
            card = report["replay"]["tool_cards"][0]
            card["ex_ante_branch_available"] = False

        self.assert_tampered(mutate, "unavailable branch must stay null")

    def test_draw_count_mismatch_is_rejected(self):
        def mutate(report):
            report["global_counters"]["draws_used"] = 3

        self.assert_tampered(mutate, "disagrees with the replay openings")

    def test_unknown_stop_conclusion_is_rejected(self):
        def mutate(report):
            report["global_counters"]["final_stop_conclusion"] = "lucky"

        self.assert_tampered(mutate, "not a known verdict")

    def test_override_divergence_is_rejected(self):
        def mutate(report):
            report["stop_rule_overrides"][0]["new_value"] = 40

        self.assert_tampered(mutate, "diverge from the replay lifecycle")

    def test_descending_lifecycle_is_rejected(self):
        def mutate(report):
            report["replay"]["lifecycle"].reverse()

        self.assert_tampered(mutate, "ascending order")

    def test_duplicate_lifecycle_entry_is_rejected(self):
        def mutate(report):
            report["replay"]["lifecycle"].append(
                dict(report["replay"]["lifecycle"][-1])
            )

        self.assert_tampered(mutate, "unique per event and type")

    def test_decision_quality_divergence_is_rejected(self):
        def mutate(report):
            report["decision_quality"]["non_optimal_openings"] = []

        self.assert_tampered(mutate, "decision quality diverges")


class MarkdownAndCLITests(unittest.TestCase):
    def markdown(self):
        report = solver.build_session_review_report(
            review_fixture_session()
        )
        return solver.render_session_review_markdown(report)

    def test_markdown_carries_every_required_section(self):
        markdown = self.markdown()
        self.assertTrue(markdown.startswith("# 盲盒整轮自动复盘："))
        for section in (
            "## 复盘结论",
            "## 开盒逐次复盘",
            "## 道具卡逐张复盘",
            "## 承诺与止损线变更",
            "## 预算与停止结论",
            "## 决策、结果与模型质量",
            "## 模型口径",
        ):
            self.assertIn(section, markdown)
        self.assertNotIn("## 不可恢复项", markdown)

    def test_markdown_names_the_actual_events_and_numbers(self):
        markdown = self.markdown()
        self.assertIn("第 1 次开盒（事件 6，端 t-a，盒 2 → A）", markdown)
        self.assertIn("100.00%", markdown)
        self.assertIn("第 2 位", markdown)
        self.assertIn("最强备选：盒 1", markdown)
        self.assertIn("-33.33pp", markdown)
        self.assertIn("排除了 A", markdown)
        self.assertIn("显示为 B", markdown)
        self.assertIn("是否决定行动：是", markdown)
        self.assertIn("停止线调整（端 t-a）：min_like_any_pp 55.0 → 45.0",
                      markdown)
        self.assertIn("候选承诺（端 t-a）", markdown)
        self.assertIn("接受（达线升级）", markdown)
        self.assertIn("切端（端 t-b）", markdown)
        self.assertIn("沉没成本风险有", markdown)
        self.assertIn("赌徒谬误风险无", markdown)
        self.assertIn("整轮确定性回放", markdown)

    def test_cli_markdown_matches_the_direct_render(self):
        expected = self.markdown()
        result = run_cli(
            json.loads(FIXTURE.read_text(encoding="utf-8")),
            "--review-session",
            "--format",
            "markdown",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)

    def test_cli_json_payload_parses_and_validates(self):
        result = run_cli(
            json.loads(FIXTURE.read_text(encoding="utf-8")),
            "--review-session",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["report_type"], "session_review")
        solver.validate_session_review_report(report)

    def test_cli_rejects_incompatible_modes(self):
        state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for flags in (
            ("--review-session", "--screen-tray"),
            ("--review-session", "--plan-depth", "1"),
            ("--review-session", "--compare-trays"),
            ("--review-session", "--calibrate-preferences"),
            ("--review-session", "--brief-preferences"),
        ):
            result = run_cli(state, *flags)
            self.assertEqual(result.returncode, 2, flags)
            self.assertIn("cannot be combined", result.stderr)

    def test_cli_rejects_reviewing_a_preference_briefing(self):
        briefing = {
            "session_schema_version": 1,
            "series": "synthetic-review-briefing",
            "regular_count": 4,
            "preferences": {
                "liked": ["A"],
                "disliked": ["C"],
                "objective_mode": "随便中个喜欢",
            },
            "meta": {"provenance": "synthetic"},
        }
        result = run_cli(briefing, "--review-session")
        self.assertEqual(result.returncode, 2)
        self.assertIn("briefing", result.stderr)


if __name__ == "__main__":
    unittest.main()
