"""A testbench generated from a DUT's interfaces, and the data it runs on.

The testbench is compiled once and runs any number of transactions: each
input interface reads its words from a ``$readmemh`` file named by a plusarg,
and every accepted input and observed output is written to one trace,
``<cycle> <direction><interface> <hex>``. Nothing in it depends on the data,
so new inputs need no recompilation (the compile key of
:mod:`forge.verification.compile_key` is over the testbench and the sources).

Protocols, as driven and observed:

- ``scalar``: an input takes the next word every cycle (the last word is
  held); an output is recorded every cycle, which is not activity: a design
  whose outputs all change every cycle ends ``drain`` cycles after its last
  input.
- ``valid``: an input word is driven with valid for one cycle, then
  ``gap`` idle cycles; an output is recorded when valid is high.
- ``ready_valid``: an input word is held with valid until ready, and a new
  word is withheld with probability ``stall`` percent; an output's ready is
  low with probability ``backpressure`` percent, and a word is recorded when
  valid and ready are both high.
- ``emp_lword``: an input word per frame, all fields as given; an output is
  recorded when valid is high, with its fields.
- ``memory``: the testbench holds the memory, zero at the start (as a BRAM
  after configuration), read with its declared latency.
  An input memory's transaction is one call's contents, written into page
  ``call % pages`` before the call starts; an output memory's every write is
  recorded. A block with memories runs one call at a time: the next starts
  after ``ap_done``.

A probe records an interface of a module instance inside the DUT the same
way, as ``<instance>/<interface>``. An X or Z on a recorded output, or on an
output's valid after reset, is written to the trace as ``<cycle> !<interface> x``. The run ends ``drain``
cycles after the inputs are consumed and every output has given the words
expected of it, or ``quiet`` cycles after the last input or output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from forge.verification.protocols import EMP_FIELDS, Interface

TB_GENERATOR_VERSION = "4"
TB_MODULE = "forge_tb"
# Drives forge_tb's clock where the simulator runs Verilog delays (XSim);
# Verilator runs forge_tb from a generated C++ main instead.
TB_CLOCKED = "forge_tb_clocked"


@dataclass
class Bench:
    top: str
    clock: str
    reset: str
    reset_active_high: bool
    interfaces: List[Interface]
    period_ns: float = 10.0
    reset_cycles: int = 8
    depth: int = 1 << 16
    # (instance, interface of its module): traffic recorded inside the DUT
    probes: List[Tuple[str, Interface]] = field(default_factory=list)
    # parameter overrides of the DUT, as Verilog expressions
    parameters: Dict[str, Any] = field(default_factory=dict)


def word_width(iface: Interface) -> int:
    if iface.protocol == "emp_lword":
        return iface.width + len(iface.fields)
    return iface.width + (1 if iface.protocol == "valid" else 0)


def _emp_order(iface: Interface) -> List[str]:
    return [f for f in reversed(EMP_FIELDS) if f in iface.fields]


def pack(iface: Interface, value: Any) -> int:
    """The word of one transaction: a number, or a mapping by data port name; an
    ``emp_lword`` transaction is ``{data, valid, start, last, start_of_orbit,
    strobe}``, packed flags first as the trace shows it."""
    if iface.protocol == "emp_lword":
        if not isinstance(value, Mapping):
            raise ValueError(f"{iface.name}: an emp_lword transaction is a mapping of data and its flags")
        data = _int(value.get("data", 0))
        if data < 0 or data >> iface.width:
            raise ValueError(f"{iface.name}: data {value.get('data')!r} does not fit {iface.width} bits")
        flags = 0
        for f in _emp_order(iface):
            flags = (flags << 1) | (1 if value.get(f, f == "strobe") else 0)
        return (flags << iface.width) | data
    if isinstance(value, Mapping):
        word, shift = 0, 0
        for p in iface.data:
            v = _int(value.get(p.name, 0))
            if v >> p.width:
                raise ValueError(f"{iface.name}.{p.name}: 0x{v:x} does not fit {p.width} bits")
            word |= v << shift
            shift += p.width
        return word
    v = _int(value)
    if v < 0 or v >> iface.width:
        raise ValueError(f"{iface.name}: {value!r} does not fit {iface.width} bits")
    return v


def unpack(iface: Interface, word: int) -> Any:
    """A traced word as :func:`pack` takes it."""
    if iface.protocol != "emp_lword":
        return hex(word)
    order = _emp_order(iface)
    flags = word >> iface.width
    out: Dict[str, Any] = {"data": hex(word & ((1 << iface.width) - 1))}
    for k, f in enumerate(order):
        out[f] = (flags >> (len(order) - 1 - k)) & 1
    return out


def _int(v: Any) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    return int(str(v), 0)


def _memory_text(b: Bench, ins: Sequence[Interface], call: Any) -> List[str]:
    """The memories the testbench holds for the DUT's ``memory`` interfaces."""
    L: List[str] = []
    for m in [i for i in b.interfaces if i.protocol == "memory"]:
        w, depth, lat = m.width, m.options["depth"], m.options["latency"]
        f = m.fields
        # zero, as a BRAM after configuration and an HLS model's static array start
        L += [f"  reg [{w - 1}:0] ram_{m.name} [0:{depth - 1}];",
              f"  initial for (int i = 0; i < {depth}; i++) ram_{m.name}[i] = '0;"]
        if m.direction == "in":
            L += [f"  reg [{w - 1}:0] mem_{m.name} [0:{b.depth - 1}];",
                  f"  reg [31:0] cnt_{m.name} [0:{b.depth - 1}];",
                  f"  integer n_{m.name} = 0, p_{m.name} = 0, off_{m.name} = 0;"]
        else:
            L.append(f"  integer expect_{m.name} = -1, seen_{m.name} = 0;")
        for k in "01":
            if f"q{k}" in f:
                rd = f"rd_{m.name}_{k}"
                L += [f"  reg [{w - 1}:0] {rd} [0:{lat - 1}];",
                      f"  always @(posedge {b.clock}) begin",
                      f"    if ({f['ce' + k].name}) {rd}[0] <= ram_{m.name}[{f['address' + k].name}];",
                      *([f"    for (int i = 1; i < {lat}; i++) {rd}[i] <= {rd}[i - 1];"] if lat > 1 else []),
                      "  end",
                      f"  always @* {f['q' + k].name} = {rd}[{lat - 1}];"]
            if f"we{k}" in f:
                d, a = f[f"d{k}"].name, f[f"address{k}"].name
                L += [f"  always @(posedge {b.clock}) if ({f['ce' + k].name} && {f['we' + k].name}) begin",
                      f"    ram_{m.name}[{a}] <= {d};"]
                if m.direction == "out":
                    L += ["    if (running) begin",
                          f'      if ($isunknown({d})) $fdisplay(fd, "%0d !{m.name} x", cycle);',
                          f'      else $fdisplay(fd, "%0d <{m.name} %h", cycle, {d});',
                          f"      seen_{m.name} = seen_{m.name} + 1; last_activity = cycle;",
                          "    end"]
                L.append("  end")
        L.append("")
    if call is not None:
        L += ["  integer done_call = 0;",
              f"  always @(posedge {b.clock}) if (running && {call.fields['done'].name}) done_call <= done_call + 1;", ""]
    return L


def _load_pages(b: Bench) -> List[str]:
    """Statements that write the next call's contents into each input memory."""
    L: List[str] = []
    for m in [i for i in b.interfaces if i.protocol == "memory" and i.direction == "in"]:
        page = m.options["depth"] // m.options["pages"]
        L += [f"        for (int i = 0; i < cnt_{m.name}[p_call]; i++)",
              f"          ram_{m.name}[(p_call % {m.options['pages']}) * {page} + i] = mem_{m.name}[off_{m.name} + i];",
              f'        $fdisplay(fd, "%0d >{m.name} %h", cycle, cnt_{m.name}[p_call]);',
              f"        off_{m.name} = off_{m.name} + cnt_{m.name}[p_call]; p_{m.name} = p_{m.name} + 1;"]
    return L


def memory(iface: Interface, values: Sequence[Any], gap: int = 0) -> List[str]:
    """The ``$readmemh`` lines of an input interface's transactions."""
    digits = max(1, -(-word_width(iface) // 4))
    lines = []
    for v in values:
        w = pack(iface, v)
        if iface.protocol == "valid":
            lines.append(f"{(1 << iface.width) | w:0{digits}x}")
            lines += ["0" * digits] * gap
        else:
            lines.append(f"{w:0{digits}x}")
    return lines


def _decl(direction: str, width: int, name: str) -> str:
    kind = "reg " if direction == "in" else "wire"
    vec = f"[{width - 1}:0] " if width > 1 else ""
    return f"  {kind} {vec}{name};"


def _concat(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else "{" + ", ".join(names) + "}"


def testbench(b: Bench) -> str:
    half = round(b.period_ns / 2, 3)   # `timescale 1ns/1ps
    active = "1'b1" if b.reset_active_high else "1'b0"
    idle = "1'b0" if b.reset_active_high else "1'b1"
    ins = [i for i in b.interfaces if i.direction == "in" and i.protocol != "memory"]
    outs = [i for i in b.interfaces if i.direction == "out" and i.protocol != "memory"]
    mem_ins = [i for i in b.interfaces if i.direction == "in" and i.protocol == "memory"]
    mem_outs = [i for i in b.interfaces if i.direction == "out" and i.protocol == "memory"]
    call = next((i for i in ins if "done" in i.fields), None) if mem_ins or mem_outs else None
    if mem_ins and call is None:
        raise ValueError(f"{b.top}: input memories ({', '.join(i.name for i in mem_ins)}) are written per call; "
                         "the design needs HLS's ap_start/ap_ready/ap_done (ap_ctrl_hs) to say when a call starts")
    L = [
        "// Written by forge (forge.verification.auto_tb); do not edit.",
        "`timescale 1ns/1ps",
        f"module {TB_CLOCKED};",
        f"  reg {b.clock} = 1'b0;",
        f"  always #{half:g} {b.clock} = ~{b.clock};",
        f"  {TB_MODULE} tb (.{b.clock}({b.clock}));",
        "endmodule",
        "",
        f"module {TB_MODULE} (input {b.clock});",
        f"  reg {b.reset} = {active};",
        "  integer cycle = 0, fd, max_cycles = 100000, drain = 32, quiet = 1000, last_activity = 0, held = 0;",
        "  reg running = 1'b0;",
        "  string trace;",
    ]
    ports = list({p.name: p for i in b.interfaces for p in i.ports()}.values())
    L += [_decl(p.direction, p.width, p.name) for p in ports]
    if call is not None and not call.data:
        L.append("  reg [0:0] call_word;")
    overrides = ", ".join(f".{k}({v})" for k, v in b.parameters.items())
    L.append(f"  {b.top} #({overrides}) dut (" if overrides else f"  {b.top} dut (")
    conns = [f"    .{b.clock}({b.clock})", f"    .{b.reset}({b.reset})"]
    conns += [f"    .{p.name}({p.name})" for p in ports]
    L += [",\n".join(conns), "  );", ""]

    for i in ins:
        w = word_width(i)
        L += [
            f"  reg [{w - 1}:0] mem_{i.name} [0:{b.depth - 1}];",
            f"  integer n_{i.name} = 0, p_{i.name} = 0, stall_{i.name} = 0;",
        ]
    for o in outs:
        L.append(f"  integer expect_{o.name} = -1, seen_{o.name} = 0, bp_{o.name} = 0;")
    L += _memory_text(b, ins, call)

    L += ["", "  initial begin", "    string path;", "    integer seed;",
          '    if (!$value$plusargs("TRACE=%s", trace)) trace = "trace.txt";',
          "    fd = $fopen(trace, \"w\");",
          '    if ($value$plusargs("SEED=%d", seed)) seed = $urandom(seed);',
          '    void\'($value$plusargs("MAX_CYCLES=%d", max_cycles));',
          '    void\'($value$plusargs("DRAIN=%d", drain));',
          '    void\'($value$plusargs("QUIET=%d", quiet));']
    for i in ins:
        L += [f'    if ($value$plusargs("STIM_{i.name}=%s", path)) $readmemh(path, mem_{i.name});',
              f'    void\'($value$plusargs("N_{i.name}=%d", n_{i.name}));',
              f'    void\'($value$plusargs("STALL_{i.name}=%d", stall_{i.name}));']
        for p in i.ports():
            if p.direction == "in":
                L.append(f"    {p.name} = '0;")
    for mi in mem_ins:
        L += [f'    if ($value$plusargs("STIM_{mi.name}=%s", path)) $readmemh(path, mem_{mi.name});',
              f'    if ($value$plusargs("CNT_{mi.name}=%s", path)) $readmemh(path, cnt_{mi.name});',
              f'    void\'($value$plusargs("N_{mi.name}=%d", n_{mi.name}));']
    for mo in mem_outs:
        L.append(f'    void\'($value$plusargs("EXPECT_{mo.name}=%d", expect_{mo.name}));')
    for o in outs:
        L += [f'    void\'($value$plusargs("EXPECT_{o.name}=%d", expect_{o.name}));',
              f'    void\'($value$plusargs("BACKPRESSURE_{o.name}=%d", bp_{o.name}));']
        if o.ready is not None:
            L.append(f"    {o.ready.name} = 1'b1;")
    L += ["  end", "",
          f"  always @(posedge {b.clock})",
          f"    if (held < {b.reset_cycles}) held <= held + 1;",
          f"    else if ({b.reset} == {active}) {b.reset} <= {idle};",
          "    else running <= 1'b1;",
          ""]

    for i in ins:
        m, n, ptr = f"mem_{i.name}", f"n_{i.name}", f"p_{i.name}"
        data = _concat([q.name for q in reversed(i.data)])
        L.append(f"  always @(posedge {b.clock}) if (running) begin")
        if i.protocol == "scalar":
            L += [f"    if ({ptr} < {n}) begin {data} <= {m}[{ptr}]; {ptr} <= {ptr} + 1;",
                  f'      $fdisplay(fd, "%0d >{i.name} %h", cycle, {m}[{ptr}]); end']
        elif i.protocol == "valid":
            v = i.valid.name  # type: ignore[union-attr]
            L += [f"    if ({ptr} < {n}) begin {{{v}, {data}}} <= {m}[{ptr}]; {ptr} <= {ptr} + 1;",
                  f'      if ({m}[{ptr}][{i.width}]) $fdisplay(fd, "%0d >{i.name} %h", cycle, {m}[{ptr}][{i.width - 1}:0]);',
                  f"    end else {v} <= 1'b0;"]
        elif i.protocol == "ready_valid":
            v, r = i.valid.name, i.ready.name  # type: ignore[union-attr]
            serial = f" && {ptr} == done_call" if i is call else ""
            data = data if i.data else "call_word"
            L += [f"    if (!{v} || {r}) begin",
                  f"      if ({ptr} < {n}{serial} && ($urandom % 100) >= stall_{i.name}) begin",
                  f"        {data} <= {m}[{ptr}]; {v} <= 1'b1; {ptr} <= {ptr} + 1;",
                  *(_load_pages(b) if i is call else []),
                  f"      end else {v} <= 1'b0;",
                  "    end",
                  f'    if ({v} && {r}) $fdisplay(fd, "%0d >{i.name} %h", cycle, {data});']
        else:
            fields = [i.fields[f].name for f in _emp_order(i)]
            L += [f"    if ({ptr} < {n}) begin {_concat(fields + [i.data[0].name])} <= {m}[{ptr}]; {ptr} <= {ptr} + 1;",
                  f'      $fdisplay(fd, "%0d >{i.name} %h", cycle, {m}[{ptr}]);',
                  f"    end else {i.fields['valid'].name} <= 1'b0;"]
        L += ["  end", ""]

    for o in outs:
        data = _concat([q.name for q in reversed(o.data)])
        if o.protocol == "emp_lword":
            data = _concat([o.fields[f].name for f in _emp_order(o)] + [o.data[0].name])
        if o.protocol == "scalar":
            fire = "1'b1"
        elif o.protocol == "ready_valid":
            fire = f"{o.valid.name} && {o.ready.name}"  # type: ignore[union-attr]
        else:
            fire = o.valid.name  # type: ignore[union-attr]
        L.append(f"  always @(posedge {b.clock}) if (running) begin")
        if o.valid is not None:
            L.append(f'    if ($isunknown({o.valid.name})) $fdisplay(fd, "%0d !{o.name} x", cycle);')
        L += [f"    if ({fire}) begin",
              f'      if ($isunknown({data})) $fdisplay(fd, "%0d !{o.name} x", cycle);',
              f'      else $fdisplay(fd, "%0d <{o.name} %h", cycle, {data});',
              f"      seen_{o.name} = seen_{o.name} + 1;"]
        if o.protocol != "scalar":
            L.append("      last_activity = cycle;")
        L.append("    end")
        if o.ready is not None:
            L.append(f"    {o.ready.name} <= ($urandom % 100) >= bp_{o.name};")
        L += ["  end", ""]

    for inst, pi in b.probes:
        ref = [f"dut.{inst}.{q.name}" for q in reversed(pi.data)]
        if pi.protocol == "emp_lword":
            ref = [f"dut.{inst}.{pi.fields[f].name}" for f in _emp_order(pi)] + [f"dut.{inst}.{pi.data[0].name}"]
        if pi.protocol == "scalar":
            fire = "1'b1"
        elif pi.protocol == "ready_valid":
            fire = f"dut.{inst}.{pi.valid.name} && dut.{inst}.{pi.ready.name}"  # type: ignore[union-attr]
        else:
            fire = f"dut.{inst}.{pi.valid.name}"  # type: ignore[union-attr]
        tag = (">" if pi.direction == "in" else "<") + f"{inst}/{pi.name}"
        L += [f"  always @(posedge {b.clock}) if (running && {fire})",
              f'    $fdisplay(fd, "%0d {tag} %h", cycle, {_concat(ref)});', ""]

    consumed = " && ".join([f"p_{i.name} >= n_{i.name}" for i in ins] or ["1'b1"])
    if call is not None:
        consumed += " && done_call >= p_call"
    if any(i.protocol == "ready_valid" for i in ins):
        consumed += " && " + " && ".join(f"!{i.valid.name}" for i in ins  # type: ignore[union-attr]
                                         if i.protocol == "ready_valid")
    reached = " && ".join([f"(expect_{o.name} < 0 || seen_{o.name} >= expect_{o.name})" for o in [*outs, *mem_outs]]
                          or ["1'b1"])
    L += [f"  always @(posedge {b.clock}) if (running) begin",
          "    cycle <= cycle + 1;",
          f"    if (!({consumed})) last_activity = cycle;",
          f"    if ((({consumed}) && ({reached}) && cycle - last_activity >= drain)",
          "        || cycle - last_activity >= quiet || cycle >= max_cycles) begin",
          '      if (cycle >= max_cycles) $fdisplay(fd, "%0d !timeout x", cycle);',
          '      $fdisplay(fd, "%0d end", cycle);',
          "      $fclose(fd);",
          "      $finish;",
          "    end",
          "  end",
          "endmodule", ""]
    return "\n".join(L)


def write_inputs(b: Bench, values: Mapping[str, Sequence[Any]], directory: Path, *, gap: int = 0
                 ) -> Dict[str, str]:
    """Write each input interface's memory file; returns the plusargs that load them."""
    directory.mkdir(parents=True, exist_ok=True)
    args: Dict[str, str] = {}
    for i in b.interfaces:
        if i.direction != "in":
            continue
        if i.protocol == "memory":
            pages = [list(v) if isinstance(v, (list, tuple)) else [v] for v in values.get(i.name, [])]
            words = [w for page in pages for w in page]
            if len(words) > b.depth:
                raise ValueError(f"{i.name}: {len(words)} words, the testbench holds {b.depth} (raise depth)")
            too_big = [len(page) for page in pages if len(page) > i.options["depth"] // i.options["pages"]]
            if too_big:
                raise ValueError(f"{i.name}: a call of {too_big[0]} words does not fit a page of "
                                 f"{i.options['depth'] // i.options['pages']}")
            digits = max(1, -(-i.width // 4))
            (directory / f"{i.name}.mem").write_text("".join(f"{pack(i, w):0{digits}x}\n" for w in words))
            (directory / f"{i.name}.cnt").write_text("".join(f"{len(page):x}\n" for page in pages))
            args.update({f"STIM_{i.name}": str(directory / f"{i.name}.mem"),
                         f"CNT_{i.name}": str(directory / f"{i.name}.cnt"), f"N_{i.name}": str(len(pages))})
            continue
        lines = memory(i, values.get(i.name, []), gap if i.protocol == "valid" else 0)
        if len(lines) > b.depth:
            raise ValueError(f"{i.name}: {len(lines)} words, the testbench holds {b.depth} (raise depth)")
        path = directory / f"{i.name}.mem"
        path.write_text("\n".join(lines) + ("\n" if lines else ""))
        args[f"STIM_{i.name}"] = str(path)
        args[f"N_{i.name}"] = str(len(lines))
    return args


def verilator_main(b: Bench) -> str:
    """The C++ main that clocks forge_tb under Verilator, without its timing support."""
    return "\n".join([
        "// Written by forge (forge.verification.auto_tb); do not edit.",
        f'#include "V{TB_MODULE}.h"',
        '#include "verilated.h"',
        "",
        "int main(int argc, char** argv) {",
        "    VerilatedContext* ctx = new VerilatedContext;",
        "    ctx->commandArgs(argc, argv);",
        f"    V{TB_MODULE}* tb = new V{TB_MODULE}{{ctx}};",
        f"    tb->{b.clock} = 0;",
        "    while (!ctx->gotFinish()) {",
        f"        tb->{b.clock} = !tb->{b.clock};",
        "        tb->eval();",
        "        ctx->timeInc(1);",
        "    }",
        "    tb->final();",
        "    delete tb;",
        "    delete ctx;",
        "    return 0;",
        "}",
        "",
    ])
