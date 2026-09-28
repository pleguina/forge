"""``forge next`` — the one thing to do next.

A new user's problem is rarely that FORGE gave them no information; it is
that ``forge check`` gave them nine lines of it and no idea which to act on
first. This command answers only "what now?".

It computes nothing of its own. The plan is explicit that ``forge next``
must consume the same project-state model as ``forge check`` rather than
growing a second rule system, so this is a renderer over
:class:`forge.project.status.ProjectStatus` — the recommended step is
whichever action that model recorded first, and the checks run in dependency
order precisely so that first action is the right one.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _render(status) -> None:
    from forge.project.status import PASS

    print(f"Project completion: {status.completion:.0%}")
    print()

    done = [s.name for s in status.sections if s.state == PASS]
    if done:
        print("Completed")
        for name in done:
            print(f"  {name}")
        print()

    action = status.recommended_action
    if action is None:
        print("Recommended next step")
        print("  Nothing — every check passes.")
        print()
        print("Run")
        print("  forge report    to collect topology, latency and results into one bundle")
        return

    print("Recommended next step")
    print(f"  {action.description}")
    print()

    reason = status.reason_for(action)
    if reason:
        print("Reason")
        print(f"  {reason}")
        print()

    if action.command:
        print("Run")
        print(f"  {action.command}")
        print()

    remaining = [a for a in status.actions[1:] if a is not action]
    if remaining:
        print("Also outstanding")
        for other in remaining[:3]:
            print(f"  {other.description}")
        if len(remaining) > 3:
            print(f"  … and {len(remaining) - 3} more (see `forge check`)")


def cmd_next(args) -> None:
    from forge.core.cli._shared import print_cli_error
    from forge.core.cli.envelope import (
        CommandEnvelope,
        emit,
        status_for_exception,
    )
    from forge.project.actions import render_all
    from forge.project.status import evaluate

    json_mode = getattr(args, "json", False)

    try:
        status = evaluate(Path(args.path).expanduser().resolve())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error("Could not determine the next step", exc)
        sys.exit(envelope.exit_code())

    action = status.recommended_action
    # `forge next` always succeeds at answering its own question: having a
    # next step is the normal state of a project under construction, not a
    # failure. The blockers themselves are `forge check`'s to fail on.
    envelope = CommandEnvelope(
        status="pass",
        metrics={
            "completion": status.completion,
            "maturity": status.maturity,
            "recommended_action": action.to_dict() if action else None,
            "completed_sections": [
                s.name for s in status.sections if s.state == "PASS"
            ],
            "blocking": len(status.blockers),
        },
        next_actions=render_all(status.actions),
    )

    if json_mode:
        sys.exit(emit(envelope, json_mode=True))
    _render(status)
    sys.exit(0)


def register(sub) -> None:
    p = sub.add_parser(
        "next",
        help="Say what to do next, and why",
        description=(
            "Render this project's health down to its single most urgent step. "
            "Same model as `forge check`, narrowed to one recommendation so the "
            "workflow does not have to be memorised."
        ),
    )
    p.add_argument(
        "path", nargs="?", default=".",
        help="Project directory (default: the current one)",
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.set_defaults(func=cmd_next)
