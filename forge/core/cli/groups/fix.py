"""``forge fix`` — apply the repairs that have exactly one right answer.

The companion to ``forge check``. Most of what ``check`` reports needs a
human, because it is a decision; some of it does not, and making someone
open an editor for a width the RTL already proves is friction with no
upside.

This command applies only repairs the project's own sources prove. It does
not offer to make semantic guesses behind a confirmation prompt — an
invented family or a chosen consumer is not this command's business at any
level of confidence, and those come back as work for the user with the
command that helps.

Rendering only. :mod:`forge.project.fix` decides what is safe.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _render(plan, *, applied: bool) -> None:
    fixes = plan.safe_fixes
    if not fixes:
        print("No automatic fixes available.")
    else:
        verb = "Applied" if applied else "Available"
        print(f"{verb}: {len(fixes)} automatic fix(es).")
        print()
        for index, fix in enumerate(fixes, start=1):
            print(f"{index}. {fix.title}")
            if fix.path is not None:
                print(f"   {fix.path}")
            for line in fix.diff():
                print(f"   {line}")
            print("   Evidence:")
            for item in fix.evidence:
                print(f"     {item.describe()}")
            print()

    # Two different things get left over, and conflating them misreads the
    # second as harder than it is: work `forge fix` simply does not perform
    # (running a generator), and genuine decisions only the user can make.
    runnable = [a for a in plan.manual if a.command]
    decisions = [a for a in plan.manual if not a.command]

    if runnable:
        print("Not fix's job — run these yourself:")
        for action in runnable:
            print(f"  - {action.description}")
            print(f"    {action.command}")
        print()

    if decisions:
        print("Needs you — these are decisions, not transcription:")
        for action in decisions:
            print(f"  - {action.description}")
        print()

    if fixes and not applied:
        print("Run `forge fix --apply` to apply them.")


def cmd_fix(args) -> None:
    from forge.core.cli._shared import print_cli_error
    from forge.core.cli.envelope import (
        CommandEnvelope,
        emit,
        status_for_exception,
    )
    from forge.project.actions import render_all
    from forge.project.fix import plan_fixes

    json_mode = getattr(args, "json", False)
    apply = getattr(args, "apply", False)
    only = [i for i in (getattr(args, "only", None) or "").split(",") if i] or None

    try:
        plan = plan_fixes(Path(args.path).expanduser().resolve(), only=only)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error("Could not plan fixes", exc)
        sys.exit(envelope.exit_code())

    # Commands are not run for the user: `regenerate-stale-artifacts` is a
    # `forge build`, and silently invoking a generator from a command called
    # "fix" would surprise anyone who expected a config repair.
    if apply:
        plan.apply()

    envelope = CommandEnvelope(
        status="pass",
        artifacts=[str(p) for p in plan.applied],
        metrics={**plan.to_dict(), "applied_count": len(plan.applied), "dry_run": not apply},
        next_actions=(
            render_all(plan.manual)
            + ([f"Run: {f.command}" for f in plan.safe_fixes if f.command])
            + ([] if apply or not plan.safe_fixes else
               ["Run `forge fix --apply` to apply the available fixes"])
        ),
    )

    if json_mode:
        sys.exit(emit(envelope, json_mode=True))
    _render(plan, applied=apply)
    sys.exit(0)


def register(sub) -> None:
    p = sub.add_parser(
        "fix",
        help="Apply the repairs this project's own sources prove",
        description=(
            "Repair what has exactly one right answer: a contract width the RTL "
            "contradicts, a missing generated contract, a stale top level. Only "
            "repairs proven by the project's own sources are applied — a semantic "
            "decision (which consumer a producer feeds, what family a port carries) "
            "is never made here, at any level of confidence. Previews by default."
        ),
    )
    p.add_argument(
        "path", nargs="?", default=".",
        help="Project directory (default: the current one)",
    )
    p.add_argument(
        "--apply", action="store_true", default=False,
        help="Write the fixes (default: preview them as a diff)",
    )
    p.add_argument(
        "--dry-run", dest="apply", action="store_false",
        help="Preview only — the default, accepted explicitly for scripts",
    )
    p.add_argument(
        "--only", default=None,
        help="Comma-separated fix ids to consider (default: all of them)",
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.set_defaults(func=cmd_fix)
