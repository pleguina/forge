"""``forge migrate`` — bring a whole project up to the current schemas.

``forge topgen migrate`` performs the individual migrations, and asks the
user to already know which files need which one. That is the wrong question
to put to someone who has just upgraded FORGE and wants to know whether
their project still works.

This command answers theirs: point it at a project, see the whole chain as
one preview, apply it in one step. It performs no migration itself —
:mod:`forge.project.migrate` delegates each one to the function in
``forge.generation.migrate`` that already owns it, so this and the
file-by-file route can never diverge.

Dry-run by default. A migration that moves files is reported with the
command that performs it rather than performed here.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _render(plan, *, applied: bool) -> None:
    print(f"Project schema migration — {plan.root}")
    print()
    print("This FORGE writes")
    for name, version in plan.target_versions.items():
        print(f"  {name:<12} {version}")
    print()

    pending = plan.pending
    if not pending:
        print("Required changes")
        print("  none — every file already declares a schema this build understands")
    else:
        verb = "Applied" if applied else "Required changes"
        print(f"{verb}: {len(pending)}")
        print()
        for index, migration in enumerate(pending, start=1):
            print(f"{index}. {migration.title}")
            print(f"   {migration.path}")
            for line in migration.diff():
                print(f"   {line}")
            print()

    if plan.manual:
        print("Not performed here — these move files:")
        for action in plan.manual:
            print(f"  - {action.description}")
            print(f"    {action.command}")
        print()

    for note in plan.notes:
        print(f"⚠️  {note}")
    if plan.notes:
        print()

    if pending and not applied:
        print("Preview only. Run `forge migrate --apply` to write these changes.")


def cmd_migrate(args) -> None:
    from forge.core.cli._shared import print_cli_error
    from forge.core.cli.envelope import (
        CommandEnvelope,
        emit,
        status_for_exception,
    )
    from forge.project.actions import render_all
    from forge.project.migrate import plan_migration

    json_mode = getattr(args, "json", False)
    apply = getattr(args, "apply", False)
    only = [i for i in (getattr(args, "only", None) or "").split(",") if i] or None

    try:
        plan = plan_migration(Path(args.path).expanduser().resolve(), only=only)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error("Could not plan the migration", exc)
        sys.exit(envelope.exit_code())

    if apply:
        plan.apply()

    envelope = CommandEnvelope(
        status="pass",
        artifacts=[str(p) for p in plan.applied],
        diagnostics=[
            {"severity": "note", "message": note} for note in plan.notes
        ],
        metrics={**plan.to_dict(), "dry_run": not apply},
        next_actions=(
            render_all(plan.manual)
            + ([] if apply or not plan.pending else
               ["Run `forge migrate --apply` to write the previewed changes"])
        ),
    )

    if json_mode:
        sys.exit(emit(envelope, json_mode=True))
    _render(plan, applied=apply)
    sys.exit(0)


def register(sub) -> None:
    p = sub.add_parser(
        "migrate",
        help="Bring a whole project up to the schemas this FORGE understands",
        description=(
            "Find every schema-bearing file in a project — both the forge.yml + "
            ".forge/ layout and the plugins/<id>/forge/ one — work out what each "
            "needs, and preview the whole chain as one diff. Performs no migration "
            "itself: each is delegated to the same function `forge topgen migrate` "
            "uses, so the two routes cannot diverge. Migrations that move files are "
            "reported with the command that performs them, never performed here."
        ),
    )
    p.add_argument(
        "path", nargs="?", default=".",
        help="Project directory (default: the current one)",
    )
    p.add_argument(
        "--apply", action="store_true", default=False,
        help="Write the migrations (default: preview them as a diff)",
    )
    p.add_argument(
        "--dry-run", dest="apply", action="store_false",
        help="Preview only — the default, accepted explicitly for scripts",
    )
    p.add_argument(
        "--only", default=None,
        help="Comma-separated migration ids to consider (default: all of them)",
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.set_defaults(func=cmd_migrate)
