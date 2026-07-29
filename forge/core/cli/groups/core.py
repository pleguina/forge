"""forge core — shared schema, CLI, and plugin utilities."""

from __future__ import annotations

import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Resource path resolution (shared with `forge doctor`)
# ---------------------------------------------------------------------------

def _walk_up_for(name: str, *, is_dir: bool) -> Path | None:
    """Walk up from cwd and this file's location looking for *name*.

    Used for repo-root artifacts that live *outside* the installed
    ``forge`` package (only present in a source checkout of the framework
    repo, e.g. ``docs/`` and ``normalized_signal_families.yaml``).
    """
    for start in (Path.cwd(), Path(__file__).resolve()):
        p = start
        for _ in range(15):
            candidate = p / name
            if (candidate.is_dir() if is_dir else candidate.is_file()):
                return candidate
            if p.parent == p:
                break
            p = p.parent
    return None


def resolve_framework_resources() -> "dict[str, dict[str, object]]":
    """Return ``{key: {"path": str, "exists": bool}}`` for known framework
    resource files/directories.

    Shared by ``forge core resources`` and ``forge doctor``.
    """
    import forge.hls as _hls
    import forge.topgen.ip as _topgen_ip

    hls_templates = Path(_hls.__file__).parent / "templates"
    canonical_roles = Path(_topgen_ip.__file__).parent / "canonical_roles.yaml"
    # These two are framework-repo-root artifacts, not installed package
    # resources — only resolvable in a source checkout of the framework
    # repo (sibling to forge/), not in a plain `pip install forge`.
    docs_dir = _walk_up_for("docs", is_dir=True)
    signal_families = _walk_up_for("normalized_signal_families.yaml", is_dir=False)

    resources: dict[str, dict[str, object]] = {
        "hls_templates": {"path": str(hls_templates), "exists": hls_templates.is_dir()},
        "canonical_roles": {"path": str(canonical_roles), "exists": canonical_roles.is_file()},
        "normalized_signal_families": {
            "path": str(signal_families) if signal_families else None,
            "exists": signal_families is not None,
        },
        "docs_root": {
            "path": str(docs_dir) if docs_dir else None,
            "exists": docs_dir is not None,
        },
    }
    return resources


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_resources(args):
    """Print installed framework resource paths."""
    import json as _json

    resources = resolve_framework_resources()

    if getattr(args, "key", None):
        value = resources.get(args.key)
        if value is None:
            print(
                f"ERROR: unknown resource key '{args.key}'. "
                f"Available keys: {', '.join(resources)}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(value["path"])
        return

    if getattr(args, "format", None) == "json":
        print(_json.dumps(resources, indent=2))
    else:
        for k, v in resources.items():
            marker = "" if v["exists"] else "  (missing)"
            print(f"{k}={v['path']}{marker}")


def cmd_verify_contract(args):
    """Verify one or more ip_interface.yaml contracts against ip_info.yaml."""
    try:
        from forge.topgen.ip.contract_verifier import (
            ContractVerifier,
            verify_all,
        )
    except ImportError as exc:
        print(f"❌ Cannot import contract verifier: {exc}", file=sys.stderr)
        sys.exit(2)

    ip_info_path = Path(args.ip_info)
    if not ip_info_path.exists():
        print(f"❌ ip_info file not found: {ip_info_path}", file=sys.stderr)
        sys.exit(2)

    verbose = getattr(args, "verbose", False)

    try:
        # ── Single-contract mode ──────────────────────────────────────────
        if args.contract:
            contract_path = Path(args.contract)
            if not contract_path.exists():
                print(f"❌ Contract file not found: {contract_path}", file=sys.stderr)
                sys.exit(2)
            verifier = ContractVerifier(ip_info_path, contract_path)
            result = verifier.verify()
            result.print_report(verbose=verbose)
            sys.exit(result.exit_code())

        # ── All-contracts mode ────────────────────────────────────────────
        search_dirs = [Path(d) for d in (args.all_contracts or [])]
        if not search_dirs:
            print(
                "❌ Provide --contract <file> or --all-contracts <dir> [<dir> …]",
                file=sys.stderr,
            )
            sys.exit(2)

        results = verify_all(ip_info_path, search_dirs, verbose=verbose)
        if not results:
            print("❌ No contracts found in the specified directories.", file=sys.stderr)
            sys.exit(1)

        n_pass = sum(1 for r in results if r.passed)
        n_warn = sum(1 for r in results if r.passed and r.warnings)
        n_fail = sum(1 for r in results if not r.passed)

        print()
        print(
            f"Summary: {len(results)} contracts checked — "
            f"{n_pass} pass ({n_warn} with warnings), {n_fail} fail"
        )

        if n_fail:
            sys.exit(2)
        elif n_warn:
            sys.exit(1)
        else:
            sys.exit(0)

    except SystemExit:
        raise
    except Exception as exc:
        print(f"❌ Contract verification failed: {exc}", file=sys.stderr)
        from forge.core.cli._shared import debug_enabled
        import traceback as _tb
        if debug_enabled():
            _tb.print_exc(file=sys.stderr)
        else:
            print("   Re-run with --debug for traceback details.", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def register(sub) -> None:
    """Register the ``forge core`` subparser and its commands."""
    import argparse

    p_core = sub.add_parser("core", help="Shared schema, CLI, and plugin utilities")
    core_sub = p_core.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # forge core resources
    p_resources = core_sub.add_parser(
        "resources", help="Print installed framework resource paths",
    )
    p_resources.add_argument("--key", help="Print only the value for a specific resource key")
    p_resources.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    p_resources.set_defaults(func=cmd_resources)

    # forge core verify-contract
    p_vc = core_sub.add_parser(
        "verify-contract",
        help="Verify ip_interface.yaml contract(s) against ip_info.yaml",
    )
    p_vc.add_argument(
        "--ip-info", required=True, type=Path, dest="ip_info",
        help="Path to ip_info.yaml (raw port metadata)",
    )
    p_vc.add_argument(
        "--contract", type=Path, default=None,
        help="Single ip_interface.yaml file to verify",
    )
    p_vc.add_argument(
        "--all-contracts", nargs="+", metavar="DIR", default=None,
        help="Directories to search recursively for ip_interface*.yaml files",
    )
    p_vc.add_argument(
        "--verbose", "-v", action="store_true", default=False,
        help="Print details even for passing contracts",
    )
    p_vc.set_defaults(func=cmd_verify_contract)
