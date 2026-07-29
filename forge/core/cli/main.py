"""forge CLI entry point.

This module is intentionally thin — it wires together the group-level
sub-parsers defined in forge.core.cli.groups.* and handles top-level flags
(--version, --debug) before dispatching to the chosen command.
"""

from __future__ import annotations

import argparse
import sys

from forge import __version__
from forge.core.cli.groups import core, topgen, hls, verify, analyze, framework, doctor, inspect, build
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
        epilog="""Groups:
  core      Shared schema, CLI, and plugin utilities
  topgen    Generate hardware topology structure
  hls       Build HLS modules and IPs
  verify    Run simulations and check correctness
  analyze   Performance analysis, latency checks, and reporting
  framework External framework import and detector I/O resolution
  doctor    Check the local forge install/environment (not a group — a single command)
  inspect   Resolve a design into the canonical IR and inspect it (not a group — a single command)
  build     Compute (and optionally apply) a deterministic generation plan (not a group — a single command)

Examples:
  forge doctor
  forge inspect design.yml --contracts-from modules.yml
  forge inspect design.yml --json
  forge inspect design.yml --emit-ir build/forge/design.ir.json
  forge build design.yml --contracts-from modules.yml --plan
  forge build design.yml --contracts-from modules.yml --apply --output algo_top.v
  forge build design.yml --accept-plan-hash <hash> --json
  forge topgen gen-top design.yml --mode verilog --output algo_top.v
  forge topgen validate design.yml
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
        help="Command group (core | topgen | hls | verify | analyze | framework | doctor | inspect | build)",
    )

    core.register(sub)
    topgen.register(sub)
    hls.register(sub)
    verify.register(sub)
    analyze.register(sub)
    framework.register(sub)
    doctor.register(sub)
    inspect.register(sub)
    build.register(sub)

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
