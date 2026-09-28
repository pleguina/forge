"""``forge connect`` — record one connection decision, non-interactively.

The third way to answer the question FORGE refuses to answer for you.
``forge adopt --interactive`` asks; an editor writes ``connections:`` by
hand; this writes the same entry from one command, which is what makes the
visual explorer's "connect these two" action real (Phase J3): the explorer
shows the command, and running it edits the project file. The UI never
writes anything itself and never holds a decision the file does not.

All three routes end in the same two lines of ``forge.yml`` and go through
the same :func:`forge.project.interactive.apply_answer`, so no route can
record a decision differently from another.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _endpoint_error(value: str) -> str:
    return (
        f"{value!r} is not an endpoint — write it as 'module.port' "
        f"(e.g. decoder.data_out)"
    )


def cmd_connect(args) -> None:
    from forge.core.cli.envelope import CommandEnvelope, emit, status_for_exception
    from forge.project.config import DeclaredConnection, ForgeConfig
    from forge.project.discovery import ProjectDiscovery
    from forge.project.paths import ROOT_CONFIG_NAME, find_project_root

    json_mode = getattr(args, "json", False)

    def fail(message: str, action: str = "") -> None:
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{"severity": "error", "message": message, "action": action}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print(f"❌ {message}", file=sys.stderr)
        if action:
            print(f"   → {action}", file=sys.stderr)
        sys.exit(envelope.exit_code())

    for endpoint in (args.producer, args.consumer):
        if str(endpoint).count(".") != 1 or not all(
            part.strip() for part in str(endpoint).split(".")
        ):
            fail(_endpoint_error(endpoint))

    start = Path(getattr(args, "project", None) or ".").expanduser().resolve()
    root = find_project_root(start)
    if root is None:
        fail(
            f"no {ROOT_CONFIG_NAME} at or above {start}",
            f"Run `forge adopt` first, or pass --project <dir>",
        )

    try:
        config = ForgeConfig.load(root / ROOT_CONFIG_NAME)
    except Exception as exc:  # noqa: BLE001
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(envelope.exit_code())

    # Both endpoints must be real ports of real modules. A declaration that
    # names a port nothing has is reported by `forge check` anyway — but
    # catching it here means the user finds out while they still remember
    # what they meant, instead of on the next scan.
    discovery = ProjectDiscovery(decisions=config.decisions()).scan(root)
    endpoints = {
        f"{unit.name}.{port.name}"
        for unit in discovery.managed_units
        for port in (*unit.inputs(), *unit.outputs())
    }
    missing = [e for e in (args.producer, args.consumer) if e not in endpoints]
    if missing:
        near = sorted(e for e in endpoints if e.split(".", 1)[0] == _module(missing[0]))
        fail(
            f"{' and '.join(missing)} is not a port of any managed module",
            (f"Ports on {_module(missing[0])}: {', '.join(near)}" if near
             else "Run `forge check` to see the modules FORGE manages"),
        )

    already = [
        c for c in config.connections
        if (c.producer, c.consumer) == (args.producer, args.consumer)
    ]
    contradicting = [
        c for c in config.connections
        if c.consumer == args.consumer and c.producer != args.producer
    ]
    if contradicting and not getattr(args, "force", False):
        fail(
            f"{args.consumer} is already declared as driven by "
            f"{contradicting[0].producer}",
            "Two drivers on one net is what this declaration exists to "
            "prevent — remove the existing entry, or pass --force to replace it",
        )

    if already:
        written = False
    else:
        config.connections = [
            c for c in config.connections if c not in contradicting
        ] + [DeclaredConnection(producer=args.producer, consumer=args.consumer)]
        if not getattr(args, "dry_run", False):
            config.save()
        written = True

    entry = {"from": args.producer, "to": args.consumer}
    envelope = CommandEnvelope(
        status="pass",
        artifacts=[str(root / ROOT_CONFIG_NAME)] if written and not getattr(args, "dry_run", False) else [],
        metrics={
            "declaration": entry,
            "written": written and not getattr(args, "dry_run", False),
            "already_declared": bool(already),
            "replaced": [
                {"from": c.producer, "to": c.consumer} for c in contradicting
            ],
        },
        next_actions=["forge check"],
    )
    if json_mode:
        sys.exit(emit(envelope, json_mode=True))

    if already:
        print(f"Already declared: {args.producer} -> {args.consumer}")
    elif getattr(args, "dry_run", False):
        print("[dry-run] would add to connections: in "
              f"{root / ROOT_CONFIG_NAME}")
        print(f"  - from: {args.producer}")
        print(f"    to: {args.consumer}")
    else:
        for replaced in contradicting:
            print(f"Replaced: {replaced.producer} -> {replaced.consumer}")
        print(f"Declared in {root / ROOT_CONFIG_NAME}:")
        print(f"  - from: {args.producer}")
        print(f"    to: {args.consumer}")
    print()
    print("Run:")
    print()
    print("  forge check")
    print()
    sys.exit(envelope.exit_code())


def _module(endpoint: str) -> str:
    return endpoint.split(".", 1)[0]


def register(sub) -> None:
    p = sub.add_parser(
        "connect",
        help="Declare which producer drives which consumer, in forge.yml",
        description=(
            "Record one connection decision in forge.yml's connections: block — "
            "the same declaration `forge adopt --interactive` writes when you "
            "answer its question, and the same one the visual explorer's connect "
            "action hands you. Both endpoints must be real ports of managed "
            "modules."
        ),
    )
    p.add_argument("producer", help="Driving endpoint, as module.port")
    p.add_argument("consumer", help="Driven endpoint, as module.port")
    p.add_argument("--project", type=Path, default=None,
                   help="Project directory (default: search upwards from the cwd)")
    p.add_argument("--force", action="store_true", default=False,
                   help="Replace an existing declaration for the same consumer")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False,
                   help="Show the declaration without writing it")
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.set_defaults(func=cmd_connect)
