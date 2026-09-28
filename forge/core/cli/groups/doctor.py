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
import sys


def _check_status(ok: bool, *, required: bool = False) -> str:
    if ok:
        return "ok"
    return "missing (required)" if required else "missing (optional)"


def _run_checks() -> dict:
    from forge import __version__ as forge_version
    from forge.core.cli.groups.core import resolve_framework_resources
    from forge.core.toolchain_versions import tool_present
    from forge.verification.rtl_introspection import _pyverilog_available

    checks: dict[str, dict[str, object]] = {}

    checks["forge_version"] = {"status": "ok", "detail": forge_version}
    checks["python_version"] = {
        "status": "ok" if sys.version_info >= (3, 9) else "missing (optional)",
        "detail": ".".join(str(v) for v in sys.version_info[:3]),
    }

    # tool_present is the same shared presence check `forge verify doctor`
    # (verify/__main__.py) uses for xvlog/xelab/xsim — so both doctor
    # commands agree on tool availability for the same environment.
    for tool in ("xvlog", "xelab", "xsim", "ghdl", "verilator"):
        found = tool_present(tool)
        checks[f"tool:{tool}"] = {
            "status": _check_status(found),
            "detail": "on PATH" if found else "not on PATH",
        }

    # Vitis HLS is not required for anything FORGE does without it, but
    # *which* release is on PATH decides whether its HLS port predictions
    # have ever been validated — so report the version's status rather than
    # only its presence. An untested release is stated as untested; nothing
    # here implies stability for a version nobody has checked.
    from forge.hls import tool_matrix

    vitis_version = tool_matrix.installed_version()
    vitis_status = tool_matrix.status_for(vitis_version)
    checks["tool:vitis_hls"] = {
        "status": "ok" if vitis_version else "missing (optional)",
        "detail": (
            f"{vitis_version} — HLS port prediction {vitis_status}: "
            f"{tool_matrix.describe_status(vitis_status)}"
            if vitis_version else "not on PATH"
        ),
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


def _checks_to_envelope(checks: dict):
    """Reshape `_run_checks()`'s `{name: {status, detail}}` dict into a
    `CommandEnvelope` via the same `from_diagnostic_report` bridge every
    other envelope-adopting command uses —
    not a third bespoke JSON shape. Every check here is currently an
    optional extra (never `required=True`, see this module's docstring),
    so a missing one is a WARNING, not an ERROR; the `status`/`required`
    split stays in `_check_status` for forward compatibility if a future
    check ever needs to be mandatory."""
    from forge.core.cli.envelope import from_diagnostic_report
    from forge.verification.diagnostics import DiagnosticReport

    report = DiagnosticReport(label="forge doctor")
    for name, c in checks.items():
        message = f"{name}: {c['detail']}"
        if c["status"] == "ok":
            report.note("", message)
        elif c["status"] == "missing (required)":
            report.error("", message, action=f"Install/configure {name} (required)")
        else:
            report.warn("", message, action=f"Install/configure {name} if you need this feature")
    return from_diagnostic_report(report, metrics={"checks": checks})


def cmd_doctor(args):
    """Report the health of the local `forge` installation/environment."""
    from forge.core.cli.envelope import emit

    json_mode = getattr(args, "json", False)
    strict = getattr(args, "strict", False)
    checks = _run_checks()
    envelope = _checks_to_envelope(checks)

    if json_mode:
        sys.exit(emit(envelope, json_mode=True, strict=strict))

    print("forge doctor — environment/toolchain health check\n")
    icon = {"ok": "✅", "missing (optional)": "⚠️ ", "missing (required)": "❌"}
    for name, c in checks.items():
        print(f"  {icon[c['status']]} {name:28s} {c['status']:20s} {c['detail']}")
    print()
    if envelope.status == "fail":
        print("❌ One or more required checks failed.")
    else:
        print("✅ No required checks failed. (Warnings above are optional extras.)")

    sys.exit(envelope.exit_code(strict=strict))


def register(sub) -> None:
    """Register the top-level ``forge doctor`` command.

    Containment note: this checks the
    *installation* (toolchains on PATH, optional Python extras, framework
    resource files) — plugin-agnostic. ``forge verify doctor`` checks one
    plugin's flow artifacts against a specific ``design.verification.yml``
    and is authoritative for that. ``forge verify preflight`` is a real
    subset of ``forge verify doctor``'s checks kept as a separate command
    (not merged) — it stays intentionally narrower and faster for use in
    tight verify-iteration loops.
    """
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
        help="Exit 1 if any check is missing, including optional extras "
             "(see docs/development/cli_exit_codes.md).",
    )
    p_doctor.set_defaults(func=cmd_doctor)
