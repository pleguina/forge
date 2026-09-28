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


def build_parser() -> argparse.ArgumentParser:
    """Construct the full `forge` argument parser (all groups registered).

    Factored out of `main()` so tests can introspect the real, live command
    tree instead of duplicating it by hand.
    """
    parser = argparse.ArgumentParser(
        prog="forge",
        description=(
            "FORGE framework CLI — topology generation, HLS build, "
            "verification orchestration, and performance analysis"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Start here — the golden path, in the order you need it:
  init        Scaffold a plugin and run validate -> build -> test -> report
  adopt       Scan an existing repository and create a project from what is in it
  check       Report whether this project is completely and consistently described
  next        Say what to do next, and why
  connect     Declare which producer drives which consumer
  explain     Explain one FORGE decision — a connection, an inference, a diagnostic
  fix         Apply the repairs this project's own sources prove
  migrate     Bring a project up to the schemas this FORGE understands
  inspect     Resolve a design into the canonical IR and read it back (read-only)
  build       Generate the structural top level from a design
  test        Prepare and run a design's verification flows
  report      Collect topology, latency and results into one report bundle
  doctor      Check the local forge install/environment

Starting from nothing, `forge init my_plugin` takes an empty directory to a
generated top level, a passing simulation and an HTML dashboard in one
command. Starting from a repository you already have, `forge adopt .` reads
it and builds the project model from its own sources — then `forge check`
and `forge next` tell you what is left to decide.

Direct stage access — the individual stages the commands above orchestrate.
Reach for these when you need a flag or a stage the golden path doesn't expose:
  topgen      Topology generation, validation, linting, migration helpers
  contract    Author interface contracts from a module's real ports
  verify      Verification generate/prepare/run/doctor/release-check
  hls         Vitis HLS TCL generation and parallel job execution
  analyze     HLS reports, latency checks, result plots, dashboards
  framework   External framework import and detector I/O resolution
  core        Shared schema, CLI, and plugin utilities

Exit codes and --json output shape are documented once, for the whole
CLI, in docs/development/cli_exit_codes.md: 0 = pass (or warn without
--strict), 1 = fail (or warn with --strict), 2 = a usage error or
unexpected internal exception (never a real finding). Every --json-
supporting command emits the same {schema_version, status, diagnostics,
artifacts, metrics, next_actions} envelope shape.

Examples — the golden path:
  forge init my_plugin
  forge adopt .
  forge check
  forge next
  forge explain ATG037
  forge fix --apply
  forge migrate
  forge doctor
  forge inspect design.yml --contracts-from modules.yml
  forge build design.yml --contracts-from modules.yml --apply --output algo_top.v
  forge test run design.verification.yml --flow my_flow_xsim --event-id 0
  forge report design.yml --contracts-from modules.yml --output out/report

Examples — direct stage access:
  forge inspect design.yml --emit-ir build/forge/design.ir.json
  forge build design.yml --accept-plan-hash <hash> --json
  forge topgen gen-top design.yml --mode verilog --output algo_top.v
  forge topgen validate design.yml
  forge contract infer my_module --contracts-from modules.yml -o m.interface.yaml
  forge hls gen-tcl --hls-config catalog.yml
  forge hls run --registry catalog.yml --stages csim,synth --jobs 4
  forge verify generate plugins/my_plugin/verify/design.verification.yml
  forge verify run plugins/my_plugin/verify/hit_decoder_xsim/verify.flow.yml --plugin my_plugin
  forge verify doctor plugins/my_plugin/verify/design.verification.yml
  forge core resources
  forge core verify-contract --ip-info ip_info.yaml --contract ip_interface.yaml
  forge analyze hls-report --hls-build-root build_hls --output out/reports
  forge analyze latency-check design.yml --contracts-from modules.yml
  forge analyze dashboard --input out/reports --output out/dashboard
  forge framework import --provider blobfish --abi payload_abi.json --endpoints payload_endpoints.json --out out/
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
        metavar="GROUP",
        help=(
            "Golden path: init | adopt | check | next | connect | explain | fix | "
            "migrate | "
            "inspect | build | test | report | doctor. Direct stage access: "
            "topgen | contract | verify | hls | analyze | framework | core."
        ),
    )

    # Golden path first — argparse lists subcommands in registration order,
    # so this is what a newcomer reads before anything else.
    init.register(sub)
    adopt.register(sub)
    check.register(sub)
    next_step.register(sub)
    connect.register(sub)
    explain.register(sub)
    fix.register(sub)
    migrate.register(sub)
    inspect.register(sub)
    build.register(sub)
    test.register(sub)
    report.register(sub)
    doctor.register(sub)
    # Direct stage access.
    topgen.register(sub)
    contract.register(sub)
    verify.register(sub)
    hls.register(sub)
    analyze.register(sub)
    framework.register(sub)
    core.register(sub)

    return parser


def main() -> None:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    _shared.set_debug(bool(getattr(args, "debug", False)))

    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        _shared.print_cli_error(
            "Unexpected error",
            exc,
            hint=(
                "Check the command arguments and input files, "
                "or re-run with --debug to capture traceback details."
            ),
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
