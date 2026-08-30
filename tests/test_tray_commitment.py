"""Regressions for issue #19: candidate-tray commitment and lossless upgrade.

Synthetic sessions only. They cover the three-state ladder (open / candidate /
accepted), auto-commitment on the first real card or open, the quality-line
upgrade to accepted, mandatory release before switching, global-counter
stability across the lifecycle, lossless legacy normalization, report and
review surfacing, counterfactual isolation, and validator tamper rejection.
"""

import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest

from session_test_helpers import make_box, make_session_payload

ROOT = pathlib.Path(__file__).parents[1]
SOLVER_PATH = ROOT / "scripts" / "blindbox_solver.py"
spec = importlib.util.spec_from_file_location(
    "blindbox_solver_commitment",
    SOLVER_PATH,
)
solver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solver
assert spec.loader is not None
spec.loader.exec_module(solver)

PREFS = {
    "liked": ["A"],
    "disliked": ["C"],
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
    # A sits in boxes 1-2: direct P(A) = 50% fails a 55% quality line.
    return [
        box("1", ["C", "D"]),
        box("2", ["C", "D"]),
        box("3", ["A", "B"]),
        box("4", ["A", "B"]),
    ]


def pair_tray(tray_id):
    # No card has run yet: usable for open and explicit-commitment scenarios.
    return {"id": tray_id, "model": copy.deepcopy(MODEL), "boxes": pair_boxes()}


def hinted_tray(tray_id):
    # A real hint already ran on box 3 (tool_used, "A" excluded); the tray is
    # auto-committed but still fails the 55% quality line.
    boxes = pair_boxes()
    boxes[2]["tool_used"] = True
    return {"id": tray_id, "model": copy.deepcopy(MODEL), "boxes": boxes}


def revealed_tray(tray_id):
    # A display card revealed box 1 = B, so P(A | box 2) = 100%: every quality
    # line passes and the candidate upgrades to accepted.
    boxes = pair_boxes()
    boxes[0].update({"tool_used": True, "known": "B"})
    return {"id": tray_id, "model": copy.deepcopy(MODEL), "boxes": boxes}


def spread_tray(tray_id):
    # A can sit in three boxes: direct P(A) = 1/3 and only the branch where a
    # display card reveals A qualifies.
    return {
        "id": tray_id,
        "model": copy.deepcopy(MODEL),
        "boxes": [
            box("1", ["D"]),
            box("2", ["D"]),
            box("3", ["D"]),
            box("4", ["A"]),
        ],
    }


def payload(trays, events, *, active=None, accepted=None, candidate=None,
            tools=None, prefs=None):
    kwargs = {}
    if candidate is not None:
        kwargs["candidate"] = candidate
    return make_session_payload(
        trays,
        events,
        series="synthetic-commitment-tests",
        preferences=prefs or PREFS,
        tools=tools or TOOLS,
        active=active,
        accepted=accepted,
        draws_used=0,
        **kwargs,
    )


def session_of(trays, events, **kwargs):
    return solver._normalize_session(payload(trays, events, **kwargs))


def switch(seq, tray_id):
    return {"seq": seq, "type": "tray_switch", "tray_id": tray_id}


def committed(seq, tray_id):
    return {"seq": seq, "type": "tray_committed", "tray_id": tray_id}


def accepted_event(seq, tray_id, reason="synthetic_acceptance"):
    return {
        "seq": seq,
        "type": "tray_accepted",
        "tray_id": tray_id,
        "reason": reason,
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


def released(seq, tray_id, reason="合成测试释放原因"):
    return {
        "seq": seq,
        "type": "tray_released",
        "tray_id": tray_id,
        "reason": reason,
    }


class LadderPhaseTests(unittest.TestCase):
    def test_open_session_states_its_phase_without_locks(self):
        session = session_of(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a")],
        )
        report = solver.build_session_report(session, plan_depth=1)

        self.assertIsNone(session["candidate_tray_id"])
        lifecycle = report["session_summary"]["tray_lifecycle"]
        self.assertEqual(lifecycle["phase"], "open")
        self.assertIn("未承诺", lifecycle["phase_label"])
        self.assertFalse(lifecycle["release_required_before_switch"])
        self.assertEqual(
            report["session_recommendation"]["action"],
            "follow_active_tray_report",
        )
        markdown = solver.render_user_markdown(report)
        self.assertIn("未承诺（可自由换端", markdown)

    def test_explicit_commitment_records_a_candidate(self):
        session = session_of(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a")],
        )
        report = solver.build_session_report(session)

        self.assertEqual(session["candidate_tray_id"], "t-a")
        self.assertEqual(session["_candidate_source"], "explicit_event")
        lifecycle = report["session_summary"]["tray_lifecycle"]
        self.assertEqual(lifecycle["phase"], "candidate")
        self.assertIn("候选承诺", lifecycle["phase_label"])
        self.assertEqual(lifecycle["commitment_source"], "explicit_event")
        self.assertTrue(lifecycle["release_required_before_switch"])
        self.assertFalse(lifecycle["upgraded_from_candidate"])
        self.assertEqual(
            report["session_recommendation"]["release_required_before_switch"],
            True,
        )

    def test_first_real_card_auto_commits_without_a_lock_request(self):
        session = session_of(
            [hinted_tray("t-a")],
            [switch(1, "t-a"), hint(2, "t-a", "3", "A")],
        )

        self.assertEqual(session["candidate_tray_id"], "t-a")
        self.assertEqual(session["_candidate_source"], "first_tool_or_open")
        self.assertEqual(
            session["_auto_commitments"],
            [
                {
                    "seq": 2,
                    "tray_id": "t-a",
                    "trigger_seq": 3,
                    "trigger": "hint_used",
                    "source": "first_tool_or_open",
                }
            ],
        )
        self.assertEqual(
            [event["type"] for event in session["events"]],
            ["tray_switch", "tray_committed", "hint_used"],
        )

        report = solver.build_session_report(session)
        lifecycle = report["session_summary"]["tray_lifecycle"]
        self.assertEqual(lifecycle["phase"], "candidate")
        self.assertEqual(lifecycle["commitment_source"], "first_tool_or_open")
        review = report["session_review"]["commitment_lifecycle"]
        self.assertEqual(review["phase"], "candidate")
        self.assertEqual(len(review["auto_commitments"]), 1)
        self.assertEqual(len(review["commit_events"]), 1)
        self.assertEqual(
            review["commit_events"][0]["source"],
            "first_tool_or_open",
        )

    def test_first_open_auto_commits_the_solo_tray(self):
        boxes = pair_boxes()
        boxes[0].update({"known": "B", "status": "opened"})
        legacy = {
            "series": "synthetic-legacy-open",
            "model": copy.deepcopy(MODEL),
            "preferences": copy.deepcopy(PREFS),
            "boxes": boxes,
            "meta": {"provenance": "synthetic"},
        }
        session = solver._normalize_session(legacy)

        self.assertTrue(session["_legacy_input"])
        self.assertEqual(session["candidate_tray_id"], "tray-1")
        self.assertEqual(session["_candidate_source"], "first_tool_or_open")

    def test_multi_tray_cannot_claim_an_automatic_commitment_source(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [
                switch(1, "t-a"),
                {
                    **committed(2, "t-a"),
                    "source": "first_tool_or_open",
                },
            ],
        )
        with self.assertRaisesRegex(
            solver.StateError,
            r"valid only for a single-tray session",
        ):
            solver._normalize_session(raw)

    def test_quality_upgrade_requires_a_real_candidate_action(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                {
                    **accepted_event(3, "t-a"),
                    "source": "quality_lines_upgrade",
                },
            ],
            accepted="t-a",
        )
        with self.assertRaisesRegex(
            solver.StateError,
            r"requires a candidate tray with a real",
        ):
            solver._normalize_session(raw)

    def test_qualifying_real_clues_upgrade_candidate_to_accepted(self):
        session = session_of(
            [revealed_tray("t-a"), spread_tray("t-b")],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                display(3, "t-a", "1", "B"),
            ],
        )
        report = solver.build_session_report(session, plan_depth=1)

        self.assertEqual(session["accepted_tray_id"], "t-a")
        self.assertIsNone(session["candidate_tray_id"])
        self.assertEqual(
            [event["type"] for event in session["events"]],
            ["tray_switch", "tray_committed", "display_used", "tray_accepted"],
        )
        self.assertEqual(session["events"][-1]["source"], "quality_lines_upgrade")
        lifecycle = report["session_summary"]["tray_lifecycle"]
        self.assertEqual(lifecycle["phase"], "accepted")
        self.assertTrue(lifecycle["upgraded_from_candidate"])
        self.assertEqual(lifecycle["accepted_via"], "quality_lines_upgrade")
        self.assertEqual(
            lifecycle["upgrade_basis"],
            "real_clues_pass_all_quality_lines",
        )
        self.assertIsNone(lifecycle["candidate_tray_id"])
        self.assertEqual(lifecycle["accepted_tray_id"], "t-a")
        self.assertTrue(
            report["tray_reports"]["t-a"]["tray_lock"]["currently_qualified"]
        )

        recommendation = report["session_recommendation"]
        self.assertIn(
            recommendation["action"],
            {"continue_with_accepted_tray", "continue_with_accepted_tray_tool_plan"},
        )
        self.assertEqual(recommendation["tray_id"], "t-a")
        self.assertTrue(recommendation["release_required_before_switch"])

        markdown = solver.render_user_markdown(report)
        self.assertIn("已接受（真实线索已通过全部质量线，由候选承诺自动升级）", markdown)

    def test_upgraded_tray_stays_locked_across_further_real_clues(self):
        # Box 1 revealed B (display) and box 3 hinted off A: two real clues,
        # every quality line passes, and the lock must survive the second one.
        boxes = pair_boxes()
        boxes[0].update({"tool_used": True, "known": "B"})
        boxes[2]["tool_used"] = True
        raw = payload(
            [
                {
                    "id": "t-a",
                    "model": copy.deepcopy(MODEL),
                    "boxes": boxes,
                },
                spread_tray("t-b"),
            ],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                display(3, "t-a", "1", "B"),
                hint(4, "t-a", "3", "A"),
            ],
        )
        session = solver._normalize_session(raw)
        report = solver.build_session_report(session, plan_depth=1)

        self.assertEqual(
            report["session_summary"]["tray_lifecycle"]["phase"],
            "accepted",
        )
        self.assertIn(
            report["session_recommendation"]["action"],
            {"continue_with_accepted_tray", "continue_with_accepted_tray_tool_plan"},
        )


class ReleaseGateTests(unittest.TestCase):
    def test_silent_switch_away_from_a_candidate_fails_closed(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a"), switch(3, "t-b")],
        )
        with self.assertRaisesRegex(
            solver.StateError, "release the candidate tray before switching"
        ):
            solver._normalize_session(raw)

    def test_multi_tray_action_requires_explicit_commitment(self):
        raw = payload(
            [hinted_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), hint(2, "t-a", "3", "A"), switch(3, "t-b")],
        )
        with self.assertRaisesRegex(
            solver.StateError, "multi-tray actions require.*tray_committed"
        ):
            solver._normalize_session(raw)

    def test_release_with_reason_reopens_the_session(self):
        session = session_of(
            [pair_tray("t-a"), spread_tray("t-b")],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                released(3, "t-a", "合成原因：换端观察"),
                switch(4, "t-b"),
            ],
            active="t-b",
        )
        report = solver.build_session_report(session)

        self.assertIsNone(session["candidate_tray_id"])
        self.assertEqual(report["session_summary"]["lock_status"], "open")
        self.assertEqual(
            report["session_review"]["commitment_lifecycle"]["commit_events"],
            [committed(2, "t-a")],
        )
        self.assertEqual(
            [
                event["type"]
                for event in report["session_review"]["acceptance_lifecycle"]
            ],
            ["tray_released"],
        )

    def test_release_without_a_reason_fails_closed(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                {"seq": 3, "type": "tray_released", "tray_id": "t-a"},
            ],
        )
        with self.assertRaisesRegex(
            solver.StateError, "tray release requires a concise reason"
        ):
            solver._normalize_session(raw)

    def test_release_must_target_the_locked_tray(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a"), released(3, "t-b")],
        )
        with self.assertRaisesRegex(
            solver.StateError,
            "tray_released must target the currently accepted or candidate tray",
        ):
            solver._normalize_session(raw)

    def test_committing_while_locked_requires_release_first(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                released(3, "t-a", "合成原因：改选他端"),
                switch(4, "t-b"),
                committed(5, "t-b"),
            ],
            active="t-b",
        )
        session = solver._normalize_session(raw)
        self.assertEqual(session["candidate_tray_id"], "t-b")

        raw["events"] = [
            switch(1, "t-a"),
            committed(2, "t-a"),
            switch(3, "t-b"),
            committed(4, "t-b"),
        ]
        with self.assertRaisesRegex(
            solver.StateError,
            "release the candidate tray before switching",
        ):
            solver._normalize_session(raw)

        raw["events"] = [
            switch(1, "t-a"),
            committed(2, "t-a"),
            committed(3, "t-a"),
        ]
        with self.assertRaisesRegex(
            solver.StateError, "the candidate tray is already committed"
        ):
            solver._normalize_session(raw)

    def test_actions_on_another_tray_are_blocked_while_locked(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a")],
        )
        other = spread_tray("t-b")
        other["boxes"][3]["tool_used"] = True
        other["boxes"][3]["excluded"] = ["A", "D"]
        raw["trays"][1] = other
        raw["events"].append(hint(3, "t-b", "4", "A"))
        with self.assertRaisesRegex(
            solver.StateError, "release the locked tray before recording"
        ):
            solver._normalize_session(raw)


class ConsistencyTests(unittest.TestCase):
    def test_global_counters_survive_the_whole_lifecycle(self):
        raw = payload(
            [revealed_tray("t-a"), hinted_tray("t-b")],
            [
                switch(1, "t-a"),
                committed(2, "t-a"),
                display(3, "t-a", "1", "B"),
                released(4, "t-a", "合成原因：复盘后换端"),
                switch(5, "t-b"),
                committed(6, "t-b"),
                hint(7, "t-b", "3", "A"),
                accepted_event(8, "t-b"),
            ],
            active="t-b",
            accepted="t-b",
        )
        session = solver._normalize_session(raw)
        report = solver.build_session_report(session)

        summary = report["session_summary"]
        self.assertEqual(summary["tools"]["hint_cards"], 2)
        self.assertEqual(summary["tools"]["display_cards"], 1)
        self.assertEqual(summary["draws_used"], 0)
        self.assertEqual(summary["tray_ids"], ["t-a", "t-b"])
        self.assertEqual(
            [event["type"] for event in report["actual_events"]],
            [
                "tray_switch",
                "tray_committed",
                "display_used",
                "tray_accepted",
                "tray_released",
                "tray_switch",
                "tray_committed",
                "hint_used",
                "tray_accepted",
            ],
        )
        self.assertEqual(summary["lock_status"], "accepted")
        self.assertEqual(summary["accepted_tray_id"], "t-b")
        self.assertIsNone(summary["candidate_tray_id"])

        review = report["session_review"]
        self.assertEqual(
            [event["type"] for event in review["acceptance_lifecycle"]],
            ["tray_accepted", "tray_released", "tray_accepted"],
        )
        self.assertEqual(len(review["commitment_lifecycle"]["auto_commitments"]), 0)
        self.assertEqual(review["commitment_lifecycle"]["phase"], "accepted")

    def test_counterfactual_planning_never_mutates_the_commitment(self):
        raw = payload(
            [hinted_tray("t-a")],
            [switch(1, "t-a"), hint(2, "t-a", "3", "A")],
        )
        session = solver._normalize_session(raw)
        before = copy.deepcopy(session)

        report = solver.build_session_report(
            session,
            plan_depth=2,
            beam_width=0,
        )

        self.assertEqual(session, before)
        self.assertEqual(session["candidate_tray_id"], "t-a")
        self.assertEqual(
            report["session_summary"]["tray_lifecycle"]["phase"],
            "candidate",
        )


class LosslessUpgradeTests(unittest.TestCase):
    def legacy_state(self, boxes):
        return {
            "series": "synthetic-legacy-commitment",
            "model": copy.deepcopy(MODEL),
            "preferences": copy.deepcopy(PREFS),
            "boxes": boxes,
            "meta": {"provenance": "synthetic"},
        }

    def solo_report(self, legacy):
        session = solver._normalize_session(legacy)
        report = solver.build_report(
            session["_tray_states"][session["active_tray_id"]],
            plan_depth=1,
        )
        solver._attach_solo_lifecycle(session, report)
        return session, report

    def test_legacy_clean_state_keeps_its_exact_report_shape(self):
        session, report = self.solo_report(self.legacy_state(pair_boxes()))

        self.assertIsNone(session["candidate_tray_id"])
        self.assertNotIn("tray_lifecycle", report)
        markdown = solver.render_user_markdown(report)
        self.assertNotIn("当前端状态", markdown)

    def test_legacy_committed_state_reports_a_candidate(self):
        session, report = self.solo_report(
            self.legacy_state(hinted_tray("ignored")["boxes"])
        )

        self.assertEqual(session["candidate_tray_id"], "tray-1")
        lifecycle = report["tray_lifecycle"]
        self.assertEqual(lifecycle["phase"], "candidate")
        self.assertEqual(lifecycle["commitment_source"], "first_tool_or_open")
        markdown = solver.render_user_markdown(report)
        self.assertIn("当前端状态：候选承诺", markdown)
        self.assertIn("首次真实用卡或开盒已自动承诺", markdown)

    def test_existing_accepted_sessions_normalize_without_migration(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), accepted_event(2, "t-a")],
            accepted="t-a",
        )
        self.assertNotIn("candidate_tray_id", raw)
        session = solver._normalize_session(raw)

        self.assertEqual(session["accepted_tray_id"], "t-a")
        self.assertIsNone(session["candidate_tray_id"])
        report = solver.build_session_report(session)
        self.assertEqual(report["session_summary"]["lock_status"], "accepted")
        self.assertEqual(
            report["session_summary"]["tray_lifecycle"]["accepted_via"],
            "explicit_event",
        )

    def test_explicit_candidate_field_must_match_the_ledger(self):
        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a")],
            candidate="t-a",
        )
        session = solver._normalize_session(raw)
        self.assertEqual(session["candidate_tray_id"], "t-a")

        raw = payload(
            [pair_tray("t-a"), spread_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a")],
            candidate="t-b",
        )
        with self.assertRaisesRegex(
            solver.StateError,
            "candidate_tray_id must match the tray commitment event history",
        ):
            solver._normalize_session(raw)


class ReportAndValidatorTests(unittest.TestCase):
    def build_report(self, events=None, **kwargs):
        raw = payload(
            [hinted_tray("t-a"), spread_tray("t-b")],
            events
            or [
                switch(1, "t-a"),
                committed(2, "t-a"),
                hint(3, "t-a", "3", "A"),
            ],
            **kwargs,
        )
        session = solver._normalize_session(raw)
        return solver.build_session_report(session, plan_depth=1)

    def test_screening_report_flags_candidate_review_over_switch(self):
        raw = payload(
            [spread_tray("t-a"), pair_tray("t-b")],
            [switch(1, "t-a"), committed(2, "t-a")],
            tools={"hint_cards": 0, "display_cards": 0},
        )
        session = solver._normalize_session(raw)
        report = solver.build_session_report(
            session,
            plan_depth=1,
            screen_tray=True,
        )

        screening = report["tray_reports"]["t-a"]["tray_screening"]
        self.assertEqual(screening["status"], "candidate_review")
        self.assertEqual(screening["recommendation"], "release_before_switch")
        self.assertEqual(screening["unlocked_recommendation"], "switch")
        self.assertEqual(screening["candidate_tray_id"], "t-a")

        markdown = solver.render_user_markdown(report, screen_tray=True)
        self.assertIn("候选承诺（已选定但未达线）", markdown)
        self.assertIn("当前端状态：候选承诺", markdown)

    def test_validator_rejects_tampered_lifecycle_blocks(self):
        report = self.build_report()
        report["session_summary"]["tray_lifecycle"]["phase"] = "mystery"
        with self.assertRaises(solver.StateError):
            solver.render_user_markdown(report)

        report = self.build_report()
        report["session_summary"]["tray_lifecycle"][
            "upgraded_from_candidate"
        ] = True
        with self.assertRaises(solver.StateError):
            solver.render_user_markdown(report)

        report = self.build_report()
        report["session_summary"]["tray_lifecycle"][
            "release_required_before_switch"
        ] = False
        with self.assertRaises(solver.StateError):
            solver.render_user_markdown(report)

        report = self.build_report()
        report["session_recommendation"]["action"] = "follow_active_tray_report"
        with self.assertRaises(solver.StateError):
            solver.render_user_markdown(report)

    def test_validator_rejects_a_dropped_session_lifecycle(self):
        report = self.build_report()
        del report["session_summary"]["tray_lifecycle"]
        with self.assertRaises(solver.StateError):
            solver.render_user_markdown(report)

    def test_cli_renders_the_candidate_commitment_state(self):
        raw = payload(
            [hinted_tray("t-a")],
            [switch(1, "t-a"), hint(2, "t-a", "3", "A")],
        )
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".json", encoding="utf-8"
        ) as state_file:
            state_file.write(json.dumps(raw, ensure_ascii=False))
            state_file.flush()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = solver.main([state_file.name, "--format", "markdown"])

        self.assertEqual(exit_code, 0)
        self.assertIn("当前端状态：候选承诺", stdout.getvalue())
        self.assertIn("首次真实用卡或开盒已自动承诺", stdout.getvalue())


class ComparisonCommitmentTests(unittest.TestCase):
    def build_comparison(self, events=None, **kwargs):
        raw = payload(
            [spread_tray("t-spread"), pair_tray("t-pair")],
            events or [switch(1, "t-spread")],
            **kwargs,
        )
        session = solver._normalize_session(raw)
        return solver.build_tray_comparison_report(session)

    def test_tool_dependent_top_tray_directs_a_candidate_commitment(self):
        report = self.build_comparison()
        rows = report["comparison"]["rows"]
        self.assertEqual(rows[0]["status"], "tool_dependent")

        commitment = report["recommendation"]["commitment_after_action"]
        self.assertEqual(commitment["phase"], "candidate")
        self.assertEqual(commitment["record_event"], "tray_committed")

        markdown = solver.render_tray_comparison_markdown(report)
        self.assertIn("不是直接合格", markdown)
        self.assertIn("tray_committed", markdown)

    def test_committed_top_tray_does_not_repeat_the_commitment_step(self):
        raw = payload(
            [spread_tray("t-spread"), hinted_tray("t-pair")],
            [
                switch(1, "t-spread"),
                switch(2, "t-pair"),
                committed(3, "t-pair"),
                hint(4, "t-pair", "3", "A"),
            ],
            active="t-pair",
        )
        report = solver.build_tray_comparison_report(
            solver._normalize_session(raw)
        )

        self.assertNotIn("commitment_after_action", report["recommendation"])
        self.assertEqual(
            report["session_summary"]["candidate_tray_id"],
            "t-pair",
        )
        markdown = solver.render_tray_comparison_markdown(report)
        self.assertIn("候选承诺", markdown)

    def test_validator_rejects_a_missing_or_malformed_commitment_step(self):
        report = self.build_comparison()
        del report["recommendation"]["commitment_after_action"]
        with self.assertRaises(solver.StateError):
            solver.render_tray_comparison_markdown(report)

        report = self.build_comparison()
        report["recommendation"]["commitment_after_action"]["phase"] = "accepted"
        with self.assertRaises(solver.StateError):
            solver.render_tray_comparison_markdown(report)

    def test_validator_rejects_a_tampered_comparison_lifecycle(self):
        report = self.build_comparison()
        report["session_summary"]["tray_lifecycle"]["phase"] = "accepted"
        with self.assertRaises(solver.StateError):
            solver.render_tray_comparison_markdown(report)


if __name__ == "__main__":
    unittest.main()
