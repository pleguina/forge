#!/usr/bin/env python3
"""Verification design contract schema and loader.

Responsibility boundary
-----------------------
This module defines the **verify.design.yml** schema — the simulation-side
analogue of ``design.yml`` for topology.

A ``verify.design.yml`` file per plugin declares:
  * Which verification flows exist (name, kind, backend)
  * DUT source provenance (gen-top vs HLS build)
  * Expected latency, pass conditions, checker configuration
    * Dataset references and default event selection
  * Simulation timing defaults

For the current v1.0 framework verification contract, named datasets are
XML-backed. A future contract revision may generalize dataset representation,
but the current public surface is intentionally explicit about XML.

A future ``gen-sim`` step would consume this contract to auto-generate
``verify.flow.yml`` files and ``tb_*.sv`` testbenches.

Public API
----------
  VerifyDesignContract   frozen dataclass — top-level container
  FlowDeclaration        frozen dataclass — one declared flow
  CheckerDeclaration     frozen dataclass — checker expectations
  DatasetDeclaration     frozen dataclass — named dataset reference
  SimulationDefaults     frozen dataclass — shared timing defaults
  load_verify_design(path)  → VerifyDesignContract

Schema example
--------------
See the docstring on ``load_verify_design`` for the full YAML schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arc.verify.exceptions import DesignContractError


# ── Field classification ───────────────────────────────────────────────────
# These constants are the authoritative classification of every FlowDeclaration
# field.  The distinction matters for enforcement:
#
#   REQUIRED_FIELDS          — must be present; loader raises ValueError if absent.
#   OPTIONAL_BEHAVIORAL_FIELDS — optional; when present the framework uses them to
#                                control code paths (e.g. which TB mechanism, which
#                                port extraction mode).  Omitting one activates a
#                                safe default; setting it explicitly changes behaviour.
#   INFORMATIONAL_FIELDS     — optional; the framework stores them for documentation
#                                and CI filtering but must NOT alter simulation
#                                behaviour based on their value.  If a future change
#                                would make an informational field control a code path,
#                                it must first be promoted to OPTIONAL_BEHAVIORAL_FIELDS.
#
# Any field added to FlowDeclaration must be placed in exactly one of these sets.

REQUIRED_FIELDS: frozenset[str] = frozenset({
    "name",
    "kind",
    "backend",
    "top_module",
    "tb_module",
    "dut_rtl_source",
    "dataset",
})

OPTIONAL_BEHAVIORAL_FIELDS: frozenset[str] = frozenset({
    "stimulus_mode",        # controls TB include mechanism
    "wave_mode",            # controls waveform capture
    "rtl_source_type",      # guides port extraction mode selection
    "checker_mode",         # controls pass/fail determination mechanism
    "layout_mode",          # controls artifact directory layout
    "requires_generated_tb",# set false to suppress TB generation
    "default_event_id",     # which XML event to use by default
    "reset_cycles",         # simulation timing — overrides contract defaults
    "idle_cycles_after_reset",
    "post_stimulus_drain_cycles",
    "checker",              # latency/tolerance/pass_condition expectations
    "xsim",                 # simulator-specific declarative config
})

INFORMATIONAL_FIELDS: frozenset[str] = frozenset({
    "flow_class",           # CI filtering tag — never alters simulation behaviour
    "coverage_intent",      # documentation only — never alters simulation behaviour
})

# Union for completeness — must equal the set of all FlowDeclaration attrs.
ALL_FLOW_FIELDS: frozenset[str] = (
    REQUIRED_FIELDS | OPTIONAL_BEHAVIORAL_FIELDS | INFORMATIONAL_FIELDS
)


def classify_field(field_name: str) -> str:
    """Return the classification of *field_name*: 'required', 'behavioral', or 'informational'.

    Raises ``KeyError`` for unrecognised field names.
    """
    if field_name in REQUIRED_FIELDS:
        return "required"
    if field_name in OPTIONAL_BEHAVIORAL_FIELDS:
        return "behavioral"
    if field_name in INFORMATIONAL_FIELDS:
        return "informational"
    raise KeyError(
        f"Unknown flow field {field_name!r}.  "
        f"Not in REQUIRED_FIELDS, OPTIONAL_BEHAVIORAL_FIELDS, or INFORMATIONAL_FIELDS."
    )


# ── Schema dataclasses ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class DatasetDeclaration:
    """A named XML-backed dataset reference."""
    name:        str
    xml:         str          # relative path to the XML file
    description: str = ""


@dataclass(frozen=True)
class CheckerDeclaration:
    """Expected checker configuration for a flow."""
    mode:           str | None = None
    binary:         str | None = None
    observed_log:   str | None = None
    args:           tuple[str, ...] = ()
    latency_cycles: int | None = None
    tolerance:      int | None = None
    pass_condition: str | None = None


@dataclass(frozen=True)
class XsimExtraSourceDeclaration:
    """Additional RTL source consumed by xsim outside the DUT manifest."""
    path: str
    lang: str = "verilog"


@dataclass(frozen=True)
class XsimDeclaration:
    """Declarative xsim configuration carried by a flow declaration."""
    use_sv_flag: bool | None = None
    top_lib: str | None = None
    extra_top_modules: tuple[str, ...] = ()
    elab_libs: tuple[str, ...] = ()
    extra_sources: tuple[XsimExtraSourceDeclaration, ...] = ()


@dataclass(frozen=True)
class SimulationDefaults:
    """Shared simulation timing defaults."""
    clk_period_ns:              float = 2.78
    reset_cycles:               int = 8
    idle_cycles_after_reset:    int = 8
    post_stimulus_drain_cycles: int = 160


@dataclass(frozen=True)
class FlowDeclaration:
    """Declaration of a single verification flow.

    Required fields
    ---------------
    name, kind, backend, top_module, tb_module, dut_rtl_source, dataset

    Optional enrichment fields
    --------------------------
    The fields below are optional.  When present they make formerly-implicit
    framework choices explicit and machine-verifiable.

    stimulus_mode
        How stimulus is delivered to the testbench.
        ``"svh_include"`` (default) — ``stimulus_current.svh`` is included via backtick-include.
        ``"structured_csv"``         — plugin provides a CSV; framework renders SVH.
        ``"none"``                   — no stimulus (for tie-off / loopback flows).

    wave_mode
        Waveform capture mode.
        ``"tcl"`` (default for xsim) — ``wave.tcl`` is passed to xsim.
        ``"none"``                   — no waveform capture.

    rtl_source_type
        Provenance of the DUT RTL.
        ``"hls"``     — HLS-generated Verilog (port extraction via rtl_introspection).
        ``"rtl"``     — manually written RTL.
        ``"gen_top"`` — topgen output.

    checker_mode
        How pass/fail is determined.
        ``"log_scan"`` (default) — framework scans simulator log for FAIL/PASS markers.
        ``"binary"``             — external checker binary is invoked post-simulation.
        ``"none"``               — no checker; simulator exit code only.

    layout_mode
        Flow directory layout mode.
        ``"flat"`` (default) — ``<verify_root>/<flow_name>/`` (canonical).
        ``"kind_subdir"``    — ``<verify_root>/<kind>/<flow_name>/`` (legacy; avoid).

    requires_generated_tb
        Whether the framework MUST generate the testbench (default: True for xsim flows).
        Set to False only for flows that ship a hand-authored TB.

    flow_class
        Informational classification: ``"integration"``, ``"unit"``, ``"smoke"``.
        Used for CI filtering; not enforced by the framework.
    """
    name:              str
    kind:              str
    backend:           str
    top_module:        str
    tb_module:         str
    dut_rtl_source:    str             # provenance: "gen-top", "build_hls/<module>", etc.
    dataset:           str             # name referencing a DatasetDeclaration
    default_event_id:  int = 1

    # Per-flow simulation overrides (None → use contract-level defaults)
    reset_cycles:               int | None = None
    idle_cycles_after_reset:    int | None = None
    post_stimulus_drain_cycles: int | None = None

    checker: CheckerDeclaration | None = None
    xsim: XsimDeclaration | None = None

    # Coverage intent — INFORMATIONAL; framework must not change behaviour based on this
    coverage_intent: str = "functional"

    # ── Optional behavioral enrichment fields (all have safe defaults) ─────
    stimulus_mode:        str = "svh_include"   # svh_include | structured_csv | none
    wave_mode:            str = "tcl"           # tcl | none
    rtl_source_type:      str = "hls"           # hls | rtl | gen_top
    checker_mode:         str = "log_scan"      # log_scan | binary | none
    layout_mode:          str = "flat"          # flat | kind_subdir (legacy)
    requires_generated_tb: bool = True

    # ── Informational metadata — NEVER alter simulation behaviour ──────────
    flow_class:           str = "integration"   # integration | unit | smoke

    # ── Experimental opt-in — set true to allow experimental flow kinds ────
    experimental:         bool = False


@dataclass(frozen=True)
class VerifyDesignContract:
    """Top-level verify design contract — read from ``verify.design.yml``.

    Analogous to ``design.yml`` for topology, this declares the full
    verification intent for a plugin.
    """
    plugin:     str
    datasets:   tuple[DatasetDeclaration, ...]
    defaults:   SimulationDefaults
    flows:      tuple[FlowDeclaration, ...]
    source_path: Path | None = None   # path to the loaded YAML file

    def get_dataset(self, name: str) -> DatasetDeclaration | None:
        """Look up a dataset by name."""
        for ds in self.datasets:
            if ds.name == name:
                return ds
        return None

    def get_flow(self, name: str) -> FlowDeclaration | None:
        """Look up a flow declaration by name."""
        for fl in self.flows:
            if fl.name == name:
                return fl
        return None


# ── Loader ─────────────────────────────────────────────────────────────────

def load_verify_design(path: Path) -> "VerifyDesignContract":
    """Load and validate a ``design.verification.yml`` (or ``verify.design.yml``) contract file.

    Accepts either a direct path to the YAML file or a directory path, in which
    case the canonical filename ``design.verification.yml`` is searched first,
    then the legacy name ``verify.design.yml`` as a fallback.

    Schema
    ------
    .. code-block:: yaml

        plugin: <plugin_id>

        datasets:
          <name>:
            xml: <relative path>
            description: <optional text>

        defaults:
          clk_period_ns: 2.78
          reset_cycles: 8
          idle_cycles_after_reset: 8
          post_stimulus_drain_cycles: 160

        flows:
          - name: <flow_name>
            kind: <flow_kind>
            backend: <backend_id>
            top_module: <module>
            tb_module: <testbench>
            dut_rtl_source: <provenance>
            dataset: <dataset_name>
            default_event_id: <int>
            simulation:
              reset_cycles: <int>              # overrides default
              idle_cycles_after_reset: <int>
              post_stimulus_drain_cycles: <int>
            checker:
                            mode: binary
                            binary: build_targeted/algo_top_xsim_checker
                            observed_log: xsim_work/algo_top_outputs.csv
                            args: ["--obs", "{observed_log}", ...]
              latency_cycles: <int>
              tolerance: <int>
              pass_condition: <str>
                        xsim:
                            use_sv_flag: false
                            top_lib: xil_defaultlib
                            extra_top_modules: [xil_defaultlib.glbl]
                            elab_libs: [unisims_ver, unimacro_ver, secureip]
                            extra_sources:
                                - path: plugins/<plugin>/verify/stubs/verilog/floating_point_v7_1_18.v
                                    lang: verilog
            coverage_intent: <str>

    Args:
        path: Path to the ``verify.design.yml`` file.

    Returns:
        A frozen ``VerifyDesignContract`` instance.

    Raises:
        DesignContractError: if the contract cannot be loaded or validated.
    """
    def _raise_contract_error(message: str, *, action: str) -> None:
        raise DesignContractError(
            message,
            action=action,
            context={"path": str(path)},
        )

    try:
        import yaml as _yaml
    except ImportError:
        _raise_contract_error(
            "PyYAML is required to load design.verification.yml files.",
            action="Install the framework dependency set, for example: pip install pyyaml",
        )

    path = Path(path).resolve()
    # If a directory is given, search for the canonical filename first, then legacy.
    if path.is_dir():
        for _candidate in ("design.verification.yml", "verify.design.yml"):
            _p = path / _candidate
            if _p.exists():
                path = _p
                break
        else:
            raise DesignContractError(
                f"No design.verification.yml (or verify.design.yml) found in: {path}",
                action="Create design.verification.yml in that directory or pass the contract file path explicitly.",
                context={"path": str(path)},
            )
    if not path.exists():
        raise DesignContractError(
            f"design.verification.yml not found: {path}",
            action="Check the contract path or generate the plugin skeleton with fw_verify init-plugin.",
            context={"path": str(path)},
        )

    try:
        raw: dict[str, Any] = _yaml.safe_load(path.read_text()) or {}
    except _yaml.YAMLError as exc:
        raise DesignContractError(
            f"verify.design.yml has invalid YAML syntax: {exc}",
            action="Fix the YAML syntax in design.verification.yml and retry.",
            context={"path": str(path)},
        ) from exc

    # ── Plugin identity
    plugin = raw.get("plugin")
    if not plugin or not isinstance(plugin, str):
        _raise_contract_error(
            f"verify.design.yml: 'plugin' is required ({path})",
            action="Add a non-empty 'plugin' field at the top level of design.verification.yml.",
        )

    # ── Datasets
    raw_datasets = raw.get("datasets", {}) or {}
    if not isinstance(raw_datasets, dict):
        _raise_contract_error(
            f"verify.design.yml: 'datasets' must be a mapping ({path})",
            action="Rewrite 'datasets' as a YAML mapping keyed by dataset name.",
        )
    datasets: list[DatasetDeclaration] = []
    for ds_name, ds_body in raw_datasets.items():
        if not isinstance(ds_body, dict) or "xml" not in ds_body:
            _raise_contract_error(
                f"verify.design.yml: dataset {ds_name!r} must have an 'xml' field ({path})",
                action="Add an 'xml' entry for every declared dataset.",
            )
        datasets.append(DatasetDeclaration(
            name=str(ds_name),
            xml=str(ds_body["xml"]),
            description=str(ds_body.get("description", "")),
        ))

    # ── Defaults
    raw_defaults = raw.get("defaults", {}) or {}
    defaults = SimulationDefaults(
        clk_period_ns=float(raw_defaults.get("clk_period_ns", 2.78)),
        reset_cycles=int(raw_defaults.get("reset_cycles", 8)),
        idle_cycles_after_reset=int(raw_defaults.get("idle_cycles_after_reset", 8)),
        post_stimulus_drain_cycles=int(raw_defaults.get("post_stimulus_drain_cycles", 160)),
    )

    # ── Flows
    raw_flows = raw.get("flows", []) or []
    if not isinstance(raw_flows, list):
        _raise_contract_error(
            f"verify.design.yml: 'flows' must be a list ({path})",
            action="Rewrite 'flows' as a YAML list of flow mappings.",
        )

    flows: list[FlowDeclaration] = []
    for i, fl_raw in enumerate(raw_flows):
        if not isinstance(fl_raw, dict):
            _raise_contract_error(
                f"verify.design.yml: flow[{i}] must be a mapping ({path})",
                action="Ensure every flow entry is a YAML mapping with the required keys.",
            )

        # Required fields
        for req_key in REQUIRED_FIELDS:
            if not fl_raw.get(req_key):
                _raise_contract_error(
                    f"verify.design.yml: flow[{i}] missing required field {req_key!r} ({path})",
                    action=f"Add the required field {req_key!r} to flow[{i}] and retry.",
                )

        # Validate (kind, backend) against the supported matrix.
        # Experimental flows are allowed only when the flow explicitly opts in
        # with  experimental: true.  Unsupported combinations are rejected.
        _kind    = str(fl_raw["kind"])
        _backend = str(fl_raw["backend"])
        _experimental_opt_in = bool(fl_raw.get("experimental", False))

        from arc.verify.supported_matrix import (  # noqa: PLC0415
            FlowClassification,
            classify_flow,
            validate_flow_matrix,
        )
        _cls = classify_flow(_kind, _backend)
        if _cls == FlowClassification.EXPERIMENTAL and not _experimental_opt_in:
            _errs = validate_flow_matrix(_kind, _backend)
            _raise_contract_error(
                f"verify.design.yml: flow[{i}] ({fl_raw.get('name', '?')!r}): "
                + "; ".join(_errs)
                + "\n  To acknowledge the risk, add experimental: true to this flow.",
                action="Use a supported flow kind/backend pair or explicitly opt into the experimental combination.",
            )
        # Note: UNSUPPORTED (completely unknown) kinds are not rejected at load
        # time — they will fail at runtime when the backend attempts to execute
        # them.  This preserves schema-level validation ordering (dataset refs,
        # required fields) independent of flow capability checks.

        # Dataset reference must match a declared dataset
        ds_ref = str(fl_raw["dataset"])
        if not any(d.name == ds_ref for d in datasets):
            _raise_contract_error(
                f"verify.design.yml: flow[{i}] references unknown dataset {ds_ref!r} ({path})",
                action="Declare the dataset under top-level 'datasets' or update the flow reference.",
            )

        # Checker (optional)
        checker_raw = fl_raw.get("checker")
        checker: CheckerDeclaration | None = None
        if checker_raw and isinstance(checker_raw, dict):
            checker = CheckerDeclaration(
                mode=_str_or_none(checker_raw.get("mode")),
                binary=_str_or_none(checker_raw.get("binary")),
                observed_log=_str_or_none(checker_raw.get("observed_log")),
                args=tuple(str(a) for a in (checker_raw.get("args") or [])),
                latency_cycles=_int_or_none(checker_raw.get("latency_cycles")),
                tolerance=_int_or_none(checker_raw.get("tolerance")),
                pass_condition=_str_or_none(checker_raw.get("pass_condition")),
            )

        xsim_raw = fl_raw.get("xsim")
        xsim: XsimDeclaration | None = None
        if xsim_raw and isinstance(xsim_raw, dict):
            extra_sources: list[XsimExtraSourceDeclaration] = []
            for src in (xsim_raw.get("extra_sources") or []):
                if not isinstance(src, dict) or not src.get("path"):
                    _raise_contract_error(
                        f"verify.design.yml: flow[{i}] xsim.extra_sources entries must have 'path' ({path})",
                        action="Add a 'path' field to each xsim.extra_sources entry.",
                    )
                extra_sources.append(XsimExtraSourceDeclaration(
                    path=str(src["path"]),
                    lang=str(src.get("lang", "verilog")),
                ))
            xsim = XsimDeclaration(
                use_sv_flag=(None if xsim_raw.get("use_sv_flag") is None
                             else bool(xsim_raw.get("use_sv_flag"))),
                top_lib=_str_or_none(xsim_raw.get("top_lib")),
                extra_top_modules=tuple(str(v) for v in (xsim_raw.get("extra_top_modules") or [])),
                elab_libs=tuple(str(v) for v in (xsim_raw.get("elab_libs") or [])),
                extra_sources=tuple(extra_sources),
            )

        # Simulation overrides (optional)
        sim_raw = fl_raw.get("simulation", {}) or {}

        flows.append(FlowDeclaration(
            name=str(fl_raw["name"]),
            kind=str(fl_raw["kind"]),
            backend=str(fl_raw["backend"]),
            top_module=str(fl_raw["top_module"]),
            tb_module=str(fl_raw["tb_module"]),
            dut_rtl_source=str(fl_raw["dut_rtl_source"]),
            dataset=ds_ref,
            default_event_id=int(fl_raw.get("default_event_id", 1)),
            reset_cycles=_int_or_none(sim_raw.get("reset_cycles")),
            idle_cycles_after_reset=_int_or_none(sim_raw.get("idle_cycles_after_refresh",
                                                              sim_raw.get("idle_cycles_after_reset"))),
            post_stimulus_drain_cycles=_int_or_none(
                sim_raw.get("post_stimulus_drain_cycles")),
            checker=checker,
            xsim=xsim,
            coverage_intent=str(fl_raw.get("coverage_intent", "functional")),
            # Optional behavioral fields
            stimulus_mode=str(fl_raw.get("stimulus_mode", "svh_include")),
            wave_mode=str(fl_raw.get("wave_mode", "tcl")),
            rtl_source_type=str(fl_raw.get("rtl_source_type", "hls")),
            checker_mode=str(fl_raw.get("checker_mode", checker.mode if checker and checker.mode else "log_scan")),
            layout_mode=str(fl_raw.get("layout_mode", "flat")),
            requires_generated_tb=bool(fl_raw.get("requires_generated_tb", True)),
            # Informational fields
            flow_class=str(fl_raw.get("flow_class", "integration")),
            # Experimental opt-in
            experimental=bool(fl_raw.get("experimental", False)),
        ))

    if not flows:
        _raise_contract_error(
            f"verify.design.yml: at least one flow is required ({path})",
            action="Declare at least one verification flow under 'flows'.",
        )

    return VerifyDesignContract(
        plugin=plugin,
        datasets=tuple(datasets),
        defaults=defaults,
        flows=tuple(flows),
        source_path=path,
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def find_verify_design(search_dir: Path) -> "Path | None":
    """Return the path to the design verification contract in *search_dir*.

    Searches for ``design.verification.yml`` first (canonical), then
    ``verify.design.yml`` (legacy name).  Returns ``None`` if neither exists.
    """
    for name in ("design.verification.yml", "verify.design.yml"):
        candidate = Path(search_dir) / name
        if candidate.exists():
            return candidate
    return None


def _int_or_none(val: Any) -> int | None:
    if val is None:
        return None
    return int(val)


def _str_or_none(val: Any) -> str | None:
    if val is None:
        return None
    return str(val)
