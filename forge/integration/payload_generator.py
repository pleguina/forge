"""
FORGE Payload Wrapper Generator (Blobfish provider)

Generates a Blobfish-compatible payload.v from:
  - payload_abi.json     (defines the exact port list)
  - payload_endpoints.json (defines active endpoints)
  - detector_io.resolved.json  (maps endpoints to detector inputs/outputs)
  - control_policies    (clock, buffer, play_data, readback policies)

The generated payload.v:
  1. Declares every port required by payload_abi.json.
  2. Slices active RX lanes into named endpoint wires.
  3. Assigns TX lanes from named endpoint wires (unused → 0).
  4. Drives algo_clk, buffinc/buffdec, play_data, readback per policy.
    5. Instantiates a named algorithm module (defaults to 'arc_algo_top').

This module is framework-agnostic in structure; Blobfish-specific naming is
entirely derived from the ABI manifest, not hardcoded here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge.integration.importer import AbiPort, Endpoint, FrameworkImport


# ---------------------------------------------------------------------------
# Policy model
# ---------------------------------------------------------------------------

@dataclass
class ControlPolicies:
    """Explicit control signal assignment policies."""

    # algo_clk: clock signal name to assign to every active site
    algo_clk: str = "logic_clk"

    # port_aliases: explicit "source_instance.source_port" -> algo top-level
    # port name overrides, for cases where the algo module's port name
    # doesn't match the generic "<source_instance>_<source_port>" derivation
    # (e.g. an internal instance was renamed relative to its external port).
    port_aliases: Dict[str, str] = field(default_factory=dict)

    # buffer_alignment: "disabled_zero" | "link_aligner"
    buffer_alignment: str = "disabled_zero"

    # play_data: "disabled_zero" | "slr_config_bit"
    play_data_source: str = "disabled_zero"
    play_data_slr:    Optional[int] = None
    play_data_bit:    int = 0
    play_data_gate:   Optional[str] = None   # e.g. "slr3_slr2_slr1_slr0_bx0"

    # readback: "zero" (initial milestone)
    readback: str = "zero"

    # tx.register: every used TX lane's word leaves through a register of its
    # own (DONT_TOUCH, so the placer can put it by the lane's quad)
    tx_register: bool = False
    # tx.orbit: algo output port driving tx_orbit of every used TX lane,
    # registered per lane like the word when tx.register is set
    tx_orbit: Optional[str] = None

    # input_register: every link word the payload hands to the algorithm
    # goes through one register, lin_q_<algo port>
    input_register: bool = False

    # The payload's own logic, for what the ABI and the resolved I/O do not
    # describe (board control, status, test features):
    #   connect: algo port -> payload net, or {net, register, width} to put it
    #     through an input register like the links
    #   drive: payload output (a port or a port slice) -> expression, or
    #     {expr, comment}; FORGE emits no tie-off for it
    #   rx_gate: RX endpoint -> mask net ANDed with each of its lane signals
    #   verilog_header / verilog_before_algo: Verilog inserted after the port
    #     list / before the algorithm instance
    connect: Dict[str, Any] = field(default_factory=dict)
    drive: Dict[str, Any] = field(default_factory=dict)
    rx_gate: Dict[str, str] = field(default_factory=dict)
    verilog_header: List[str] = field(default_factory=list)
    verilog_before_algo: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict, base: Optional[Path] = None) -> "ControlPolicies":
        """*base* is the directory relative ``verilog`` file paths resolve against."""
        p = cls()
        if "algo_clk" in d:
            p.algo_clk = d["algo_clk"].get("default", "logic_clk")
        if "buffer_alignment" in d:
            p.buffer_alignment = d["buffer_alignment"].get("default", "disabled_zero")
        if "play_data" in d:
            pd = d["play_data"]
            p.play_data_source = pd.get("source", "disabled_zero")
            p.play_data_slr    = pd.get("slr")
            p.play_data_bit    = pd.get("bit", 0)
            p.play_data_gate   = pd.get("gate")
        if "readback" in d:
            p.readback = d["readback"].get("default", "zero")
        if "port_aliases" in d:
            p.port_aliases = dict(d["port_aliases"])
        if "tx" in d:
            p.tx_register = bool(d["tx"].get("register", False))
            p.tx_orbit = d["tx"].get("orbit")
        p.input_register = bool(d.get("input_register", False))
        p.connect = dict(d.get("connect") or {})
        p.drive = {str(k): v for k, v in (d.get("drive") or {}).items()}
        p.rx_gate = dict(d.get("rx_gate") or {})
        for where, paths in (d.get("verilog") or {}).items():
            if where not in ("header", "before_algo"):
                raise ValueError(f"verilog.{where}: want header or before_algo")
            texts = [(Path(base or ".") / f).read_text() for f in paths]
            getattr(p, f"verilog_{where}").extend(texts)
        return p


# ---------------------------------------------------------------------------
# Verilog generation helpers
# ---------------------------------------------------------------------------

def _vw(width: int) -> str:
    """Return a Verilog width specifier string, empty for width==1."""
    return f"[{width - 1}:0] " if width > 1 else ""


def _port_decl(port: AbiPort) -> str:
    """Return a single Verilog port declaration line (no trailing comma)."""
    direction = "input  wire" if port.direction == "input" else "output wire"
    w = _vw(port.width)
    return f"    {direction} {w}{port.name}"


def _assign(lhs: str, rhs: str, comment: str = "") -> str:
    c = f"  // {comment}" if comment else ""
    return f"assign {lhs} = {rhs};{c}"


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class PayloadWrapperGenerator:
    """Generates payload.v from a FrameworkImport + resolved I/O."""

    def __init__(
        self,
        framework:   FrameworkImport,
        resolved_io: Any,              # DetectorIOResolved (optional; None → dummy stub)
        algo_module: str = "arc_algo_top",
        policies:    Optional[ControlPolicies] = None,
    ) -> None:
        self.fw          = framework
        self.resolved_io = resolved_io
        self.algo_module = algo_module
        self.policies    = policies or ControlPolicies()
        self._ports_by_name = {port.name: port for port in self.fw.ports}

    def _lane_signal(self, port_name: str, lane: int) -> str:
        port = self._ports_by_name.get(port_name)
        if port is not None and port.width == 1:
            return port_name
        return f"{port_name}[{lane}]"

    def _algo_input_port(self, output_wiring: Dict[str, Any], index: int) -> str:
        """Derive the algo top-level input port name for a resolved detector input.

        A plugin can declare an explicit ``algo_port`` in detector_io.yml's
        ``output:`` block when its algo module's port name doesn't follow the
        generic derivation below. Without an override, "name[idx]" style
        logical target ports (e.g. "dt_inputs[0]") are sanitized to
        "name_idx"; anything else is passed through unchanged.
        """
        override = output_wiring.get("algo_port")
        if override:
            return override
        target = output_wiring.get("target_port", "")
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$", target)
        if match:
            return f"{match.group(1)}_{match.group(2)}"
        return target or f"det_in_{index}"

    def _algo_input_expr(self, ep: Endpoint) -> str:
        if ep.protocol and ep.protocol.startswith("csp"):
            return f"{{{ep.endpoint_id}_tdata, {ep.endpoint_id}_tlast, {ep.endpoint_id}_tfirst, {ep.endpoint_id}_tvalid}}"
        return f"{ep.endpoint_id}_tdata"

    def _algo_output_port(self, source_instance: str, source_port: str) -> str:
        """Derive the algo top-level output port name for a resolved trigger output.

        A plugin can declare an explicit alias in the policies YAML's
        ``port_aliases`` map (keyed "source_instance.source_port") when its
        algo module's port name doesn't follow the generic
        "<source_instance>_<source_port>" derivation below.
        """
        alias = self.policies.port_aliases.get(f"{source_instance}.{source_port}")
        if alias:
            return alias
        if source_instance and source_port:
            prefix = f"{source_instance}_"
            return source_port if source_port.startswith(prefix) else f"{source_instance}_{source_port}"
        return source_port

    def _algo_output_expr(self, ep: Endpoint) -> str:
        if ep.protocol and ep.protocol.startswith("csp"):
            return f"{ep.endpoint_id}_csp"
        return f"{ep.endpoint_id}_tdata"

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def generate(self, module_name: Optional[str] = None) -> str:
        """Return the full payload.v content as a string."""
        module_name = module_name or self.fw.module_name
        lines: List[str] = []

        lines += self._header(module_name)
        lines += self._port_list()
        lines += self._verilog(self.policies.verilog_header)
        lines += [""];  lines += self._algo_clk_section()
        lines += [""];  lines += self._buffinc_buffdec_section()
        lines += [""];  lines += self._play_data_section()
        lines += [""];  lines += self._readback_section()
        lines += [""];  lines += self._rx_lane_slicing_section()
        lines += [""];  lines += self._tx_lane_assignment_section()
        lines += self._verilog(self.policies.verilog_before_algo)
        lines += self._input_register_section()
        lines += self._drive_section()
        lines += [""];  lines += self._algo_instantiation()
        lines += [""];  lines += ["endmodule"]

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # Section generators
    # ------------------------------------------------------------------ #

    def _header(self, module_name: str) -> List[str]:
        return [
            "// ==========================================================================",
            f"// {module_name}.v — Blobfish-compatible payload wrapper",
            "// GENERATED by FORGE — do not edit manually.",
            f"// Provider:   {self.fw.provider}",
            f"// Board:      {self.fw.board}",
            f"// Project:    {self.fw.project}",
            "// ABI source: payload_abi.json",
            "// ==========================================================================",
            "",
            f"module {module_name} (",
        ]

    def _port_list(self) -> List[str]:
        decls = [_port_decl(p) for p in self.fw.ports]
        lines = []
        for i, decl in enumerate(decls):
            sep = "," if i < len(decls) - 1 else ""
            lines.append(decl + sep)
        lines.append(");")
        return lines

    def _algo_clk_section(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// Algo clocks",
            "// --------------------------------------------------------------------------",
        ]
        for port in self.fw.ports:
            if port.kind == "gt_algo_clk":
                lines.append(_assign(port.name, self.policies.algo_clk,
                                     f"SLR{port.slr()} gt{port.gt_site()}"))
        return lines

    def _buffinc_buffdec_section(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// Buffer increment/decrement",
            "// --------------------------------------------------------------------------",
        ]
        policy = self.policies.buffer_alignment
        for port in self.fw.ports:
            if port.kind in ("gt_buffer_increment", "gt_buffer_decrement"):
                if policy == "disabled_zero":
                    rhs = f"{port.width}'b0" if port.width > 1 else "1'b0"
                    lines.append(_assign(port.name, rhs, "link aligner disabled"))
                # future: else drive from link_aligner instance
        return lines

    def _play_data_section(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// Play data (FIFO mode quads)",
            "// --------------------------------------------------------------------------",
        ]
        p = self.policies
        for port in self.fw.ports:
            if port.kind == "gt_play_data" and port.name not in p.drive:
                if p.play_data_source == "disabled_zero":
                    lines.append(_assign(port.name, "1'b0", "play_data disabled"))
                elif p.play_data_source == "slr_config_bit":
                    slr_idx = p.play_data_slr if p.play_data_slr is not None else port.slr()
                    cfg_port = f"slr{slr_idx}_config_registers_in"
                    bit_sel  = f"{cfg_port}[{p.play_data_bit}]"
                    if p.play_data_gate:
                        rhs = f"({bit_sel} & {p.play_data_gate})"
                    else:
                        rhs = bit_sel
                    lines.append(_assign(port.name, rhs,
                                         f"SLR{slr_idx} config bit {p.play_data_bit}"))
        return lines

    def _readback_section(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// Readback registers",
            "// --------------------------------------------------------------------------",
        ]
        for port in self.fw.ports:
            if port.kind == "slr_readback_registers" and port.name not in self.policies.drive:
                rhs = f"{port.width}'b0"
                lines.append(_assign(port.name, rhs, "diagnostics not implemented"))
        return lines

    def _rx_lane_slicing_section(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// RX lane slicing: endpoint wire declarations + assigns",
            "// --------------------------------------------------------------------------",
        ]
        if self.resolved_io is None:
            lines.append("// (no resolved I/O — dummy stub only)")
            return lines

        for ri in self.resolved_io.inputs:
            ep      = ri.endpoint
            prefix  = ep.abi_port_prefix or f"slr{ep.slr}_gt_{ep.gt_site}"
            lane    = ep.lane
            ep_name = ep.endpoint_id
            lw      = ep.lane_width

            lines.append(f"// {ep_name}  ->  {ri.name}")
            lines.append(f"wire [{lw-1}:0] {ep_name}_tdata;")
            lines.append(f"wire           {ep_name}_tvalid;")
            lines.append(f"wire           {ep_name}_tfirst;")
            lines.append(f"wire           {ep_name}_tlast;")
            mask = self.policies.rx_gate.get(ep_name)
            tdata = f"{prefix}_rx_tdata[{lw}*{lane} +: {lw}]"
            lines.append(_assign(f"{ep_name}_tdata",
                                 f"{{{lw}{{{mask}}}}} & {tdata}" if mask else tdata))
            for sig in ("tvalid", "tfirst", "tlast"):
                rhs = self._lane_signal(f"{prefix}_rx_{sig}", lane)
                lines.append(_assign(f"{ep_name}_{sig}", f"{mask} & {rhs}" if mask else rhs))
            lines.append("")

        return lines

    def _tx_lane_assignment_section(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// TX lane assignments",
            "// --------------------------------------------------------------------------",
        ]

        # Collect all active TX endpoints
        active_tx: Dict[tuple, str] = {}  # (slr, gt_site, lane) → endpoint_id
        if self.resolved_io is not None:
            for ro in self.resolved_io.outputs:
                ep = ro.endpoint
                active_tx[(ep.slr, ep.gt_site, ep.lane)] = ep.endpoint_id

        orbit = self.policies.tx_orbit
        if orbit and active_tx:
            lines.append(f"wire {self._TX_ORBIT};  // algo_top {orbit}")
            lines.append("")

        # For every TX port group, emit assignments
        for port in self.fw.ports:
            if port.kind != "gt_tx_tdata":
                continue
            slr  = port.slr()
            site = port.gt_site()
            n    = port.lanes() or 0
            pfx  = port.name.replace("_tx_tdata", "")
            lw   = port.lane_width() or 64

            for lane in range(n):
                ep_id = active_tx.get((slr, site, lane))
                if ep_id:
                    word = f"{ep_id}_csp"
                    lines.append(f"// TX lane {lane} <- {ep_id}")
                    lines.append(f"wire [66:0] {ep_id}_csp;")
                    if self.policies.tx_register:
                        lines += self._register(word, 67)
                        word = f"{word}_q"
                    lines.append(f"wire [{lw-1}:0] {ep_id}_tdata;")
                    lines.append(f"wire           {ep_id}_tvalid;")
                    lines.append(f"wire           {ep_id}_tfirst;")
                    lines.append(f"wire           {ep_id}_tlast;")
                    lines.append(_assign(f"{ep_id}_tdata",  f"{word}[66:3]"))
                    lines.append(_assign(f"{ep_id}_tvalid", f"{word}[0]"))
                    lines.append(_assign(f"{ep_id}_tfirst", f"{word}[1]"))
                    lines.append(_assign(f"{ep_id}_tlast",  f"{word}[2]"))
                    lines.append(_assign(f"{pfx}_tx_tdata[{lw}*{lane} +: {lw}]",
                                         f"{ep_id}_tdata"))
                    lines.append(_assign(self._lane_signal(f"{pfx}_tx_tvalid", lane), f"{ep_id}_tvalid"))
                    lines.append(_assign(self._lane_signal(f"{pfx}_tx_tfirst", lane), f"{ep_id}_tfirst"))
                    lines.append(_assign(self._lane_signal(f"{pfx}_tx_tlast", lane),  f"{ep_id}_tlast"))
                    if orbit:
                        if f"{pfx}_tx_orbit" not in self._ports_by_name:
                            raise ValueError(f"tx.orbit: the ABI has no {pfx}_tx_orbit port")
                        marker = self._TX_ORBIT
                        if self.policies.tx_register:
                            lines += self._register(marker, 1, f"{ep_id}_orbit_q")
                            marker = f"{ep_id}_orbit_q"
                        lines.append(_assign(self._lane_signal(f"{pfx}_tx_orbit", lane), marker))
                else:
                    lines.append(f"// TX lane {lane} (SLR{slr} gt{site}) unused")
                    ties = [(f"{pfx}_tx_tdata[{lw}*{lane} +: {lw}]", f"{lw}'b0")] + [
                        (self._lane_signal(f"{pfx}_tx_{sig}", lane), "1'b0")
                        for sig in ("tvalid", "tfirst", "tlast")
                    ]
                    lines += [_assign(lhs, rhs) for lhs, rhs in ties if lhs not in self.policies.drive]
                lines.append("")

        return lines

    _TX_ORBIT = "algo_tx_orbit"

    @staticmethod
    def _register(src: str, width: int, name: Optional[str] = None) -> List[str]:
        q = name or f"{src}_q"
        decl = f"[{width - 1}:0] " if width > 1 else ""
        return [
            f'(* DONT_TOUCH = "true" *) reg {decl}{q} = {width}\'b0;',
            f"always @(posedge logic_clk) {q} <= {src};",
        ]

    def _verilog(self, blocks: List[str]) -> List[str]:
        lines: List[str] = []
        for block in blocks:
            lines += block.rstrip("\n").split("\n") + [""]
        return lines

    def _link_inputs(self) -> List[tuple]:
        """(algo port, payload expression, width) of every resolved input."""
        if self.resolved_io is None:
            return []
        out = []
        for i, ri in enumerate(self.resolved_io.inputs):
            ep = ri.endpoint
            csp = bool(ep.protocol and ep.protocol.startswith("csp"))
            out.append((self._algo_input_port(ri.output_wiring, i), self._algo_input_expr(ep),
                        ep.lane_width + 3 if csp else ep.lane_width))
        return out

    def _connections(self) -> List[tuple]:
        """(algo port, payload net, register width or None) of the connect policy."""
        out = []
        for port, spec in self.policies.connect.items():
            if isinstance(spec, dict):
                width = int(spec.get("width", 1)) if spec.get("register") else None
                out.append((port, str(spec["net"]), width))
            else:
                out.append((port, str(spec), None))
        return out

    def _input_register_section(self) -> List[str]:
        regs = [(port, expr, w) for port, expr, w in self._link_inputs()] if self.policies.input_register else []
        regs += [(port, net, w) for port, net, w in self._connections() if w is not None]
        if not regs:
            return []
        lines = [
            "// --------------------------------------------------------------------------",
            "// Algo input registers",
            "// --------------------------------------------------------------------------",
        ]
        for port, expr, width in regs:
            q = f"lin_q_{port}"
            lines.append(f'(* shreg_extract = "no" *) reg {_vw(width)}{q} = {width}\'b0;')
            lines.append(f"always @(posedge logic_clk) {q} <= {expr};")
        return lines + [""]

    def _drive_section(self) -> List[str]:
        if not self.policies.drive:
            return []
        lines = [
            "// --------------------------------------------------------------------------",
            "// Payload outputs driven by the control policies",
            "// --------------------------------------------------------------------------",
        ]
        for lhs, rhs in self.policies.drive.items():
            if isinstance(rhs, dict):
                lines.append(_assign(lhs, str(rhs["expr"]), str(rhs.get("comment", ""))))
            else:
                lines.append(_assign(lhs, str(rhs)))
        return lines + [""]

    def _algo_instantiation(self) -> List[str]:
        lines = [
            "// --------------------------------------------------------------------------",
            "// Algorithm top instantiation",
            "// --------------------------------------------------------------------------",
            f"{self.algo_module} u_algo_top (",
        ]
        conns = ["    .ap_clk  (logic_clk)", "    .ap_rst  (1'b0)"]
        if self.resolved_io is not None and self.resolved_io.inputs:
            for port, expr, _ in self._link_inputs():
                conns.append(f"    .{port} ({f'lin_q_{port}' if self.policies.input_register else expr})")
            if self.policies.tx_orbit and self.resolved_io.outputs:
                conns.append(f"    .{self.policies.tx_orbit} ({self._TX_ORBIT})")
            for ro in self.resolved_io.outputs:
                port = self._algo_output_port(ro.source_instance, ro.source_port)
                conns.append(f"    .{port} ({self._algo_output_expr(ro.endpoint)})")
        for port, net, width in self._connections():
            conns.append(f"    .{port} ({f'lin_q_{port}' if width is not None else net})")
        lines.append(",\n".join(conns))
        lines.append(");")
        return lines


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def generate_payload_verilog(
    framework:     FrameworkImport,
    resolved_io:   Any,
    algo_module:   str = "arc_algo_top",
    policies:      Optional[ControlPolicies] = None,
    output_path:   Optional[Path] = None,
    module_name:   Optional[str]  = None,
) -> str:
    """Generate payload.v content and optionally write it to a file."""
    gen = PayloadWrapperGenerator(
        framework   = framework,
        resolved_io = resolved_io,
        algo_module = algo_module,
        policies    = policies,
    )
    content = gen.generate(module_name=module_name)
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(content)
    return content
