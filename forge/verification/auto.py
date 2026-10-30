"""Verification planned from the design itself: no plugin, no stimulus script.

:func:`plan` reads a built project (its IR, build manifest and ``forge.yml``)
and decides what can be checked at each level:

- ``design``: the generated (or authored) top level, driven and observed
  through the interfaces its ports form (:mod:`forge.verification.protocols`);
- ``module:<name>``: each RTL module on its own, with its own ports.

Every level is compiled and elaborated, reset, and run on its input (or idle,
without one), with every observed output checked for X and Z. Its function is
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
import shutil
import subprocess
from dataclasses import dataclass, field
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

    @classmethod
    def from_spec(cls, spec: Any, root: Path, links: Optional[Mapping[str, int]] = None) -> "DataRef":
        if isinstance(spec, (str, Path)):
            spec = {"path": str(spec)}
        path = (root / str(spec["path"])).resolve()
        fmt = str(spec.get("format") or _guess_format(path))
        if fmt not in data_formats():
            raise ValueError(f"{path}: format {fmt!r}, want one of {', '.join(data_formats())}")
        return cls(path, fmt, {str(k): int(v) for k, v in (links or {}).items()})

    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": str(self.path), "format": self.format, "sha256": self.sha256()}
        if self.links:
            out["links"] = dict(self.links)
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

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"level": self.name, "top": self.top}
        if self.skip:
            out["skipped"] = self.skip
            return out
        assert self.bench is not None
        out["clock"] = self.bench.clock
        out["clock_period_ns"] = self.bench.period_ns
        out["reset"] = {"port": self.bench.reset, "active": "high" if self.bench.reset_active_high else "low"}
        out["interfaces"] = [{
            "name": i.name, "direction": i.direction, "protocol": i.version, "width": i.width,
            "ports": [p.name for p in i.ports()]} for i in self.bench.interfaces]
        out["input"] = str(self.input.path) if self.input else None
        out["expected"] = str(self.expected.path) if self.expected else None
        out["checks"] = ["compile", "reset", "no_x_on_outputs"] + (["function"] if self.expected else [])
        if not self.expected:
            out["function"] = "no expected output: NOT_VERIFIED_NO_ORACLE"
        out["compare"] = self.compare.to_dict()
        return out


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


def plan(project: Any, *, spec: Optional[Mapping[str, Any]] = None,
         input_override: Optional[Any] = None, expected_override: Optional[Any] = None) -> VerificationPlan:
    """The plan of a built ``forge.yml`` project (``forge.project.resolve.ResolvedProject``).

    *spec* is ``forge.yml``'s ``verification:`` mapping; *input_override* and
    *expected_override* (``--input``, ``--expected``; absolute paths or specs)
    replace its ``input`` and ``expected``.
    """
    spec = dict(spec or {})
    root: Path = project.root
    gen = project.top_level().parent
    manifest = json.loads((gen / "build_manifest.json").read_text())
    ir = json.loads((gen / "design.ir.json").read_text())["design"]
    notes: List[str] = []
    backends = [str(b) for b in spec.get("backends") or ["xsim", "verilator"]]
    for b in backends:
        if b not in BACKENDS:
            raise ValueError(f"verification.backends: {b!r}, want {' or '.join(BACKENDS)}")
    levels_wanted = [str(x) for x in spec.get("levels") or ["design", "modules"]]
    compare = sb.Compare.from_dict(spec.get("compare"))
    links = spec.get("links") or {}

    def data(value: Any) -> Optional[DataRef]:
        return DataRef.from_spec(value, root, links) if value else None

    period = project.config.clock.resolved_period_ns()
    per_module = spec.get("modules") or {}
    ir_modules = {m["name"]: m for m in ir.get("modules", [])}
    module_benches: Dict[str, Union[str, ModuleBench]] = {}
    module_ifaces: Dict[str, List[Interface]] = {}
    for name, mod in manifest.get("modules", {}).items():
        files = [Path(f) for f in mod.get("verilog_files", [])]
        if mod.get("kind") != "rtl" or not files:
            continue
        resolved = _module_bench(ir_modules.get(name), project.config)
        module_benches[name] = resolved
        if not isinstance(resolved, str):
            ports, mclock, mreset, _ = resolved
            module_ifaces[name] = infer(ports, exclude=[mclock, mreset],
                                        declared=(per_module.get(name) or {}).get("protocols"))

    top_ports = [Port(p["name"], p["direction"], int(p["width"])) for p in ir["top_ports"]]
    clock = next((p["name"] for p in ir["top_ports"] if p.get("origin") == "clock"), None)
    reset = next((p["name"] for p in ir["top_ports"] if p.get("origin") == "reset"), None)
    levels: List[Level] = []
    if "design" in levels_wanted:
        top = manifest["top_module"]
        if clock is None or reset is None:
            levels.append(Level("design", top, [], [], None, skip="the top level has no clock or reset port"))
        else:
            ifaces = infer(top_ports, exclude=[clock, reset], declared=spec.get("protocols"))
            probes = [(inst["id"], i) for inst in ir.get("instances", []) if spec.get("record_modules")
                      for i in module_ifaces.get(inst["module"], [])]
            notes.append(f"design: clock {clock}, reset {reset} (active high, generated top), "
                         + ", ".join(f"{i.name}: {i.protocol}" for i in ifaces))
            levels.append(Level(
                "design", top, [Path(f) for f in manifest["verilog_files"]],
                [Path(d) for d in manifest.get("include_dirs", [])],
                Bench(top, clock, reset, True, ifaces, period_ns=period,
                      depth=int(spec.get("depth", 1 << 16)), probes=probes),
                data(input_override or spec.get("input")), data(expected_override or spec.get("expected")),
                compare, int(spec.get("gap", 0))))
    if "modules" in levels_wanted:
        for name, mod in sorted(manifest.get("modules", {}).items()):
            files = [Path(f) for f in mod.get("verilog_files", [])]
            top = str(mod.get("top") or name)
            mspec = per_module.get(name) or {}
            if mod.get("kind") != "rtl" or not files:
                levels.append(Level(f"module:{name}", top, [], [], None,
                                    skip="not an RTL module; checked through the design level"))
                continue
            bench = module_benches[name]
            if isinstance(bench, str):
                levels.append(Level(f"module:{name}", top, [], [], None, skip=bench))
                continue
            _, mclock, mreset, active_high = bench
            levels.append(Level(
                f"module:{name}", top, files, [Path(d) for d in mod.get("include_dirs", [])],
                Bench(top, mclock, mreset, active_high, module_ifaces[name], period_ns=period),
                data(mspec.get("input")), data(mspec.get("expected")),
                sb.Compare.from_dict(mspec.get("compare")), int(mspec.get("gap", 0))))
            notes.append(f"module:{name}: clock {mclock}, reset {mreset} "
                         f"(active {'high' if active_high else 'low'})")
    notes.append(f"clock period {period:g} ns (forge.yml clock)")
    return VerificationPlan(levels, backends, notes)


# ── Data ────────────────────────────────────────────────────────────────────

def load_inputs(ref: DataRef, bench: Bench) -> Dict[str, List[Any]]:
    by = {i.name: i for i in bench.interfaces if i.direction == "in"}
    if _is_concise_json(ref):
        doc = json.loads(ref.path.read_text())
        doc = doc.get("inputs", doc)
        unknown = sorted(set(doc) - set(by))
        if unknown:
            raise ValueError(f"{ref.path}: no input interface {', '.join(unknown)} (have {', '.join(by)})")
        return {k: list(v) for k, v in doc.items()}
    return _from_patterns(ref, by, inputs=True)


def load_expected(ref: DataRef, bench: Bench) -> Dict[str, List[sb.Expected]]:
    by = {i.name: i for i in bench.interfaces if i.direction == "out"}
    if _is_concise_json(ref):
        doc = json.loads(ref.path.read_text())
        doc = doc.get("outputs", doc)
        unknown = sorted(set(doc) - set(by))
        if unknown:
            raise ValueError(f"{ref.path}: no output interface {', '.join(unknown)} (have {', '.join(by)})")
        out = {}
        for k, values in doc.items():
            rows = []
            for v in values:
                if isinstance(v, Mapping) and "value" in v:
                    rows.append(sb.Expected(pack(by[k], v["value"]), v.get("cycle"), v.get("source")))
                else:
                    rows.append(sb.Expected(pack(by[k], v)))
            out[k] = rows
        return out
    return {k: [sb.Expected(pack(by[k], v)) for v in vals]
            for k, vals in _from_patterns(ref, by, inputs=False).items()}


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


def _dataset_links(ref: DataRef, by: Mapping[str, Interface]) -> Tuple[Dict[int, List[Any]], Dict[str, List[Any]]]:
    """``(words per link, values per interface)`` from the format's registered loader.

    A loader with ``read`` (the pattern-file loaders) gives every frame of
    every link, idle ones included. Any other loader is read through its
    events: an event's ``links`` (link -> words) are concatenated per link,
    and values it keys by an interface name are concatenated per interface.
    """
    from forge.verification.dataset_format import get_format_loader
    loader = get_format_loader(ref.path, ref.format)
    if hasattr(loader, "read"):
        return dict(loader.read(ref.path).links), {}
    links: Dict[int, List[Any]] = {}
    values: Dict[str, List[Any]] = {}
    for event in loader.load(ref.path).events:
        for link, words in (event.get("links") or {}).items():
            links.setdefault(int(link), []).extend(_word(w) for w in words)
        for name in by:
            if name in event:
                v = event[name]
                values.setdefault(name, []).extend(v if isinstance(v, list) else [v])
    return links, values


def _from_patterns(ref: DataRef, by: Mapping[str, Interface], *, inputs: bool) -> Dict[str, List[Any]]:
    board_links, by_name = _dataset_links(ref, by)
    if by_name:
        return by_name
    wanted = {k: v for k, v in ref.links.items() if k in by}
    if not wanted:
        raise ValueError(f"{ref.path}: verification.links maps no interface of this level to a link")
    out: Dict[str, List[Any]] = {}
    for name, link in wanted.items():
        if link not in board_links:
            raise ValueError(f"{ref.path}: no link {link} (for {name})")
        words = board_links[link]
        iface = by[name]
        if iface.protocol == "emp_lword":
            keep = words if inputs else [w for w in words if w.valid]
            out[name] = [{"data": w.data, "valid": w.valid, "start": w.start, "last": w.last,
                          "start_of_orbit": w.start_of_orbit, "strobe": w.strobe} for w in keep]
        else:
            out[name] = [w.data for w in words if w.valid]
    return out


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
    if backend == "xsim":
        cmds = []
        if vhdl:
            cmds.append(["xvhdl", *vhdl])
        cmds.append(["xvlog", "-sv", *[a for d in incs for a in ("-i", d)], *vlog, str(tb)])
        cmds.append(["xelab", TB_CLOCKED, "-s", "forge_tb_snap", "-timescale", "1ns/1ps"])
        return cmds
    if vhdl:
        raise RuntimeError("Verilator does not read VHDL")
    # forge_tb needs no timing support; with --no-timing a source's own delays
    # (HLS writes `initial #0 ...`) are taken as zero.
    return [["verilator", "--cc", "--exe", "--build", "--no-timing", "-j", "0", "-Wno-fatal", "-Wno-lint", "-Wno-style",
             "-Wno-STMTDLY", "--top-module", TB_MODULE, "-Mdir", str(work / "obj"), "-o", "forge_tb",
             *[f"-I{d}" for d in incs], *vlog, str(tb), str(work / "forge_tb_main.cpp")]]


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


def _evidence(level: Level, backend: str, status: str, **extra: Any) -> Evidence:
    doc: Dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "level": level.name,
        "top": level.top,
        "backend": backend,
        "status": status,
        "sources": [{"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                    for f in level.sources if f.is_file()],
        "input": level.input.to_dict() if level.input else None,
        "expected": _expected_ref(level),
        "checker": {"version": sb.CHECKER_VERSION, **level.compare.to_dict()},
        "protocols": {i.name: i.version for i in level.bench.interfaces} if level.bench else {},
        "clock_period_ns": level.bench.period_ns if level.bench else None,
        "testbench_generator": TB_GENERATOR_VERSION,
        **extra,
    }
    body = json.dumps(doc, sort_keys=True).encode()
    doc["evidence_id"] = hashlib.sha256(body).hexdigest()
    return Evidence(level.name, backend, status, doc)


def run(level: Level, backend: str, work: Path, *, seed: int = 1, stall: int = 0, backpressure: int = 0,
        max_cycles: int = 1_000_000) -> Evidence:
    """Simulate *level* on *backend* in *work* and compare; never raises for a failing design."""
    if level.skip or level.bench is None:
        return _evidence(level, backend, sb.SKIPPED, reason=level.skip)
    missing = missing_tools(backend)
    if missing:
        return _evidence(level, backend, sb.BLOCKED_MISSING_TOOL, reason=f"{', '.join(missing)} not on PATH")
    bench = level.bench
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

    values = load_inputs(level.input, bench) if level.input else {}
    expected = load_expected(level.expected, bench) if level.expected else None
    plusargs = write_inputs(bench, values, work / "data", gap=level.gap)
    trace = work / "trace.txt"
    plusargs.update({"TRACE": str(trace), "SEED": str(seed), "MAX_CYCLES": str(max_cycles)})
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
             "seed": seed, "stall_percent": stall, "backpressure_percent": backpressure}
    if proc.returncode != 0 or not trace.is_file():
        return _evidence(level, backend, sb.FAILED, **ident, reason=f"simulation failed, see {work / 'sim.log'}")
    t = _parse_trace(trace.read_text())
    if not t["ended"] or "timeout" in t["unknown"]:
        return _evidence(level, backend, sb.FAILED, **ident, reason=f"simulation did not finish within {max_cycles} cycles")

    first_in = next((i.name for i in bench.interfaces if i.direction == "in"), None)
    input_cycles = [c for c, _ in t["inputs"].get(first_in, [])] if first_in else []
    results = []
    for o in (i for i in bench.interfaces if i.direction == "out"):
        exp = None if expected is None else expected.get(o.name)
        results.append(sb.compare(o.name, exp, t["outputs"].get(o.name, []), level.compare,
                                  input_cycles=input_cycles, unknown=t["unknown"].get(o.name, [])))
    status = sb.combine([r.status for r in results])
    if expected is None and status != sb.FAILED:
        status = sb.NOT_VERIFIED_NO_ORACLE
    if bench.probes:
        ident["recorded_modules"] = _record(bench, t, work / "module_traces", {
            "level": level.name, "backend": backend, "status": status, "compile_key": key,
            "input_sha256": level.input.sha256() if level.input else None,
            "note": "recorded from the design level: a regression reference, not an independent oracle"})
    return _evidence(level, backend, status, **ident,
                     inputs_driven={k: len(v) for k, v in t["inputs"].items()},
                     interfaces=[r.to_dict() for r in results])


def run_plan(p: VerificationPlan, work_root: Path, **kwargs: Any) -> List[Evidence]:
    out = []
    for level in p.levels:
        for backend in p.backends:
            work = work_root / level.name.replace(":", "_") / backend
            out.append(run(level, backend, work, **kwargs))
    return out


def summary(evidence: Sequence[Evidence]) -> str:
    return sb.combine([e.status for e in evidence if e.status != sb.SKIPPED]) if any(
        e.status != sb.SKIPPED for e in evidence) else sb.SKIPPED
