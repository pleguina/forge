#!/usr/bin/env python3
"""Generic flow schema definitions, building blocks, and FlowConfig type.

Responsibility boundary
-----------------------
This module owns the **framework-generic** parts of the verification flow
schema:

  * Shared helpers for path resolution and YAML field extraction
  * Generic required-field declarations
  * Checker hook and stimulus hook validation
  * Generic ``CheckerConfig`` and ``FlowConfig`` dataclasses
  * ``parse_generic_flow_fields()`` — validates generic schema and returns
    a flat dict ready for use in a ``FlowConfig`` (or plugin subclass)
    constructor
  * ``resolve_consumer_root()`` — consumer root resolution with env-var
    fallback (caller supplies the env-var key)

This module does NOT own:
  * Plugin-specific schema fields (e.g. event_id, bx_trace)    → plugin flow_config
  * Artifact existence checks                                   → fw_verify.preflight
  * Backend discovery or dispatch                               → fw_verify.backend_registry

Public API (for plugin flow loaders)
-------------------------------------
  resolve_consumer_root(consumer_root, env_key)   → Path
  parse_generic_flow_fields(raw, flow_path, flow_dir, consumer_root)
                                                  → dict[str, Any]
  CheckerConfig  (frozen dataclass)
  FlowConfig     (frozen dataclass — generic fields only)

Also exports the following for plugin validation helpers:
  _REQUIRED_TOP_GENERIC, _REQUIRED_CHECKER, _ALLOWED_STIMULUS_FALLBACK,
  _ALLOWED_FLOW_KINDS, _abs, _abs_opt, _nested
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.verify.exceptions import (
    FlowConfigError,
    MissingArtifactError,
    PluginBootstrapError,
    SupportedMatrixViolation,
)


# ── Schema: generic required fields ───────────────────────────────────────

# (section, key) pairs required in every compliant flow file.
# For v1.0 verification, ``dataset.xml`` is a framework-standard requirement:
# flows are XML-backed unless a future contract revision explicitly generalizes
# dataset representation. Plugin-specific required fields (e.g.
# dataset.default_event_id) are declared in the plugin's own load_flow() and
# validated before calling parse_generic_flow_fields().
_REQUIRED_TOP_GENERIC: list[tuple[str, str]] = [
    ("flow",       "name"),
    ("flow",       "kind"),
    ("flow",       "backend"),
    ("flow",       "top_module"),
    ("flow",       "tb_module"),
    ("dut",        "rtl"),
    # dut.manifest, dut.port_map, dut.tb_bindings, dut.port_signature are
    # OPTIONAL — single-module HLS flows do not have gen-top outputs.
    ("dataset",    "xml"),
    ("simulation", "clk_period_ns"),
    ("simulation", "reset_cycles"),
    ("simulation", "idle_cycles_after_reset"),
    ("simulation", "post_stimulus_drain_cycles"),
]

# If the checker section is present at all, ALL of these sub-fields are required.
_REQUIRED_CHECKER: list[str] = [
    "observed_log",
]

# Allowed values for stimulus.fallback.
_ALLOWED_STIMULUS_FALLBACK: frozenset[str] = frozenset({"idle", "fail"})

# Allowed values for flow.kind.  Framework-owned authority (Stage 10).
_ALLOWED_FLOW_KINDS: frozenset[str] = frozenset({
    "full_chip_rtl",
    "reduced_chain_rtl",
    "single_module_rtl",
    "hls_csim",
    # hls_cosim: allowed in YAML to prevent parse errors on legacy files, but
    # no backend adapter implements it.  Explicitly not planned — see
    # docs/VERIFY_BACKEND_SUPPORT_MATRIX.md for the decision record.
    "hls_cosim",
})


# ── Helpers ────────────────────────────────────────────────────────────────

def _nested(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _abs(raw: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def _abs_opt(raw: str | None, base: Path) -> Path | None:
    return (base / raw).resolve() if raw else None


# ── Consumer root resolution ───────────────────────────────────────────────

def resolve_consumer_root(
    consumer_root: Path | None,
    env_key: str = "VERIFY_CONSUMER_ROOT",
) -> Path:
    """Resolve the consumer root path.

    Checks ``consumer_root`` first; falls back to the env variable named by
    *env_key*.

    Raises:
        FlowConfigError — if neither argument nor env variable is set.
    """
    if consumer_root is not None:
        return Path(consumer_root).resolve()
    env_val = os.environ.get(env_key, "")
    if env_val:
        return Path(env_val).resolve()
    raise FlowConfigError(
        f"consumer_root is required: pass it explicitly or set {env_key}.",
        action=f"Pass --consumer-root explicitly or set the {env_key} environment variable.",
        context={"env_key": env_key},
    )


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckerConfig:
    """Resolved checker hook configuration."""
    mode:           str
    tool:           str
    binary:         Path | None
    args:           tuple[str, ...]
    observed_log:   Path
    latency_cycles: int
    tolerance:      int
    pass_condition: str


@dataclass(frozen=True)
class XsimExtraSourceConfig:
    """Resolved extra source entry from the xsim block."""
    path: Path
    lang: str


@dataclass(frozen=True)
class XsimConfig:
    """Resolved declarative xsim configuration."""
    use_sv_flag: bool
    top_lib: str | None
    extra_top_modules: tuple[str, ...]
    elab_libs: tuple[str, ...]
    extra_sources: tuple[XsimExtraSourceConfig, ...]


@dataclass(frozen=True)
class FlowConfig:
    """Framework-generic, fully resolved, immutable snapshot of a flow file.

    Contains only fields that every flow has regardless of plugin.
    Plugin-specific fields are added by subclassing::

        @dataclass(frozen=True)
        class MyFlowConfig(FlowConfig):
            my_plugin_field: int
    """

    # ── Source bookkeeping
    flow_file:      Path
    consumer_root:  Path

    # ── flow:
    flow_name:  str
    flow_kind:  str
    backend:    str
    top_module: str
    tb_module:  str

    # ── dut:  (all absolute paths; manifest/port_map/tb_bindings/port_signature
    #          are None for HLS single-module flows that lack gen-top outputs)
    dut_rtl:             Path
    dut_manifest:        Path | None
    dut_port_map:        Path | None
    dut_tb_bindings:     Path | None
    dut_tb_bindings_dir: Path | None  # parent of dut_tb_bindings (None when tb_bindings absent)
    dut_port_signature:  Path | None
    dut_probe_map:       Path | None

    # ── dataset:
    # For v1.0 verification this is the framework-standard XML stimulus source.
    dataset_xml: Path
    dataset_parts: tuple[Path, ...]

    # ── simulation:
    sim_clk_period_ns:     float
    sim_reset_cycles:      int
    sim_idle_after_reset:  int
    sim_post_drain_cycles: int

    # ── checker: (None if no checker section in flow file)
    checker: CheckerConfig | None
    checker_mode: str

    # ── xsim: declarative simulator config
    xsim: XsimConfig | None

    # ── outputs: (None for absent fields)
    outputs_waveform: Path | None

    # ── stimulus: (defaults applied when section absent)
    stimulus_tool:     str   # binary name
    stimulus_fallback: str   # "idle" | "fail"

    # ── compile:
    compile_excluded_sources: tuple[Path, ...]

    # ── plugin identity (optional; enables bootstrap lifecycle guard):
    plugin_id: str | None = None

    @property
    def has_checker(self) -> bool:
        return self.checker is not None


# ── Generic parsing building block ────────────────────────────────────────

def parse_generic_flow_fields(
    raw:           dict[str, Any],
    flow_path:     Path,
    flow_dir:      Path,
    consumer_root: Path,
) -> dict[str, Any]:
    """Validate the generic schema and return a dict of resolved field values.

    The returned dict has exactly the fields of ``FlowConfig`` keyed by name.
    Plugin loaders pass ``**result_dict`` (plus plugin-specific fields) into
    their ``MyFlowConfig(...)`` constructor.

    Validates:
      * Generic required fields
      * ``flow.kind`` against ``_ALLOWED_FLOW_KINDS``
      * ``flow.backend`` and ``(kind, backend)`` compatibility via the registry
      * Stimulus hook shape
      * Compile hook shape
      * Checker section completeness

    Raises:
        FlowConfigError           — malformed flow schema.
        SupportedMatrixViolation  — unsupported kind/backend declaration.
        PluginBootstrapError      — plugin bootstrap lifecycle not satisfied.
    """
    def _raise_flow_error(message: str, *, action: str) -> None:
        raise FlowConfigError(
            message,
            action=action,
            context={"flow_path": str(flow_path)},
        )

    # ── Validate generic required fields
    errors: list[str] = []
    for section, key in _REQUIRED_TOP_GENERIC:
        val = _nested(raw, section, key)
        if val is None or (isinstance(val, str) and not val.strip()):
            errors.append(f"  {section}.{key}  (required)")
    if errors:
        _raise_flow_error(
            f"verify.flow.yml missing required fields ({flow_path}):\n" + "\n".join(errors),
            action="Add the required fields to verify.flow.yml and regenerate the flow if needed.",
        )

    flow_sec     = raw["flow"]
    dut          = raw["dut"]
    dataset      = raw["dataset"]
    sim          = raw["simulation"]
    checker_raw  = raw.get("checker")
    xsim_raw     = raw.get("xsim", {}) or {}
    outputs_raw  = raw.get("outputs",  {}) or {}
    stimulus_raw = raw.get("stimulus", {}) or {}
    compile_raw  = raw.get("compile",  {}) or {}

    # ── Extract plugin identity and enforce bootstrap lifecycle guard
    _plugin_id: str | None = _nested(raw, "flow", "plugin") or None
    if _plugin_id is not None:
        import forge.verify.plugin_registry as _pr  # local import: avoids circular dep
        try:
            _pr.require_plugin_bootstrapped(_plugin_id)
        except RuntimeError as exc:
            raise PluginBootstrapError(
                str(exc),
                action="Bootstrap the plugin before loading the flow, or pass --plugin to fw_verify commands.",
                context={"plugin": _plugin_id, "flow_path": str(flow_path)},
            ) from exc

    # ── Validate flow.kind
    _kind_val = str(flow_sec["kind"]).strip()
    if _kind_val not in _ALLOWED_FLOW_KINDS:
        raise SupportedMatrixViolation(
            f"verify.flow.yml: flow.kind must be one of "
            f"{sorted(_ALLOWED_FLOW_KINDS)}, got {_kind_val!r} ({flow_path})",
            action="Use a supported flow.kind value in verify.flow.yml.",
            context={"flow_path": str(flow_path), "flow_kind": _kind_val},
        )

    # ── Validate flow.backend and kind/backend compatibility via registry
    _backend_val = str(flow_sec["backend"]).strip()
    import forge.verify.backend_registry as _br  # local import: populated by plugin shim
    _allowed_backends = _br.get_allowed_backends()
    _compat_pairs     = _br.get_compatible_pairs()
    if _backend_val not in _allowed_backends:
        raise SupportedMatrixViolation(
            f"verify.flow.yml: flow.backend must be one of "
            f"{sorted(_allowed_backends)}, got {_backend_val!r} ({flow_path})",
            action="Use a supported flow.backend value or bootstrap the plugin that registers it.",
            context={"flow_path": str(flow_path), "backend": _backend_val},
        )
    if (_kind_val, _backend_val) not in _compat_pairs:
        raise SupportedMatrixViolation(
            f"verify.flow.yml: flow.kind={_kind_val!r} is incompatible with "
            f"flow.backend={_backend_val!r} ({flow_path})",
            action="Use a supported (flow.kind, flow.backend) pair in verify.flow.yml.",
            context={
                "flow_path": str(flow_path),
                "flow_kind": _kind_val,
                "backend": _backend_val,
            },
        )

    # ── Validate stimulus hook (if section present)
    if stimulus_raw:
        _tool_val = stimulus_raw.get("tool", "")
        if not isinstance(_tool_val, str) or not _tool_val.strip():
            _raise_flow_error(
                f"verify.flow.yml: stimulus.tool must be a non-empty string ({flow_path})",
                action="Set stimulus.tool to the stimulus generator binary name or remove the invalid stimulus block.",
            )
        _fb_val = stimulus_raw.get("fallback", "idle")
        if _fb_val not in _ALLOWED_STIMULUS_FALLBACK:
            _raise_flow_error(
                f"verify.flow.yml: stimulus.fallback must be one of "
                f"{sorted(_ALLOWED_STIMULUS_FALLBACK)}, got {_fb_val!r} ({flow_path})",
                action="Use a supported stimulus.fallback value such as 'idle' or 'fail'.",
            )

    # ── Validate compile hook (if section present)
    if compile_raw:
        _exc = compile_raw.get("excluded_sources")
        if _exc is not None and not isinstance(_exc, list):
            _raise_flow_error(
                f"verify.flow.yml: compile.excluded_sources must be a list ({flow_path})",
                action="Rewrite compile.excluded_sources as a YAML list of paths.",
            )

    # ── Validate checker section (if present, observed_log is required)
    checker: CheckerConfig | None = None
    checker_mode = str(raw.get(
        "checker_mode",
        checker_raw.get("mode", "log_scan") if isinstance(checker_raw, dict) else "log_scan",
    ))
    if checker_raw is not None:
        missing_checker = [
            f"  checker.{k}" for k in _REQUIRED_CHECKER
            if not checker_raw.get(k)
        ]
        if missing_checker:
            _raise_flow_error(
                f"verify.flow.yml: checker section present but missing required fields "
                f"({flow_path}):\n" + "\n".join(missing_checker),
                action="Add the missing checker fields or remove the incomplete checker block.",
            )
        checker = CheckerConfig(
            mode           = str(checker_raw.get("mode", checker_mode)),
            tool           = str(checker_raw.get("tool", "")),
            binary         = _abs_opt(checker_raw.get("binary"), consumer_root),
            args           = tuple(str(a) for a in (checker_raw.get("args") or [])),
            observed_log   = _abs(checker_raw["observed_log"], flow_dir),
            latency_cycles = int(checker_raw.get("latency_cycles", 0)),
            tolerance      = int(checker_raw.get("tolerance", 0)),
            pass_condition = str(checker_raw.get("pass_condition", "zero_mismatches")),
        )

    # ── Validate xsim section (if present)
    xsim: XsimConfig | None = None
    if xsim_raw:
        extra_sources: list[XsimExtraSourceConfig] = []
        for src in (xsim_raw.get("extra_sources") or []):
            if not isinstance(src, dict) or not src.get("path"):
                _raise_flow_error(
                    f"verify.flow.yml: xsim.extra_sources entries must have a path ({flow_path})",
                    action="Add a path entry to each xsim.extra_sources item.",
                )
            extra_sources.append(XsimExtraSourceConfig(
                path=_abs(str(src["path"]), consumer_root),
                lang=str(src.get("lang", "verilog")),
            ))
        xsim = XsimConfig(
            use_sv_flag=bool(xsim_raw.get("use_sv_flag", True)),
            top_lib=(str(xsim_raw["top_lib"]) if xsim_raw.get("top_lib") not in (None, "", "~") else None),
            extra_top_modules=tuple(str(v) for v in (xsim_raw.get("extra_top_modules") or [])),
            elab_libs=tuple(str(v) for v in (xsim_raw.get("elab_libs") or [])),
            extra_sources=tuple(extra_sources),
        )

    dataset_parts: list[Path] = []
    parts_glob = dataset.get("parts_glob")
    if parts_glob:
        pattern = Path(str(parts_glob))
        if pattern.is_absolute():
            dataset_parts.extend(sorted(pattern.parent.glob(pattern.name)))
        else:
            dataset_parts.extend(sorted(consumer_root.glob(str(pattern))))

    parts_list = dataset.get("parts") or []
    if parts_list:
        if not isinstance(parts_list, list):
            _raise_flow_error(
                f"verify.flow.yml: dataset.parts must be a list ({flow_path})",
                action="Rewrite dataset.parts as a YAML list of XML paths.",
            )
        dataset_parts.extend(_abs(str(part), consumer_root) for part in parts_list)

    # ── Build dut_tb_bindings (may be None for single-module HLS flows)
    dut_tb_bindings = _abs_opt(dut.get("tb_bindings"), consumer_root)

    return {
        "flow_file":      flow_path.resolve(),
        "consumer_root":  consumer_root,
        "flow_name":      str(flow_sec["name"]),
        "flow_kind":      _kind_val,
        "backend":        _backend_val,
        "top_module":     str(flow_sec["top_module"]),
        "tb_module":      str(flow_sec["tb_module"]),
        "dut_rtl":             _abs(dut["rtl"],           consumer_root),
        "dut_manifest":        _abs_opt(dut.get("manifest"),       consumer_root),
        "dut_port_map":        _abs_opt(dut.get("port_map"),       consumer_root),
        "dut_tb_bindings":     dut_tb_bindings,
        "dut_tb_bindings_dir": dut_tb_bindings.parent if dut_tb_bindings else None,
        "dut_port_signature":  _abs_opt(dut.get("port_signature"), consumer_root),
        "dut_probe_map":       _abs_opt(dut.get("probe_map"), consumer_root),
        "dataset_xml":         _abs(dataset["xml"], consumer_root),
        "dataset_parts":       tuple(dict.fromkeys(path.resolve() for path in dataset_parts)),
        "sim_clk_period_ns":      float(sim["clk_period_ns"]),
        "sim_reset_cycles":       int(sim["reset_cycles"]),
        "sim_idle_after_reset":   int(sim["idle_cycles_after_reset"]),
        "sim_post_drain_cycles":  int(sim["post_stimulus_drain_cycles"]),
        "checker":  checker,
        "checker_mode": checker_mode,
        "xsim": xsim,
        "outputs_waveform": _abs_opt(outputs_raw.get("waveform"), flow_dir),
        "stimulus_tool":     str(stimulus_raw.get("tool",     "xml_to_sv_stimulus")),
        "stimulus_fallback": str(stimulus_raw.get("fallback", "idle")),
        "compile_excluded_sources": tuple(
            _abs(str(e), consumer_root)
            for e in (compile_raw.get("excluded_sources") or [])
        ),
        "plugin_id": _plugin_id,
    }


# ── Generic single-file loader (for framework CLI and other consumers) ────

def load_generic_flow(
    flow_path:     Path,
    consumer_root: Path | None = None,
) -> "FlowConfig":
    """Load a verify.flow.yml and return a framework-generic ``FlowConfig``.

    This is the framework's own entry point: it constructs only the 24 generic
    fields and returns a ``FlowConfig`` (not a plugin subclass).  It is useful
    for contexts where the plugin extension fields are not needed — such as the
    framework preflight CLI.

    For full plugin-specific loading (including extension fields), use the plugin's
    own ``load_flow()`` instead.

    Args:
        flow_path:     Path to ``verify.flow.yml``.
        consumer_root: Repo root for path resolution.  Falls back to
                       ``VERIFY_CONSUMER_ROOT`` environment variable.

    Raises:
        MissingArtifactError      — if *flow_path* does not exist.
        FlowConfigError           — malformed flow schema.
        SupportedMatrixViolation  — unsupported kind/backend declaration.
        PluginBootstrapError      — plugin bootstrap lifecycle not satisfied.
    """
    try:
        import yaml as _yaml
    except ImportError as exc:
        raise FlowConfigError(
            "PyYAML is required to load verify.flow.yml files.",
            action="Install the framework dependency set, for example: pip install pyyaml",
            context={"flow_path": str(flow_path)},
        ) from exc

    flow_path = Path(flow_path).resolve()
    if not flow_path.exists():
        raise MissingArtifactError(
            f"verify.flow.yml not found: {flow_path}",
            action="Check the flow path or re-run fw_verify generate to create verify.flow.yml.",
            context={"flow_path": str(flow_path)},
        )

    try:
        raw = _yaml.safe_load(flow_path.read_text()) or {}
    except _yaml.YAMLError as exc:
        raise FlowConfigError(
            f"verify.flow.yml has invalid YAML syntax: {exc}",
            action="Fix the YAML syntax in verify.flow.yml and retry.",
            context={"flow_path": str(flow_path)},
        ) from exc
    flow_dir = flow_path.parent

    resolved_root = resolve_consumer_root(
        consumer_root,
        env_key="VERIFY_CONSUMER_ROOT",
    )

    fields = parse_generic_flow_fields(raw, flow_path, flow_dir, resolved_root)
    return FlowConfig(**fields)
