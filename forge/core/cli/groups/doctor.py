"""forge doctor — top-level environment/toolchain health check.

Unlike ``forge verify doctor`` (which checks one plugin's flow artifacts
against a specific design.verification.yml), this is a plugin-agnostic
check of whether the *installation itself* is sane: is the right Python
version in use, are the optional simulator/toolchain binaries on PATH,
are the optional Python extras importable, and do the framework's own
resource files resolve. Nothing here is fatal by default — everything
checked is an optional extra — so new users discover missing tools up
front instead of hitting a failure deep inside whatever command needed
them.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys


def _check_status(ok: bool, *, required: bool = False) -> str:
    if ok:
        return "ok"
    return "missing (required)" if required else "missing (optional)"


def _run_checks() -> dict:
    from forge import __version__ as forge_version
    from forge.core.cli.groups.core import resolve_framework_resources
    from forge.verify.rtl_introspection import _pyverilog_available

    checks: dict[str, dict[str, object]] = {}

    checks["forge_version"] = {"status": "ok", "detail": forge_version}
    checks["python_version"] = {
        "status": "ok" if sys.version_info >= (3, 9) else "missing (optional)",
        "detail": ".".join(str(v) for v in sys.version_info[:3]),
    }

    for tool in ("xvlog", "xelab", "xsim", "ghdl", "verilator"):
        found = shutil.which(tool)
        checks[f"tool:{tool}"] = {
            "status": _check_status(found is not None),
            "detail": found or "not on PATH",
        }

    checks["python:pyverilog"] = {
        "status": _check_status(_pyverilog_available()),
        "detail": "parser mode available" if _pyverilog_available() else "regex fallback active",
    }
    for module in ("matplotlib", "numpy", "networkx"):
        found = importlib.util.find_spec(module) is not None
        checks[f"python:{module}"] = {
            "status": _check_status(found),
            "detail": "importable" if found else "not installed",
        }

    for key, resource in resolve_framework_resources().items():
        checks[f"resource:{key}"] = {
            "status": _check_status(bool(resource["exists"])),
            "detail": resource["path"] or "not found",
        }

    return checks


def cmd_doctor(args):
    """Report the health of the local `forge` installation/environment."""
    checks = _run_checks()
    any_required_missing = any(c["status"] == "missing (required)" for c in checks.values())

    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(
            {"checks": checks, "ok": not any_required_missing}, indent=2,
        ))
    else:
        print("forge doctor — environment/toolchain health check\n")
        icon = {"ok": "✅", "missing (optional)": "⚠️ ", "missing (required)": "❌"}
        for name, c in checks.items():
            print(f"  {icon[c['status']]} {name:28s} {c['status']:20s} {c['detail']}")
        print()
        if any_required_missing:
            print("❌ One or more required checks failed.")
        else:
            print("✅ No required checks failed. (Warnings above are optional extras.)")

    if getattr(args, "strict", False) and any_required_missing:
        sys.exit(1)
    sys.exit(0)


def register(sub) -> None:
    """Register the top-level ``forge doctor`` command."""
    p_doctor = sub.add_parser(
        "doctor",
        help="Check the local forge install/environment (toolchains, optional deps)",
    )
    p_doctor.add_argument(
        "--json", action="store_true", default=False,
        help="Machine-readable JSON output instead of the human report.",
    )
    p_doctor.add_argument(
        "--strict", action="store_true", default=False,
        help="Exit 1 if any required check fails (currently all checks are optional extras).",
    )
    p_doctor.set_defaults(func=cmd_doctor)
