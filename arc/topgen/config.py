# config.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional, Tuple, Dict, Any
import yaml


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

@dataclass
class InstanceAssign:
    """Maps a range of producer instances to a consumer partition label."""
    instances: Tuple[int, int]  # [start, end) half-open range
    partition: str

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
                    )
                )

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
                ia_list.append(InstanceAssign(
                    instances=(int(inst_range[0]), int(inst_range[1])),
                    partition=ia["partition"],
                ))
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
