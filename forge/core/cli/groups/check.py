"""``forge check`` — is this project sufficiently described to work?

The complement to ``forge doctor``, and the distinction is worth keeping
sharp: ``doctor`` answers "is the FORGE *installation* sane?" and knows
nothing about any project; ``check`` answers "is *this project* completely
and consistently described?" and knows nothing about the toolchain. Running
one when you needed the other is the most common way a new user gets a
confident PASS that does not mean what they hoped.

Everything printed here comes from :func:`forge.project.status.evaluate` —
this module renders, it does not decide. ``forge next`` renders the same
object down to its single most urgent line.
"""

from __future__ import annotations

import sys
from pathlib import Path

_STATE_ICON = {
    "PASS": "✅",
    "WARN": "⚠️ ",
    "FAIL": "❌",
    "PARTIAL": "◐ ",
    "NOT CONFIGURED": "○ ",
    "STALE": "⟳ ",
}


def _render(status) -> None:
    """Print the human report: sections, then the first blocker in full."""
    from forge.project.status import FAIL

    print("FORGE project status")
    print("─" * 60)
    print(f"  {status.root}")
    print()
    for section in status.sections:
        icon = _STATE_ICON.get(section.state, "  ")
        detail = f"   {section.detail}" if section.detail else ""
        print(f"  {icon} {section.name:<26}{section.state:<16}{detail}")

    print()
    print(f"  Project completion: {status.completion:.0%} ({status.maturity})")

    blockers = status.blockers
    if blockers:
        print()
        print("Blocking issues")
        print("─" * 60)
        for diagnostic in blockers:
            print(f"  [{diagnostic.code}] {diagnostic.message}")
            candidates = diagnostic.context.get("candidates")
            if candidates:
                print("    Candidates:")
                for candidate in candidates:
                    print(f"      {candidate}")
            if diagnostic.action:
                print(f"    → {diagnostic.action}")
            # Every blocker is one command away from its full account —
            # what the code means, where it fired, and what FORGE
            # considered before it stopped.
            print(f"    Learn: forge explain {diagnostic.code}")
            print()

    warnings = status.warnings
    if warnings:
        if not blockers:
            print()
        print("Warnings")
        print("─" * 60)
        for diagnostic in warnings:
            print(f"  [{diagnostic.code}] {diagnostic.message}")
            if diagnostic.action:
                print(f"    → {diagnostic.action}")
        print()

    action = status.recommended_action
    if action:
        print("Next")
        print("─" * 60)
        print(f"  {action.description}")
        if action.command:
            print(f"    {action.command}")
    else:
        print("Next")
        print("─" * 60)
        print("  Nothing outstanding — this project is fully described.")
    print()


def cmd_check(args) -> None:
    from forge.core.cli._shared import print_cli_error
    from forge.core.cli.envelope import (
        CommandEnvelope,
        emit,
        from_diagnostic_report,
        status_for_exception,
    )
    from forge.project.actions import render_all
    from forge.project.status import evaluate

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)

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
            sys.exit(emit(envelope, json_mode=True, strict=strict))
        print_cli_error("Project check failed", exc)
        sys.exit(envelope.exit_code(strict=strict))

    envelope = from_diagnostic_report(
        status.report,
        metrics={
            "root": str(status.root),
            "completion": status.completion,
            "maturity": status.maturity,
            "sections": [s.to_dict() for s in status.sections],
            "actions": [a.to_dict() for a in status.actions],
        },
    )
    # The structured actions carry what a bare `action` string cannot (a
    # runnable command, whether `forge fix` could apply it), so they replace
    # the strings from_diagnostic_report derived — same list, more in it.
    envelope.next_actions = render_all(status.actions)

    if json_mode:
        sys.exit(emit(envelope, json_mode=True, strict=strict))
    _render(status)
    sys.exit(envelope.exit_code(strict=strict))


def register(sub) -> None:
    p = sub.add_parser(
        "check",
        help="Report whether this project is completely and consistently described",
        description=(
            "Assess one FORGE project: are its sources where it says they are, is "
            "every module contracted, does its topology resolve without guessing, "
            "is it generated, is it verified. Reports every blocker with the step "
            "that clears it. `forge doctor` checks the installation instead."
        ),
    )
    p.add_argument(
        "path", nargs="?", default=".",
        help="Project directory (default: the current one; parents are searched for forge.yml)",
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.add_argument("--strict", action="store_true", default=False,
                   help="Exit 1 on warnings as well as blockers")
    p.set_defaults(func=cmd_check)
