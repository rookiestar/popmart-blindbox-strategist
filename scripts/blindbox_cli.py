"""Command-line interface for the blind-box solver domain module."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Mapping, Optional, Sequence


def _round_floats(obj: Any, digits: int = 8) -> Any:
    if isinstance(obj, float):
        return round(obj, digits)
    if isinstance(obj, list):
        return [_round_floats(x, digits) for x in obj]
    if isinstance(obj, dict):
        return {k: _round_floats(v, digits) for k, v in obj.items()}
    return obj


COMPACT_BRANCH_KEYS = (
    "outcome",
    "probability",
    "best_box_after_outcome",
    "recommended_draw_after_outcome",
    "next_action_after_outcome",
)


def _compact_branch(branch: Mapping[str, Any]) -> Dict[str, Any]:
    """Reduce one outcome branch to its decision-relevant summary.

    The workflow is adaptive: apply one real outcome, then rerun the solver on
    the updated state. Full per-branch metrics are therefore audit data; keep
    them only with ``--full-branches``.
    """
    compact = {key: branch[key] for key in COMPACT_BRANCH_KEYS if key in branch}
    decision = branch.get("draw_decision_after_outcome")
    if decision is not None and not decision["should_draw"]:
        compact["stop_reasons"] = list(decision["reasons"])
    return compact


def _slim_plan(
    plan: Mapping[str, Any],
    top_actions: int,
    full_branches: bool,
    action_summarizer: Any,
) -> Dict[str, Any]:
    slimmed = dict(plan)
    if full_branches:
        ranking = list(plan["action_ranking"])
    else:
        ranking = []
        for action in plan["action_ranking"]:
            compact_action = dict(action)
            compact_action["branches"] = [
                _compact_branch(branch) for branch in action["branches"]
            ]
            ranking.append(compact_action)
    if top_actions > 0 and len(ranking) > top_actions:
        slimmed["other_actions_ranked"] = [
            action_summarizer(action) for action in ranking[top_actions:]
        ]
        ranking = ranking[:top_actions]
    slimmed["action_ranking"] = ranking
    if ranking:
        slimmed["recommended_action"] = ranking[0]
    if isinstance(slimmed.get("baseline_best_draw"), Mapping):
        slimmed["baseline_best_draw"] = slimmed["baseline_best_draw"]["box_id"]
    return slimmed


def _slim_report(
    report: Mapping[str, Any],
    top_actions: int,
    full_branches: bool,
    action_summarizer: Any,
) -> Dict[str, Any]:
    """Output-layer slimming; plan_tools results themselves stay complete."""
    if "tray_reports" in report:
        slimmed = dict(report)
        slimmed["tray_reports"] = {
            tray_id: _slim_report(
                tray_report,
                top_actions,
                full_branches,
                action_summarizer,
            )
            for tray_id, tray_report in report["tray_reports"].items()
        }
        return slimmed
    plan = report.get("next_tool_plan")
    if plan is None:
        return dict(report)
    slimmed = dict(report)
    slimmed["next_tool_plan"] = _slim_plan(
        plan,
        top_actions,
        full_branches,
        action_summarizer,
    )
    return slimmed


def run_cli(solver: Any, argv: Optional[Sequence[str]] = None) -> int:
    StateError = solver.StateError
    _attach_solo_lifecycle = solver._attach_solo_lifecycle
    _normalize_session = solver._normalize_session
    _read_json = solver._read_json
    build_preference_briefing_report = solver.build_preference_briefing_report
    build_preference_calibration_report = solver.build_preference_calibration_report
    build_report = solver.build_report
    build_session_report = solver.build_session_report
    build_session_review_report = solver.build_session_review_report
    build_tray_comparison_report = solver.build_tray_comparison_report
    render_preference_briefing_markdown = solver.render_preference_briefing_markdown
    render_preference_calibration_markdown = solver.render_preference_calibration_markdown
    render_session_review_markdown = solver.render_session_review_markdown
    render_tray_comparison_markdown = solver.render_tray_comparison_markdown
    render_user_markdown = solver.render_user_markdown

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", help="Path to state JSON, or - for stdin")
    parser.add_argument("--session-state", action="store_true",
                        help="Export a canonical single/multi-tray session with its event ledger")
    parser.add_argument("--explain", action="store_true",
                        help="Render a concise explanation and conditional tool outcomes")
    parser.add_argument(
        "--plan-one",
        action="store_true",
        help="Backward-compatible alias for --plan-depth 1",
    )
    parser.add_argument(
        "--plan-depth",
        type=int,
        choices=(1, 2),
        help="Plan up to one or two adaptive card actions before drawing/stopping",
    )
    parser.add_argument(
        "--screen-tray",
        action="store_true",
        help=(
            "Assess whether to keep the current tray; defaults to one-card "
            "planning and reports reusable acceptance lines"
        ),
    )
    parser.add_argument(
        "--calibrate-preferences",
        action="store_true",
        help=(
            "Use complete per-design scores to show current-tray attainable "
            "ranges and candidate stop-rule bundles without recommending a draw"
        ),
    )
    parser.add_argument(
        "--brief-preferences",
        action="store_true",
        help=(
            "Summarize a zero-tray preference briefing as blind baselines and "
            "judgment-labeled reference lines without observing any tray"
        ),
    )
    parser.add_argument(
        "--compare-trays",
        action="store_true",
        help=(
            "Compare every operable tray in the session under the shared "
            "strategy and quality lines; each tray is solved independently"
        ),
    )
    parser.add_argument(
        "--compare-depth",
        type=int,
        choices=(1, 2),
        help=(
            "Planning horizon for --compare-trays; defaults to one step. "
            "Depth two only expands the top ranked trays and requires at "
            "least two available cards"
        ),
    )
    parser.add_argument(
        "--review-session",
        action="store_true",
        help=(
            "Replay the append-only event ledger into a whole-session "
            "review: per-opening ex-ante probabilities, per-card effects, "
            "lifecycle and stop-rule changes, and bias checks"
        ),
    )
    parser.add_argument(
        "--top-actions",
        type=int,
        default=3,
        help=(
            "Keep only the top N ranked tool actions in next_tool_plan; "
            "truncated ones collapse to other_actions_ranked summaries. "
            "0 keeps every action."
        ),
    )
    parser.add_argument(
        "--full-branches",
        action="store_true",
        help=(
            "Keep full per-branch metrics in next_tool_plan instead of the "
            "compact decision summary (audit mode)."
        ),
    )
    parser.add_argument(
        "--beam-width",
        type=int,
        default=3,
        help=(
            "At depth 2, expand the second layer only for the top N depth-1 "
            "card actions. 0 disables truncation (exact but slower)."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help=(
            "Output JSON (backward-compatible default) or the validated "
            "reader-facing Markdown report"
        ),
    )
    parser.add_argument("--indent", type=int, default=0)
    parser.add_argument("--digits", type=int, default=8)
    args = parser.parse_args(argv)

    try:
        if args.top_actions < 0:
            raise StateError("--top-actions must be >= 0")
        if args.beam_width < 0:
            raise StateError("--beam-width must be >= 0")
        session = _normalize_session(_read_json(args.state))
        if args.session_state:
            if (args.plan_one or args.plan_depth or args.screen_tray or args.review_session
                    or args.compare_trays or args.compare_depth or args.calibrate_preferences
                    or args.brief_preferences or args.explain or args.format != "json"):
                raise StateError("--session-state is a standalone JSON state export")
            print(json.dumps(solver.export_session_state(session), ensure_ascii=False, indent=2))
            return 0
        if args.explain and (args.format != "markdown" or args.screen_tray or args.review_session
                            or args.compare_trays or args.calibrate_preferences or args.brief_preferences):
            raise StateError("--explain requires a decision report with --format markdown")
        if args.plan_one and args.plan_depth not in {None, 1}:
            raise StateError("--plan-one cannot be combined with --plan-depth 2")
        if args.calibrate_preferences and (
            args.plan_one
            or args.plan_depth is not None
            or args.screen_tray
        ):
            raise StateError(
                "--calibrate-preferences cannot be combined with planning or "
                "--screen-tray"
            )
        if args.brief_preferences and (
            args.plan_one
            or args.plan_depth is not None
            or args.screen_tray
            or args.calibrate_preferences
            or args.compare_trays
        ):
            raise StateError(
                "--brief-preferences cannot be combined with planning, "
                "--screen-tray, --calibrate-preferences, or --compare-trays"
            )
        if args.compare_depth is not None and not args.compare_trays:
            raise StateError(
                "--compare-depth requires --compare-trays"
            )
        if args.compare_trays and (
            args.plan_one
            or args.plan_depth is not None
            or args.screen_tray
            or args.calibrate_preferences
        ):
            raise StateError(
                "--compare-trays cannot be combined with planning, "
                "--screen-tray, or --calibrate-preferences"
            )
        if args.review_session and (
            args.plan_one
            or args.plan_depth is not None
            or args.screen_tray
            or args.calibrate_preferences
            or args.compare_trays
            or args.compare_depth is not None
            or args.brief_preferences
        ):
            raise StateError(
                "--review-session cannot be combined with planning, "
                "--screen-tray, --calibrate-preferences, --compare-trays, "
                "or --brief-preferences"
            )
        requested_depth = 1 if args.plan_one else args.plan_depth
        if (
            args.format == "markdown"
            and requested_depth is None
            and not args.calibrate_preferences
            and not args.brief_preferences
            and not args.compare_trays
            and not args.review_session
        ):
            requested_depth = 1
        if args.brief_preferences:
            if not session.get("_briefing_input"):
                raise StateError(
                    "--brief-preferences requires a briefing state with "
                    "regular_count and no trays or boxes"
                )
            report = build_preference_briefing_report(
                session["_briefing_state"]
            )
            if args.format == "markdown":
                markdown = render_preference_briefing_markdown(report)
        elif session.get("_briefing_input"):
            raise StateError(
                "this state is a preference briefing without boxes; rerun with "
                "--brief-preferences, or add trays/boxes once a real tray is "
                "observed"
            )
        elif args.compare_trays:
            if session["_legacy_input"]:
                raise StateError(
                    "--compare-trays needs a multi-tray session state with at "
                    "least two trays; a single tray keeps using --screen-tray "
                    "or the formal report"
                )
            compare_depth = args.compare_depth or 1
            report = build_tray_comparison_report(
                session,
                compare_depth=compare_depth,
                beam_width=args.beam_width,
            )
            if args.format == "markdown":
                markdown = render_tray_comparison_markdown(report)
            else:
                report = _slim_report(
                    report,
                    args.top_actions,
                    args.full_branches,
                    solver._action_summary,
                )
        elif args.review_session:
            report = build_session_review_report(session)
            if args.format == "markdown":
                markdown = render_session_review_markdown(report)
        elif args.calibrate_preferences:
            active_tray_id = session["active_tray_id"]
            state = session["_tray_states"][active_tray_id]
            report = build_preference_calibration_report(
                state,
                tray_id=(
                    None
                    if session["_legacy_input"]
                    else active_tray_id
                ),
            )
            if args.format == "markdown":
                markdown = render_preference_calibration_markdown(report)
        else:
            if session["_legacy_input"]:
                state = session["_tray_states"][session["active_tray_id"]]
                report = build_report(
                    state,
                    plan_depth=requested_depth,
                    screen_tray=args.screen_tray,
                    beam_width=args.beam_width,
                )
                _attach_solo_lifecycle(session, report)
            else:
                report = build_session_report(
                    session,
                    plan_depth=requested_depth,
                    screen_tray=args.screen_tray,
                    beam_width=args.beam_width,
                )
            if args.format == "markdown":
                markdown = (solver.render_explanation_markdown(report) if args.explain
                            else render_user_markdown(report, screen_tray=args.screen_tray))
            else:
                report = _slim_report(
                    report,
                    args.top_actions,
                    args.full_branches,
                    solver._action_summary,
                )
    except (OSError, json.JSONDecodeError, StateError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    if args.format == "markdown":
        print(markdown, end="")
        return 0

    print(
        json.dumps(
            _round_floats(report, args.digits),
            ensure_ascii=False,
            indent=args.indent,
            sort_keys=False,
        )
    )
    return 0
