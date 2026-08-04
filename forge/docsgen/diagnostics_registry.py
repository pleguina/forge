"""Diagnostic-code catalogue generator (release-plan Phase 9, Defect 13).

Diagnostic codes are emitted through several different real mechanisms in
this codebase, not one:

1. Structured construction — ``Diagnostic(code="FWV021", ...)`` /
   ``ATGDiagnostic(code="ATG007", ...)`` (``forge/verify/results.py``).
2. Convenience-method calls — ``report.note("FWV000", ...)`` /
   ``.warn(...)`` / ``.error(...)`` / ``.critical(...)`` on
   ``DiagnosticReport``/``ATGDiagnosticReport`` (``forge/verify/__main__.py``,
   ``forge/verify/release_check.py``), where the code is the first
   *positional* argument, not a keyword.
3. Raw dict literals — ``{"code": "ATG007", ...}``
   (``forge/core/cli/groups/topgen.py``), building a JSON-envelope
   diagnostic without going through either dataclass.
4. Bare ``[FWVxxx]``/``[ATGxxx]``-prefixed string literals embedded in an
   f-string or plain string — ``add_issue(f"[FWV003] ...")``
   (``forge/verify/supported_path_validator.py``, ``forge/core/stale_detection.py``).

A text grep for ``FWV\\d+``/``ATG\\d+`` would false-positive on comments,
docstrings (``forge/verify/exceptions.py`` documents FWV codes in prose
without emitting them), and tests. This module instead walks the real AST
of every production source file (``forge/``, excluding ``forge/tests/``)
and structurally recognizes all four mechanisms above, so
``check_diagnostics_registry_matches_source()`` can verify every code
*actually emitted* is present in ``DIAGNOSTICS`` — catching exactly the
kind of gap a prior investigation found for real (``FWV000``/``FWV021``/
``FWV022`` were emitted but undocumented in the old docstring tables).

The AST scanner verifies one direction only (every real emission site's
code is registered) — a documented-but-currently-unemitted code (kept for
history or a not-yet-wired-up reserved slot) is not itself a bug, matching
the plan's scoping for this registry.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import forge as _forge_pkg

_FORGE_PKG_ROOT = Path(_forge_pkg.__file__).parent

_STRUCTURED_CALLEES = {"Diagnostic", "ATGDiagnostic"}
_REPORT_METHOD_NAMES = {"note", "warn", "error", "critical"}
_CODE_PREFIX_RE = re.compile(r"^\[((?:FWV|ATG)\d+)\]")


# ── Registry ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DiagnosticDefinition:
    code: str
    family: str  # "FWV" | "ATG"
    default_severity: str
    description: str
    remediation: str


def _fwv(code: str, severity: str, description: str, remediation: str = "") -> DiagnosticDefinition:
    return DiagnosticDefinition(code, "FWV", severity, description, remediation)


def _atg(code: str, severity: str, description: str, remediation: str = "") -> DiagnosticDefinition:
    return DiagnosticDefinition(code, "ATG", severity, description, remediation)


# `default_severity` reflects the most common/documented usage. A few
# codes are genuinely emitted at more than one severity depending on call
# site (FWV004, FWV010, ATG003) — noted explicitly rather than picking one
# arbitrarily.
DIAGNOSTICS: "dict[str, DiagnosticDefinition]" = {
    d.code: d for d in [
        _fwv("FWV000", "note", "General informational health-check status note (doctor/release-check environment and artifact confirmations)."),
        _fwv("FWV001", "error", "Unsupported (kind, backend) combination.", "Use a supported kind/backend pair from the support matrix."),
        _fwv("FWV002", "error", "Experimental flow declared without explicit opt-in.", "Set `experimental: true` on the flow in design.verification.yml to acknowledge the risk."),
        _fwv("FWV003", "error", "Invalid or non-canonical layout (kind-subdir, missing directory)."),
        _fwv("FWV004", "warning or error (call-site dependent)", "Missing required framework-generated artifact (TB, verify.flow.yml, port_map.yaml, …).", "Run `forge verify generate <design.verification.yml> --flow <flow>` to (re)generate the missing artifact."),
        _fwv("FWV005", "warning", "RTL Verilog file not found."),
        _fwv("FWV006", "warning", "Parser dependency (pyverilog) not installed — regex fallback used.", "pip install -e 'forge[parser]'"),
        _fwv("FWV007", "error", "Parser mode failed on the given RTL."),
        _fwv("FWV008", "error", "Regex fallback extracted zero ports — dangerous failure mode."),
        _fwv("FWV009", "warning", "Zero ports found in an existing port_map.yaml."),
        _fwv("FWV010", "error or warning (call-site dependent)", "Stimulus contract failure (missing task, $finish, …).", "Run `python <tools/gen_stimulus.py> --flow <flow>` to regenerate stimulus."),
        _fwv("FWV011", "error", "Backend pre-execution validation failure."),
        _fwv("FWV012", "error", "Required simulator tool not on PATH."),
        _fwv("FWV013", "error", "Simulator subprocess non-zero exit."),
        _fwv("FWV014", "error", "Plugin bootstrap not importable, or missing a bootstrap() function."),
        _fwv("FWV015", "error", "Design contract parse or schema error."),
        _fwv("FWV016", "warning", "Stale generated artifact (older than its source contract/RTL)."),
        _fwv("FWV017", "warning", "Missing stimulus file — the user generates this later.", "Run `python <tools/gen_stimulus.py> --flow <flow>`."),
        _fwv("FWV018", "error", "Missing csim testbench binary."),
        _fwv("FWV019", "error", "bootstrap.py not found.", "Re-run `forge verify init-plugin` or restore tools/bootstrap.py."),
        _fwv("FWV020", "warning", "gen_stimulus.py not found (warning for xsim flows)."),
        _fwv("FWV021", "error", "An event's compile or elaborate stage failed (real ExecutionStage tagged)."),
        _fwv("FWV022", "error", "Checker rejected the result even though the simulator itself exited 0."),

        _atg("ATG001", "error", "Missing design contract (design.yml not found / not loadable)."),
        _atg("ATG002", "error", "Ambiguous topology — multiple modules match the same port."),
        _atg("ATG003", "warning or error (strict-mode dependent)", "Unsupported wiring type (rpc, compat_mode in a strict context)."),
        _atg("ATG004", "error", "Missing partition, or no contract for a module."),
        _atg("ATG005", "warning", "Open output port — no connection described."),
        _atg("ATG006", "warning", "Stale ip_info.yaml (older than design.yml or its IP sources)."),
        _atg("ATG007", "warning", "Stale generated artifacts (algo_top.v older than design.yml).", "Re-run `forge topgen gen-top` to rebuild."),
        _atg("ATG008", "error", "Registry / modules.yml not found or invalid."),
        _atg("ATG009", "warning", "Non-strict topology in use (compat_mode or auto-match)."),
        _atg("ATG010", "warning", "Port-range wiring in use (non-canonical)."),
        _atg("ATG011", "error", "Hash mismatch — port_signature.json differs from the generated port_map."),
        _atg("ATG012", "warning", "gen-top --strict flag not set (risk of silent topology drift)."),
        _atg("ATG013", "warning", "Missing maturity_report.json (release gate not generated)."),
        _atg("ATG014", "warning", "Missing port_signature.json (cross-hash unavailable)."),
        _atg("ATG015", "error", "Configuration schema error (required field missing or wrong type)."),
        _atg("ATG016", "warning", "RTL resource root unset (src: ${TOPGEN_RTL_RESOURCE_ROOT} used)."),
        _atg("ATG017", "warning", "Testbench-generation warnings (auto-generated TB used as-is)."),
        _atg("ATG018", "warning", "HLS metrics JSON missing (release maturity data unavailable)."),
        _atg("ATG019", "warning", "Output format differs from topology A canonical (BD / VHDL only)."),
        _atg("ATG020", "error", "General internal / unexpected error."),
        _atg("ATG021", "error", "Unknown/unsupported cdc.kind value."),
        _atg("ATG022", "error", "Missing or invalid kind-specific cdc field (min_spacing_cycles, depth)."),
        _atg("ATG023", "error", "Undeclared clock-domain crossing."),
        _atg("ATG024", "error", "Undeclared reset-domain crossing."),
        _atg("ATG025", "error", "Invalid reset_domains.*.sync value."),
        _atg("ATG026", "error", "async_fifo depth not a power of two."),
        _atg("ATG027", "error", "Conflicting cdc kinds declared between the same module pair."),
    ]
}


# ── AST scanning ──────────────────────────────────────────────────────────

def iter_production_python_files() -> "Iterator[Path]":
    """Every ``.py`` file under the installed ``forge`` package, excluding
    ``forge/tests/`` (a prior FWV000/021/022 investigation found exactly
    the false-negative a text grep over tests/docstrings would produce —
    this is the fix)."""
    for path in sorted(_FORGE_PKG_ROOT.rglob("*.py")):
        if "tests" in path.relative_to(_FORGE_PKG_ROOT).parts:
            continue
        yield path


def _callee_name(node: ast.Call) -> "str | None":
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_const(node: "ast.AST | None") -> "str | None":
    # `ast.Dict.keys` entries are `None` for a `**spread` item (e.g.
    # `{**other, "a": 1}`) — a real possibility this scanner must tolerate,
    # not just a defensive-programming nicety.
    if node is None:
        return None
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def find_diagnostic_codes_in_file(path: Path) -> "set[str]":
    """Structurally find every diagnostic code emitted in *path*, across
    all four real mechanisms this codebase uses (see module docstring)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: "set[str]" = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _callee_name(node)

            # Mechanism 1: Diagnostic(code="...")/ATGDiagnostic(code="...")
            if name in _STRUCTURED_CALLEES:
                for kw in node.keywords:
                    if kw.arg == "code":
                        val = _string_const(kw.value)
                        if val:
                            found.add(val)

            # Mechanism 2: report.note("FWV000", ...) / .warn / .error / .critical
            if isinstance(node.func, ast.Attribute) and node.func.attr in _REPORT_METHOD_NAMES:
                if node.args:
                    val = _string_const(node.args[0])
                    if val:
                        found.add(val)

        # Mechanism 3: {"code": "ATG007", ...} dict literal
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _string_const(key) == "code":
                    val = _string_const(value)
                    if val:
                        found.add(val)

        # Mechanism 4: a bare "[FWVxxx] ..." / f"[ATGxxx] ..." literal
        text: "str | None" = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            text = _string_const(first)
        if text is not None:
            m = _CODE_PREFIX_RE.match(text)
            if m:
                found.add(m.group(1))

    return found


def find_all_emitted_diagnostic_codes() -> "set[str]":
    found: "set[str]" = set()
    for path in iter_production_python_files():
        found |= find_diagnostic_codes_in_file(path)
    return found


def check_diagnostics_registry_matches_source() -> "list[str]":
    """Return a list of human-readable problems, empty if every code
    actually emitted in production source is present in ``DIAGNOSTICS``."""
    emitted = find_all_emitted_diagnostic_codes()
    missing = sorted(emitted - set(DIAGNOSTICS))
    return [
        f"code {code!r} is emitted in production source but missing from "
        f"DIAGNOSTICS"
        for code in missing
    ]


def generate_diagnostics_page() -> str:
    problems = check_diagnostics_registry_matches_source()
    if problems:
        raise RuntimeError(
            "DIAGNOSTICS registry is out of sync with emitted codes:\n  "
            + "\n  ".join(problems)
        )

    lines = [
        "# Diagnostic Catalogue",
        "",
        "<!-- Generated by `python -m forge.docsgen`. Do not edit directly. -->",
        "",
        "Every `FWVxxx`/`ATGxxx` diagnostic code FORGE can emit. Generated",
        "from a typed registry (`forge.docsgen.diagnostics_registry.DIAGNOSTICS`)",
        "that is checked, at generation time, against an AST scan of every",
        "real emission site across `forge/` (excluding `forge/tests/`) —",
        "structurally, not a text grep — so this page cannot silently go",
        "stale the way the old hand-written docstring tables did (a prior",
        "investigation found `FWV000`/`FWV021`/`FWV022` emitted but",
        "undocumented, and `FWV005`/`FWV007`/`FWV008`/`FWV014` documented but",
        "never actually emitted).",
        "",
        "## FWV (verification framework) codes",
        "",
        "| Code | Default severity | Description | Remediation |",
        "|---|---|---|---|",
    ]
    for defn in sorted(
        (d for d in DIAGNOSTICS.values() if d.family == "FWV"), key=lambda d: d.code,
    ):
        lines.append(
            f"| `{defn.code}` | {defn.default_severity} | {defn.description} | "
            f"{defn.remediation or '—'} |"
        )

    lines += [
        "",
        "## ATG (topology generation) codes",
        "",
        "| Code | Default severity | Description | Remediation |",
        "|---|---|---|---|",
    ]
    for defn in sorted(
        (d for d in DIAGNOSTICS.values() if d.family == "ATG"), key=lambda d: d.code,
    ):
        lines.append(
            f"| `{defn.code}` | {defn.default_severity} | {defn.description} | "
            f"{defn.remediation or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)
