"""``emp_payload`` around a FORGE top level, for the emp-fwk checkout it is built with.

The entity, with its ``library``/``use`` clauses, is copied from that checkout's
``components/payload/firmware/hdl/emp_payload.vhd``, so its ports are the ones
of that release (``bc0`` up to 0.9.x, ``ttc2``/``tts_busy``/``ext_lvds_*`` from
0.10). The architecture comes from a spec:

    emp_payload:
      emp_fwk: v0.10.3                  # optional: the checkout must be this release
      algo_entity: algo_top
      clock: clk_p                      # EMP signal driving ap_clk
      reset: {source: "rst_loc(0)"}     # VHDL expression, active high; or: disabled
      inputs:                           # algorithm input <- one field of one link
        dec_0_raw_hit: {link: 0, field: data, bits: [31, 0]}
        dec_0_raw_valid: {link: 0, field: valid}
      outputs:                          # output link <- algorithm outputs, per field
        0: {data: tout_trigger_word, valid: tout_out_valid}
      drive: {}                         # payload output -> VHDL expression

Every algorithm input must be mapped, with the width the IR gives it. Output
links not listed carry ``LWORD_NULL``; other payload outputs are tied off by
type, or driven as the spec says.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ENTITY_FILE = Path("components/payload/firmware/hdl/emp_payload.vhd")
FIELDS = ("data", "valid", "start", "last", "start_of_orbit", "strobe")
_TIE_OFF = [
    (re.compile(r"^std_logic$"), "'0'"),
    (re.compile(r"^std_logic_vector\b"), "(others => '0')"),
    (re.compile(r"^ipb_rbus$"), "IPB_RBUS_NULL"),
    (re.compile(r"^slink_input_data_quad_array\b"), "(others => SLINK_INPUT_DATA_ARRAY_NULL)"),
]


@dataclass(frozen=True)
class Port:
    name: str
    direction: str
    type: str


@dataclass(frozen=True)
class EmpRelease:
    """The payload entity of one emp-fwk checkout."""
    root: Path
    version: Optional[str]
    context: str          # library and use clauses
    port_clause: str      # "port( ... );" verbatim
    ports: Tuple[Port, ...]
    sha256: str

    @classmethod
    def read(cls, emp_fwk: Path) -> "EmpRelease":
        path = emp_fwk / ENTITY_FILE
        if not path.is_file():
            raise ValueError(f"{emp_fwk} is not an emp-fwk checkout: no {ENTITY_FILE}")
        text = path.read_text()
        m = re.search(r"^entity\s+emp_payload\s+is\s*(port\s*\(.*?\)\s*;)\s*end\b", text, re.S | re.I | re.M)
        if not m:
            raise ValueError(f"{path}: no emp_payload entity with a port clause")
        context = "\n".join(line for line in text[:m.start()].splitlines()
                            if re.match(r"\s*(library|use)\b", line, re.I))
        body = re.sub(r"--[^\n]*", "", m.group(1))
        inner = body[body.index("(") + 1: body.rindex(")")]
        ports = []
        for decl in inner.split(";"):
            if ":" not in decl:
                continue
            names, rest = decl.split(":", 1)
            direction, _, typ = rest.strip().partition(" ")
            for name in names.split(","):
                ports.append(Port(name.strip(), direction.lower(), " ".join(typ.split())))
        try:
            version = subprocess.run(["git", "-C", str(emp_fwk), "describe", "--tags", "--exact-match"],
                                     capture_output=True, text=True, timeout=30).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            version = None
        return cls(emp_fwk, version, context, m.group(1), tuple(ports),
                   hashlib.sha256(text.encode()).hexdigest())


def _algo_ports(ir: Mapping) -> Dict[str, Tuple[str, int]]:
    design = ir.get("design", ir)
    return {p["name"]: (p["direction"], int(p["width"])) for p in design["top_ports"]
            if p["name"] not in ("ap_clk", "ap_rst")}


def _slice(width: int) -> str:
    return f"({width - 1} downto 0)"


def generate(spec: Mapping[str, Any], release: EmpRelease, ir: Mapping) -> str:
    """The VHDL of ``emp_payload`` for *spec*, *release* and the top level described by *ir*."""
    want = spec.get("emp_fwk")
    if want and release.version != want:
        raise ValueError(f"the spec is for emp-fwk {want}, the checkout {release.root} is "
                         f"{release.version or 'not at a release tag'}")
    entity = spec.get("algo_entity", "algo_top")
    algo = _algo_ports(ir)
    emp = {p.name: p for p in release.ports}
    for sig in ("clk_p", "d", "q"):
        if sig not in emp:
            raise ValueError(f"emp-fwk {release.version}: emp_payload has no {sig} port")

    reset = spec.get("reset")
    if reset is None:
        raise ValueError("emp_payload.reset is required: {source: <VHDL expression>} or disabled")
    rst = "'0'" if reset == "disabled" else str(reset["source"])
    maps = [f"      ap_clk => {spec.get('clock', 'clk_p')}", f"      ap_rst => {rst}"]

    inputs = dict(spec.get("inputs") or {})
    for port, (direction, width) in algo.items():
        if direction != "in":
            continue
        m = inputs.pop(port, None)
        if m is None:
            raise ValueError(f"algorithm input {port} has no link field in emp_payload.inputs")
        field = m.get("field", "data")
        if field not in FIELDS:
            raise ValueError(f"inputs.{port}.field: want one of {', '.join(FIELDS)}")
        expr = f"d({int(m['link'])}).{field}"
        if field == "data":
            hi, lo = m.get("bits", [63, 0])
            if hi - lo + 1 != width:
                raise ValueError(f"inputs.{port}: bits {hi}..{lo} are {hi - lo + 1} wide, the port {width}")
            expr += f"({hi} downto {lo})"
        elif width != 1:
            raise ValueError(f"inputs.{port}: {field} is one bit, the port is {width} wide")
        maps.append(f"      {port} => {expr}")
    if inputs:
        raise ValueError(f"emp_payload.inputs names ports algo_top does not have as inputs: {', '.join(inputs)}")

    signals: List[str] = []
    assigns: List[str] = []
    used_links = set()
    for link, fields in (spec.get("outputs") or {}).items():
        link = int(link)
        used_links.add(link)
        for field in FIELDS:
            src = (fields or {}).get(field)
            if src is None:
                default = {"data": "(others => '0')", "strobe": "'1'"}.get(field, "'0'")
                assigns.append(f"  q({link}).{field} <= {default};")
                continue
            if algo.get(src, ("", 0))[0] != "out":
                raise ValueError(f"outputs.{link}.{field}: {src} is not an output of {entity}")
            width = algo[src][1]
            sig = f"algo_{src}"
            if sig not in {s.split()[1] for s in signals}:
                signals.append(f"  signal {sig} : std_logic{'_vector' + _slice(width) if width > 1 else ''};")
                maps.append(f"      {src} => {sig}")
            if field == "data":
                if width > 64:
                    raise ValueError(f"outputs.{link}.data: {src} is {width} bits, a link word 64")
                pad = f"(63 downto {width} => '0') & " if width < 64 else ""
                assigns.append(f"  q({link}).data <= {pad}{sig};")
            else:
                if width != 1:
                    raise ValueError(f"outputs.{link}.{field}: {src} is {width} bits, the field one")
                assigns.append(f"  q({link}).{field} <= {sig};")
    for port, (direction, _) in algo.items():
        if direction == "out" and f"      {port} => algo_{port}" not in maps:
            maps.append(f"      {port} => open")

    drive = dict(spec.get("drive") or {})
    ties = []
    for out_port in release.ports:
        if out_port.direction != "out" or out_port.name == "q":
            continue
        if out_port.name in drive:
            ties.append(f"  {out_port.name} <= {drive.pop(out_port.name)};")
            continue
        value = next((v for rx, v in _TIE_OFF if rx.match(out_port.type)), None)
        if value is None:
            raise ValueError(f"emp-fwk {release.version}: no tie-off is known for output {out_port.name} "
                             f"({out_port.type}); drive it in emp_payload.drive")
        ties.append(f"  {out_port.name} <= {value};")
    if drive:
        raise ValueError(f"emp_payload.drive names signals that are not payload outputs: {', '.join(drive)}")

    lines = [
        f"-- Written by forge from {spec.get('_source', 'an emp_payload spec')}; do not edit.",
        f"-- Entity of emp-fwk {release.version or '(no release tag)'} "
        f"({ENTITY_FILE}, sha256 {release.sha256}).",
        release.context,
        "",
        "entity emp_payload is",
        "  " + release.port_clause,
        "end emp_payload;",
        "",
        "architecture forge of emp_payload is",
        *signals,
        "begin",
        "",
        f"  algo : entity work.{entity}",
        "    port map(",
        ",\n".join(maps),
        "      );",
        "",
        *assigns,
        "  gen_idle : for i in q'range generate",
        f"    idle : if {' and '.join(f'i /= {k}' for k in sorted(used_links)) or 'true'} generate",
        "      q(i) <= LWORD_NULL;",
        "    end generate;",
        "  end generate;",
        "",
        *ties,
        "",
        "end forge;",
        "",
    ]
    return "\n".join(lines)


def write(spec_path: Path, emp_fwk: Path, ir_path: Path, out: Path) -> EmpRelease:
    import yaml

    spec = (yaml.safe_load(spec_path.read_text()) or {}).get("emp_payload")
    if not isinstance(spec, dict):
        raise ValueError(f"{spec_path}: no emp_payload mapping")
    spec = {**spec, "_source": spec_path.name}
    release = EmpRelease.read(emp_fwk)
    text = generate(spec, release, json.loads(ir_path.read_text()))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return release
