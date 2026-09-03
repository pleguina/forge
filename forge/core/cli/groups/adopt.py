"""``forge adopt`` — enter an existing repository instead of replacing it.

``forge init`` creates a project shaped like FORGE. That covers the first
five minutes and nothing after: the moment a user has a repository of their
own, ``init`` has nothing to offer them, and the documented alternative was
to hand-author a module registry, a design topology and one interface
contract per module before FORGE would do anything at all.

``forge adopt`` is the other direction. It reads a repository FORGE has
never seen, works out what is in it, and writes the project files that
follow — a ``forge.yml`` the user maintains and a ``.forge/`` tree FORGE
maintains. What it cannot determine, it refuses to invent: those come back
as unresolved decisions with the command that settles each one.

Nothing in the user's own tree is moved, renamed or rewritten.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _render_scan(result, root: Path) -> None:
    """The "here is what is in your repository" block."""
    print("Scanning project...")
    print()

    counts = result.counts()
    rtl_by_language = {}
    for unit in result.units:
        rtl_by_language[unit.language] = rtl_by_language.get(unit.language, 0) + 1
    if rtl_by_language:
        print("RTL")
        for language, count in sorted(rtl_by_language.items()):
            print(f"  {count:>3} {language} modules")
    if result.hls_candidates:
        print("HLS")
        print(f"  {len(result.hls_candidates):>3} candidate kernels")
    if counts["testbench_files"]:
        print("Simulation")
        print(f"  {counts['testbench_files']:>3} candidate testbench files")
    if counts["constraint_files"]:
        print("Constraints")
        print(f"  {counts['constraint_files']:>3} constraint files")
    print()

    if result.top_candidates:
        print("Likely top-level modules")
        for name in result.top_candidates[:5]:
            marker = "  (existing structural top)" if name == result.structural_top else ""
            print(f"  {name}{marker}")
        print()

    if result.clocks:
        print("Detected clocks")
        for clock in result.clocks:
            print(f"  {clock.name}")
        print()
    if result.resets:
        print("Detected resets")
        for reset in result.resets:
            level = "active low" if reset.active_low else "active high"
            print(f"  {reset.name}  ({level})")
        print()

    if result.connections:
        print("Inferred topology")
        for connection in result.connections:
            print(f"  {connection.producer}  ->  {connection.consumer}"
                  f"   [{connection.width} bit, {connection.confidence}]")
        print()


def _render_unresolved(plan) -> None:
    """Every decision adoption declined to make, and how to make it."""
    if not plan.unresolved:
        return
    print(f"Unresolved decisions: {len(plan.unresolved)}")
    print("─" * 60)
    for ambiguity in plan.unresolved:
        print(f"  [{ambiguity.classification.upper()}] {ambiguity.question}")
        for option in ambiguity.options:
            print(f"      - {option}")
        if ambiguity.action:
            print(f"    → {ambiguity.action.description}")
        print()


def _run_interactive(plan, root: Path, part: str, project_name):
    """Ask the questions, write the answers, and adopt again from them.

    The second adoption is the point: answers land in ``forge.yml``, and
    everything downstream — the module registry, the topology, ``forge
    check`` — is then regenerated *from that file*, exactly as it would be
    for a user who had typed the same lines by hand. Nothing the prompt
    learned survives anywhere else.
    """
    from forge.project.adopt import plan_adoption
    from forge.project.interactive import resolve_interactively
    from forge.project.paths import ROOT_CONFIG_NAME

    questions = list(plan.unresolved)
    if not questions:
        print("Nothing to decide — every question resolved from the sources.")
        print()
        return plan, []

    print(f"{len(questions)} decision(s) FORGE will not make for you.")
    print("Each answer is written to forge.yml, where you can change it later.")
    print()

    config, answers, unanswered = resolve_interactively(
        plan.config, questions, ask=lambda prompt: input(prompt),
    )
    if not answers:
        print("No answers given — nothing written to forge.yml.")
        print()
        return plan, unanswered

    # Written before re-planning, because re-planning *reads* it: the file
    # is the only channel the answers travel through.
    config.save()
    print(f"Recorded {len(answers)} answer(s) in {config.root / ROOT_CONFIG_NAME}:")
    for answer in answers:
        print(f"  {answer.summary}")
    print()

    return plan_adoption(root, project_name=project_name, part=part), unanswered


def cmd_adopt(args) -> None:
    from forge.core.cli._shared import print_cli_error
    from forge.core.cli.envelope import (
        CommandEnvelope,
        emit,
        status_for_exception,
    )
    from forge.project.actions import render_all
    from forge.project.adopt import adoption_actions, plan_adoption
    from forge.project.paths import ROOT_CONFIG_NAME

    json_mode = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    try:
        root = Path(args.path).expanduser().resolve()
        plan = plan_adoption(
            root,
            project_name=getattr(args, "name", None),
            part=args.part,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error("Adoption failed", exc)
        sys.exit(envelope.exit_code())

    if not plan.discovery.units and not plan.discovery.hls_candidates:
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{
                "severity": "error",
                "code": "ATG031",
                "message": (
                    f"no RTL modules or HLS kernels found under {root} — "
                    f"nothing to adopt"
                ),
                "action": (
                    "Point `forge adopt` at the directory holding the design's "
                    "sources, or run `forge init <name>` to start a new project"
                ),
            }],
            metrics={"root": str(root), "scanned_files": len(plan.discovery.files)},
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print(f"❌ No RTL modules or HLS kernels found under {root}.", file=sys.stderr)
        print(f"   Scanned {len(plan.discovery.files)} files.", file=sys.stderr)
        print("   → Point `forge adopt` at the design's source directory, or run "
              "`forge init <name>` to start a new project.", file=sys.stderr)
        sys.exit(1)

    interactive = getattr(args, "interactive", False)
    if interactive:
        if json_mode:
            print("❌ --interactive cannot be combined with --json.", file=sys.stderr)
            print("   → Run it without --json, or answer in forge.yml directly.",
                  file=sys.stderr)
            sys.exit(1)
        if not sys.stdin.isatty():
            print("❌ --interactive needs a terminal to ask questions on.", file=sys.stderr)
            print("   → Run `forge adopt` without it and answer in forge.yml.",
                  file=sys.stderr)
            sys.exit(1)
        _render_scan(plan.discovery, root)
        plan, _unanswered = _run_interactive(
            plan, root, args.part, getattr(args, "name", None),
        )
        # An interactive run answers questions *into* forge.yml, so it must
        # then be allowed to write it — the file it just edited is the file
        # adoption is about to render.
        force = True

    existing_config = (root / ROOT_CONFIG_NAME).is_file()
    written = [] if dry_run else plan.write(overwrite=force)

    diagnostics = [
        {
            "severity": "warning",
            "code": "ATG037" if ambiguity.classification == "ambiguous" else "ATG042",
            "message": ambiguity.question,
            "action": ambiguity.action.description if ambiguity.action else "",
            "context": {"options": list(ambiguity.options)},
        }
        for ambiguity in plan.unresolved
    ]
    if existing_config and not force and not dry_run:
        diagnostics.append({
            "severity": "note",
            "message": (
                f"{ROOT_CONFIG_NAME} already existed and was left untouched — "
                f"it is yours to maintain. Everything under .forge/ was regenerated."
            ),
            "action": f"Pass --force to overwrite {ROOT_CONFIG_NAME} as well",
        })

    envelope = CommandEnvelope(
        status="warn" if plan.unresolved else "pass",
        diagnostics=diagnostics,
        artifacts=[str(p) for p in written],
        metrics={
            **plan.summary(),
            "dry_run": dry_run,
            "discovery": plan.discovery.counts(),
            "decisions": [e.to_dict() for e in plan.decisions],
        },
        next_actions=render_all(adoption_actions(plan)),
    )

    if json_mode:
        sys.exit(emit(envelope, json_mode=True))

    if not interactive:
        _render_scan(plan.discovery, root)
    if dry_run:
        print("[dry-run] would create:")
        for path in plan.files:
            print(f"  {path}")
        print()
    else:
        print("Created:")
        for path in written:
            print(f"  {path}")
        if existing_config and not force:
            print(f"  ({ROOT_CONFIG_NAME} already existed — left untouched)")
        print()
    _render_unresolved(plan)
    print("Run:")
    print()
    print("  forge check")
    print()
    sys.exit(envelope.exit_code())


def register(sub) -> None:
    p = sub.add_parser(
        "adopt",
        help="Scan an existing repository and create a FORGE project from what is in it",
        description=(
            "Read a repository FORGE has never seen — modules, ports, instantiation "
            "graph, clocks, resets, HLS kernels, testbenches, constraints — and write "
            "the project files that follow: forge.yml for you, .forge/ for FORGE. "
            "Nothing in your tree is moved or rewritten, and nothing FORGE could not "
            "determine is filled in with a guess."
        ),
    )
    p.add_argument("path", nargs="?", default=".",
                   help="Repository to adopt (default: the current directory)")
    p.add_argument("--name", default=None,
                   help="Project name (default: the directory name)")
    from forge.project.config import DEFAULT_PART

    p.add_argument("--part", default=DEFAULT_PART,
                   help=f"Target device (default: {DEFAULT_PART}, a placeholder you can change later)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False,
                   help="Show what would be written without writing it")
    p.add_argument("--force", action="store_true", default=False,
                   help="Overwrite an existing forge.yml (the .forge/ tree is always regenerated)")
    p.add_argument("--interactive", "-i", action="store_true", default=False,
                   help=(
                       "Ask about each decision FORGE will not make for you, and "
                       "record the answers in forge.yml (where you can change them)"
                   ))
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.set_defaults(func=cmd_adopt)
