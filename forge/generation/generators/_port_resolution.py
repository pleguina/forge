"""Shared top-level-port naming and clock/reset-collapse rules.

Every generator (structural Verilog, Block Design Tcl, and eventually VHDL)
must agree on exactly which instance pins get lifted to the top level and
what they're named — a producer with a two-compatible-consumer choice here
is the same class of ambiguity ``forge.ir.model.ResolvedTopLevelPort``'s own
docstring warns about wanting exactly one implementation of. This module is
that one implementation. A generator keeps its own logic for *how* a driven
connection is wired (a Verilog net name vs. a Tcl ``connect_bd_net``); this
module only decides *whether* a pin is a top-level port, and if so what it's
called — plus, for a design with no CDC/register-stage/delay-cycle/
reset-sync features, whether an unconnected pin should be tied off or left
open.

Deliberately excluded: CDC synchronizers, ``register_stages``,
``delay_cycles``, and ``reset_domains.*.sync: reset_sync``. Those pick
*which intermediate net* a driven connection routes through, not whether a
pin is driven/tied/open at all — that stays generator-specific. BD mode
doesn't implement any of that today (see ``block_design.py``), so
``classify_connections`` below is only correct for designs that don't use
those features; callers must guard for that themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from ...contracts.config import DesignConfig, Module
from forge.core.utils.signal_names import is_clock_name, is_reset_name
from forge.core.utils.hdl_parser import _scan_ports as scan_vhdl_ports
from forge.core.utils.hdl_parser import _scan_verilog_ports as scan_vlog_ports

_re_trailing_num = re.compile(r"^(.*?)(?:_(\d+)|(\d+))$")


def _port_exists(ip_info: Dict[str, Dict], mod: str, pin: str) -> bool:
    try:
        return any(p["name"] == pin for p in ip_info[mod]["ports"])
    except KeyError:
        return False


def _canon_pin(ip_info: Dict[str, Dict], mod: str, pin: str) -> str:
    """Map e.g. cfg_vec15 → cfg_vec if only the base pin exists."""
    if _port_exists(ip_info, mod, pin):
        return pin
    m = _re_trailing_num.match(pin)
    if not m:
        return pin
    base = m.group(1)
    if _port_exists(ip_info, mod, base):
        return base
    cand = f"{base}_{m.group(2) or m.group(3)}"
    return cand if _port_exists(ip_info, mod, cand) else pin


def _inst(mod: Module, idx: int) -> str:
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"


#: The clock/reset names collapsed onto the single top-level ``ap_clk``/
#: ``ap_rst`` net. A name *outside* this that the design declared as its own
#: domain (``clk_b``) must keep its own net — that distinction is
#: load-bearing for CDC, handled entirely by the caller (not here).
def _is_standard_clock(name: str) -> bool:
    return (
        name in ("clk", "clock", "ap_clk")
        or name.endswith("_clk") or name.endswith("_ap_clk")
    )


def _is_standard_reset(name: str) -> bool:
    return (
        name in ("rst", "reset", "ap_rst", "rst_n")
        or name.endswith("_rst") or name.endswith("_ap_rst")
        or name.endswith("_rst_n")
    )


def _collapses_to_global(name: str, *, is_clock: bool, own_nets) -> bool:
    """Whether this clock/reset pin is driven by the single global net.

    Three cases:
    1. A **standard** name (``clk``, ``ap_clk``, ``*_clk``) always collapses
       onto ``ap_clk``/``ap_rst``, even when the design also lists it as a
       domain — that is what ``connect_clock``/``connect_reset`` mean.
    2. A **non-standard** name the design declared as its own domain
       (``clk_b``) keeps its own top-level net.
    3. A name that reads as a clock or reset by the shared convention but is
       **neither** standard nor a declared domain (``pclk``, ``presetn``,
       ``s_axi_aclk``) collapses onto the global net.
    """
    standard = _is_standard_clock(name) if is_clock else _is_standard_reset(name)
    if standard:
        return True
    reads_as = is_clock_name(name) if is_clock else is_reset_name(name)
    return reads_as and name not in (own_nets or ())


def _domain_to_top_level_net(
    domain_name: str, *, is_clock: bool, own_nets: "Set[str] | Tuple[str, ...]" = (),
) -> str:
    """Translate a resolved clock/reset domain identity (the raw port name
    — see ``forge.contracts.domains.resolve_domain_nets``) into the actual
    top-level net a generator wires it to.

    Both generators' own clock/reset auto-map (``_auto_mapped_global``
    above) collapses every *standard*-name variant onto the single literal
    ``ap_clk``/``ap_rst`` top-level port, regardless of which specific
    variant string a module's contract declared. A domain with its own
    declared global net (``clk_b``, a named second clock domain) keeps that
    net instead — pass those names as *own_nets*, and they win over the
    collapse.

    Used by CDC synchronizer / reset-synchronizer emission to reference the
    net that actually exists in the generated output, not the raw domain
    identity string, so it must resolve exactly as the per-instance port
    mapping does.
    """
    if not _collapses_to_global(domain_name, is_clock=is_clock, own_nets=own_nets):
        return domain_name
    return "ap_clk" if is_clock else "ap_rst"


def _reset_net_for_domain(
    domain_name: "str | None",
    reset_sync_domains: Dict[str, Dict[str, Any]],
    own_nets: "Set[str] | Tuple[str, ...]" = (),
    *,
    ident_fn=lambda s: s,
) -> str:
    """Resolve a module's reset domain to the net that actually carries a
    real, synchronized reset.

    A ``reset_domains.<name>.sync: reset_sync`` domain's real reset signal
    is the ``cdc_reset_sync`` instance's own ``sync_rst_out`` — not the raw
    top-level ``<name>`` net, which is never driven by anything once a real
    synchronizer exists for it (see ``design_cdc.yml``'s own header for why
    that top-level port is intentionally left unconnected/dangling). Every
    consumer of a resolved reset domain — a CDC synchronizer's own
    src_rst/dst_rst, and a member instance's own reset pin — must resolve
    through this same rule, not ``_domain_to_top_level_net`` alone: using
    the raw domain net instead is a real, silent miscompile (a module reads
    a permanently-unasserted reset and its registers stay X forever).

    *ident_fn* legalizes the synchronized net's name for the calling
    generator's output language (e.g. a Verilog-safe identifier); it is
    only applied to the synthesized ``rst_sync_<name>`` name, never to a
    domain name returned unchanged.
    """
    if domain_name and domain_name in reset_sync_domains:
        return ident_fn(f"rst_sync_{domain_name}")
    if not domain_name:
        return "ap_rst"
    return _domain_to_top_level_net(domain_name, is_clock=False, own_nets=own_nets)


def _normalize_ports(raw_ports: List[Dict]) -> List[Dict]:
    """Return [{name, dir, width}] with dir∈{in,out}, width:int."""
    out = []
    for p in raw_ports or []:
        name = p["name"]
        width = int(p.get("width", 1))
        dirn = p.get("dir")
        if not dirn and "direction" in p:
            dirn = p["direction"].lower()  # IN/OUT → in/out
        dirn = dirn or "in"
        if dirn == "in":
            dirn = "input"
        elif dirn == "out":
            dirn = "output"
        out.append({"name": name, "dir": dirn, "width": width})
    return out


def _fetch_module_ports(mod: Module, meta: Dict, ip_root: Path) -> List[Dict]:
    """Return [{name,dir,width}], prefer ip_info. If missing, scan RTL under ip_root/<mod>/…"""
    ports = meta.get("ports", [])
    ports = _normalize_ports(ports)
    if ports and all(("name" in p and "dir" in p and "width" in p) for p in ports):
        return ports

    mod_root = ip_root / mod.name
    scanned: Dict[str, Tuple[str, int]] = {}
    for ext in (".vhd", ".v", ".sv"):
        for p in sorted(mod_root.rglob(f"*{ext}")):
            try:
                scanned = scan_vhdl_ports(p) if ext == ".vhd" else scan_vlog_ports(p)
                if scanned:
                    break
            except Exception:
                continue
        if scanned:
            break

    if scanned:
        result = []
        for n, (d, w) in scanned.items():
            if d == "in":
                d = "input"
            elif d == "out":
                d = "output"
            result.append({"name": n, "dir": d, "width": int(w)})
        return result

    return ports  # whatever we had, normalized


def _parse_system_aliases(
    cfg: DesignConfig, system_yml: Optional[Path],
) -> Tuple[Dict[Tuple[str, str], str], Dict[Tuple[str, str], str]]:
    """
    Returns:
      alias_in [(inst_label, algo_input_pin)]  -> top_port_name (framework RX/CFG)
      alias_out[(inst_label, algo_output_pin)] -> top_port_name (framework TX)
    """
    alias_in: Dict[Tuple[str, str], str] = {}
    alias_out: Dict[Tuple[str, str], str] = {}
    if not system_yml:
        return alias_in, alias_out

    sys_cfg = yaml.safe_load(system_yml.read_text()) or {}
    name_to_mod = {m.name: m for m in cfg.modules}

    def _inst_label(mod_name: str, idx: Optional[int]) -> str:
        m = name_to_mod.get(mod_name)
        n = int(idx or 0)
        return mod_name if (m and m.instances == 1) else f"{mod_name}_{n}"

    def _is_fw(x: Optional[str]) -> bool:
        return str(x).lower() in ("framework", "links")

    for conn in sys_cfg.get("connections", []):
        src = conn.get("from")
        dst = conn.get("to")
        sidx = conn.get("from_instance")
        didx = conn.get("to_instance")
        for a, b in conn.get("port_map", []):
            if _is_fw(src):
                ilab = _inst_label(dst, didx)
                alias_in[(ilab, b)] = a
            elif _is_fw(dst):
                ilab = _inst_label(src, sidx)
                alias_out[(ilab, a)] = b
    return alias_in, alias_out


def _is_user_external(mod: Module, pname: str, pdir: str) -> Optional[str]:
    """
    Check user-declared external_in_ports / external_out_ports on a *name or
    prefix* basis — cross-checked against *pname*'s own real direction, so
    an unrelated, oppositely-directioned port whose name happens to share a
    prefix with a declared external can never match (e.g. a real output
    port ``x_out`` must never match a declared external *input* ``x``, even
    though ``"x_out".startswith("x_")`` is true).
    Returns 'input' / 'output' / None.
    """
    user_in = tuple(mod.external_in_ports or [])
    user_out = tuple(mod.external_out_ports or [])
    if pdir == "input" and any(pname == x or pname.startswith(x + "_") for x in user_in):
        return "input"
    if pdir == "output" and any(pname == x or pname.startswith(x + "_") for x in user_out):
        return "output"
    return None


def _auto_mapped_global(
    mod: Module, pname: str, pdir: str, *, cfg: DesignConfig, global_nets: Dict,
) -> Optional[str]:
    """Whether this port is swallowed by the global clock/reset fan-out.

    An explicit ``external_in_ports`` entry is the user saying "this pin is
    driven from above, not from FORGE's global net", and it wins — an
    explicit declaration always beats an implicit name convention.
    """
    if _is_user_external(mod, pname, pdir):
        return None
    if cfg.connect_clock and _collapses_to_global(pname, is_clock=True, own_nets=global_nets):
        return "clock"
    if cfg.connect_reset and _collapses_to_global(pname, is_clock=False, own_nets=global_nets):
        return "reset"
    return None


@dataclass
class TopPortResolution:
    #: Ordered, deduplicated top-level port list: each entry has ``name``,
    #: ``direction`` (``"in"``/``"out"``), ``width``, and ``origin`` (one of
    #: ``clock``/``reset``/``control_signal``/``global_net``/``external``/
    #: ``debug`` — the latter two also carry ``instance``/``port``).
    top_ports: List[Dict[str, Any]]
    alias_in: Dict[Tuple[str, str], str]
    alias_out: Dict[Tuple[str, str], str]
    ports_by_mod: Dict[str, List[Dict]]
    inst_to_mod: Dict[str, str]
    #: (instance, port) -> top-level name, for output pins lifted to the top
    #: level (external or system.yml-aliased) — a generator uses this to
    #: know an output is *not* also tied/left open, it's routed to a name.
    external_output_bindings: Dict[Tuple[str, str], str]


def resolve_top_ports(
    cfg: DesignConfig,
    ip_info: Dict[str, Dict],
    ip_root: Path,
    global_nets: Dict[str, List[Tuple[str, str]]],
    *,
    system_yml: Optional[Path] = None,
) -> TopPortResolution:
    """Decide which instance pins become top-level ports, and name them.

    This is ``write_structural_verilog``'s original top-level-port-lifting
    pass (clock/reset, control signals, other global nets, user/alias
    externals, debug ports), extracted so ``write_bd_tcl`` can produce the
    exact same top-level port set/names without Vivado's own
    ``make_bd_pins_external`` auto-naming and without a second,
    independently-drifting reimplementation of this logic.
    """
    alias_in, alias_out = _parse_system_aliases(cfg, system_yml)

    ports_by_mod: Dict[str, List[Dict]] = {}
    for mod in cfg.modules:
        ports_by_mod[mod.name] = _fetch_module_ports(mod, ip_info[mod.name], ip_root)

    inst_to_mod: Dict[str, str] = {}
    for mod in cfg.modules:
        for i in range(mod.instances):
            inst_to_mod[_inst(mod, i)] = mod.name

    external_output_bindings: Dict[Tuple[str, str], str] = {}
    declared_names: Set[str] = set()
    top_ports: List[Dict[str, Any]] = []

    if cfg.connect_clock:
        declared_names.add("ap_clk")
        top_ports.append({"name": "ap_clk", "direction": "in", "width": 1, "origin": "clock"})
    if cfg.connect_reset:
        declared_names.add("ap_rst")
        top_ports.append({"name": "ap_rst", "direction": "in", "width": 1, "origin": "reset"})

    control_signal_sources: Dict[str, str] = {}
    for mod in cfg.modules:
        for p in ports_by_mod[mod.name]:
            if p["dir"] == "output" and p["name"] in cfg.control_signals:
                control_signal_sources[p["name"]] = mod.name

    for sig_name, sig_config in cfg.control_signals.items():
        if sig_name in control_signal_sources:
            continue
        if sig_name not in declared_names:
            declared_names.add(sig_name)
            top_ports.append({
                "name": sig_name, "direction": "in", "width": sig_config.width,
                "origin": "control_signal",
            })

    for gnet_name, binds in global_nets.items():
        if gnet_name in ("ap_clk", "clk", "clock", "ap_rst", "rst", "reset", "rst_n"):
            continue
        if gnet_name not in declared_names and binds:
            declared_names.add(gnet_name)
            top_ports.append({
                "name": gnet_name, "direction": "in", "width": 1, "origin": "global_net",
            })

    for mod in cfg.modules:
        for p in ports_by_mod[mod.name]:
            pname, pdir, w = p["name"], p["dir"], int(p["width"])

            if _auto_mapped_global(mod, pname, pdir, cfg=cfg, global_nets=global_nets):
                continue
            if pname in global_nets:
                continue

            for i in range(mod.instances):
                ilabel = _inst(mod, i)

                ext_dir = None
                top_port_name = None
                if (ilabel, pname) in alias_in:
                    ext_dir = "input"
                    top_port_name = alias_in[(ilabel, pname)]
                elif (ilabel, pname) in alias_out:
                    ext_dir = "output"
                    top_port_name = alias_out[(ilabel, pname)]
                else:
                    ext_dir = _is_user_external(mod, pname, pdir)
                    if ext_dir:
                        top_port_name = f"{ilabel}_{pname}"

                if not ext_dir:
                    continue

                if ext_dir == "output":
                    external_output_bindings[(ilabel, pname)] = top_port_name

                if top_port_name in declared_names:
                    continue
                declared_names.add(top_port_name)
                top_ports.append({
                    "name": top_port_name,
                    "direction": "in" if ext_dir == "input" else "out",
                    "width": w,
                    "origin": "external",
                    "instance": ilabel,
                    "port": pname,
                })

    for mod in cfg.modules:
        if not mod.debug:
            continue
        for p in ports_by_mod[mod.name]:
            pname, pdir, w = p["name"], p["dir"], int(p["width"])
            if cfg.connect_clock and is_clock_name(pname):
                continue
            if cfg.connect_reset and is_reset_name(pname):
                continue
            for i in range(mod.instances):
                ilabel = _inst(mod, i)
                debug_port_name = f"debug_{ilabel}_{pname}"
                if debug_port_name in declared_names:
                    continue
                declared_names.add(debug_port_name)
                top_ports.append({
                    "name": debug_port_name, "direction": "out", "width": w,
                    "origin": "debug", "instance": ilabel, "port": pname,
                })

    return TopPortResolution(
        top_ports=top_ports,
        alias_in=alias_in,
        alias_out=alias_out,
        ports_by_mod=ports_by_mod,
        inst_to_mod=inst_to_mod,
        external_output_bindings=external_output_bindings,
    )


def classify_connections(
    cfg: DesignConfig,
    ip_info: Dict[str, Dict],
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],
    global_nets: Dict[str, List[Tuple[str, str]]],
    resolution: TopPortResolution,
) -> Tuple[List[Tuple[str, str, int]], List[Tuple[str, str, int]]]:
    """Classify every instance pin not already routed to a top-level/global
    net as driven, tied-to-zero (input with no driver), or open (output
    with no sink) — mirrors ``write_structural_verilog``'s per-instance
    exclusion order (global clock/reset/other-global net, then system.yml
    alias, then user-declared external, then driven-vs-not), minus the
    CDC-synchronizer/register-stage/delay-cycle/reset-sync-domain special
    cases, which pick *which net* a driven connection resolves to rather
    than whether it's driven at all, and which BD mode doesn't implement
    (callers must reject those designs themselves — ``write_bd_tcl`` does).

    Returns ``(open_outputs, tied_to_zero)``, both
    ``[(instance, port, width)]`` — the same shape
    ``write_structural_verilog``'s report uses.
    """
    driven_outs: Set[Tuple[str, str]] = set()
    incoming: Dict[Tuple[str, str], str] = {}
    for (src_i, dst_i), pairs in conn_map.items():
        src_mod = resolution.inst_to_mod[src_i]
        dst_mod = resolution.inst_to_mod[dst_i]
        for s_pin_raw, d_pin_raw in pairs:
            s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
            d_pin = _canon_pin(ip_info, dst_mod, d_pin_raw)
            driven_outs.add((src_i, s_pin))
            incoming.setdefault((dst_i, d_pin), src_i)  # first-driver-wins, ensured upstream

    open_outputs: List[Tuple[str, str, int]] = []
    tied_to_zero: List[Tuple[str, str, int]] = []

    for mod in cfg.modules:
        for i in range(mod.instances):
            ilabel = _inst(mod, i)
            for p in resolution.ports_by_mod[mod.name]:
                pname, pdir, w = p["name"], p["dir"], int(p["width"])

                # Already routed: global clock/reset/other-global net.
                if _auto_mapped_global(mod, pname, pdir, cfg=cfg, global_nets=global_nets):
                    continue
                if pname in global_nets:
                    continue
                # Already routed: system.yml alias.
                if (ilabel, pname) in resolution.alias_in or (ilabel, pname) in resolution.alias_out:
                    continue
                # Already routed: user-declared external.
                if _is_user_external(mod, pname, pdir):
                    continue

                if pdir == "output":
                    if (ilabel, pname) not in driven_outs:
                        open_outputs.append((ilabel, pname, w))
                else:
                    if (ilabel, pname) not in incoming:
                        tied_to_zero.append((ilabel, pname, w))

    return open_outputs, tied_to_zero
