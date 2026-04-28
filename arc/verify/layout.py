#!/usr/bin/env python3
"""Canonical filesystem layout resolver for fw_verify plugins.

Responsibility boundary
-----------------------
This module owns all knowledge about the canonical verify directory layout.
No other framework module should construct plugin paths independently —
all callers go through the functions here.

Canonical layout
----------------
::

    plugins/<plugin_id>/verify/
        design.verification.yml
        tools/
            bootstrap.py
            gen_stimulus.py
        schemas/
            data/
        tests/
        <flow_name>/
            verify.flow.yml
            tb_<tb_module>.sv
            wave.tcl
            port_map.yaml
            stimulus_current.svh
            xsim_work/

Public API
----------
  PluginPaths              frozen dataclass — all standard paths for a plugin
  resolve_plugin_paths(verify_root)  → PluginPaths
  canonical_flow_dir(verify_root, flow_name) → Path
  canonical_flow_artifact(verify_root, flow_name, artifact) → Path
  validate_layout(verify_root)       → list[str]
  enforce_layout(verify_root)        → None  (raises ValueError on violation)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# ── Standard artifact names inside a flow directory ───────────────────────

FLOW_ARTIFACTS = {
    "flow_yml":        "verify.flow.yml",
    "tb_sv":           None,       # tb_{tb_module}.sv — caller fills name
    "wave_tcl":        "wave.tcl",
    "port_map":        "port_map.yaml",
    "stimulus":        "stimulus_current.svh",
    "xsim_work":       "xsim_work",
}

# Legacy kind-subdir names to detect and warn about
_LEGACY_KIND_DIRS = frozenset({
    "single_module_rtl",
    "reduced_chain_rtl",
    "full_chip_rtl",
    "hls_csim",
    "hls_cosim",
})


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PluginPaths:
    """All standard paths for one plugin's verify tree.

    Presence is NOT checked — callers decide which paths to require.
    """
    verify_root:     Path   # plugins/<plugin>/verify/
    design_yml:      Path   # verify/design.verification.yml
    tools_dir:       Path   # verify/tools/
    bootstrap_py:    Path   # verify/tools/bootstrap.py
    gen_stimulus_py: Path   # verify/tools/gen_stimulus.py
    schemas_dir:     Path   # verify/schemas/
    tests_dir:       Path   # verify/tests/


# ── Public API ─────────────────────────────────────────────────────────────

def resolve_plugin_paths(verify_root: Path) -> PluginPaths:
    """Return all canonical paths for a plugin rooted at *verify_root*.

    Does not check existence — callers decide which paths to require.

    Args:
        verify_root: Path to the plugin's ``verify/`` directory.

    Returns:
        ``PluginPaths`` with all standard paths resolved.
    """
    r = Path(verify_root).resolve()
    return PluginPaths(
        verify_root=r,
        design_yml=r / "design.verification.yml",
        tools_dir=r / "tools",
        bootstrap_py=r / "tools" / "bootstrap.py",
        gen_stimulus_py=r / "tools" / "gen_stimulus.py",
        schemas_dir=r / "schemas",
        tests_dir=r / "tests",
    )


def canonical_flow_dir(verify_root: Path, flow_name: str) -> Path:
    """Return the canonical directory for one named flow's artifacts.

    The canonical layout is **flat**: ``<verify_root>/<flow_name>/``.

    This is the single authoritative function for resolving flow directories.
    No other code should construct flow directory paths independently.

    Args:
        verify_root: Plugin's ``verify/`` directory.
        flow_name:   Flow name as declared in ``design.verification.yml``.

    Returns:
        Absolute path ``<verify_root>/<flow_name>/``.
    """
    return Path(verify_root).resolve() / flow_name


def canonical_flow_artifact(
    verify_root: Path,
    flow_name: str,
    artifact: str,
    *,
    tb_module: str | None = None,
) -> Path:
    """Return the canonical path for a named artifact inside a flow directory.

    Args:
        verify_root: Plugin's ``verify/`` directory.
        flow_name:   Flow name.
        artifact:    Key from ``FLOW_ARTIFACTS`` (e.g. ``"flow_yml"``).
        tb_module:   Required when *artifact* is ``"tb_sv"``; ignored otherwise.

    Returns:
        Absolute path to the artifact file.

    Raises:
        KeyError:   if *artifact* is not a known artifact key.
        ValueError: if *artifact* is ``"tb_sv"`` and *tb_module* is not provided.
    """
    if artifact not in FLOW_ARTIFACTS:
        raise KeyError(
            f"Unknown artifact key {artifact!r}.  "
            f"Valid keys: {sorted(FLOW_ARTIFACTS)}"
        )
    flow_dir = canonical_flow_dir(verify_root, flow_name)
    filename = FLOW_ARTIFACTS[artifact]
    if filename is None:
        if not tb_module:
            raise ValueError("tb_module is required when artifact='tb_sv'")
        filename = f"tb_{tb_module}.sv"
    return flow_dir / filename


def validate_layout(verify_root: Path) -> list[str]:
    """Check *verify_root* for canonical layout compliance.

    Returns a list of error strings.  An empty list means compliant.

    Checks:
    * ``design.verification.yml`` present
    * ``tools/bootstrap.py`` present
    * No legacy kind-subdir directories exist at the verify root level
    """
    errors: list[str] = []
    paths = resolve_plugin_paths(Path(verify_root))

    if not paths.design_yml.exists():
        errors.append(
            f"design.verification.yml not found: {paths.design_yml}\n"
            f"  → Create it with: fw_verify init-plugin <plugin_id>"
        )
    if not paths.bootstrap_py.exists():
        errors.append(
            f"bootstrap.py not found: {paths.bootstrap_py}\n"
            f"  → Create it with: fw_verify init-plugin <plugin_id>"
        )

    # Detect legacy conflicting kind-subdir layout
    for kind_dir_name in sorted(_LEGACY_KIND_DIRS):
        legacy = paths.verify_root / kind_dir_name
        if legacy.is_dir():
            errors.append(
                f"Legacy kind-subdir layout detected: {legacy}\n"
                f"  The canonical layout is flat: <verify_root>/<flow_name>/\n"
                f"  Remove or migrate this directory to the flat layout."
            )

    return errors


def enforce_layout(verify_root: Path) -> None:
    """Assert canonical layout compliance, raising ``ValueError`` on violation.

    Convenience wrapper around :func:`validate_layout` for callers that
    want an exception rather than a list.

    Raises:
        ValueError: with all violation messages joined.
    """
    errors = validate_layout(Path(verify_root))
    if errors:
        raise ValueError(
            "Canonical layout violations in " + str(verify_root) + ":\n"
            + "\n".join(f"  {e}" for e in errors)
        )


# ── Generated-file path resolution ────────────────────────────────────────

@dataclass(frozen=True)
class GeneratedFlowFiles:
    """Canonical paths for all framework-generated files in one flow directory.

    Presence is NOT checked — callers decide which files to require.
    ``tb_sv`` is ``None`` for csim flows (they have no RTL testbench).
    """
    flow_dir:       Path   # <verify_root>/<flow_name>/
    flow_yml:       Path   # verify.flow.yml
    port_map:       Path   # port_map.yaml
    tb_sv:          Path | None  # tb_<tb_module>.sv  (None for csim flows)
    wave_tcl:       Path   # wave.tcl
    stimulus_svh:   Path   # stimulus_current.svh


def resolve_generated_files(
    verify_root: Path,
    flow_name: str,
    tb_module: str,
    *,
    is_csim: bool = False,
) -> GeneratedFlowFiles:
    """Return canonical paths for all framework-generated files for one flow.

    This is the single authoritative function for resolving per-flow artifact
    paths.  All framework commands (generate, prepare, run, doctor) must use
    this function rather than constructing paths independently.

    Args:
        verify_root: Plugin's ``verify/`` directory.
        flow_name:   Flow name as declared in ``design.verification.yml``.
        tb_module:   Testbench module name (used to derive ``tb_<name>.sv``).
        is_csim:     Pass ``True`` for ``hls_csim`` flows to set ``tb_sv`` to
                     ``None`` (csim flows do not use an RTL testbench).

    Returns:
        ``GeneratedFlowFiles`` dataclass with all resolved paths.
    """
    flow_dir = canonical_flow_dir(verify_root, flow_name)
    return GeneratedFlowFiles(
        flow_dir=flow_dir,
        flow_yml=flow_dir / "verify.flow.yml",
        port_map=flow_dir / "port_map.yaml",
        tb_sv=None if is_csim else flow_dir / f"{tb_module}.sv",
        wave_tcl=flow_dir / "wave.tcl",
        stimulus_svh=flow_dir / "stimulus_current.svh",
    )


def missing_generated_files(
    files: GeneratedFlowFiles,
    *,
    require_stimulus: bool = False,
) -> list[str]:
    """Return a list of missing generated-file errors for *files*.

    ``verify.flow.yml``, ``port_map.yaml`` (xsim only), and ``tb_*.sv``
    (xsim only) are always checked.  ``stimulus_current.svh`` is checked
    only when *require_stimulus* is ``True``.

    Args:
        files:            ``GeneratedFlowFiles`` returned by :func:`resolve_generated_files`.
        require_stimulus: Also check that ``stimulus_current.svh`` exists.

    Returns:
        List of human-readable error strings; empty list means all present.
    """
    errors: list[str] = []
    if not files.flow_yml.exists():
        errors.append(
            f"verify.flow.yml missing: {files.flow_yml}\n"
            f"  → fw_verify generate <design.verification.yml>"
        )
    if files.tb_sv is not None and not files.tb_sv.exists():
        errors.append(
            f"TB not found: {files.tb_sv.name}\n"
            f"  → fw_verify generate <design.verification.yml>"
        )
    if files.tb_sv is not None and not files.port_map.exists():
        errors.append(
            f"port_map.yaml missing: {files.port_map}\n"
            f"  → fw_verify generate <design.verification.yml>"
        )
    if require_stimulus and not files.stimulus_svh.exists():
        errors.append(
            f"stimulus_current.svh missing: {files.stimulus_svh}\n"
            f"  → python3 tools/gen_stimulus.py --flow {files.flow_dir.name}"
        )
    return errors
