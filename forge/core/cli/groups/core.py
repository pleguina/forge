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
    from forge.core.cli.envelope import CommandEnvelope, emit

    json_mode = getattr(args, "json", False) or getattr(args, "format", None) == "json"
    resources = resolve_framework_resources()

    if getattr(args, "key", None):
        value = resources.get(args.key)
        if value is None:
            envelope = CommandEnvelope(
                status="error",
                diagnostics=[{
                    "severity": "error",
                    "message": f"unknown resource key '{args.key}'. Available keys: {', '.join(resources)}",
                }],
            )
            if json_mode:
                sys.exit(emit(envelope, json_mode=True))
            print(
                f"ERROR: unknown resource key '{args.key}'. "
                f"Available keys: {', '.join(resources)}",
                file=sys.stderr,
            )
            sys.exit(envelope.exit_code())
        if json_mode:
            envelope = CommandEnvelope(status="pass", metrics={"resources": {args.key: value}})
            sys.exit(emit(envelope, json_mode=True))
        print(value["path"])
        return

    missing = [k for k, v in resources.items() if not v["exists"]]
    status = "warn" if missing else "pass"
    envelope = CommandEnvelope(
        status=status,
        diagnostics=[
            {"severity": "warning", "message": f"resource {k!r} not found"} for k in missing
        ],
        metrics={"resources": resources},
    )

    if json_mode:
        # --format json is this command's original (pre-envelope) JSON
        # flag; --json (added alongside the sweep, release-plan Phase 6
        # §6.7) is the standard envelope flag. Both now produce the same
        # envelope shape.
        sys.exit(emit(envelope, json_mode=True))
    for k, v in resources.items():
        marker = "" if v["exists"] else "  (missing)"
        print(f"{k}={v['path']}{marker}")
    sys.exit(envelope.exit_code())


def cmd_verify_contract(args):
    """Verify one or more ip_interface.yaml contracts against ip_info.yaml."""
    from forge.core.cli.envelope import CommandEnvelope, emit

    json_mode = getattr(args, "json", False)

    def _usage_error(msg: str) -> None:
        envelope = CommandEnvelope(status="error", diagnostics=[{"severity": "error", "message": msg}])
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print(f"❌ {msg}", file=sys.stderr)
        sys.exit(envelope.exit_code())

    try:
        from forge.topgen.ip.contract_verifier import ContractVerifier, verify_all
    except ImportError as exc:
        _usage_error(f"Cannot import contract verifier: {exc}")
        return

    ip_info_path = Path(args.ip_info)
    if not ip_info_path.exists():
        _usage_error(f"ip_info file not found: {ip_info_path}")

    verbose = getattr(args, "verbose", False)

    def _issue_to_diagnostic(issue, *, contract_path: Path, module_name: str) -> dict:
        return {
            "severity": issue.severity, "message": issue.message, "code": None,
            "category": issue.role, "path": str(contract_path), "module": module_name,
        }

    try:
        # ── Single-contract mode ──────────────────────────────────────────
        if args.contract:
            contract_path = Path(args.contract)
            if not contract_path.exists():
                _usage_error(f"Contract file not found: {contract_path}")
            verifier = ContractVerifier(ip_info_path, contract_path)
            result = verifier.verify()
            if not json_mode:
                result.print_report(verbose=verbose)

            diagnostics = [
                _issue_to_diagnostic(i, contract_path=result.contract_path, module_name=result.module_name)
                for i in result.issues
            ]
            status = "fail" if result.errors else ("warn" if result.warnings else "pass")
            envelope = CommandEnvelope(status=status, diagnostics=diagnostics)
            sys.exit(emit(envelope, json_mode=json_mode))

        # ── All-contracts mode ────────────────────────────────────────────
        search_dirs = [Path(d) for d in (args.all_contracts or [])]
        if not search_dirs:
            _usage_error("Provide --contract <file> or --all-contracts <dir> [<dir> ...]")

        if json_mode:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                results = verify_all(ip_info_path, search_dirs, verbose=verbose)
        else:
            results = verify_all(ip_info_path, search_dirs, verbose=verbose)

        if not results:
            envelope = CommandEnvelope(
                status="fail",
                diagnostics=[{
                    "severity": "error",
                    "message": f"No contracts found under: {[str(d) for d in search_dirs]}",
                }],
            )
            sys.exit(emit(envelope, json_mode=json_mode))

        diagnostics = [
            _issue_to_diagnostic(i, contract_path=r.contract_path, module_name=r.module_name)
            for r in results for i in r.issues
        ]
        n_pass = sum(1 for r in results if r.passed)
        n_warn = sum(1 for r in results if r.passed and r.warnings)
        n_fail = sum(1 for r in results if not r.passed)
        status = "fail" if n_fail else ("warn" if n_warn else "pass")

        if not json_mode:
            print()
            print(
                f"Summary: {len(results)} contracts checked — "
                f"{n_pass} pass ({n_warn} with warnings), {n_fail} fail"
            )

        envelope = CommandEnvelope(
            status=status, diagnostics=diagnostics,
            metrics={"contracts_checked": len(results), "pass": n_pass, "warn": n_warn, "fail": n_fail},
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    except SystemExit:
        raise
    except Exception as exc:
        envelope = CommandEnvelope(status="error", diagnostics=[{"severity": "error", "message": str(exc)}])
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print(f"❌ Contract verification failed: {exc}", file=sys.stderr)
        from forge.core.cli._shared import debug_enabled
        import traceback as _tb
        if debug_enabled():
            _tb.print_exc(file=sys.stderr)
        else:
            print("   Re-run with --debug for traceback details.", file=sys.stderr)
        sys.exit(envelope.exit_code())


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
        help="Output format (default: text) — deprecated alias for --json",
    )
    p_resources.add_argument(
        "--json", action="store_true", default=False, help="Machine-readable JSON output",
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
    p_vc.add_argument(
        "--json", action="store_true", default=False, help="Machine-readable JSON output",
    )
    p_vc.set_defaults(func=cmd_verify_contract)
