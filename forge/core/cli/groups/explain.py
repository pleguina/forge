"""``forge explain`` — why did FORGE do that?

The framework decides a great deal on the user's behalf: it connects two
ports, derives a width, predicts an HLS interface, reads a port name as a
clock, refuses an ambiguous match. Guiding principle 3.4 of the adoption
plan is that none of those may be a black box — a user must be able to point
at any one of them and get a deterministic account back.

This module renders. Everything it prints comes from
:func:`forge.project.explain.explain`, which resolves the target against the
canonical IR where the design has been resolved and against source discovery
where it has not — and says which, because the two differ in authority.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLES = """
Targets:
  forge explain ATG037                  what a diagnostic code means, and where it fired
  forge explain shaper                  a module: source, ports, contract, connections
  forge explain shaper.shaped           a port: HDL facts, contract role, what it is wired to
  forge explain connection:a.q->b.d     a resolved connection's full matching evidence
  forge explain clock:clk               why FORGE reads a port as the clock
  forge explain reset:rst_n             the same for a reset, including its active level
  forge explain hls:regression          an HLS kernel's predicted RTL interface
  forge explain artifact:.forge/generated/algo_top.v
                                        what wrote a file, and whether it is still current

An ambiguous name can be disambiguated with an explicit prefix
(`module:clock` for a module actually called "clock").
"""


def _render(explanation) -> None:
    print(explanation.summary)
    print()
    for section in explanation.sections:
        print(f"{section.heading}")
        for line in section.lines:
            print(f"  {line}")
        print()
    if explanation.evidence:
        print("Evidence")
        for item in explanation.distinct_evidence:
            print(f"  {item.describe()}")
        print()
    print(f"Source: {explanation.source}")


def cmd_explain(args) -> None:
    from forge.core.cli._shared import print_cli_error
    from forge.core.cli.envelope import (
        CommandEnvelope,
        emit,
        status_for_exception,
    )
    from forge.project.explain import UnknownTarget, explain

    json_mode = getattr(args, "json", False)

    try:
        explanation = explain(args.target, Path(args.path).expanduser().resolve())
    except SystemExit:
        raise
    except UnknownTarget as exc:
        envelope = CommandEnvelope(
            status="fail",
            diagnostics=[{"severity": "error", "message": str(exc)}],
            next_actions=["Run `forge explain --help` for the target forms"],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(envelope.exit_code())
    except Exception as exc:  # noqa: BLE001
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error("Could not explain that", exc)
        sys.exit(envelope.exit_code())

    envelope = CommandEnvelope(
        status="pass",
        metrics={"explanation": explanation.to_dict()},
    )
    if json_mode:
        sys.exit(emit(envelope, json_mode=True))
    _render(explanation)
    sys.exit(0)


def register(sub) -> None:
    import argparse

    p = sub.add_parser(
        "explain",
        help="Explain one FORGE decision — a connection, an inference, a diagnostic",
        description=(
            "Give a deterministic account of something FORGE decided: why two ports "
            "are connected, why a port reads as the clock, what an HLS kernel's "
            "interface is predicted to be, what a diagnostic code means, or why "
            "FORGE refused to decide at all. Explained from the canonical IR where "
            "the design has been resolved, and from source discovery where it has "
            "not — the answer says which."
        ),
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("target", help="What to explain (see the examples below)")
    p.add_argument(
        "--path", default=".",
        help="Project directory (default: the current one; parents are searched for forge.yml)",
    )
    p.add_argument("--json", action="store_true", default=False,
                   help="Machine-readable JSON output")
    p.set_defaults(func=cmd_explain)
