"""forge CLI entry point.

This module is intentionally thin — it wires together the group-level
sub-parsers defined in forge.core.cli.groups.* and handles top-level flags
(--version, --debug) before dispatching to the chosen command.
"""

from __future__ import annotations

import argparse
import sys

from forge import __version__
from forge.core.cli.groups import (
    contract, core, topgen, hls, verify, analyze, framework, doctor, inspect,
    build, test, report, init, adopt, check, next_step, connect, explain, fix, migrate,
)
from forge.core.cli import _shared


#: The everyday commands, listed first by `forge --help`.
PRIMARY_COMMANDS = ("init", "adopt", "check", "build", "test", "report", "inspect", "explain")
#: Public too, listed after them.
SECONDARY_COMMANDS = ("next", "fix", "connect", "migrate", "doctor")
#: Stage-level groups, under `forge dev`. Their old top-level names still
#: parse (with a notice from main()) but appear in no listing.
DEV_GROUPS = ("topgen", "contract", "verify", "hls", "analyze", "framework", "core")

_GROUP_MODULES = {
    "init": init, "adopt": adopt, "check": check, "build": build, "test": test,
    "report": report, "inspect": inspect, "explain": explain,
    "next": next_step, "fix": fix, "connect": connect, "migrate": migrate, "doctor": doctor,
    "topgen": topgen, "contract": contract, "verify": verify, "hls": hls,
    "analyze": analyze, "framework": framework, "core": core,
}


def build_parser() -> argparse.ArgumentParser:
    """Construct the full `forge` argument parser (all groups registered).

    Factored out of `main()` so tests can introspect the real, live command
    tree instead of duplicating it by hand.
    """
    parser = argparse.ArgumentParser(
        prog="forge",
        description=(
            "FORGE — generate the structural top level of an FPGA design from its "
            "modules, balance its pipeline latencies, and simulate it"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands:
  init        Scaffold a plugin, write forge.yml, and run build -> test -> report
  adopt       Scan an existing repository and create forge.yml from what is in it
  check       Report what is missing or inconsistent, and whether the tools are here
  build       Generate the structural top level
  test        Build the design and run its verification flows
  report      Collect topology, latency and results into one report bundle
  inspect     Resolve a design into the canonical IR and read it back (read-only)
  explain     Explain one FORGE decision — a connection, an inference, a diagnostic

Also:
  next        Say what to do next, and why
  fix         Apply the repairs this project's own sources prove
  connect     Declare which producer drives which consumer
  migrate     Bring a project up to the schemas this FORGE understands
  doctor      Check the forge install and the simulators on PATH

Stage-level:
  dev         topgen, contract, verify, hls, analyze, framework, core

Inside a forge.yml project, check, build, test, report and inspect need no arguments.
`forge dev --help` lists the stage-level groups; their old top-level names
(`forge topgen ...`) still work.

Exit codes: 0 = pass (or warn without --strict), 1 = fail (or warn with
--strict), 2 = a usage error or an internal exception. Every --json command
emits the same {schema_version, status, diagnostics, artifacts, metrics,
next_actions} envelope (docs/development/cli_exit_codes.md).

Examples:
  forge init my_plugin
  forge adopt .
  forge check
  forge build
  forge test
  forge report
  forge explain ATG037
  forge build design.yml --contracts-from modules.yml --output algo_top.v
  forge dev topgen gen-top design.yml --mode verilog --output algo_top.v
  forge dev analyze latency-check design.yml --contracts-from modules.yml
        """,
    )

    parser.add_argument("--version", action="version", version=f"forge {__version__}")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Print Python tracebacks for unexpected failures.",
    )

    sub = parser.add_subparsers(
        dest="group",
        required=True,
        metavar="COMMAND",
        help=(
            "init | adopt | check | build | test | report | inspect | explain; also "
            "next | fix | connect | migrate | doctor; and dev "
            "(topgen | contract | verify | hls | analyze | framework | core)."
        ),
    )
    for name in PRIMARY_COMMANDS + SECONDARY_COMMANDS:
        _GROUP_MODULES[name].register(sub)

    dev = sub.add_parser(
        "dev",
        help="Stage-level commands behind the ones above",
        description=(
            "The individual stages build, test and report orchestrate, for a flag or a "
            "stage the everyday commands do not expose."
        ),
    )
    dev_sub = dev.add_subparsers(dest="dev_group", required=True, metavar="GROUP")
    for name in DEV_GROUPS:
        _GROUP_MODULES[name].register(dev_sub)
    for name in DEV_GROUPS:
        _GROUP_MODULES[name].register(sub)

    # The epilog is the one listing, so argparse's own is dropped; every
    # name still parses.
    sub._choices_actions = []
    return parser


def main() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    _shared.set_debug(bool(getattr(args, "debug", False)))
    if args.group in DEV_GROUPS:
        print(
            f"note: `forge {args.group}` is now `forge dev {args.group}`; the old name still works",
            file=sys.stderr,
        )

    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(_shared.report_command_error(exc))


if __name__ == "__main__":
    main()
