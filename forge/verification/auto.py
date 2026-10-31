"""Verification planned from the design itself: no plugin, no stimulus script.

:func:`plan` reads a built project (its IR, build manifest and ``forge.yml``)
and decides what can be checked at each level:

- ``design``: the generated (or authored) top level, driven and observed
  through the interfaces its ports form (:mod:`forge.verification.protocols`);
- ``module:<name>``: each RTL module on its own, with its own ports.

``verification.targets`` names the levels instead, each with a scope (the
design, a module, one instance, or a subsystem of instances), its data, and
whether its result is required. An instance or subsystem can replay the
traffic a design target recorded at its boundary.

Every level is compiled and elaborated, reset, and run on its input (or idle,
without one), with every observed output checked for X and Z (from ``settle``
cycles after reset, for a pipeline whose data registers fill with X until
its first input reaches the output). Its function is
checked only against an expected output given for it: a level with none is
``NOT_VERIFIED_NO_ORACLE``, however cleanly it simulated. A level whose
simulator is not installed is ``BLOCKED_MISSING_TOOL``.

:func:`run` executes a level on a backend (XSim or Verilator) with one
generated testbench, compiled once per compile key, running every transaction
in one simulation, and returns its :class:`Evidence`: what was simulated
(sources, testbench, compile key, tool), on what (input and expected, by
content hash), how it was compared (protocols, checker), and the result per
interface and transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from forge.verification import scoreboard as sb
from forge.verification.auto_tb import (
    TB_CLOCKED, TB_GENERATOR_VERSION, TB_MODULE, Bench, pack, testbench, unpack, verilator_main, write_inputs,
)
from forge.verification.compile_key import build_key, reusable, tool_version
from forge.verification.protocols import Interface, Port, infer

EVIDENCE_SCHEMA = "forge.verification_evidence/1"
BACKENDS = {"xsim": ("xvlog", "xelab", "xsim"), "verilator": ("verilator",)}


def data_formats() -> List[str]:
    """Formats ``verification.input``/``expected`` accept: those registered with
    :mod:`forge.verification.dataset_format`."""
    from forge.verification.dataset_format import known_formats
    return known_formats()


@dataclass
class DataRef:
    path: Path
    format: str = "json"
    links: Dict[str, int] = field(default_factory=dict)   # interface -> link, for pattern files
    events: Optional[Path] = None      # names of the events the file holds, in order
    association: Dict[str, Any] = field(default_factory=dict)   # max_skew, missing

    @classmethod
    def from_spec(cls, spec: Any, root: Path, links: Optional[Mapping[str, int]] = None,
                  association: Optional[Mapping[str, Any]] = None) -> "DataRef":
        if isinstance(spec, (str, Path)):
            spec = {"path": str(spec)}
        path = (root / str(spec["path"])).resolve()
        fmt = str(spec.get("format") or _guess_format(path))
        if fmt not in data_formats():
            raise ValueError(f"{path}: format {fmt!r}, want one of {', '.join(data_formats())}")
        events = (root / str(spec["events"])).resolve() if spec.get("events") else None
        return cls(path, fmt, {str(k): int(v) for k, v in (links or {}).items()}, events,
                   dict(association or {}))

    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": str(self.path), "format": self.format, "sha256": self.sha256()}
        if self.links:
            out["links"] = dict(self.links)
        if self.events is not None:
            out["events"] = {"path": str(self.events),
                             "sha256": hashlib.sha256(self.events.read_bytes()).hexdigest()}
        if self.association:
            out["association"] = dict(self.association)
        if self.format == "json":
            doc = json.loads(self.path.read_text())
            if isinstance(doc, dict) and isinstance(doc.get("provenance"), dict):
                out["provenance"] = doc["provenance"]
        return out


def _guess_format(path: Path) -> str:
    from forge.verification.dataset_format import get_format_loader
    try:
        loader = get_format_loader(path)
    except ValueError:
        raise ValueError(f"{path}: name its format (one of {', '.join(data_formats())})") from None
    named = {name: get_format_loader(path, name) for name in data_formats()}
    for match in (lambda other: other is loader, lambda other: type(other) is type(loader)):
        for name, other in named.items():
            if match(other):
                return name
    raise ValueError(f"{path}: name its format (one of {', '.join(data_formats())})")


@dataclass
class Level:
    name: str
    top: str
    sources: List[Path]
    include_dirs: List[Path]
    bench: Optional[Bench]
    input: Optional[DataRef] = None
    expected: Optional[DataRef] = None
    compare: sb.Compare = field(default_factory=sb.Compare)
    gap: int = 0
    skip: str = ""          # why the level is not simulated
    defines: Dict[str, str] = field(default_factory=dict)
    decoder: Any = None     # project function naming the fields of a word, for mismatch rows
    scope: str = ""         # design, module:<name>, instance:<path> or subsystem
    required: bool = True   # whether its result decides the project's verification status
    events: Optional[List[str]] = None      # only these events of its data (names or positions)
    replay: Optional["Replay"] = None       # its data is traffic a design target recorded
    members: List[str] = field(default_factory=list)    # a subsystem's instances
    backends: Optional[List[str]] = None    # only these of the plan's simulators
    outputs: Optional[List[str]] = None     # the outputs its reference covers; the others are traced only
    streams: Dict[str, List[str]] = field(default_factory=dict)    # one output read from several, in cycle order
    drain: Optional[int] = None             # cycles simulated after the last input
    settle: int = 0                         # cycles after reset in which X on an output is not an error
    simulation: Dict[str, Any] = field(default_factory=dict)       # verification.xsim

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"level": self.name, "top": self.top, "required": self.required}
        if self.scope and self.scope != self.name:
            out["scope"] = self.scope
        if self.members:
            out["instances"] = list(self.members)
        if self.backends:
            out["backends"] = list(self.backends)
        if self.skip:
            out["skipped"] = self.skip
            return out
        if self.defines:
            out["defines"] = dict(self.defines)
        if self.decoder is not None:
            out["decoder"] = f"{getattr(self.decoder, '__module__', '')}.{getattr(self.decoder, '__qualname__', '')}"
        assert self.bench is not None
        out["clock"] = self.bench.clock
        out["clock_period_ns"] = self.bench.period_ns
        out["reset"] = {"port": self.bench.reset, "active": "high" if self.bench.reset_active_high else "low"}
        out["interfaces"] = [{
            "name": i.name, "direction": i.direction, "protocol": i.version, "width": i.width,
            "ports": [p.name for p in i.ports()]} for i in self.bench.interfaces]
        if self.bench.parameters:
            out["parameters"] = dict(self.bench.parameters)
        if self.replay is not None:
            out["input"] = out["expected"] = f"recorded by {self.replay.source}"
            out["replays"] = dict(self.replay.instances)
        else:
            out["input"] = str(self.input.path) if self.input else None
            out["expected"] = str(self.expected.path) if self.expected else None
        if self.events:
            out["events"] = list(self.events)
        checked = self.expected is not None or self.replay is not None
        out["checks"] = ["compile", "reset", "no_x_on_outputs"] + (["function"] if checked else [])
        if not checked:
            out["function"] = "no expected output: NOT_VERIFIED_NO_ORACLE"
        out["compare"] = self.compare.to_dict()
        if self.outputs is not None:
            out["outputs"] = list(self.outputs)
        if self.streams:
            out["streams"] = {k: list(v) for k, v in self.streams.items()}
        if self.drain is not None:
            out["drain"] = self.drain
        if self.settle:
            out["settle"] = self.settle
        if self.simulation:
            out["xsim"] = dict(self.simulation)
        return out


@dataclass
class Replay:
    """Data recorded during a design target's run: ``instances`` maps each
    recorded instance to the prefix its interfaces carry in the replaying
    level (empty for an instance level, ``<instance>_`` in a subsystem)."""

    source: str
    instances: Dict[str, str]


@dataclass
class VerificationPlan:
    levels: List[Level]
    backends: List[str]
    inferred: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"backends": self.backends, "inferred": self.inferred,
                "levels": [lv.to_dict() for lv in self.levels]}


def _role_port(module: Mapping[str, Any], role: str) -> Optional[str]:
    for iface in module.get("interfaces") or []:
        if iface.get("name") == role:
            for member in iface.get("members") or []:
                raw = (member.get("binding") or {}).get("raw_port")
                if raw:
                    return str(raw)
    return None


def _contract_reset_level(module: Mapping[str, Any]) -> Optional[str]:
    path = module.get("contract_path")
    if not path or not Path(path).is_file():
        return None
    import yaml
    roles = ((yaml.safe_load(Path(path).read_text()) or {}).get("ip_interface") or {}).get("roles") or {}
    level = (roles.get("reset_primary") or {}).get("active_level")
    return str(level).lower() if level else None


ModuleBench = Tuple[List[Port], str, str, bool]


def _module_bench(module: Optional[Mapping[str, Any]], config: Any) -> Union[str, ModuleBench]:
    """``(ports, clock, reset, reset_active_high)`` of one IR module, or why not.

    Ports, clock and reset come from the resolved design: the module's own
    port metadata and its contract's clock/reset roles, falling back to the
    names ``forge.yml`` gives the design's clock and reset.
    """
    from forge.core.utils.signal_names import is_active_low_reset

    if module is None:
        return "not in the resolved design"
    ports = [Port(p["name"], p["direction"], int(p["width"])) for p in module.get("ports") or []]
    if not ports:
        return "no resolved port metadata for this module"
    names = {p.name for p in ports}
    clock = _role_port(module, "clock_primary") or (
        config.clock.port if config.clock.port in names else None)
    reset = _role_port(module, "reset_primary") or (
        config.reset.port if config.reset.port in names else None)
    if clock is None or reset is None:
        missing = " or ".join(x for x, v in (("clock", clock), ("reset", reset)) if v is None)
        return (f"no {missing} port: neither a clock_primary/reset_primary contract role nor "
                f"forge.yml's {config.clock.port}/{config.reset.port}")
    level = _contract_reset_level(module)
    if level is None and reset == config.reset.port:
        level = config.reset.active
    if level is None:
        level = "low" if is_active_low_reset(reset) else "high"
    return ports, clock, reset, level == "high"


@dataclass
class Want:
    """What a level is asked to check: its data, how outputs are compared, and
    the interfaces declared where port names follow no convention."""

    input: Optional[DataRef] = None
    expected: Optional[DataRef] = None
    compare: sb.Compare = field(default_factory=sb.Compare)
    gap: int = 0
    protocols: Optional[Mapping[str, Any]] = None
    depth: int = 1 << 16


def _level(name: str, scope: str, top: str, sources: List[Path], incs: List[Path], bench: Bench,
           want: Want, **extra: Any) -> Level:
    return Level(name, top, sources, incs, bench, want.input, want.expected, want.compare, want.gap,
                 scope=scope, **extra)


class _Generated:
    """The levels of a design ``forge build`` generated: its top, its modules,
    their instances, and subsystems of them built on their own."""

    def __init__(self, project: Any, spec: Mapping[str, Any], gen: Optional[Path] = None) -> None:
        self.project = project
        gen = gen or project.top_level().parent
        self.manifest = json.loads((gen / "build_manifest.json").read_text())
        self.ir = json.loads((gen / "design.ir.json").read_text())["design"]
        self.period = project.config.clock.resolved_period_ns()
        self.per_module: Mapping[str, Any] = spec.get("modules") or {}
        self.ir_modules = {m["name"]: m for m in self.ir.get("modules", [])}
        self.instances = {i["id"]: i["module"] for i in self.ir.get("instances", [])}
        self.benches: Dict[str, Union[str, ModuleBench]] = {}
        for name, mod in self.manifest.get("modules", {}).items():
            if mod.get("kind") == "rtl" and mod.get("verilog_files"):
                self.benches[name] = _module_bench(self.ir_modules.get(name), project.config)
        self.notes: List[str] = []

    def protocols(self, module: str) -> Optional[Mapping[str, Any]]:
        return (self.per_module.get(module) or {}).get("protocols")

    def _top(self, name: str, scope: str, manifest: Mapping[str, Any], ir: Mapping[str, Any],
             want: Want) -> Level:
        top = manifest["top_module"]
        top_ports = [Port(p["name"], p["direction"], int(p["width"])) for p in ir["top_ports"]]
        clock = next((p["name"] for p in ir["top_ports"] if p.get("origin") == "clock"), None)
        reset = next((p["name"] for p in ir["top_ports"] if p.get("origin") == "reset"), None)
        if clock is None or reset is None:
            return Level(name, top, [], [], None, skip="the top level has no clock or reset port", scope=scope)
        ifaces = infer(top_ports, exclude=[clock, reset], declared=want.protocols)
        self.notes.append(f"{name}: clock {clock}, reset {reset} (active high, generated top), "
                          + ", ".join(f"{i.name}: {i.protocol}" for i in ifaces))
        return _level(name, scope, top, [Path(f) for f in manifest["verilog_files"]],
                      [Path(d) for d in manifest.get("include_dirs", [])],
                      Bench(top, clock, reset, True, ifaces, period_ns=self.period, depth=want.depth), want)

    def design(self, name: str, want: Want) -> Level:
        return self._top(name, "design", self.manifest, self.ir, want)

    def module(self, name: str, module: str, want: Want) -> Level:
        mod = self.manifest.get("modules", {}).get(module)
        if mod is None:
            raise ValueError(f"no module {module!r} in the design (have {', '.join(sorted(self.manifest['modules']))})")
        top = str(mod.get("top") or module)
        bench = self.benches.get(module, "not an RTL module; checked through the design level")
        if isinstance(bench, str):
            return Level(name, top, [], [], None, skip=bench, scope=f"module:{module}")
        ports, clock, reset, active_high = bench
        ifaces = infer(ports, exclude=[clock, reset], declared=want.protocols)
        params = dict((self.ir_modules.get(module) or {}).get("parameters") or {})
        self.notes.append(f"{name}: clock {clock}, reset {reset} (active {'high' if active_high else 'low'})")
        return _level(name, f"module:{module}", top, [Path(f) for f in mod.get("verilog_files", [])],
                      [Path(d) for d in mod.get("include_dirs", [])],
                      Bench(top, clock, reset, active_high, ifaces, period_ns=self.period, depth=want.depth,
                            parameters=params), want)

    def module_of(self, instance: str) -> str:
        if instance not in self.instances:
            raise ValueError(f"no instance {instance!r} in the design (have {', '.join(sorted(self.instances))})")
        return self.instances[instance]

    def instance(self, name: str, path: str, want: Want) -> Level:
        module = self.module_of(path)
        if want.protocols is None:
            want.protocols = self.protocols(module)
        level = self.module(name, module, want)
        level.scope = f"instance:{path}"
        return level

    def all_instances(self) -> List[str]:
        return sorted(self.instances)

    def probes(self, instances: Sequence[str]) -> List[Tuple[str, Interface]]:
        out: List[Tuple[str, Interface]] = []
        for inst in instances:
            module = self.module_of(inst)
            bench = self.benches.get(module, "not an RTL module")
            if isinstance(bench, str):
                raise ValueError(f"cannot record instance {inst}: {bench}")
            ports, clock, reset, _ = bench
            out += [(inst, i) for i in infer(ports, exclude=[clock, reset], declared=self.protocols(module))]
        return out

    def subsystem_instances(self, members: Sequence[Any]) -> List[str]:
        """The instances *members* selects: instance ids, or a block's name for
        every instance of it."""
        by_block: Dict[str, List[str]] = {}
        for inst, block in self.instances.items():
            by_block.setdefault(block, []).append(inst)
        out: List[str] = []
        for member in (str(m) for m in members):
            if member in self.instances:
                out.append(member)
            elif member in by_block:
                out += sorted(by_block[member])
            else:
                raise ValueError(f"no instance or block {member!r} in the design "
                                 f"(have {', '.join(sorted(self.instances))})")
        return sorted(set(out))

    def subsystem(self, name: str, instances: Sequence[str], want: Want) -> Level:
        gen = subsystem_dir(self.project, name)
        if not (gen / "build_manifest.json").is_file():
            return Level(name, f"{name}_top", [], [], None, scope="subsystem",
                         skip="not generated yet: `forge test` generates it before simulating")
        manifest = json.loads((gen / "build_manifest.json").read_text())
        ir = json.loads((gen / "design.ir.json").read_text())["design"]
        return self._top(name, "subsystem", manifest, ir, want)


class _Existing:
    """The levels of a project's own top, as written: the top, any module in
    its sources, and any instance in its hierarchy."""

    def __init__(self, config: Any, spec: Mapping[str, Any]) -> None:
        from forge.core.utils.hdl_parser import parse_verilog_defines
        from forge.project.hdl_scan import scan_hdl_file, split_verilog_modules

        self.config = config
        root: Path = config.root
        self.top = str(spec.get("top") or config.top or "")
        if not self.top:
            raise ValueError("verification.target: existing needs the top to simulate: set top: (or "
                             "verification.top) in forge.yml to the name of your existing top-level module")
        if spec.get("filelist"):
            sources, incs, defines = read_filelist(root / str(spec["filelist"]))
        else:
            sources, incs, defines = list(config.resolve_sources("rtl")), [], {}
        if not sources:
            raise ValueError("verification.target: existing found no sources: set sources.rtl or verification.filelist")
        raw_defines = spec.get("defines") or {}
        defines.update(parse_verilog_defines(raw_defines) if isinstance(raw_defines, list)
                       else {str(k): str(v) for k, v in raw_defines.items()})
        incs += [(root / str(d)).resolve() for d in spec.get("include_dirs") or []]
        for src in sources:
            if src.parent not in incs:
                incs.append(src.parent)
        self.sources, self.incs, self.defines = sources, incs, defines
        self.per_module: Mapping[str, Any] = spec.get("modules") or {}
        self.period = config.clock.resolved_period_ns()
        self.units = {u.name: u for src in sources for u in scan_hdl_file(src)}
        self.verilog: Dict[str, str] = {}
        for src in sources:
            if src.suffix.lower() not in (".vhd", ".vhdl"):
                self.verilog.update(split_verilog_modules(src.read_text(errors="replace")))
        if self.top not in self.units:
            raise ValueError(f"no module or entity named {self.top!r} in the {len(sources)} source(s) of "
                             + ("verification.filelist" if spec.get("filelist") else "sources.rtl"))
        self.notes: List[str] = []

    def protocols(self, module: str) -> Optional[Mapping[str, Any]]:
        return (self.per_module.get(module) or {}).get("protocols")

    def _ports(self, module: str, overrides: Optional[Mapping[str, Any]] = None) -> List[Port]:
        from forge.core.utils.hdl_parser import (
            UnresolvedWidthError, apply_declared_widths, scan_verilog_port_decls, scan_vhdl_port_decls,
        )

        unit = self.units[module]
        if unit.language == "vhdl":
            decls = scan_vhdl_port_decls(unit.path.read_text())
        else:
            decls = scan_verilog_port_decls(self.verilog[module], overrides, self.defines)
        policy = self.config.modules.get(module)
        decls = apply_declared_widths(decls, policy.widths if policy else None, f"module {module!r}")
        unresolved = {n: d.range_text for n, d in decls.items() if d.width is None}
        if unresolved:
            raise UnresolvedWidthError(unresolved, f"module {module!r}")
        return [Port(n, d.direction, int(d.width or 0)) for n, d in decls.items()]

    def _bench(self, module: str, clock: str, reset: str, active_high: bool, want: Want,
               parameters: Optional[Mapping[str, Any]] = None) -> Bench:
        ports = self._ports(module, parameters)
        names = {p.name for p in ports}
        missing = [n for n in (clock, reset) if n not in names]
        if missing:
            raise ValueError(f"{module} has no port {' or '.join(missing)}; set clock.port / reset.port in forge.yml")
        return Bench(module, clock, reset, active_high,
                     infer(ports, exclude=[clock, reset], declared=want.protocols),
                     period_ns=self.period, depth=want.depth, parameters=dict(parameters or {}))

    def design(self, name: str, want: Want) -> Level:
        bench = self._bench(self.top, self.config.clock.port, self.config.reset.port,
                            self.config.reset.active == "high", want)
        return _level(name, "design", self.top, self.sources, list(self.incs), bench, want,
                      defines=dict(self.defines))

    def module(self, name: str, module: str, want: Want) -> Level:
        if module not in self.units:
            raise ValueError(f"no module {module!r} in the sources (have {', '.join(sorted(self.units))})")
        reset = self.config.reset.port
        bench = self._bench(module, self.config.clock.port, reset, self.config.reset.active == "high", want)
        return _level(name, f"module:{module}", module, self.sources, list(self.incs), bench, want,
                      defines=dict(self.defines))

    def resolve(self, path: str) -> Tuple[str, Dict[str, Any], str, str, bool]:
        """``(module, parameters, clock, reset, reset_active_high)`` of the
        instance at *path* (labels from the top down, joined by ``.``).

        Each parameter assignment is evaluated in its parent's parameters;
        the clock and reset are the ports wired to the parent's, an inverting
        connection (``~rst``) flipping the reset's active level."""
        from forge.core.utils.hdl_parser import eval_verilog_constant, verilog_module_parameters
        from forge.project.hdl_scan import verilog_instances

        parent = self.top
        params = verilog_module_parameters(self.verilog.get(parent, ""), None, self.defines)
        clock, reset, active_high = self.config.clock.port, self.config.reset.port, self.config.reset.active == "high"
        overrides: Dict[str, Any] = {}
        for label in path.split("."):
            if parent not in self.verilog:
                raise ValueError(f"instance {path}: {parent} is not Verilog; its instances cannot be read")
            found = {i.label: i for i in verilog_instances(self.verilog[parent], parent)}
            inst = found.get(label)
            if inst is None:
                raise ValueError(f"instance {path}: {parent} has no instance {label!r} "
                                 f"(has {', '.join(sorted(found)) or 'none'})")
            if inst.positional:
                raise ValueError(f"instance {path}: {label} connects by position; only named "
                                 "connections (.name(...)) say which port is which")
            if inst.module not in self.units:
                raise ValueError(f"instance {path}: module {inst.module} is not in the sources")
            overrides = {}
            for key, expr in inst.parameters.items():
                try:
                    overrides[key] = eval_verilog_constant(expr, params, self.defines)
                except ValueError:
                    if any(re.search(rf"\b{re.escape(p)}\b", expr) for p in params):
                        raise ValueError(f"instance {path}: parameter {key} = {expr} cannot be evaluated") from None
                    overrides[key] = expr
            nets = {expr.replace(" ", ""): port for port, expr in inst.ports.items()}
            next_clock = nets.get(clock)
            next_reset = nets.get(reset)
            for inverted in (f"~{reset}", f"!{reset}"):
                if next_reset is None and inverted in nets:
                    next_reset, active_high = nets[inverted], not active_high
            if next_clock is None or next_reset is None:
                missing = " or ".join(n for n, v in (("clock", next_clock), ("reset", next_reset)) if v is None)
                raise ValueError(f"instance {path}: no port of {label} is wired to the {missing} "
                                 f"({clock if next_clock is None else reset}) of {parent}")
            clock, reset = next_clock, next_reset
            parent = inst.module
            params = verilog_module_parameters(self.verilog.get(parent, ""), overrides, self.defines)
        return parent, overrides, clock, reset, active_high

    def instance(self, name: str, path: str, want: Want) -> Level:
        module, overrides, clock, reset, active_high = self.resolve(path)
        if want.protocols is None:
            want.protocols = self.protocols(module)
        bench = self._bench(module, clock, reset, active_high, want, overrides)
        self.notes.append(f"{name}: {module} at {path}, clock {clock}, reset {reset} "
                          f"(active {'high' if active_high else 'low'})")
        return _level(name, f"instance:{path}", module, self.sources, list(self.incs), bench, want,
                      defines=dict(self.defines))

    def all_instances(self) -> List[str]:
        from forge.project.hdl_scan import verilog_instances

        return sorted(i.label for i in verilog_instances(self.verilog.get(self.top, ""), self.top)
                      if i.module in self.units and not i.positional)

    def probes(self, instances: Sequence[str]) -> List[Tuple[str, Interface]]:
        out: List[Tuple[str, Interface]] = []
        for path in instances:
            module, overrides, clock, reset, _ = self.resolve(path)
            ports = self._ports(module, overrides)
            out += [(path, i) for i in infer(ports, exclude=[clock, reset], declared=self.protocols(module))]
        return out

    def subsystem(self, name: str, instances: Sequence[str], want: Want) -> Level:
        raise ValueError(f"verification.targets.{name}: a subsystem is built from a generated design; in your "
                         "own top, a module that holds the instances is one: use scope instance:<its path>")

    def subsystem_instances(self, members: Sequence[Any]) -> List[str]:
        return [str(m) for m in members]


def plan_existing(config: Any, spec: Mapping[str, Any], input_override: Optional[Any] = None,
                  expected_override: Optional[Any] = None) -> Level:
    """The level that simulates the project's own top as it is written.

    The top is ``verification.top`` or forge.yml's ``top``; its sources are
    ``verification.filelist`` when given, else forge.yml's ``sources:``; its
    ports, clock and reset come from that top's declaration and forge.yml's
    ``clock``/``reset``. Nothing is generated and no adoption question has to
    be answered first.
    """
    design = _Existing(config, spec)
    links = spec.get("links") or {}

    def data(value: Any) -> Optional[DataRef]:
        return DataRef.from_spec(value, config.root, links, spec.get("association")) if value else None

    return design.design(f"existing:{design.top}", Want(
        data(input_override or spec.get("input")), data(expected_override or spec.get("expected")),
        sb.Compare.from_dict(spec.get("compare")), int(spec.get("gap", 0)), spec.get("protocols"),
        int(spec.get("depth", 1 << 16))))


_TARGET_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_TARGET_KEYS = frozenset({"scope", "instances", "input", "expected", "compare", "gap", "protocols",
                          "backends", "required", "replay", "record", "depth", "variant", "outputs", "drain",
                          "settle"})
_XSIM_KEYS = frozenset({"elab_libs", "extra_top_modules", "extra_sources"})


def subsystem_dir(project: Any, name: str) -> Path:
    """Where ``forge test`` generates the top level of subsystem target *name*."""
    return project.paths.state_root / "verify" / name / "generated"


def variant_generated_dir(project: Any, name: str) -> Path:
    """Where the top level of ``forge.yml`` variant *name* is generated."""
    return project.paths.state_root / "variants" / name / "generated"


def plan(project: Any, *, spec: Optional[Mapping[str, Any]] = None,
         input_override: Optional[Any] = None, expected_override: Optional[Any] = None,
         select: Optional[Sequence[str]] = None, events: Optional[Sequence[str]] = None) -> VerificationPlan:
    """The plan of a ``forge.yml`` project: a built one
    (``forge.project.resolve.ResolvedProject``), or one whose own top is
    simulated (``verification.target: existing``).

    *spec* is ``forge.yml``'s ``verification:`` mapping. Without ``targets``
    the levels are the design and each module; the design's result is the
    one required, a module's only when it has an expected output of its own.
    *select* keeps only the named levels; *events* (names or positions)
    restricts every level with data of its own to those events.
    *input_override* and *expected_override* (``--input``, ``--expected``)
    replace the data of the design level, or of the one selected target.
    """
    spec = dict(spec or {})
    root: Path = project.root
    backends = [str(b) for b in spec.get("backends") or ["xsim", "verilator"]]
    for b in backends:
        if b not in BACKENDS:
            raise ValueError(f"verification.backends: {b!r}, want {' or '.join(BACKENDS)}")
    kind = str(spec.get("target") or "generated")
    if kind not in ("generated", "existing"):
        raise ValueError(f"verification.target: {kind!r}, want generated or existing")
    decoder = load_decoder(spec["decoder"], root) if spec.get("decoder") else None
    links = spec.get("links") or {}

    def data(value: Any) -> Optional[DataRef]:
        return DataRef.from_spec(value, root, links, spec.get("association")) if value else None

    design: Union[_Generated, _Existing] = (
        _Existing(project.config, spec) if kind == "existing" else _Generated(project, spec))
    if spec.get("targets"):
        levels = _declared_levels(design, spec, data, select, input_override, expected_override)
    else:
        levels = _implicit_levels(design, spec, data, input_override, expected_override)
        if select:
            known = {lv.name for lv in levels}
            unknown = [s for s in select if s not in known]
            if unknown:
                raise ValueError(f"no target {', '.join(unknown)} (have {', '.join(sorted(known))}); "
                                 "name targets under verification.targets in forge.yml")
            levels = [lv for lv in levels if lv.name in select]
    if events:
        own = [lv for lv in levels if lv.replay is None and lv.input is not None]
        if not own:
            raise ValueError("--event-id/--event-list: no selected level has input data of its own to select from")
        for lv in own:
            lv.events = [str(e) for e in events]
    notes = list(design.notes)
    if isinstance(design, _Existing):
        notes.append(f"existing:{design.top}: the project's own top, as written; nothing generated")
    notes.append(f"clock period {design.period:g} ns (forge.yml clock)")
    streams = spec.get("streams") or {}
    if not isinstance(streams, Mapping):
        raise ValueError("verification.streams: want a mapping of stream name to its output ports (names or globs)")
    xsim = dict(spec.get("xsim") or {})
    extra = sorted(set(xsim) - _XSIM_KEYS)
    if extra:
        raise ValueError(f"verification.xsim: unknown key {', '.join(extra)} (want {', '.join(sorted(_XSIM_KEYS))})")
    if xsim.get("extra_sources"):
        xsim["extra_sources"] = [str((root / os.path.expandvars(str(f))).resolve()) for f in xsim["extra_sources"]]
    targets = spec.get("targets") or {}
    for level in levels:
        level.decoder = decoder
        level.simulation = xsim
        t = (targets.get(level.name) or {}) if isinstance(targets, Mapping) else {}
        drain = t.get("drain", spec.get("drain"))
        level.drain = None if drain is None else int(drain)
        level.settle = int(t.get("settle", spec.get("settle", 0)))
        if level.compare.fields and decoder is None:
            raise ValueError(f"{level.name}: compare.fields names fields of the decoder's reading: "
                             "give verification.decoder")
        if level.bench is not None:
            _resolve_outputs(level, streams, t.get("outputs", spec.get("outputs") if level.scope == "design" else None))
    return VerificationPlan(levels, backends, notes)


def _resolve_outputs(level: Level, streams: Mapping[str, Any], outputs: Any) -> None:
    """The streams and compared outputs of *level*, by name or glob over its
    output interfaces; a pattern that matches nothing is an error."""
    from fnmatch import fnmatchcase

    assert level.bench is not None
    names = [i.name for i in level.bench.interfaces if i.direction == "out"]
    for stream, patterns in streams.items():
        members = [n for pat in ([patterns] if isinstance(patterns, str) else list(patterns))
                   for n in names if fnmatchcase(n, str(pat))]
        if members:
            level.streams[str(stream)] = list(dict.fromkeys(members))
        elif level.scope == "design":
            raise ValueError(f"verification.streams.{stream}: no output of {level.name} matches {patterns}")
    if outputs is None:
        return
    if isinstance(outputs, str):
        outputs = [outputs]
    known = [*level.streams, *[n for n in names if not any(n in m for m in level.streams.values())]]
    chosen: List[str] = []
    for pat in outputs:
        found = [n for n in known if fnmatchcase(n, str(pat))]
        if not found:
            raise ValueError(f"verification.targets.{level.name}.outputs: {pat!r} matches no output "
                             f"(have {', '.join(known)})")
        chosen += [n for n in found if n not in chosen]
    level.outputs = chosen


def _implicit_levels(design: Union[_Generated, _Existing], spec: Mapping[str, Any], data: Any,
                     input_override: Optional[Any], expected_override: Optional[Any]) -> List[Level]:
    compare = sb.Compare.from_dict(spec.get("compare"))
    want = Want(data(input_override or spec.get("input")), data(expected_override or spec.get("expected")),
                compare, int(spec.get("gap", 0)), spec.get("protocols"), int(spec.get("depth", 1 << 16)))
    if isinstance(design, _Existing):
        level = design.design(f"existing:{design.top}", want)
        if spec.get("record_modules") and level.bench is not None:
            level.bench.probes = design.probes(design.all_instances())
        return [level]
    wanted = [str(x) for x in spec.get("levels") or ["design", "modules"]]
    levels: List[Level] = []
    if "design" in wanted:
        level = design.design("design", want)
        if spec.get("record_modules") and level.bench is not None:
            recordable = [i for i in design.all_instances()
                          if not isinstance(design.benches.get(design.module_of(i), ""), str)]
            level.bench.probes = design.probes(recordable)
        levels.append(level)
    if "modules" in wanted:
        for name in sorted(design.manifest.get("modules", {})):
            mspec = design.per_module.get(name) or {}
            level = design.module(f"module:{name}", name, Want(
                data(mspec.get("input")), data(mspec.get("expected")), sb.Compare.from_dict(mspec.get("compare")),
                int(mspec.get("gap", 0)), mspec.get("protocols")))
            level.required = level.expected is not None
            levels.append(level)
    return levels


def _declared_levels(design: Union[_Generated, _Existing], spec: Mapping[str, Any], data: Any,
                     select: Optional[Sequence[str]], input_override: Optional[Any],
                     expected_override: Optional[Any]) -> List[Level]:
    targets = spec["targets"]
    if not isinstance(targets, Mapping):
        raise ValueError("verification.targets: want a mapping of target name to its scope and data")
    unknown = [s for s in select or [] if s not in targets]
    if unknown:
        raise ValueError(f"no target {', '.join(unknown)} under verification.targets "
                         f"(have {', '.join(map(str, targets))})")
    if (input_override or expected_override) and len(select or targets) != 1:
        raise ValueError("--input/--expected replace one target's data: name it with --target")
    levels: List[Level] = []
    replays: List[Level] = []
    sources: Dict[str, Union[_Generated, _Existing]] = {}
    for name, t in targets.items():
        name, t = str(name), dict(t or {})
        where = f"verification.targets.{name}"
        if not _TARGET_NAME.fullmatch(name):
            raise ValueError(f"{where}: a target name is letters, digits, '_', '.' and '-'")
        extra = sorted(set(t) - _TARGET_KEYS)
        if extra:
            raise ValueError(f"{where}: unknown key {', '.join(extra)} (want {', '.join(sorted(_TARGET_KEYS))})")
        replay = t.get("replay")
        if replay and (t.get("input") or t.get("expected")):
            raise ValueError(f"{where}: replay and input/expected are alternatives; give one")
        chosen = select is None or name in select
        want = Want(data(input_override if chosen and input_override else t.get("input")),
                    data(expected_override if chosen and expected_override else t.get("expected")),
                    sb.Compare.from_dict(t.get("compare", spec.get("compare"))), int(t.get("gap", spec.get("gap", 0))),
                    t.get("protocols"), int(t.get("depth", spec.get("depth", 1 << 16))))
        scope = str(t.get("scope") or "design")
        if t.get("variant") and (scope != "design" or not isinstance(design, _Generated)):
            raise ValueError(f"{where}: variant: names the generated design a design-scope target simulates")
        if scope == "design":
            if replay:
                raise ValueError(f"{where}: a design target is what records traffic; replay is for "
                                 "instance and subsystem targets")
            if want.protocols is None:
                want.protocols = spec.get("protocols")
            origin: Union[_Generated, _Existing] = design
            if t.get("variant"):
                assert isinstance(design, _Generated)
                gen = variant_generated_dir(design.project, str(t["variant"]))
                if not (gen / "build_manifest.json").is_file():
                    level = Level(name, "algo_top", [], [], None, scope="design",
                                  skip=f"variant {t['variant']} not generated yet: `forge test` generates it")
                    levels.append(level)
                    sources[name] = design
                    level.required = bool(t.get("required", True))
                    continue
                origin = _Generated(design.project, spec, gen)
            level = origin.design(name, want)
            sources[name] = origin
            if t.get("record") and level.bench is not None:
                level.bench.probes = origin.probes(origin.all_instances())
        elif scope.startswith("module:"):
            if replay:
                raise ValueError(f"{where}: a module on its own has no recorded traffic; "
                                 "replay one of its instances with scope instance:<id>")
            module = scope.split(":", 1)[1]
            if want.protocols is None:
                want.protocols = design.protocols(module)
            level = design.module(name, module, want)
        elif scope.startswith("instance:"):
            path = scope.split(":", 1)[1]
            level = design.instance(name, path, want)
            if replay:
                level.replay = Replay(str(replay), {path: ""})
        elif scope == "subsystem":
            members = t.get("instances")
            if not members or not isinstance(members, list):
                raise ValueError(f"{where}: a subsystem lists its instances: instances: [a, b, ...]")
            insts = design.subsystem_instances(members)
            level = design.subsystem(name, insts, want)
            level.members = insts
            if replay:
                level.replay = Replay(str(replay), {i: f"{i}_" for i in insts})
        else:
            raise ValueError(f"{where}.scope: {scope!r}, want design, module:<name>, instance:<path> or subsystem")
        level.required = bool(t.get("required", True))
        if t.get("backends"):
            level.backends = [str(b) for b in t["backends"]]
        levels.append(level)
        if level.replay is not None:
            replays.append(level)
    by_name = {lv.name: lv for lv in levels}
    for level in replays:
        assert level.replay is not None
        source = by_name.get(level.replay.source)
        if source is None or source.scope != "design":
            raise ValueError(f"verification.targets.{level.name}.replay: {level.replay.source!r} is not a "
                             "design target of this project")
        if source.bench is not None:
            have = {(i, p.name) for i, p in source.bench.probes}
            source.bench.probes += [pr for pr in sources[source.name].probes(list(level.replay.instances))
                                    if (pr[0], pr[1].name) not in have]
    levels.sort(key=lambda lv: lv.scope != "design")
    return [lv for lv in levels if select is None or lv.name in select]


# ── Data ────────────────────────────────────────────────────────────────────

#: An interface's transactions, each with the event it belongs to (None when
#: the data names no events).
Tagged = Dict[str, List[Tuple[Optional[str], Any]]]


def load_inputs(ref: DataRef, bench: Bench) -> Dict[str, List[Any]]:
    return {k: [v for _, v in rows] for k, rows in load_tagged(ref, bench, inputs=True).items()}


def load_expected(ref: DataRef, bench: Bench, outputs: Optional[Sequence[Interface]] = None
                  ) -> Dict[str, List[sb.Expected]]:
    """*ref*'s expected words for the output interfaces of *bench*, or for
    *outputs* (a level's compared outputs, its streams among them)."""
    ifaces = list(outputs) if outputs is not None else [i for i in bench.interfaces if i.direction == "out"]
    by = {i.name: i for i in ifaces}
    out: Dict[str, List[sb.Expected]] = {}
    for k, rows in _load_tagged(ref, by, inputs=False).items():
        out[k] = []
        for event, v in rows:
            if isinstance(v, Mapping) and "value" in v:
                out[k].append(sb.Expected(pack(by[k], v["value"]), v.get("cycle"), v.get("source"),
                                          v.get("event", event)))
            else:
                out[k].append(sb.Expected(pack(by[k], v), event=event))
    return out


def load_tagged(ref: DataRef, bench: Bench, *, inputs: bool) -> Tagged:
    """*ref*'s transactions for the input (or output) interfaces of *bench*,
    each tagged with its event."""
    return _load_tagged(ref, {i.name: i for i in bench.interfaces if i.direction == ("in" if inputs else "out")},
                        inputs=inputs)


def _load_tagged(ref: DataRef, by: Mapping[str, Interface], *, inputs: bool) -> Tagged:
    if _is_concise_json(ref):
        doc = json.loads(ref.path.read_text())
        doc = doc.get("inputs" if inputs else "outputs", doc)
        unknown = sorted(set(doc) - set(by))
        if unknown:
            kind = "input" if inputs else "output"
            raise ValueError(f"{ref.path}: no {kind} interface {', '.join(unknown)} (have {', '.join(by)})")
        return {k: [(None, v) for v in values] for k, values in doc.items()}
    return _from_dataset(ref, by, inputs=inputs)


def _is_concise_json(ref: DataRef) -> bool:
    """``{"inputs"|"outputs": {interface: [values]}}`` or ``{interface: [values]}``,
    as opposed to a ``forge.dataset`` JSON envelope with ``events``."""
    if ref.format != "json":
        return False
    doc = json.loads(ref.path.read_text())
    return not (isinstance(doc, dict) and "events" in doc)


def _word(raw: Any) -> Any:
    from types import SimpleNamespace
    if not isinstance(raw, Mapping):
        return SimpleNamespace(data=int(str(raw), 0), valid=True, start=False, last=False,
                               start_of_orbit=False, strobe=True)
    return SimpleNamespace(
        data=int(str(raw.get("data", 0)), 0), valid=bool(raw.get("valid", True)),
        start=bool(raw.get("start", False)), last=bool(raw.get("last", False)),
        start_of_orbit=bool(raw.get("start_of_orbit", False)), strobe=bool(raw.get("strobe", True)))


def _event_names(ref: DataRef, count: int) -> List[str]:
    """The name of each of *count* events: from ``events:`` (run/lumi/event/bx
    or ``id`` per event) when the data spec gives one, else its index."""
    if ref.events is None:
        return [str(k) for k in range(count)]
    listed = json.loads(ref.events.read_text())
    if len(listed) != count:
        raise ValueError(f"{ref.events}: names {len(listed)} event(s), {ref.path} holds {count}")
    names = []
    for entry in listed:
        if not isinstance(entry, Mapping):
            names.append(str(entry))
        elif "id" in entry:
            names.append(str(entry["id"]))
        else:
            name = ":".join(str(entry[k]) for k in ("run", "lumi", "event") if k in entry)
            names.append(name + (f"@bx{entry['bx']}" if "bx" in entry else ""))
    return names


def _emp_dict(w: Any) -> Dict[str, Any]:
    return {"data": w.data, "valid": w.valid, "start": w.start, "last": w.last,
            "start_of_orbit": w.start_of_orbit, "strobe": w.strobe}


def _from_dataset(ref: DataRef, by: Mapping[str, Interface], *, inputs: bool) -> Tagged:
    """Transactions from the format's registered loader.

    A loader with ``read`` (the pattern-file loaders) gives every frame of
    every link. The links ``verification.links`` maps are put together into
    events by :func:`forge.integration.emp.associate_events` — a link missing
    a packet is an error, not a shift — and each word is tagged with its
    event. Any other loader is read through its events: values an event keys
    by an interface name, or words under its ``links``, in event order; an
    entry whose ``id`` is null holds words of no event (a configuration
    written before the first event, idle words after the last).
    """
    from forge.verification.dataset_format import get_format_loader
    loader = get_format_loader(ref.path, ref.format)
    wanted = {k: v for k, v in ref.links.items() if k in by}
    out: Tagged = {}
    if hasattr(loader, "read"):
        from forge.integration.emp import associate_events
        if not wanted:
            raise ValueError(f"{ref.path}: verification.links maps no interface of this level to a link")
        board = loader.read(ref.path)
        for name, link in wanted.items():
            if link not in board.links:
                raise ValueError(f"{ref.path}: no link {link} (for {name})")
        events = associate_events(board, sorted(set(wanted.values())),
                                  max_skew=int(ref.association.get("max_skew", 0)),
                                  missing=str(ref.association.get("missing", "error")))
        names = _event_names(ref, len(events))
        for name, link in wanted.items():
            frame_event: Dict[int, str] = {}
            for event_name, grouped in zip(names, events):
                first, last, _ = grouped[link]
                for frame in range(first, last + 1):
                    frame_event[frame] = event_name
            rows: List[Tuple[Optional[str], Any]] = []
            for frame, w in enumerate(board.links[link]):
                if by[name].protocol == "emp_lword":
                    if inputs or w.valid:
                        rows.append((frame_event.get(frame), _emp_dict(w)))
                elif w.valid:
                    rows.append((frame_event.get(frame), w.data))
            out[name] = rows
        return out
    dataset = loader.load(ref.path)
    ids: List[Optional[str]] = [None if "id" in e and e["id"] is None else str(e.get("id", k))
                                for k, e in enumerate(dataset.events)]
    if ref.events is not None:
        ids = list(_event_names(ref, len(ids)))
    per_cycle = [n for n, i in by.items() if inputs and i.protocol == "scalar"
                 and any(n in e for e in dataset.events)]
    held: Dict[str, Any] = {n: 0 for n in per_cycle}
    for tag, event in zip(ids, dataset.events):
        given = {n: (event[n] if isinstance(event[n], list) else [event[n]]) for n in by if n in event}
        _align_cycles(ref, tag, given, per_cycle, held)
        for name, values in given.items():
            out.setdefault(name, []).extend((tag, x) for x in values)
        for name, link in wanted.items():
            for w in (event.get("links") or {}).get(str(link), []):
                word = _word(w)
                if by[name].protocol == "emp_lword":
                    out.setdefault(name, []).append((tag, _emp_dict(word)))
                elif word.valid:
                    out.setdefault(name, []).append((tag, word.data))
    if not out:
        raise ValueError(f"{ref.path}: no event names an interface of this level"
                         + ("" if wanted else ", and verification.links maps none to a link"))
    return out


def _align_cycles(ref: DataRef, tag: Optional[str], given: Dict[str, List[Any]], per_cycle: Sequence[str],
                  last: Dict[str, Any]) -> None:
    """Make every per-cycle (``scalar``) input span the same cycles in one
    dataset entry, so each entry starts on the same cycle on every input: an
    input the entry gives fewer words, or none, holds its last word. Within
    a named event the inputs it names must give the same number of words: one
    missing a word would shift every later event on that input alone."""
    lengths = {n: len(given[n]) for n in per_cycle if n in given}
    if not lengths:
        return
    span = max(lengths.values())
    short = sorted(n for n, k in lengths.items() if k < span)
    if tag is not None and short:
        raise ValueError(f"{ref.path}: event {tag} gives {short[0]} {lengths[short[0]]} word(s) and other per-cycle "
                         f"inputs {span}: a per-cycle input missing a word would shift every later event")
    for n in per_cycle:
        values = given.get(n, [])
        if values:
            last[n] = values[-1]
        given[n] = values + [last[n]] * (span - len(values))


# ── Backends ────────────────────────────────────────────────────────────────

def missing_tools(backend: str) -> List[str]:
    return [t for t in BACKENDS[backend] if shutil.which(t) is None]


def _list_file(level: Level, generated: Sequence[Path], path: Path) -> None:
    lines = [f'-I"{d}"' for d in level.include_dirs] + [f'"{f}"' for f in [*level.sources, *generated]]
    path.write_text("\n".join(lines) + "\n")


def _compile_commands(backend: str, level: Level, tb: Path, work: Path) -> List[List[str]]:
    vhdl = [str(f) for f in level.sources if f.suffix.lower() in (".vhd", ".vhdl")]
    vlog = [str(f) for f in level.sources if f.suffix.lower() not in (".vhd", ".vhdl")]
    incs = [str(d) for d in level.include_dirs]
    defs = [f"{k}={v}" if v != "" else k for k, v in level.defines.items()]
    if backend == "xsim":
        sim = level.simulation
        cmds = []
        if vhdl:
            cmds.append(["xvhdl", *vhdl])
        cmds.append(["xvlog", "-sv", *[a for d in incs for a in ("-i", d)], *[a for d in defs for a in ("-d", d)],
                     *vlog, *[str(f) for f in sim.get("extra_sources") or []], str(tb)])
        cmds.append(["xelab", TB_CLOCKED, *[str(t) for t in sim.get("extra_top_modules") or []],
                     *[a for lib in sim.get("elab_libs") or [] for a in ("-L", str(lib))],
                     "-s", "forge_tb_snap", "-timescale", "1ns/1ps"])
        return cmds
    if vhdl:
        raise RuntimeError("Verilator does not read VHDL")
    # forge_tb needs no timing support; with --no-timing a source's own delays
    # (HLS writes `initial #0 ...`) are taken as zero.
    return [["verilator", "--cc", "--exe", "--build", "--no-timing", "-j", "0", "-Wno-fatal", "-Wno-lint", "-Wno-style",
             "-Wno-STMTDLY", "--top-module", TB_MODULE, "-Mdir", str(work / "obj"), "-o", "forge_tb",
             *[f"-I{d}" for d in incs], *[f"-D{d}" for d in defs], *vlog, str(tb), str(work / "forge_tb_main.cpp")]]


def _stage_memory_files(level: Level, work: Path) -> int:
    """Link the ``.dat`` files next to *level*'s sources into *work*: HLS
    writes ROM contents there and reads them from the simulator's directory."""
    linked = 0
    for d in dict.fromkeys([*(f.parent for f in level.sources), *level.include_dirs]):
        for dat in sorted(d.glob("*.dat")) if d.is_dir() else []:
            dst = work / dat.name
            if not dst.exists():
                dst.symlink_to(dat.resolve())
                linked += 1
    return linked


def _compared(level: Level) -> List[Interface]:
    """The outputs *level* compares: each stream, as one interface named after
    it, and every other output, kept to ``outputs`` when the level names them."""
    assert level.bench is not None
    outs = {i.name: i for i in level.bench.interfaces if i.direction == "out"}
    in_streams = {m for members in level.streams.values() for m in members}
    compared = [replace(outs[members[0]], name=name) for name, members in level.streams.items()]
    compared += [i for n, i in outs.items() if n not in in_streams]
    if level.outputs is not None:
        compared = [i for i in compared if i.name in level.outputs]
    return compared


def _observed(level: Level, name: str, trace: Mapping[str, Any]) -> Tuple[List[Tuple[int, int]], List[int]]:
    """``(cycle, word)`` of output *name*, a stream's members merged in cycle
    order (by member at equal cycles), and the cycles it carried X or Z."""
    members = level.streams.get(name)
    if members is None:
        return trace["outputs"].get(name, []), trace["unknown"].get(name, [])
    merged = sorted((c, k, v) for k, m in enumerate(members) for c, v in trace["outputs"].get(m, []))
    unknown = sorted({c for m in members for c in trace["unknown"].get(m, [])})
    return [(c, v) for c, _, v in merged], unknown


def _field_key(level: Level, name: str) -> Optional[sb.Key]:
    fields = level.compare.fields
    if not fields:
        return None
    decoder = level.decoder

    def key(word: int) -> Optional[Tuple[Any, ...]]:
        got = decoder(name, word)
        if got is None:
            return None
        missing = [f for f in fields if f not in got]
        if missing:
            raise ValueError(f"verification.decoder gives no field {', '.join(missing)} for {name} "
                             f"(compare.fields of {level.name}); it gives {', '.join(got)}")
        return tuple(got[f] for f in fields)
    return key


def _sim_command(backend: str, work: Path, plusargs: Mapping[str, str]) -> List[str]:
    if backend == "xsim":
        cmd = ["xsim", "forge_tb_snap", "-R"]
        for k, v in plusargs.items():
            cmd += ["-testplusarg", f"{k}={v}"]
        return cmd
    return [str(work / "obj" / "forge_tb"), *[f"+{k}={v}" for k, v in plusargs.items()]]


def _parse_trace(text: str) -> Dict[str, Any]:
    ins: Dict[str, List[Tuple[int, int]]] = {}
    outs: Dict[str, List[Tuple[int, int]]] = {}
    unknown: Dict[str, List[int]] = {}
    ended = False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "end":
            ended = True
            continue
        if len(parts) != 3:
            continue
        cycle, tag, value = int(parts[0]), parts[1], parts[2]
        kind, name = tag[0], tag[1:]
        if kind == ">":
            ins.setdefault(name, []).append((cycle, int(value, 16)))
        elif kind == "<":
            outs.setdefault(name, []).append((cycle, int(value, 16)))
        elif kind == "!":
            unknown.setdefault(name, []).append(cycle)
    return {"inputs": ins, "outputs": outs, "unknown": unknown, "ended": ended}


@dataclass
class Evidence:
    level: str
    backend: str
    status: str
    doc: Dict[str, Any]

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.doc, indent=2) + "\n")


DERIVED = "derived.json"


def _expected_ref(level: Level) -> Optional[Dict[str, Any]]:
    if level.expected is None:
        return None
    ref = level.expected.to_dict()
    sidecar = level.expected.path.parent / DERIVED
    if sidecar.is_file():
        ref["derived_from"] = json.loads(sidecar.read_text())
    return ref


def _record(bench: Bench, trace: Mapping[str, Any], out: Path, origin: Dict[str, Any]) -> List[str]:
    """Each probed instance's inputs and outputs as the JSON a module level reads."""
    written = []
    for inst in sorted({i for i, _ in bench.probes}):
        ifaces = [pi for i, pi in bench.probes if i == inst]
        d = out / inst
        d.mkdir(parents=True, exist_ok=True)
        for name, direction in (("input.json", "inputs"), ("expected.json", "outputs")):
            want = "in" if direction == "inputs" else "out"
            doc = {pi.name: [unpack(pi, v) for _, v in trace[direction].get(f"{inst}/{pi.name}", [])]
                   for pi in ifaces if pi.direction == want}
            (d / name).write_text(json.dumps(doc, indent=1) + "\n")
        (d / DERIVED).write_text(json.dumps({**origin, "instance": inst}, indent=1) + "\n")
        written.append(str(d))
    return written


def read_filelist(path: Path, _seen: Optional[set] = None) -> Tuple[List[Path], List[Path], Dict[str, str]]:
    """``(sources, include dirs, defines)`` of a simulator compile list (``.f``).

    Reads file paths, ``+incdir+``, ``-I``, ``+define+``, ``-D`` and nested
    ``-f``; relative paths resolve against the list's own directory.
    """
    seen = _seen if _seen is not None else set()
    path = path.resolve()
    if path in seen:
        return [], [], {}
    seen.add(path)
    base = path.parent
    sources: List[Path] = []
    incs: List[Path] = []
    defines: Dict[str, str] = {}
    text = "\n".join(line.split("//", 1)[0].split("#", 1)[0] for line in path.read_text().splitlines())
    tokens = text.split()
    i = 0

    def define(item: str) -> None:
        name, _, value = item.partition("=")
        if name:
            defines[name] = value or "1"

    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok.startswith("+incdir+"):
            incs += [(base / d).resolve() for d in tok[len("+incdir+"):].split("+") if d]
        elif tok.startswith("+define+"):
            for item in tok[len("+define+"):].split("+"):
                define(item)
        elif tok in ("-I", "-D", "-f", "-F") and nxt:
            if tok == "-I":
                incs.append((base / nxt).resolve())
            elif tok == "-D":
                define(nxt)
            else:
                sub = read_filelist(base / nxt, seen)
                sources += sub[0]
                incs += sub[1]
                defines.update(sub[2])
            i += 1
        elif tok.startswith("-I"):
            incs.append((base / tok[2:]).resolve())
        elif tok.startswith("-D"):
            define(tok[2:])
        elif not tok.startswith(("-", "+")):
            sources.append((base / tok).resolve())
        i += 1
    return sources, incs, defines


def _event_cycles(tagged: Tagged, driven: Mapping[str, Sequence[Tuple[int, int]]]) -> Optional[Dict[str, int]]:
    """The cycle of each event's first input word on any input interface, or
    None when the input names no events. The trace records the words driven
    with valid set, so each interface is matched against those."""
    cycles: Dict[str, int] = {}
    for name, rows in tagged.items():
        if not any(event is not None for event, _ in rows):
            continue
        valid = [event for event, v in rows if not (isinstance(v, Mapping) and not v.get("valid", True))]
        seen = list(driven.get(name, []))
        events = valid if len(valid) == len(seen) else [event for event, _ in rows]
        if len(events) != len(seen):
            continue
        for event, (cycle, _) in zip(events, seen):
            if event is not None and (event not in cycles or cycle < cycles[event]):
                cycles[event] = cycle
    return cycles or None


def load_decoder(spec: Any, root: Path) -> Any:
    """The project's word decoder, ``path/to/file.py:function``: called as
    ``function(interface, word)`` and returning ``{field: value}``."""
    import importlib.util

    file, _, func = str(spec).partition(":")
    path = (root / file).resolve()
    module_spec = importlib.util.spec_from_file_location(f"forge_decoder_{path.stem}", path)
    if module_spec is None or module_spec.loader is None or not func:
        raise ValueError(f"verification.decoder {spec!r}: want path/to/file.py:function")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return getattr(module, func)


def _decode_rows(decoder: Any, iface: Interface, rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        for key in ("expected", "observed"):
            if key in row:
                try:
                    row[f"{key}_fields"] = dict(decoder(iface.name, int(row[key], 16)))
                except Exception as exc:  # noqa: BLE001 — a decoder failure must not hide the raw words
                    row[f"{key}_fields"] = {"decoder_error": str(exc)}


def plan_identity(level: Level) -> str:
    """sha256 of everything about *level* other than file contents that decides
    its result: interfaces and protocols, clock, reset, data references, compare
    settings, checker and testbench generator versions."""
    body = {**level.to_dict(), "checker": sb.CHECKER_VERSION, "testbench_generator": TB_GENERATOR_VERSION}
    body.pop("required")
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _headers(level: Level) -> List[Dict[str, str]]:
    from forge.core.utils.hdl_includes import include_tree

    seen: Dict[str, str] = {}
    for inc in include_tree(level.sources, level.include_dirs):
        if inc.path is not None and str(inc.path) not in seen:
            seen[str(inc.path)] = hashlib.sha256(inc.path.read_bytes()).hexdigest()
    return [{"path": p, "sha256": h} for p, h in sorted(seen.items())]


def _evidence(level: Level, backend: str, status: str, **extra: Any) -> Evidence:
    doc: Dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "level": level.name,
        "top": level.top,
        "backend": backend,
        "status": status,
        "sources": [{"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                    for f in level.sources if f.is_file()],
        "headers": _headers(level),
        "plan_sha256": plan_identity(level),
        "input": level.input.to_dict() if level.input else None,
        "expected": _expected_ref(level),
        "checker": {"version": sb.CHECKER_VERSION, **level.compare.to_dict()},
        "protocols": {i.name: i.version for i in level.bench.interfaces} if level.bench else {},
        "clock_period_ns": level.bench.period_ns if level.bench else None,
        "testbench_generator": TB_GENERATOR_VERSION,
        "scope": level.scope or level.name,
        "required": level.required,
        **extra,
    }
    expected = doc["expected"] or {}
    doc["reference"] = ("derived" if level.replay is not None or expected.get("derived_from")
                        else "functional" if expected else "none")
    body = json.dumps(doc, sort_keys=True).encode()
    doc["evidence_id"] = hashlib.sha256(body).hexdigest()
    return Evidence(level.name, backend, status, doc)


def _replayed(level: Level, backend: str, work: Path, recorded: Path) -> Union[str, Tuple[DataRef, DataRef, Dict[str, Any]]]:
    """The input and expected output of a replaying *level*, merged from the
    traffic its source recorded under *recorded* (preferably by the same
    simulator), or why there is none."""
    assert level.replay is not None and level.bench is not None
    wanted = level.replay.instances
    candidates = [recorded / backend] + sorted(d for d in recorded.glob("*") if d.is_dir() and d.name != backend)
    source = next((d for d in candidates if all((d / "module_traces" / i / "expected.json").is_file()
                                                for i in wanted)), None)
    if source is None:
        return (f"nothing recorded for {', '.join(sorted(wanted))}: run "
                f"`forge test --target {level.replay.source}` first")
    names = {i.name: i.direction for i in level.bench.interfaces}
    docs: Dict[str, Dict[str, Any]] = {"in": {}, "out": {}}
    files, origins = [], []
    for inst, prefix in sorted(wanted.items()):
        d = source / "module_traces" / inst
        for doc_name in ("input.json", "expected.json"):
            path = d / doc_name
            files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            for iface, values in json.loads(path.read_text()).items():
                direction = names.get(prefix + iface)
                if direction is not None:
                    docs[direction][prefix + iface] = values
        origins.append(json.loads((d / DERIVED).read_text()))
    out = work / "replay"
    out.mkdir(parents=True, exist_ok=True)
    (out / "input.json").write_text(json.dumps(docs["in"], indent=1) + "\n")
    (out / "expected.json").write_text(json.dumps(docs["out"], indent=1) + "\n")
    (out / DERIVED).write_text(json.dumps({"replayed_from": level.replay.source, "backend": source.name,
                                           "recordings": origins}, indent=1) + "\n")
    return (DataRef(out / "input.json"), DataRef(out / "expected.json"),
            {"source": level.replay.source, "backend": source.name, "files": files})


def _select_events(level: Level, tagged: Tagged,
                   expected: Optional[Dict[str, List[sb.Expected]]]) -> Tuple[Tagged, Optional[Dict[str, List[sb.Expected]]], List[str]]:
    """*tagged* and *expected* restricted to the events *level* selects, by
    name or by position (``7``, ``10-12``). Words of no event (a setup, the
    idle frames of a pattern file) are kept."""
    assert level.events and level.input is not None
    if level.compare.mode == "exact_cycle":
        raise ValueError("--event-id/--event-list with compare mode exact_cycle: the expected cycles are "
                         "those of the whole run; use transaction or a latency mode")
    order: List[str] = []
    for rows in tagged.values():
        for event, _ in rows:
            if event is not None and event not in order:
                order.append(event)
    if not order:
        raise ValueError(f"{level.input.path} names no events to select from: give it an events: file "
                         "(verification.input: {path, events}) or use a pattern file")
    chosen: List[str] = []
    for token in level.events:
        if token in order:
            chosen.append(token)
            continue
        lo, sep, hi = token.partition("-")
        if not (lo.isdigit() and (not sep or hi.isdigit())):
            raise ValueError(f"no event {token!r} in {level.input.path} (events are named "
                             f"{', '.join(order[:5])}{', ...' if len(order) > 5 else ''})")
        for k in range(int(lo), int(hi or lo) + 1):
            if k >= len(order):
                raise ValueError(f"event position {k}: {level.input.path} holds {len(order)} events")
            chosen.append(order[k])
    keep = set(chosen)
    tagged = {k: [(e, v) for e, v in rows if e is None or e in keep] for k, rows in tagged.items()}
    if expected is not None:
        expected = {k: [x for x in rows if x.event in keep] for k, rows in expected.items()}
    return tagged, expected, [e for e in order if e in keep]


def run(level: Level, backend: str, work: Path, *, seed: int = 1, stall: int = 0, backpressure: int = 0,
        max_cycles: int = 1_000_000, recorded: Optional[Path] = None) -> Evidence:
    """Simulate *level* on *backend* in *work* and compare; never raises for a failing design.

    *recorded* is the work directory of the target a replaying level replays."""
    if level.skip or level.bench is None:
        return _evidence(level, backend, sb.SKIPPED, reason=level.skip)
    missing = missing_tools(backend)
    if missing:
        return _evidence(level, backend, sb.BLOCKED_MISSING_TOOL, reason=f"{', '.join(missing)} not on PATH")
    replayed: Dict[str, Any] = {}
    if level.replay is not None:
        found = _replayed(level, backend, work.resolve(), recorded or work)
        if isinstance(found, str):
            return _evidence(level, backend, sb.SKIPPED, reason=found)
        level = replace(level, input=found[0], expected=found[1])
        replayed = {"replay": found[2]}
    bench = level.bench
    assert bench is not None
    level.sources = [f.resolve() for f in level.sources]
    level.include_dirs = [d.resolve() for d in level.include_dirs]
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    tb = work / f"{TB_MODULE}.sv"
    text = testbench(bench)
    if not tb.is_file() or tb.read_text() != text:
        tb.write_text(text)
    generated = [tb]
    if backend == "verilator":
        generated.append(work / "forge_tb_main.cpp")
        generated[-1].write_text(verilator_main(bench))
    _stage_memory_files(level, work)
    lst = work / "sources.f"
    _list_file(level, generated, lst)
    try:
        cmds = _compile_commands(backend, level, tb, work)
    except RuntimeError as exc:
        return _evidence(level, backend, sb.SKIPPED, reason=str(exc))
    key = build_key(lst, cmds, BACKENDS[backend][:1])
    sentinel = work / ".forge_compile_key"
    reused = reusable(sentinel, key)
    if not reused:
        sentinel.unlink(missing_ok=True)
        for cmd in cmds:
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
            if proc.returncode != 0:
                (work / "compile.log").write_text(proc.stdout + proc.stderr)
                return _evidence(level, backend, sb.FAILED, compile_key=key,
                                 reason=f"{cmd[0]} failed, see {work / 'compile.log'}")
        sentinel.write_text(key)

    tagged = load_tagged(level.input, bench, inputs=True) if level.input else {}
    compared = _compared(level)
    expected = load_expected(level.expected, bench, compared) if level.expected else None
    if level.events:
        tagged, expected, chosen = _select_events(level, tagged, expected)
        replayed["events"] = chosen
    values = {k: [v for _, v in rows] for k, rows in tagged.items()}
    plusargs = write_inputs(bench, values, work / "data", gap=level.gap)
    trace = work / "trace.txt"
    plusargs.update({"TRACE": str(trace), "SEED": str(seed), "MAX_CYCLES": str(max_cycles)})
    if level.drain is not None:
        plusargs["DRAIN"] = str(level.drain)
    for i in bench.interfaces:
        if i.direction == "in" and i.protocol == "ready_valid":
            plusargs[f"STALL_{i.name}"] = str(stall)
        if i.direction == "out":
            if expected is not None and i.name in expected:
                plusargs[f"EXPECT_{i.name}"] = str(len(expected[i.name]))
            if i.ready is not None:
                plusargs[f"BACKPRESSURE_{i.name}"] = str(backpressure)
    trace.unlink(missing_ok=True)
    proc = subprocess.run(_sim_command(backend, work, plusargs), cwd=work, capture_output=True, text=True)
    (work / "sim.log").write_text(proc.stdout + proc.stderr)
    ident = {"compile_key": key, "compile_reused": reused, "tool": tool_version(BACKENDS[backend][0]),
             "seed": seed, "stall_percent": stall, "backpressure_percent": backpressure, **replayed}
    if proc.returncode != 0 or not trace.is_file():
        return _evidence(level, backend, sb.FAILED, **ident, reason=f"simulation failed, see {work / 'sim.log'}")
    t = _parse_trace(trace.read_text())
    if not t["ended"] or "timeout" in t["unknown"]:
        return _evidence(level, backend, sb.FAILED, **ident, reason=f"simulation did not finish within {max_cycles} cycles")

    first_in = next((i.name for i in bench.interfaces if i.direction == "in"), None)
    input_cycles = [c for c, _ in t["inputs"].get(first_in, [])] if first_in else []
    event_cycles = _event_cycles(tagged, t["inputs"])
    results = []
    for o in compared:
        exp = None if expected is None else expected.get(o.name)
        observed, unknown = _observed(level, o.name, t)
        unknown = [c for c in unknown if c >= level.settle]
        result = sb.compare(o.name, exp, observed, level.compare, input_cycles=input_cycles, unknown=unknown,
                            event_cycles=event_cycles, key=_field_key(level, o.name))
        if level.decoder is not None:
            _decode_rows(level.decoder, o, result.rows)
        results.append(result)
    status = sb.combine([r.status for r in results])
    if expected is None and status != sb.FAILED:
        status = sb.NOT_VERIFIED_NO_ORACLE
    if bench.probes:
        ident["recorded_modules"] = _record(bench, t, work / "module_traces", {
            "level": level.name, "backend": backend, "status": status, "compile_key": key,
            "input_sha256": level.input.sha256() if level.input else None,
            "events": replayed.get("events"),
            "note": "recorded from the design level: a regression reference, not an independent oracle"})
    return _evidence(level, backend, status, **ident,
                     inputs_driven={k: len(v) for k, v in t["inputs"].items()},
                     interfaces=[r.to_dict() for r in results])


def level_dir(work_root: Path, name: str) -> Path:
    return work_root / name.replace(":", "_")


def run_plan(p: VerificationPlan, work_root: Path, **kwargs: Any) -> List[Evidence]:
    out = []
    for level in p.levels:
        recorded = level_dir(work_root, level.replay.source) if level.replay is not None else None
        for backend in p.backends:
            if level.backends and backend not in level.backends:
                continue
            out.append(run(level, backend, level_dir(work_root, level.name) / backend, recorded=recorded, **kwargs))
    return out


# ── Board captures ──────────────────────────────────────────────────────────

BOARD = "board"


def _link_frames(ref: DataRef, iface: Interface, link: int, board: Any, offset: int = 0) -> List[Tuple[int, int]]:
    """``(frame, word)`` of every word *iface* carries on *link* of *board*:
    the valid ones, packed as the testbench traces them."""
    words = board.links.get(link)
    if words is None:
        raise ValueError(f"{ref.path}: no link {link} (for {iface.name})")
    return [(frame + offset, pack(iface, _emp_dict(w)) if iface.protocol == "emp_lword" else w.data)
            for frame, w in enumerate(words) if w.valid]


def build_identity(level: Level, generated: Optional[Path] = None) -> Dict[str, Any]:
    """What a board build of *level* was made from: its top, every source and
    header by content hash, its defines and, for a generated top, the IR and
    output hashes of the build that wrote it; ``id`` is a hash over all of it."""
    identity: Dict[str, Any] = {
        "top": level.top,
        "sources": [{"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                    for f in level.sources if f.is_file()],
        "headers": _headers(level),
        "defines": dict(level.defines),
    }
    provenance = generated / "provenance.json" if generated is not None else None
    if provenance is not None and provenance.is_file():
        doc = json.loads(provenance.read_text())
        identity["generated"] = {k: doc.get(k) for k in ("ir_content_hash", "plan_hash", "output_hashes")}
    identity["id"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity


def compare_capture(level: Level, capture: DataRef, *, input_offset: Optional[int] = None,
                    build: Optional[Mapping[str, Any]] = None) -> Evidence:
    """A board capture of *level*'s outputs compared with its expected output
    by the scoreboard simulation uses, as evidence on backend ``board``.

    *capture* is a pattern file (a format whose loader reads link words);
    each output interface is read from the link ``verification.links`` maps
    it to, and a capture's cycles are its frames. A latency mode measures
    from the input: *input_offset* is the capture frame at which the input's
    frame 0 was played, which only the board setup knows. *build* is the
    identity of the firmware the capture came from."""
    from forge.verification.dataset_format import get_format_loader

    if level.skip or level.bench is None:
        return _evidence(level, BOARD, sb.SKIPPED, reason=level.skip)
    if level.expected is None:
        raise ValueError(f"{level.name}: no expected output to compare a capture with")
    bench = level.bench
    loader = get_format_loader(capture.path, capture.format)
    if not hasattr(loader, "read"):
        raise ValueError(f"{capture.path}: a capture is a pattern file ({capture.format} carries no link words)")
    links = level.expected.links
    outputs = [i for i in bench.interfaces if i.direction == "out"]
    unmapped = [o.name for o in outputs if o.name not in links]
    if unmapped:
        raise ValueError(f"verification.links maps no link to {', '.join(unmapped)}, so the capture cannot be read for it")
    if level.compare.mode in ("fixed_latency", "latency_window") and input_offset is None:
        raise ValueError(f"compare mode {level.compare.mode} measures latency from the input: give the capture "
                         "frame at which the input's frame 0 was played (--capture-offset)")
    board = loader.read(capture.path)
    expected: Optional[Dict[str, List[sb.Expected]]] = load_expected(level.expected, bench)
    tagged: Tagged = load_tagged(level.input, bench, inputs=True) if level.input is not None else {}
    extra: Dict[str, Any] = {}
    if level.events:
        tagged, expected, extra["events"] = _select_events(level, tagged, expected)
    driven: Dict[str, List[Tuple[int, int]]] = {}
    if input_offset is not None and level.input is not None:
        source = get_format_loader(level.input.path, level.input.format)
        if not hasattr(source, "read"):
            raise ValueError(f"{level.input.path}: --capture-offset times the input as played, which needs it "
                             "as a pattern file")
        played = source.read(level.input.path)
        for i in (i for i in bench.interfaces if i.direction == "in" and i.name in level.input.links):
            driven[i.name] = _link_frames(level.input, i, level.input.links[i.name], played, input_offset)
    first_in = next((i.name for i in bench.interfaces if i.direction == "in" and i.name in driven), None)
    input_cycles = [c for c, _ in driven.get(first_in, [])] if first_in else []
    event_cycles = _event_cycles(tagged, driven) if driven else None
    results = []
    for o in outputs:
        observed = _link_frames(capture, o, links[o.name], board)
        result = sb.compare(o.name, None if expected is None else expected.get(o.name), observed, level.compare,
                            input_cycles=input_cycles, unknown=[], event_cycles=event_cycles)
        if level.decoder is not None:
            _decode_rows(level.decoder, o, result.rows)
        results.append(result)
    status = sb.combine([r.status for r in results])
    return _evidence(level, BOARD, status, capture=capture.to_dict(), build=dict(build or {}),
                     input_offset=input_offset, links={o.name: links[o.name] for o in outputs},
                     interfaces=[r.to_dict() for r in results], **extra)


def summary(evidence: Sequence[Evidence]) -> str:
    """One status for a run: that of the required levels (every level when
    none is). A required level that could not be simulated counts against it;
    one that is not required and was skipped does not."""
    required = [e for e in evidence if e.doc.get("required", True)]
    statuses = [e.status for e in required] if required else [
        e.status for e in evidence if e.status != sb.SKIPPED]
    return sb.combine(statuses) if statuses else sb.SKIPPED
