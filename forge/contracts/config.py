# config.py
from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import List, Literal, Optional, Tuple, Dict, Any
import yaml


# Schema-version identity for the two user-authored YAML schemas this
# module loads — see forge/core/schema_version.py for
# the shared compatibility policy these are checked against.
DESIGN_SCHEMA_VERSION = "1.0"
MODULE_REGISTRY_SCHEMA_VERSION = "1.0"


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = json.loads(json.dumps(base))
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_declared_path(path_value: str | Path, root: Path) -> Path:
    expanded = Path(os.path.expandvars(str(path_value))).expanduser()
    if not expanded.is_absolute():
        expanded = root / expanded
    return expanded.resolve()


class UnknownConfigKeyError(ValueError):
    """A design/registry file declares a key the schema doesn't define.

    Its own class so callers can distinguish "the user's file is wrong"
    (a real, reportable finding — exit 1) from "FORGE fell over"
    (exit 2). See docs/development/cli_exit_codes.md.
    """


def _reject_unknown_keys(cls: type, data: Dict[str, Any], source: Path) -> None:
    """Fail with a readable message on keys the dataclass doesn't define.

    Passing an unrecognised key through to a dataclass constructor produces
    `__init__() got an unexpected keyword argument 'x'` — technically true,
    useless to the person who typed it. This names the file, the key, and
    the closest real field, which is almost always the actual typo.
    """
    known = {f.name for f in dataclass_fields(cls)}
    unknown = [k for k in data if k not in known]
    if not unknown:
        return

    lines = [f"{source}: unrecognised key" + ("s" if len(unknown) > 1 else "") + ":"]
    for key in sorted(unknown):
        near = difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.6)
        hint = f" — did you mean {near[0]!r}?" if near else ""
        lines.append(f"  {key!r}{hint}")
    if not any("did you mean" in ln for ln in lines):
        lines.append(f"  valid top-level keys: {', '.join(sorted(known))}")
    raise UnknownConfigKeyError("\n".join(lines))


def _load_registry(registry_path: Path) -> dict[str, dict]:
    """Load a modules.yml registry, returning identity fields keyed by canonical module name.

    Only the identity fields (name, kind, top, src, includes, rtl_lang, etc.) are
    returned. The build: and verify: sections are intentionally stripped — they are
    consumed exclusively by the HLS orchestration catalog and verification framework,
    respectively.  Source paths are pre-resolved to absolute paths relative to the
    registry file so that downstream config.py path resolution works regardless of
    where the consuming design.yml lives.
    """
    raw = yaml.safe_load(registry_path.read_text()) or {}
    registry_root = registry_path.parent
    # Fields that belong to identity (passed through to Module dataclass)
    IDENTITY_FIELDS = {
        "name", "kind", "top", "src", "includes", "rtl_lang", "vhdl_library",
        "vhdl_version", "rtl_include_dirs", "verilog_defines", "rtl_packages",
        "cflags", "stages",
        # Latency annotation fields — recognized pass-through keys, not
        # identity/equality semantics; converted into a typed ModuleTiming
        # by _pop_timing() at Module-construction time, not stored flat.
        "latency_cycles", "latency_hint", "variable_latency",
        # Structured latency: {kind: fixed|bounded|elastic, ...} declaration
        # — same pass-through treatment as the three flat fields above.
        "latency",
    }
    result: dict[str, dict] = {}
    for raw_mod in raw.get("modules", []):
        name = raw_mod.get("name")
        if not name:
            continue
        mod_entry = {k: v for k, v in raw_mod.items() if k in IDENTITY_FIELDS}
        # Pre-resolve src and includes to absolute paths so they resolve correctly
        # regardless of where the consuming design.yml is located.
        if "src" in mod_entry:
            mod_entry["src"] = [
                str(resolve_declared_path(p, registry_root))
                for p in mod_entry["src"]
            ]
        if "includes" in mod_entry:
            mod_entry["includes"] = [
                str(resolve_declared_path(p, registry_root))
                for p in mod_entry["includes"]
            ]
        result[name] = mod_entry
    return result

# ────────────────────────────────────────────────────────────
# Public types / constants
# ────────────────────────────────────────────────────────────

Stage       = Literal["project", "csim", "synth", "cosim", "ip", "clean"]
DEFAULT_STAGES: List[Stage] = ["project", "synth", "ip"]

BlockProto  = Literal["chain", "hs", "none"]
ModuleKind  = Literal["hls", "rtl"]
RtlLang     = Literal["vhdl", "verilog", "systemverilog"]

# ────────────────────────────────────────────────────────────
# Dataclasses
# ────────────────────────────────────────────────────────────

@dataclass
class TestBenchArgs:
    json_file: Optional[str] = None
    num_events: Optional[int] = None

@dataclass
class TestBenchConfig:
    """Configuration for testbench generation and stimulus"""
    xml_stimulus_path: Optional[str] = None  # Path to XML file (relative to design.yml)
    event_id: int = 1  # Which event to extract from XML
    generate: bool = True  # Whether to generate testbench by default

# The three timing kinds this module requires. Duplicated (not
# imported) from forge.analysis.latency_model.LATENCY_KINDS deliberately —
# forge/contracts is the schema/config layer and forge/analysis is a
# downstream consumer of it; importing analysis from here would be a
# wrong-direction dependency. Three fixed, closed values, unlikely to
# drift; if it ever needs to grow, both copies grow together.
_LATENCY_KINDS = ("fixed", "bounded", "elastic")


@dataclass
class LatencyDeclaration:
    """A structured ``latency: {kind: fixed|bounded|elastic, ...}``
    declaration — coexists with,
    does not replace, :class:`ModuleTiming`'s existing flat
    ``latency_cycles``/``latency_hint``/``variable_latency`` fields (real,
    currently-used YAML — ``latency_hint`` appears throughout both
    reference plugins). ``latency: {kind: elastic}`` is the new,
    equivalent, going-forward-preferred spelling for
    ``variable_latency: true`` — see :func:`_pop_timing`'s normalization,
    which sets ``variable_latency=True`` under the hood so every existing
    ``variable_latency`` consumer keeps working unchanged.
    """
    kind: str
    cycles: Optional[int] = None
    min_cycles: Optional[int] = None
    max_cycles: Optional[int] = None

    def __post_init__(self) -> None:
        if self.kind not in _LATENCY_KINDS:
            raise ValueError(
                f"LatencyDeclaration: 'kind' must be one of {_LATENCY_KINDS}, "
                f"got {self.kind!r}. (User-facing YAML input should be caught "
                "earlier by RegistryValidator; this is a defense-in-depth "
                "check for direct construction.)"
            )
        if self.kind == "fixed" and self.cycles is None:
            raise ValueError("LatencyDeclaration: kind='fixed' requires 'cycles'")
        if self.kind == "bounded":
            if self.min_cycles is None or self.max_cycles is None:
                raise ValueError(
                    "LatencyDeclaration: kind='bounded' requires both "
                    "'min_cycles' and 'max_cycles'"
                )
            if self.min_cycles > self.max_cycles:
                raise ValueError(
                    f"LatencyDeclaration: min_cycles ({self.min_cycles}) must "
                    f"be <= max_cycles ({self.max_cycles})"
                )
        if self.kind == "elastic" and (
            self.cycles is not None or self.min_cycles is not None or self.max_cycles is not None
        ):
            raise ValueError(
                "LatencyDeclaration: kind='elastic' must not declare "
                "cycles/min_cycles/max_cycles — elastic timing has no fixed "
                "cycle count by definition"
            )


@dataclass
class ModuleTiming:
    """Optional per-module latency/timing metadata from the module
    registry (modules.yml) or a design-level module entry.

    Absent (``Module.timing is None``) is the default and behaves
    identically to every existing consumer that predates this field.

    Resolution order used by consumers (forge.analysis.latency_static,
    the canonical IR): a structured ``latency:`` declaration (explicit,
    authoritative) > ``latency_cycles`` (explicit,
    authoritative, the older flat spelling) > an externally-supplied HLS
    synthesis report (not modeled here — a runtime overlay, not
    registry/design data) > ``latency_hint`` (a rough manual estimate) >
    unknown.
    """
    latency_cycles: Optional[int] = None
    latency_hint: Optional[int] = None
    variable_latency: bool = False
    latency: Optional[LatencyDeclaration] = None

    def __post_init__(self) -> None:
        if self.latency_cycles is not None and self.variable_latency:
            raise ValueError(
                "ModuleTiming: 'latency_cycles' (a fixed explicit latency) "
                "and 'variable_latency: true' are contradictory — declare "
                "only one. (User-facing YAML input should be caught earlier "
                "by RegistryValidator; this is a defense-in-depth check for "
                "direct construction.)"
            )
        # Deliberately no flat-vs-latency: contradiction check here: a
        # `latency: {kind: elastic}` declaration legitimately normalizes to
        # variable_latency=True (see _pop_timing) so every existing
        # variable_latency consumer keeps working — that's the intended
        # coexistent representation, not a contradiction. The real
        # both-syntaxes-used-at-once check happens in _pop_timing, against
        # the *original* raw YAML keys, before that normalization runs.


def _pop_timing(raw: dict) -> Optional["ModuleTiming"]:
    """Pop latency_cycles/latency_hint/variable_latency/latency out of a
    merged module dict and return a ModuleTiming, or None if none were
    present — so modules that don't declare timing are completely
    unaffected."""
    has_flat = any(k in raw for k in ("latency_cycles", "latency_hint", "variable_latency"))
    has_latency_block = "latency" in raw
    lat_cycles = raw.pop("latency_cycles", None)
    lat_hint = raw.pop("latency_hint", None)
    variable = raw.pop("variable_latency", False)
    latency_block = raw.pop("latency", None)
    if not has_flat and not has_latency_block:
        return None

    if has_flat and has_latency_block:
        raise ValueError(
            "ModuleTiming: the structured 'latency:' block and one of the "
            "flat 'latency_cycles'/'latency_hint'/'variable_latency' fields "
            "are two ways of declaring the same thing — declare only one. "
            "(User-facing YAML input should be caught earlier by "
            "RegistryValidator; this is a defense-in-depth check for direct "
            "construction.)"
        )

    declaration: Optional[LatencyDeclaration] = None
    if latency_block is not None:
        declaration = LatencyDeclaration(
            kind=latency_block.get("kind"),
            cycles=latency_block.get("cycles"),
            min_cycles=latency_block.get("min_cycles"),
            max_cycles=latency_block.get("max_cycles"),
        )
        if declaration.kind == "elastic":
            # Soft-migration alias (see LatencyDeclaration's docstring):
            # every existing variable_latency consumer keeps working with
            # zero changes, while richer .latency data is also available.
            variable = True

    return ModuleTiming(
        latency_cycles=int(lat_cycles) if lat_cycles is not None else None,
        latency_hint=int(lat_hint) if lat_hint is not None else None,
        variable_latency=bool(variable),
        latency=declaration,
    )


@dataclass
class Module:
    # Essential identity
    name: str
    top:  str
    src:  List[str]

    # Classification + RTL knobs
    kind:         ModuleKind = "hls"
    rtl_lang:     Optional[RtlLang] = None
    vhdl_library: Optional[str]     = None
    vhdl_version: Optional[str]     = None

    # RTL ancillary include/defines
    rtl_include_dirs: List[str] = field(default_factory=list)
    verilog_defines:  List[str] = field(default_factory=list)

    # HDL packages (e.g. VHDL packages) to import first
    rtl_packages: List[str] = field(default_factory=list)
    
    # RTL module parameters (e.g., for parameterized Verilog modules)
    parameters: Dict[str, Any] = field(default_factory=dict)

    # HLS fields
    tb:        List[str] = field(default_factory=list)
    includes:  List[str] = field(default_factory=list)
    cflags:    List[str] = field(default_factory=list)
    stages:    List[Stage] = field(default_factory=lambda: DEFAULT_STAGES.copy())
    version:   str = "1.0"
    tb_args:   Optional[TestBenchArgs] = None
    csim_opts: Optional[str] = None
    csynth_opts: Optional[str] = None
    cosim_opts:  Optional[str] = None
    ip_opts:     Optional[str] = None

    # Instances & externalization hints
    instances:            int = 1
    external_in_ports:    List[str] = field(default_factory=list)
    external_out_ports:   List[str] = field(default_factory=list)
    
    # Debug mode - expose all module ports as top-level debug ports
    debug: bool = False

    # Output alignment delays (new)
    output_delays: Dict[str, int] = field(default_factory=dict)

    # When a design.yml entry uses  ref: <canonical>  the canonical registry
    # name is preserved here so that ip_info and contract lookups use the right
    # key even when the instance has an override name (e.g. out_csp_best_constr
    # → canonical ip_key = csp_pack_bx_sync).
    ip_info_key: Optional[str] = None

    # Optional per-module latency/timing metadata (see ModuleTiming) — a
    # separate typed field, not flattened into identity-field semantics.
    timing: Optional[ModuleTiming] = None

    # Resolved paths (filled by loader)
    abs_src:              List[Path] = field(default_factory=list, init=False)
    abs_tb:               List[Path] = field(default_factory=list, init=False)
    abs_includes:         List[Path] = field(default_factory=list, init=False)
    abs_rtl_include_dirs: List[Path] = field(default_factory=list, init=False)
    abs_rtl_packages:     List[Path] = field(default_factory=list, init=False)

    def all_sources_exist(self) -> bool:
        return all(p.exists() for p in (*self.abs_src, *self.abs_tb))

@dataclass
class Connection:
    from_:    str
    to:       str  # Single destination (fan-out is expanded during load)
    port_map: List[Tuple[str, str]] = field(default_factory=list)
    port_map_ranges: List[dict]     = field(default_factory=list)
    register_stages: int = 0  # Number of pipeline register stages to insert
    delay_cycles: int = 0     # Number of delay cycles to insert (using signal_delay)
    contract_wiring: bool = False  # Derive array port_map_ranges from contract wiring_kind matching

    # Opaque physical-boundary tag. When set, the generator emits a protected
    # slr_crossing_delay instance instead of a plain signal_delay, gives it a
    # stable deterministic name, and writes a stage-level crossing manifest.
    # The tag is resolved to physical SLR placement exclusively by blobfish;
    # arc-framework and omtf-firmware treat it as an opaque string.
    # A boundary tag requires delay_cycles > 0 or register_stages > 0.
    boundary: Optional[str] = None

    # Declares an approved clock/reset-domain-crossing adapter for this
    # connection: {"kind": "2ff_sync"|"async_fifo",
    # "depth": int|None}. Covers both clock- and reset-crossing approval
    # for the connection — see forge.contracts.cdc.verify_cdc, which is
    # what actually checks a connection against this declaration.
    cdc: Optional[Dict[str, Any]] = None

    # Declares this connection as a control/reset strobe (e.g. an
    # event-boundary reset, a bank-swap pulse, an output-stamp tag) rather
    # than a data path. Found needed on a real external consumer's
    # topology: a bunch-crossing timing controller distributes several
    # such strobes (new_event_rgf/mem/arb/best/nn) whose arrival cycle is
    # deliberately derived from each receiving stage's own accumulated datapath depth —
    # not required to exact-cycle-align with a sibling *data* predecessor
    # the way two real data paths into the same merge point must. See
    # forge.analysis.latency_static.graph for how this exempts the
    # connection from exact-cycle merge-point comparison, the same way a
    # cdc: mailbox_transfer/async_fifo edge is already exempted for a
    # different reason (genuinely unknowable, not deliberately scheduled).
    control_strobe: bool = False
    # The data on this connection *enters the design at the source node* —
    # through one of that node's ``external_in_ports`` — rather than flowing
    # into it from its own predecessors. A node can be both a merge point for
    # some inputs and an injection point for others (an aggregator that
    # gathers already-decoded streams from upstream while a second family of
    # raw streams arrives straight at its own top-level ports), and a single
    # scalar node latency cannot express that: every branch leaving the node
    # inherits the deepest arrival time among its predecessors, including
    # branches whose data never traversed that path. Setting this stops the
    # latency chain-fold at the source node, charging the branch only that
    # node's own latency, so an injected stream is not billed for an upstream
    # it never travelled. Found on a real external consumer's topology, where
    # a stream arriving directly at an aggregator's own input ports was
    # charged the full decode depth of the *other*, genuinely upstream
    # streams that aggregator gathers, on top of its own alignment delay —
    # reporting a large phantom mismatch at two downstream merge points on a
    # design whose paths are in fact aligned.
    external_source: bool = False

@dataclass
class InstanceAssign:
    """Maps a range of producer instances to a consumer coordinate.

    Either the legacy scalar ``partition`` label or the structured
    ``coordinates`` mapping (or both, if consistent) must be set — see
    ``forge.contracts.coordinates``.
    """
    instances: Tuple[int, int]  # [start, end) half-open range
    partition: Optional[str] = None
    coordinates: Optional[Dict[str, Any]] = None

    def coordinate_key(self):
        from .coordinates import coordinate_key
        return coordinate_key({"partition": self.partition, "coordinates": self.coordinates})

@dataclass
class TopologyGroup:
    """Declares a contract-derived grouped connection between modules."""
    name: str
    family: str
    from_: str
    to: str  # Single destination (fan-out expanded during load)
    wiring_kind: Optional[str] = None  # filter to specific wiring_kind (None = all in family)
    instance_assign: List[InstanceAssign] = field(default_factory=list)
    port_map: List[Tuple[str, str]] = field(default_factory=list)  # supplementary scalar pairs
    role_pairs: Optional[List[Tuple[str, str]]] = None  # explicit role pairing override
    src_instance_offset: int = 0  # source instance index offset for diagonal instance mapping
    notes: Optional[str] = None
    # Same meaning as Connection.external_source: the streams this group
    # carries enter the design at ``from_`` through that module's
    # ``external_in_ports``, so the latency chain-fold must stop there.
    external_source: bool = False

@dataclass
class ControlSignalTarget:
    """Describes where a control signal should be distributed and with what delay."""
    module: str
    delay_cycles: int

@dataclass
class ControlSignal:
    """Configuration for a broadcast control signal (e.g., new_event)."""
    type: str = "broadcast"  # Currently only "broadcast" is supported
    width: int = 1
    distribution: List[ControlSignalTarget] = field(default_factory=list)

@dataclass
class SweepSpec:
    n_iters: Optional[int]                    = None
    vary:    Optional[Dict[str, List[Any]]]   = None
    fixed:   Optional[Dict[str, Any]]         = None
    list:    Optional[List[Dict[str, Any]]]   = None

@dataclass
class AllowedUnconnected:
    """Glob patterns for ports that are intentionally left open or tied to zero.

    Each pattern is matched against ``instance.port`` using fnmatch.
    """
    open_outputs: List[str] = field(default_factory=list)
    tied_inputs:  List[str] = field(default_factory=list)

@dataclass
class DesignConfig:
    part:              str
    clock_period:      float
    max_parallel_jobs: int = 1

    # External synchronous reference period in ns that testbench timing
    # parameters (e.g. batches-per-event, counter modulo) are derived
    # against — for example an accelerator's bunch-crossing clock. Defaults
    # to 25.0 ns (the LHC 40 MHz BX period) when unset, for designs that
    # don't declare one.
    reference_period_ns: Optional[float] = None

    modules:          List[Module]         = field(default_factory=list)
    connections:      List[Connection]     = field(default_factory=list)
    topology_groups:  List[TopologyGroup]  = field(default_factory=list)
    sweeps:           Dict[str, SweepSpec] = field(default_factory=dict)

    block_protocol: BlockProto = "none"
    connect_clock:  bool       = True
    connect_reset:  bool       = True

    # Delay configuration
    control_signals: Dict[str, ControlSignal] = field(default_factory=dict)

    
    # Testbench configuration (optional)
    testbench: Optional[TestBenchConfig] = None

    # Plugin-owned metadata for generated interface artifacts.
    interface_metadata: Dict[str, Any] = field(default_factory=dict)
    interface_metadata_file: Optional[str] = None

    # Optional module registry path (relative to this design.yml or absolute).
    # When set, module entries in this design may use  ref: <canonical_name>
    # to inherit identity fields (top, src, kind, includes) from the registry.
    registry: Optional[str] = None

    # Intentionally unconnected ports (glob patterns matched against instance.port).
    allowed_unconnected: AllowedUnconnected = field(default_factory=AllowedUnconnected)

    # Optional schema-version identity. None means the
    # file doesn't declare one — a fully backward-compatible, silent case,
    # not an error (see forge/core/schema_version.py). Checked by
    # DesignValidator.validate_schema_version(), not here.
    schema_version: Optional[str] = None

    # Optional, purely descriptive domain-relationship declarations,
    # keyed by the already-resolved net name (e.g.
    # "ap_clk" — see forge.contracts.domains.resolve_domain_nets). Each
    # entry: {"derived_from": str|None, "ratio": int|None, "sync": str|None}.
    # `derived_from`/`ratio` do NOT auto-approve crossings between related
    # domains — every data crossing still needs an explicit
    # per-connection `cdc:` declaration; this is documentation, not an
    # enforcement mechanism. `sync` (reset_domains only) is the one
    # exception: `sync: reset_sync`
    # actually triggers generation of a real reset synchronizer for that
    # destination reset domain — a reset crossing is a domain property,
    # not a `connections:`-level data crossing, so it doesn't go through
    # `cdc:` at all. See forge.contracts.cdc's module docstring.
    clock_domains: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reset_domains: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ────────────────────────────────────────────────────────
    # Loader (now with validate_sources switch)
    # ────────────────────────────────────────────────────────
    @classmethod
    def load(
        cls,
        file: Path | str,
        *,
        validate_sources: bool = True,
    ) -> "DesignConfig":
        """
        Load a YAML design file into a DesignConfig.
        If validate_sources=False, missing src/tb/rtl_packages files do not raise.
        """
        yaml_path = Path(file).expanduser().resolve()

        # Allow pointing to a directory
        if yaml_path.is_dir():
            for candidate in ("design.yaml", "design.yml"):
                p = yaml_path / candidate
                if p.exists():
                    yaml_path = p
                    break
            else:
                raise FileNotFoundError(f"No design.yaml or design.yml in {yaml_path}")

        if not yaml_path.exists():
            raise FileNotFoundError(f"Config file not found: {yaml_path}")
        if yaml_path.suffix not in (".yaml", ".yml"):
            raise ValueError(f"Config file must end in .yaml/.yml: {yaml_path}")

        data = yaml.safe_load(yaml_path.read_text())

        # ── Extract sweeps
        raw_sweeps = data.pop("sweeps", {}) or {}
        sweeps: Dict[str, SweepSpec] = {}
        for mod_name, spec in raw_sweeps.items():
            n = spec.get("n_iters")
            sp = SweepSpec(
                n_iters = n,
                vary    = spec.get("vary"),
                fixed   = spec.get("fixed"),
                list    = spec.get("list"),
            )
            if sp.n_iters is None:
                raise ValueError(f"Sweep for module '{mod_name}' missing required 'n_iters'")
            if sp.vary:
                for k, lst in sp.vary.items():
                    if len(lst) != sp.n_iters:
                        raise ValueError(
                            f"Sweep '{mod_name}': length of vary[{k}] is {len(lst)}, expected n_iters={sp.n_iters}"
                        )
            if sp.list and len(sp.list) != sp.n_iters:
                raise ValueError(
                    f"Sweep '{mod_name}': length of list is {len(sp.list)}, expected n_iters={sp.n_iters}"
                )
            sweeps[mod_name] = sp

        # High-level toggles
        proto    = data.pop("block_protocol", "chain")
        clk_conn = data.pop("connect_clock",   True)
        rst_conn = data.pop("connect_reset",   True)

        module_dicts     = data.pop("modules",     [])
        connection_dicts = data.pop("connections", [])

        # ── Module registry: resolve ref: entries before constructing Module objects
        registry_path_str = data.pop("registry", None)
        registry_modules: dict[str, dict] = {}
        if registry_path_str:
            registry_yaml_path = resolve_declared_path(registry_path_str, yaml_path.parent)
            if not registry_yaml_path.exists():
                raise FileNotFoundError(f"registry file not found: {registry_yaml_path}")
            registry_modules = _load_registry(registry_yaml_path)

        # ── Modules
        modules: List[Module] = []
        for m in module_dicts:
            ref = m.pop("ref", None)
            if ref:
                if ref not in registry_modules:
                    raise ValueError(
                        f"Module '{m.get('name', '?')}' has ref: '{ref}' "
                        f"but that name was not found in the registry. "
                        f"Available: {', '.join(sorted(registry_modules))}"
                    )
                # Registry provides identity; design-level fields override
                base = dict(registry_modules[ref])
                base.update(m)
                m = base
                # Preserve canonical registry name for ip_info/contract lookups
                # (the instance name in m["name"] may differ from the ip type key)
                if "ip_info_key" not in m:
                    m["ip_info_key"] = ref
            # Pop latency fields into a typed ModuleTiming *after* the
            # ref-merge above, so a design-level inline override of e.g.
            # latency_hint correctly wins over the registry's value — same
            # "design-level fields override registry identity" precedence
            # used for every other field.
            m["timing"] = _pop_timing(m)
            mod = Module(**m)
            # Default: RTL modules do not run HLS stages unless explicitly set
            if mod.kind == "rtl" and m.get("stages") is None:
                mod.stages = []
            if mod.external_in_ports is None:
                mod.external_in_ports = []
            if mod.external_out_ports is None:
                mod.external_out_ports = []
            if mod.output_delays is None:
                mod.output_delays = {}
            modules.append(mod)

        # ── Connections
        connections: List[Connection] = []
        for c in connection_dicts:
            if "from" not in c or "to" not in c:
                raise ValueError(f"Bad connection entry: {c}")
            raw_map = c.get("port_map", [])
            pm: List[Tuple[str, str]] = []
            for pair in raw_map:
                if not (isinstance(pair, list) and len(pair) == 2):
                    raise ValueError(f"port_map entries must be 2-tuples: {pair}")
                pm.append((pair[0], pair[1]))
            raw_ranges = c.get("port_map_ranges", []) or []
            reg_stages = c.get("register_stages", 0)
            delay_cycles = c.get("delay_cycles", 0)
            contract_wiring = c.get("contract_wiring", False)
            boundary = c.get("boundary", None)
            cdc = c.get("cdc", None)
            control_strobe = bool(c.get("control_strobe", False))
            external_source = bool(c.get("external_source", False))

            # Validation: a boundary tag without an actual register stage is
            # meaningless — the generator cannot emit a protected crossing delay
            # with zero depth.
            if boundary and int(delay_cycles) <= 0 and int(reg_stages) <= 0:
                raise ValueError(
                    f"Connection {c['from']!r} -> {c.get('to')!r} sets "
                    f"boundary={boundary!r} but delay_cycles and register_stages "
                    "are both zero. A boundary tag must protect an inserted "
                    "register stage."
                )

            # Validation: cdc: declares an approved clock/reset-domain-
            # crossing adapter — see
            # forge.contracts.cdc.KNOWN_CDC_KINDS (kept in sync with the
            # literal tuple below by hand; cdc.py cannot be imported here,
            # it already imports DesignConfig from this module).
            # 'reset_sync' is deliberately NOT accepted here — a reset
            # crossing is a property of a destination reset *domain*, not
            # a data connection between two modules; it is declared under
            # reset_domains.<name>.sync instead (see _pop_domain_relationships).
            if cdc is not None:
                if not isinstance(cdc, dict) or "kind" not in cdc:
                    raise ValueError(
                        f"[ATG021] Connection {c['from']!r} -> {c.get('to')!r}: 'cdc' "
                        f"must be a mapping with at least 'kind', got: {cdc!r}"
                    )
                kind = cdc["kind"]
                if kind not in ("level_sync", "2ff_sync", "pulse_sync", "mailbox_transfer", "async_fifo"):
                    raise ValueError(
                        f"[ATG021] Connection {c['from']!r} -> {c.get('to')!r}: cdc.kind "
                        f"{kind!r} must be one of ('level_sync' (alias '2ff_sync'), "
                        "'pulse_sync', 'mailbox_transfer', 'async_fifo')"
                    )
                if kind == "2ff_sync":
                    # Backwards-compatible alias — normalize to the
                    # canonical name so every downstream consumer (the
                    # generator, the IR builder, verify_cdc) only ever
                    # has to recognize one spelling.
                    kind = "level_sync"
                    cdc = {**cdc, "kind": "level_sync"}

                depth = cdc.get("depth")
                if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0):
                    raise ValueError(
                        f"[ATG022] Connection {c['from']!r} -> {c.get('to')!r}: cdc.depth "
                        f"must be a positive integer, got: {depth!r}"
                    )

                if kind == "async_fifo":
                    if depth is None:
                        raise ValueError(
                            f"[ATG022] Connection {c['from']!r} -> {c.get('to')!r}: "
                            "cdc.kind='async_fifo' requires an explicit 'depth' — a "
                            "real hardware FIFO must not get a silent default size."
                        )
                    if depth & (depth - 1) != 0:
                        raise ValueError(
                            f"[ATG026] Connection {c['from']!r} -> {c.get('to')!r}: "
                            f"cdc.depth={depth} must be a power of two for "
                            "kind='async_fifo' (Gray-code pointer comparison requires it)."
                        )

                if kind == "pulse_sync":
                    min_spacing = cdc.get("min_spacing_cycles")
                    if min_spacing is None:
                        raise ValueError(
                            f"[ATG022] Connection {c['from']!r} -> {c.get('to')!r}: "
                            "cdc.kind='pulse_sync' requires 'min_spacing_cycles' — the "
                            "declared minimum source-event spacing this synchronizer "
                            "assumes."
                        )
                    if not isinstance(min_spacing, int) or isinstance(min_spacing, bool) or min_spacing <= 0:
                        raise ValueError(
                            f"[ATG022] Connection {c['from']!r} -> {c.get('to')!r}: "
                            f"cdc.min_spacing_cycles must be a positive integer, got: "
                            f"{min_spacing!r}"
                        )

            # Expand fan-out connections (to as list) into individual connections
            to_modules = c["to"] if isinstance(c["to"], list) else [c["to"]]
            for to_module in to_modules:
                connections.append(
                    Connection(
                        from_           = c["from"],
                        to              = to_module,
                        port_map        = pm,
                        port_map_ranges = raw_ranges,
                        register_stages = reg_stages,
                        delay_cycles    = delay_cycles,
                        contract_wiring = contract_wiring,
                        boundary        = boundary,
                        cdc             = cdc,
                        control_strobe  = control_strobe,
                        external_source = external_source,
                    )
                )

        # forge.generation.generators.
        # structural_verilog's cdc_map (and conn_map's own per-instance-pair
        # grouping) is keyed by (src_module, dst_module) alone, not per-pin —
        # a real limitation discovered wiring a genuine multi-crossing design
        # for the first time (design_cdc.yml, vision_pipeline_demo), not
        # something any existing design/test ever exercised. Declaring two
        # `cdc:` connections between the same module pair with *different*
        # kinds previously merged silently (last-declared kind wins for
        # every pin between that pair — a real risk of generating the wrong
        # synchronizer for a real signal with zero diagnostic). Turned into
        # a real, actionable load-time error instead: route each distinct
        # cdc kind between the same two modules through its own dedicated
        # module pair (see design_cdc.yml's per-crossing-kind module split
        # for the pattern). A genuinely per-pin cdc_map is a larger,
        # separate refactor not attempted here.
        _cdc_kind_by_pair: Dict[Tuple[str, str], str] = {}
        for conn in connections:
            if not conn.cdc:
                continue
            pair = (conn.from_, conn.to)
            kind = conn.cdc.get("kind")
            prior_kind = _cdc_kind_by_pair.get(pair)
            if prior_kind is not None and prior_kind != kind:
                raise ValueError(
                    f"[ATG027] Multiple connections from {conn.from_!r} to {conn.to!r} "
                    f"declare different cdc kinds ({prior_kind!r} and {kind!r}) — "
                    "forge.generation.generators.structural_verilog's cdc_map is keyed by "
                    "(src_module, dst_module) only, not per-pin, so the second "
                    "declaration would silently overwrite the first for every pin "
                    "between this module pair. Route each distinct cdc kind between "
                    "the same two modules through its own dedicated module pair."
                )
            _cdc_kind_by_pair[pair] = kind

        # ── Topology Groups
        topo_group_dicts = data.pop("topology_groups", []) or []
        topology_groups: List[TopologyGroup] = []
        for tg in topo_group_dicts:
            if "name" not in tg or "family" not in tg or "from" not in tg or "to" not in tg:
                raise ValueError(f"topology_group missing required fields (name/family/from/to): {tg}")
            raw_pm = tg.get("port_map", [])
            tg_pm: List[Tuple[str, str]] = []
            for pair in raw_pm:
                if not (isinstance(pair, list) and len(pair) == 2):
                    raise ValueError(f"topology_group port_map entries must be 2-tuples: {pair}")
                tg_pm.append((pair[0], pair[1]))
            raw_ia = tg.get("instance_assign", []) or []
            ia_list: List[InstanceAssign] = []
            for ia in raw_ia:
                inst_range = ia.get("instances")
                if not (isinstance(inst_range, list) and len(inst_range) == 2):
                    raise ValueError(f"instance_assign.instances must be [start, end): {ia}")
                entry = InstanceAssign(
                    instances=(int(inst_range[0]), int(inst_range[1])),
                    partition=ia.get("partition"),
                    coordinates=ia.get("coordinates"),
                )
                if entry.coordinate_key() is None:
                    raise ValueError(
                        f"instance_assign entry must declare 'partition' or "
                        f"'coordinates': {ia}"
                    )
                ia_list.append(entry)
            raw_rp = tg.get("role_pairs")
            rp: Optional[List[Tuple[str, str]]] = None
            if raw_rp is not None:
                rp = []
                for pair in raw_rp:
                    if not (isinstance(pair, list) and len(pair) == 2):
                        raise ValueError(f"topology_group role_pairs entries must be 2-tuples: {pair}")
                    rp.append((pair[0], pair[1]))
            to_modules = tg["to"] if isinstance(tg["to"], list) else [tg["to"]]
            src_offset = int(tg.get("src_instance_offset", 0))
            for to_module in to_modules:
                topology_groups.append(TopologyGroup(
                    name=tg["name"],
                    family=tg["family"],
                    from_=tg["from"],
                    to=to_module,
                    wiring_kind=tg.get("wiring_kind"),
                    instance_assign=ia_list,
                    port_map=tg_pm,
                    role_pairs=rp,
                    src_instance_offset=src_offset,
                    notes=tg.get("notes"),
                    external_source=bool(tg.get("external_source", False)),
                ))

        # ── Control Signals
        raw_ctrl_sigs = data.pop("control_signals", {}) or {}
        control_signals: Dict[str, ControlSignal] = {}
        for sig_name, sig_spec in raw_ctrl_sigs.items():
            dist_list = sig_spec.get("distribution", [])
            targets = []
            for d in dist_list:
                targets.append(ControlSignalTarget(
                    module=d["module"],
                    delay_cycles=d["delay_cycles"]
                ))
            control_signals[sig_name] = ControlSignal(
                type=sig_spec.get("type", "broadcast"),
                width=sig_spec.get("width", 1),
                distribution=targets
            )

        data.pop("bx_counter", None)  # removed; silently ignore if present in YAML

        schema_version = data.pop("schema_version", None)

        def _pop_domain_relationships(top_key: str, *, allow_sync: bool = False) -> Dict[str, Dict[str, Any]]:
            raw = data.pop(top_key, {}) or {}
            if not isinstance(raw, dict):
                raise ValueError(f"'{top_key}' must be a mapping keyed by domain (net) name, got: {raw!r}")
            out: Dict[str, Dict[str, Any]] = {}
            for domain_name, rel in raw.items():
                rel = rel or {}
                if not isinstance(rel, dict):
                    raise ValueError(f"'{top_key}.{domain_name}' must be a mapping, got: {rel!r}")
                derived_from = rel.get("derived_from")
                ratio = rel.get("ratio")
                if derived_from is not None and not isinstance(derived_from, str):
                    raise ValueError(f"'{top_key}.{domain_name}.derived_from' must be a string")
                if ratio is not None and (not isinstance(ratio, int) or isinstance(ratio, bool) or ratio <= 0):
                    raise ValueError(f"'{top_key}.{domain_name}.ratio' must be a positive integer")

                # 'sync': declares a
                # real reset synchronizer for this destination reset
                # domain — only meaningful on reset_domains (a reset
                # crossing is a domain property, not a data connection;
                # see forge.contracts.cdc's module docstring for why this
                # is NOT a Connection.cdc field). Requires derived_from,
                # since you can't synchronize a reset with no declared
                # source domain to synchronize it from.
                sync = rel.get("sync")
                if sync is not None:
                    if not allow_sync:
                        raise ValueError(
                            f"[ATG025] '{top_key}.{domain_name}.sync' is not supported — "
                            "'sync' is only valid under reset_domains."
                        )
                    if sync != "reset_sync":
                        raise ValueError(
                            f"[ATG025] 'reset_domains.{domain_name}.sync' {sync!r} must "
                            "be 'reset_sync' (the only supported kind this release)."
                        )
                    if derived_from is None:
                        raise ValueError(
                            f"[ATG025] 'reset_domains.{domain_name}.sync' requires "
                            "'derived_from' — a reset can't be synchronized without a "
                            "declared source domain."
                        )

                out[domain_name] = {"derived_from": derived_from, "ratio": ratio, "sync": sync}
            return out

        clock_domains = _pop_domain_relationships("clock_domains")
        reset_domains = _pop_domain_relationships("reset_domains", allow_sync=True)

        # ── Testbench Configuration
        raw_tb_config = data.pop("testbench", None)
        testbench_config: Optional[TestBenchConfig] = None
        if raw_tb_config:
            testbench_config = TestBenchConfig(
                xml_stimulus_path=raw_tb_config.get("xml_stimulus_path"),
                event_id=raw_tb_config.get("event_id", 1),
                generate=raw_tb_config.get("generate", True)
            )

        interface_metadata = data.pop("interface_metadata", {}) or {}
        interface_metadata_file = data.pop("interface_metadata_file", None)

        # ── Allowed unconnected (glob patterns for intentionally open/tied ports)
        raw_unconnected = data.pop("allowed_unconnected", {}) or {}
        allowed_unconnected = AllowedUnconnected(
            open_outputs=raw_unconnected.get("open_outputs", []) or [],
            tied_inputs=raw_unconnected.get("tied_inputs", []) or [],
        )
        if interface_metadata_file:
            metadata_path = resolve_declared_path(interface_metadata_file, yaml_path.parent)
            if not metadata_path.exists():
                raise FileNotFoundError(
                    f"interface_metadata_file not found: {metadata_path}"
                )
            loaded_metadata = yaml.safe_load(metadata_path.read_text()) or {}
            if not isinstance(loaded_metadata, dict):
                raise ValueError(
                    f"interface_metadata_file must contain a mapping: {metadata_path}"
                )
            interface_metadata = _deep_merge_dict(loaded_metadata, interface_metadata)

        # Every key this method understands has been popped by now, so
        # anything still in `data` is passed straight to the dataclass
        # constructor. An unrecognised key used to surface as a bare
        # `TypeError: __init__() got an unexpected keyword argument 'x'`
        # with no file, no line and no suggestion — for what is realistically
        # the most common mistake anyone makes in a design.yml. Check it here
        # instead and report it as a normal, actionable error.
        _reject_unknown_keys(cls, data, yaml_path)

        cfg = cls(
            modules=modules,
            connections=connections,
            topology_groups=topology_groups,
            sweeps=sweeps,
            block_protocol=proto,
            connect_clock=clk_conn,
            connect_reset=rst_conn,
            control_signals=control_signals,
            testbench=testbench_config,
            interface_metadata=interface_metadata,
            interface_metadata_file=interface_metadata_file,
            allowed_unconnected=allowed_unconnected,
            schema_version=schema_version,
            clock_domains=clock_domains,
            reset_domains=reset_domains,
            **data
        )
        cfg._source_file = str(yaml_path)

        # ── Resolve & (optionally) validate paths
        root = yaml_path.parent
        valid_stages = set(DEFAULT_STAGES + ["csim", "cosim", "clean"])

        for mod in cfg.modules:
            # Stage sanity
            bad = set(mod.stages) - valid_stages
            if bad:
                raise ValueError(f"Module {mod.name}: unknown stages {bad}")

            # Path resolution
            mod.abs_src              = [resolve_declared_path(p, root) for p in mod.src]
            mod.abs_tb               = [resolve_declared_path(p, root) for p in mod.tb]
            mod.abs_includes         = [resolve_declared_path(p, root) for p in mod.includes]
            mod.abs_rtl_include_dirs = [resolve_declared_path(p, root) for p in mod.rtl_include_dirs]
            mod.abs_rtl_packages     = [resolve_declared_path(p, root) for p in mod.rtl_packages]

            # Optional existence check
            if validate_sources:
                missing = [
                    str(p) for p in
                    (*mod.abs_src, *mod.abs_tb, *mod.abs_rtl_packages)
                    if not p.exists()
                ]
                if missing:
                    raise FileNotFoundError(
                        f"Module '{mod.name}' references missing file(s):\n"
                        + "\n".join(f"  • {m}" for m in missing)
                    )

        if cfg.max_parallel_jobs < 1:
            raise ValueError("max_parallel_jobs must be ≥ 1")

        return cfg

    # ────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────
    def dump_yaml(self, path: Path) -> None:
        with path.open("w") as f:
            yaml.safe_dump(
                json.loads(json.dumps(self, default=lambda o: o.__dict__)),
                f, sort_keys=False
            )

    @classmethod
    def scaffold(cls, path: Path) -> None:
        skeleton = cls(
            part="xcvu13p-fsga2577-1-e",
            clock_period=2.77,
            modules=[Module(name="my_module", top="my_module", src=["src/my_module.cpp"])],
        )
        skeleton.dump_yaml(path)
        print(f"✨ Wrote template to {path}")

    @classmethod
    def load_relaxed(cls, file: Path | str) -> "DesignConfig":
        """
        Convenience wrapper: parse the YAML without verifying that src/tb/rtl files exist.
        Useful when integrating prebuilt IPs while keeping src: entries in the YAML.
        """
        return cls.load(file, validate_sources=False)
