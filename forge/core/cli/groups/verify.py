"""forge verify — simulation and verification orchestration.

Delegates all sub-commands transparently to :mod:`forge.verify.__main__`.
"""

from __future__ import annotations

import argparse
import sys


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _dispatch_fw_verify(remaining: list) -> None:
    """Delegate ``forge verify <subcommand> [args]`` to the active verify runtime.

    Prefer the in-repo ``forge.verify`` implementation so workspace fixes take
    effect immediately.  Fall back to the installed ``fw_verify`` package for
    environments that still depend on the split runtime.
    """
    try:
        from forge.verify.__main__ import main as _fw_verify_main  # type: ignore[import]
    except ImportError:
        from fw_verify.__main__ import main as _fw_verify_main  # type: ignore[import]

    old_argv = sys.argv[:]
    sys.argv = ["forge verify"] + list(remaining)
    try:
        _fw_verify_main()
    except SystemExit:
        raise
    finally:
        sys.argv = old_argv


def cmd_verify_dispatch(args):
    _dispatch_fw_verify(args.verify_args or [])


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``forge verify`` subparser."""
    p_verify = sub.add_parser(
        "verify",
        help="Run simulations and check correctness",
        description=(
            "Simulation and verification commands.\n\n"
            "Available sub-commands:\n"
            "  generate      Generate verify.flow.yml, tb_*.sv, wave.tcl\n"
            "  run           Execute the full verification lifecycle\n"
            "  preflight     Run DUT artifact preflight checks\n"
            "  prepare       Generate + validate + check layout in one step\n"
            "  doctor        Comprehensive read-only health check\n"
            "  release-check Release-readiness gate (RC-01 … RC-10)\n"
            "  init-plugin   Scaffold a new plugin skeleton\n\n"
            "Pass --help after any sub-command to see its options:\n"
            "  forge verify run --help"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  forge verify generate plugins/my_plugin/verify/design.verification.yml\n"
            "  forge verify run plugins/my_plugin/verify/hit_decoder_xsim/verify.flow.yml"
            " --plugin my_plugin\n"
            "  forge verify doctor plugins/my_plugin/verify/design.verification.yml\n"
            "  forge verify release-check plugins/my_plugin/verify/design.verification.yml\n"
            "  forge verify init-plugin my_trigger\n"
        ),
    )
    p_verify.add_argument("verify_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p_verify.set_defaults(func=cmd_verify_dispatch)
