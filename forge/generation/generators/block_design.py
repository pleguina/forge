from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from ...contracts.config import DesignConfig, Module, resolve_declared_path
from ...contracts.domains import resolve_domain_nets
from ._port_resolution import (
    _boundary_inst_name,
    _domain_to_top_level_net,
    classify_connections,
    resolve_top_ports,
    write_crossing_manifest,
)
from ..support_rtl import resolve_support_rtl

NL = "\n"


# ───────────────────────── helpers ──────────────────────────

def _inst(mod: Module, idx: int) -> str:
    """Instance label inside the BD."""
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"


_re_num = re.compile(r"^(\w+?)_(\d+)$")


def _pins_are_numeric(pins: List[str]) -> Tuple[bool, str]:
    """Return (True, basename) if all pins share <base>_<num> form."""
    bases = set()
    for p in pins:
        m = _re_num.match(p)
        if not m:
            return False, ""
        bases.add(m.group(1))
    return len(bases) == 1, bases.pop() if bases else ""


# ───────────────────────── helpers ──────────────────────────
def _for_loop(var: str, count: int, body: List[str]) -> str:
    """Return a Tcl for-loop around BODY lines."""
    header = f"for {{set {var} 0}} {{${var} < {count}}} {{incr {var}}} {{\n"
    inner  = "\n".join(f"    {ln}" for ln in body)
    return header + inner + "\n}"

def _port_exists(ip_info: Dict[str, Dict], mod: str, pin: str) -> bool:
    try:
        return any(p["name"] == pin for p in ip_info[mod]["ports"])
    except KeyError:
        return False

_re_trailing_num = re.compile(r"^(.*?)(?:_(\d+)|(\d+))$")  # matches foo_3 or foo3

def _canon_pin(ip_info: Dict[str, Dict], mod: str, pin: str) -> str:
    if _port_exists(ip_info, mod, pin):
        return pin
    m = _re_trailing_num.match(pin)
    if not m:
        return pin
    base = m.group(1)
    # Prefer exact scalar base if present
    if _port_exists(ip_info, mod, base):
        return base
    # Or try underscore form explicitly
    cand = f"{base}_{m.group(2) or m.group(3)}"
    if _port_exists(ip_info, mod, cand):
        return cand
    return pin

def _is_hdl(meta: Dict) -> bool:
    """Detect HDL modules even if 'kind' is missing in ip_info."""
    if str(meta.get("kind", "")).lower() == "hdl":
        return True
    if str(meta.get("library", "")).lower() == "hdl":
        return True
    if str(meta.get("version", "")).lower() in ("rtl", "hdl"):
        return True
    # If an explicit entity name exists (typical for HDL) and no obvious VLNV usage
    if meta.get("entity") and not meta.get("vendor"):
        return True
    return False

def _resolve_paths(paths: List[str] | None, src_root: Path) -> List[Path]:
    out: List[Path] = []
    for s in (paths or []):
        p = resolve_declared_path(s, src_root)
        if p.exists():
            out.append(p)
    return out

def _guess_lang(path: Path) -> str:
    ext = path.suffix.lower()
    return "vhdl" if ext == ".vhd" else "verilog"

def _config_dict(params: Dict) -> str:
    """Tcl -dict literal mapping design.yml parameters to CONFIG.* properties."""
    items = " ".join(f"CONFIG.{k} {{{v}}}" for k, v in params.items())
    return f"[list {items}]"

def _gather_hdl_sources_for_mod(mod: Module, meta: Dict, src_root: Path) -> List[tuple[str,str]]:
    """
    Returns [(file, lang)].
    Sources priority:
      1) ip_info['sources'] + ip_info['packages'] (if provided)
      2) mod.src + mod.<rtl_packages|vhdl_packages|include_packages> (from YAML)
    """
    out: List[tuple[str,str]] = []

    # 1) ip_info-provided lists
    ip_sources  = meta.get("sources")  or []
    ip_packages = meta.get("packages") or []
    for p in ip_sources + ip_packages:
        f = Path(p)
        # ip_info entries may already be absolute; keep as-is
        out.append((f.as_posix(), _guess_lang(f)))

    if out:
        return out

    # 2) YAML module: primary source(s)
    src_files = _resolve_paths(getattr(mod, "src", []) or [], src_root)

    #    YAML module: optional package files (support a few field names)
    pkg_fields = (
        "rtl_packages",       # preferred
        "vhdl_packages",      # alias
        "include_packages",   # alias
        "packages",           # alias
    )
    pkg_files: List[Path] = []
    for fld in pkg_fields:
        pkg_files += _resolve_paths(getattr(mod, fld, []) or [], src_root)

    # If src points to a dir, we still do the old heuristic (pick best match),
    # but we *also* append all declared package files.
    cand: List[Path] = []
    for p in src_files:
        if p.is_file():
            cand.append(p)
        elif p.is_dir():
            cand.extend(sorted(p.rglob("*.vhd")))
            cand.extend(sorted(p.rglob("*.v")))
            cand.extend(sorted(p.rglob("*.sv")))

    want = meta.get("entity") or mod.top or mod.name
    def score(f: Path) -> int:
        if f.stem == want: return 100
        if want.lower() in f.name.lower(): return 50
        return 1

    cand = [c for c in cand if c.suffix.lower() in (".vhd",".v",".sv")]
    cand.sort(key=score, reverse=True)

    if cand:
        f = cand[0]
        out.append((f.as_posix(), _guess_lang(f)))

    # Append declared package files (if any)
    for f in pkg_files:
        out.append((f.as_posix(), _guess_lang(f)))

    return out


def _create_port_cmd(direction: str, name: str, width: int) -> str:
    dir_flag = "I" if direction == "in" else "O"
    if width == 1:
        return f"create_bd_port -dir {dir_flag} {name}"
    return f"create_bd_port -dir {dir_flag} -from {width - 1} -to 0 {name}"


# ───────────────────────── main writer ──────────────────────

def write_bd_tcl(
    cfg: DesignConfig,
    ip_info: Dict[str, Dict],
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],
    global_nets: Dict[str, List[Tuple[str, str]]],
    out_path: Path,
    *,
    bd_name: str = "top_bd",
    loop_threshold: int = 10,
    src_root: Optional[Path] = None,
    ip_root: Optional[Path] = None,
    system_yml: Optional[Path] = None,
    contracts: Optional[Dict[str, Any]] = None,
    match_report: Any = None,
) -> Dict[str, Any]:
    """Generate a Vivado Block Design Tcl script wiring algorithm modules
    together — the ``--mode bd`` counterpart to
    ``write_structural_verilog``.

    Returns a report dict with exactly the same shape
    ``write_structural_verilog`` returns (``open_outputs``, ``tied_to_zero``,
    ``total_modules``, ``total_instances``, ``total_connections``,
    ``top_ports``), so ``forge topgen gen-top``'s reporting/manifest
    pipeline works unmodified against either mode.

    Plain ``register_stages``/``delay_cycles`` (no ``boundary:`` tag) are
    supported: a real ``RegisterStage``/``signal_delay`` cell is
    instantiated between the source and destination pins, the same modules
    verilog mode uses.

    A ``boundary:`` tag is supported too: a real, protected
    ``slr_crossing_delay`` cell is instantiated instead (whichever of
    ``register_stages``/``delay_cycles`` is set becomes its ``DEPTH``,
    config.py's own load-time validation already guarantees one of them is
    positive), and a ``block_design.crossings.json`` manifest is written
    alongside the Tcl — the same file
    ``blobfish_build.slr_crossings.generate_xdc`` reads for a flat-Verilog
    build, with a bd-mode hierarchy prefix
    (``f"{bd_name}_i/{instance_name}/inst"`` — the extra ``/inst`` level is
    Vivado's own convention for a ``-type module -reference`` cell,
    confirmed against real Vivado 2024.1 synthesis). The consumer matches
    patterns with a wildcard-prefixed glob, so only this relative suffix
    needs to be right; where the payload lands in a real board build stays
    the board's own concern.

    A ``reset_domains.*.sync: reset_sync`` domain is supported too: a real
    ``cdc_reset_sync`` cell is instantiated per domain and its
    ``sync_rst_out`` fans out to each member instance's reset pin directly
    (no intermediate net declaration needed, unlike verilog mode — a BD
    connects pins straight to pins).

    So is a ``cdc:`` connection (``level_sync``/``2ff_sync``,
    ``pulse_sync``, ``mailbox_transfer``, ``async_fifo``): the matching
    real synchronizer/FIFO cell is instantiated, wired to each side's own
    resolved clock/reset (which may differ from ``ap_clk``/``ap_rst`` in a
    multi-domain design). A connection declaring both ``cdc:`` and
    ``register_stages``/``delay_cycles`` gets only the CDC cell — no real
    design combines them today, and BD mode makes the same choice verilog
    mode's conn_map-keyed maps would make implicitly.

    *contracts* and *match_report* are required whenever a reset_sync
    domain or a cdc: connection is present, so each domain/connection's
    member instances and their clock can be resolved
    (``forge.contracts.domains.resolve_domain_nets``, the same resolver
    verilog mode uses for this).
    """
    reset_sync_domains = {
        name: rel for name, rel in (cfg.reset_domains or {}).items()
        if rel.get("sync") == "reset_sync"
    }

    # register_stages/delay_cycles/boundary/cdc, expanded per-instance —
    # same construction write_structural_verilog uses.
    reg_stages_map: Dict[Tuple[str, str], int] = {}
    delay_cycles_map: Dict[Tuple[str, str], int] = {}
    boundary_map: Dict[Tuple[str, str], str] = {}
    cdc_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for conn in cfg.connections:
        src_mod_obj = next(m for m in cfg.modules if m.name == conn.from_)
        dst_mod_obj = next(m for m in cfg.modules if m.name == conn.to)
        for src_idx in range(src_mod_obj.instances):
            for dst_idx in range(dst_mod_obj.instances):
                src_inst = _inst(src_mod_obj, src_idx)
                dst_inst = _inst(dst_mod_obj, dst_idx)
                if conn.register_stages > 0:
                    reg_stages_map[(src_inst, dst_inst)] = conn.register_stages
                if conn.delay_cycles > 0:
                    delay_cycles_map[(src_inst, dst_inst)] = conn.delay_cycles
                if conn.boundary:
                    boundary_map[(src_inst, dst_inst)] = conn.boundary
                if conn.cdc:
                    # '2ff_sync' is a backwards-compatible alias for
                    # 'level_sync' — normalized here, once, same as verilog
                    # mode, so every dispatch site below only recognizes
                    # one spelling.
                    cdc = conn.cdc
                    if cdc.get("kind") == "2ff_sync":
                        cdc = {**cdc, "kind": "level_sync"}
                    cdc_map[(src_inst, dst_inst)] = cdc

    if (reset_sync_domains or cdc_map) and match_report is None:
        raise ValueError(
            "a reset_domains.*.sync: reset_sync entry or a cdc: connection "
            "is present but write_bd_tcl was not given a match_report — "
            "pass the MatchReport auto_match_ports returned so each "
            "domain/connection's member instances and their clock can be "
            "resolved (forge.contracts.domains.resolve_domain_nets)."
        )

    # Single implementation of "which pins become top-level ports and what
    # they're named" — shared with write_structural_verilog. See
    # forge.ir.model.ResolvedTopLevelPort's docstring.
    resolution = resolve_top_ports(
        cfg, ip_info, ip_root or Path("."), global_nets, system_yml=system_yml,
    )
    open_outputs, tied_to_zero = classify_connections(
        cfg, ip_info, conn_map, global_nets, resolution,
    )
    # cdc.write_enable_pin: an async_fifo connection may name one of the
    # *source* instance's own other output pins as its real write-enable —
    # never a port_map pair, so classify_connections (which deliberately
    # excludes CDC entirely, see its own docstring) has no way to know it's
    # driven and would otherwise misreport it as an open output.
    _we_pins = {
        (src_i, _canon_pin(ip_info, resolution.inst_to_mod[src_i], cdc["write_enable_pin"]))
        for (src_i, _dst_i), cdc in cdc_map.items() if cdc.get("write_enable_pin")
    }
    if _we_pins:
        open_outputs = [o for o in open_outputs if (o[0], o[1]) not in _we_pins]

    # Resolve each module's real clock/reset domain so a reset_sync
    # domain's cdc_reset_sync cell, and a cdc: connection's synchronizer,
    # can be clocked/reset by the real domain in play, not always assumed
    # to be ap_clk/ap_rst.
    clock_of_module: Dict[str, Optional[str]] = {}
    reset_of_module: Dict[str, Optional[str]] = {}
    if reset_sync_domains or cdc_map:
        clock_of_module, reset_of_module, _unresolved = resolve_domain_nets(
            cfg, contracts or {}, match_report, global_nets, resolution.inst_to_mod,
        )

     # Collect HDL sources for -type module references (pairs: file, lang)
    hdl_sources_flat: List[str] = []
    for mod in cfg.modules:
        meta = ip_info.get(mod.name, {}) or {}
        if _is_hdl(meta):
            for f, lang in _gather_hdl_sources_for_mod(mod, meta, src_root or Path(".")):
                hdl_sources_flat += [f, lang]

    # Framework support RTL for register_stages/delay_cycles/cdc — the
    # copies shipped in forge/rtl/support/ (same files verilog mode's
    # build_manifest.json compiles), unless a module of this design already
    # brings its own file of that name.
    _cdc_kinds_used = {cdc.get("kind") for cdc in cdc_map.values()}
    _CDC_RTL_BY_KIND = {
        "level_sync": "cdc_sync2ff.v",
        "pulse_sync": "cdc_pulse_sync.v",
        "mailbox_transfer": "cdc_mailbox.v",
        "async_fifo": "cdc_async_fifo.v",
    }
    # A boundary-tagged pair uses slr_crossing_delay instead of plain
    # RegisterStage/signal_delay — only a *non*-boundary reg_stages_map/
    # delay_cycles_map entry needs the plain module.
    _plain_reg_stages = any(k not in boundary_map for k in reg_stages_map)
    _plain_delays = any(k not in boundary_map for k in delay_cycles_map)
    _needed = (
        (["RegisterStage.v"] if _plain_reg_stages else [])
        + (["signal_delay.v"] if _plain_delays else [])
        + (["slr_crossing_delay.v"] if boundary_map else [])
        + (["cdc_reset_sync.v"] if reset_sync_domains else [])
        + [f for k, f in _CDC_RTL_BY_KIND.items() if k in _cdc_kinds_used]
    )
    for found in resolve_support_rtl(_needed, hdl_sources_flat[0::2]):
        hdl_sources_flat += [found.as_posix(), "verilog"]

    tcl: List[str] = [
        "# ------------------------------------------------------------",
        "#  Auto-generated by hls-auto – DO NOT EDIT BY HAND",
        "# ------------------------------------------------------------",
        f"set _bd_name {bd_name}",
        "",
        "# Add HDL sources needed for -type module references (idempotent).",
        f"set _hdl_sources {{{' '.join(hdl_sources_flat)}}}",
        "set _n [llength $_hdl_sources]",
        "for {set i 0} {$i < $_n} {incr i 2} {",
        "  set f    [lindex $_hdl_sources $i]",
        "  set lang [lindex $_hdl_sources [expr {$i+1}]]",
        "  if {![llength [get_files -quiet $f]]} {",
        "    add_files -norecurse $f",
        "    if {$lang eq \"vhdl\"} { set_property file_type VHDL [get_files $f] }",
        "  }",
        "}",
        "if {$i > 0} { update_compile_order -fileset sources_1 }",
        "",
        "# Prefer opening an existing BD file in the project.",
        'set _bd_file [lindex [get_files -quiet "*${_bd_name}.bd"] 0]',
        'if {[string length $_bd_file]} {',
        '  puts "INFO: Opening existing BD \'$_bd_name\' from file: $_bd_file"',
        '  open_bd_design $_bd_file',
        '  current_bd_design $_bd_name',
        '  puts "INFO: Clearing BD contents"',
        '  set _objs [concat [get_bd_intf_ports -quiet] [get_bd_ports -quiet] [get_bd_cells -quiet]]',
        '  if {[llength $_objs] > 0} { delete_bd_objs $_objs }',
        '} else {',
        '  # If a BD with this name is already open, reuse it; else create it.',
        '  set _existing_bd [get_bd_designs -quiet $_bd_name]',
        '  if {[llength $_existing_bd]} {',
        '    puts "INFO: Reusing open BD \'$_bd_name\' (clearing contents)"',
        '    current_bd_design $_bd_name',
        '    set _objs [concat [get_bd_intf_ports -quiet] [get_bd_ports -quiet] [get_bd_cells -quiet]]',
        '    if {[llength $_objs] > 0} { delete_bd_objs $_objs }',
        '  } else {',
        '    puts "INFO: Creating BD \'$_bd_name\'"',
        '    create_bd_design $_bd_name',
        '  }',
        '}',
        "",
    ]

    # ── I/O ports ────────────────────────────────────────────
    if cfg.connect_clock:
        tcl.append("create_bd_port -dir I -type clk ap_clk")
    if cfg.connect_reset:
        tcl += [
            "create_bd_port -dir I -type rst ap_rst",
            "set_property CONFIG.POLARITY ACTIVE_HIGH [get_bd_ports ap_rst]",
        ]
    if cfg.connect_clock or cfg.connect_reset:
        tcl.append("")

    # ── top-level ports for control signals / other global nets ─
    # (deterministic, from the shared resolver — clock/reset already
    # created above; these are the remaining single, non-per-instance
    # top-level ports the "global nets" wiring section below expects to
    # already exist via get_bd_ports.)
    _other_global_ports = [
        p for p in resolution.top_ports if p["origin"] in ("control_signal", "global_net")
    ]
    if _other_global_ports:
        tcl.append("# -- top-level ports: control signals / other global nets --")
        for p in _other_global_ports:
            tcl.append(_create_port_cmd(p["direction"], p["name"], p["width"]))
        tcl.append("")

    # ── IP / HDL instances ───────────────────────────────────
    tcl.append("# -- HLS IP / HDL module instantiation ----------------------")
    for mod in cfg.modules:
        meta = ip_info[mod.name]
        if _is_hdl(meta):
            # HDL module
            ref = meta.get("entity", mod.top)
            if mod.instances == 1:
                tcl.append(f"create_bd_cell -type module -reference {ref} {mod.name}")
                if mod.parameters:
                    tcl.append(
                        f"set_property -dict {_config_dict(mod.parameters)} "
                        f"[get_bd_cells {mod.name}]"
                    )
            else:
                body = [f"create_bd_cell -type module -reference {ref} {mod.name}_${{idx}}"]
                if mod.parameters:
                    body.append(
                        f"set_property -dict {_config_dict(mod.parameters)} "
                        f"[get_bd_cells {mod.name}_${{idx}}]"
                    )
                tcl.append(_for_loop("idx", mod.instances, body))
        else:
            # Packaged IP (VLNV)
            vlnv = "{vendor}:{lib}:{name}:{ver}".format(
                vendor=meta.get("vendor", "user"),
                lib=meta.get("library", "hls"),
                name=meta.get("name", mod.top),
                ver=meta.get("version", "1.0"),
            )
            if mod.instances == 1:
                tcl.append(f"create_bd_cell -type ip -vlnv {vlnv} {mod.name}")
                if mod.parameters:
                    tcl.append(
                        f"set_property -dict {_config_dict(mod.parameters)} "
                        f"[get_bd_cells {mod.name}]"
                    )
            else:
                body = [f"create_bd_cell -type ip -vlnv {vlnv} {mod.name}_${{idx}}"]
                if mod.parameters:
                    body.append(
                        f"set_property -dict {_config_dict(mod.parameters)} "
                        f"[get_bd_cells {mod.name}_${{idx}}]"
                    )
                tcl.append(_for_loop("idx", mod.instances, body))
    tcl.append("")

    # ── global clock / reset nets (star) ─────────────────────
    if global_nets:
        tcl.append("# -- global clock / reset nets ----------------------------")

        # Map alternative clock/reset names to standard ap_clk/ap_rst
        def _map_to_top_port(net_name: str) -> str:
            """Map module pin names to the top-level port name."""
            if net_name in ("clk", "clock"):
                return "ap_clk"
            if net_name in ("rst", "reset", "rst_n"):
                return "ap_rst"
            return net_name

        for net, binds in global_nets.items():
            if net in reset_sync_domains:
                # This domain's member instances are reset from a real
                # cdc_reset_sync cell's sync_rst_out below instead of the
                # raw domain net — the top-level port for `net` still gets
                # created (origin "global_net", above), intentionally
                # left unconnected: nothing drives it any more once a real
                # synchronizer exists for the domain.
                continue
            top_port = _map_to_top_port(net)
            groups: Dict[str, List[str]] = {}
            for inst, _ in binds:
                m = _re_num.match(inst)
                key = m.group(1) if m else inst
                groups.setdefault(key, []).append(inst)

            for base, insts in groups.items():
                numeric, _ = _pins_are_numeric(insts)
                if numeric:
                    cmd_tpl = (
                        f"connect_bd_net [get_bd_ports {top_port}] "
                        f"[get_bd_pins {base}_${{idx}}/{net}]"
                    )
                    tcl.append(_for_loop("idx", len(insts), [cmd_tpl]))
                else:
                    for inst in insts:
                        tcl.append(
                            f"connect_bd_net [get_bd_ports {top_port}] [get_bd_pins {inst}/{net}]"
                        )
        tcl.append("")

    # ── reset synchronizers for reset_domains.*.sync: reset_sync ─
    # reset_sync_output_pin[domain] is a full "[get_bd_pins <cell>/
    # sync_rst_out]" Tcl reference — not a bare net name — since a BD has
    # no wire declaration to name; it's what a cdc: connection's own
    # src_rst/dst_rst below must fan out from too when that side's reset
    # domain is itself a reset_sync destination (the same rule verilog
    # mode's _reset_net_for_domain enforces).
    reset_sync_output_pin: Dict[str, str] = {}
    if reset_sync_domains:
        tcl.append("# -- reset synchronizers for reset_domains.*.sync: reset_sync --")
        for rst_sync_counter, (name, rel) in enumerate(sorted(reset_sync_domains.items())):
            derived_from = rel.get("derived_from")
            async_rst_net = _domain_to_top_level_net(derived_from, is_clock=False, own_nets=global_nets)

            # The synchronizer's own destination clock: whichever clock
            # domain this reset domain's member instances actually use (a
            # reset domain and its instances are assumed to share one
            # clock domain). Falls back to ap_clk if unresolvable.
            member_mod_names = [mod for mod, dom in reset_of_module.items() if dom == name]
            dst_clk_domain = clock_of_module.get(member_mod_names[0]) if member_mod_names else None
            dst_clk_net = (
                _domain_to_top_level_net(dst_clk_domain, is_clock=True, own_nets=global_nets)
                if dst_clk_domain else "ap_clk"
            )

            cell_name = f"rst_sync_{rst_sync_counter}"
            tcl += [
                f"# Reset domain {name!r}: real member instances bound to sync_rst_out",
                f"create_bd_cell -type module -reference cdc_reset_sync {cell_name}",
                f"connect_bd_net [get_bd_ports {dst_clk_net}] [get_bd_pins {cell_name}/dst_clk]",
                f"connect_bd_net [get_bd_ports {async_rst_net}] [get_bd_pins {cell_name}/async_rst_in]",
            ]
            for inst, port in global_nets.get(name, []):
                tcl.append(
                    f"connect_bd_net [get_bd_pins {cell_name}/sync_rst_out] "
                    f"[get_bd_pins {inst}/{port}]"
                )
            reset_sync_output_pin[name] = f"[get_bd_pins {cell_name}/sync_rst_out]"
            tcl.append("")

    def _pin_width(inst: str, pin: str) -> int:
        mod_name = resolution.inst_to_mod[inst]
        for p in resolution.ports_by_mod[mod_name]:
            if p["name"] == pin:
                return int(p["width"])
        return 1

    # ── point-to-point nets (loops where possible) ───────────
    tcl.append("# -- auto-matched intra-design nets -------------------------")
    emitted: set[str] = set()
    reg_stage_counter = 0
    delay_counter = 0
    cdc_sync_counter = 0
    _tie_wr_en_one_created = False
    boundary_infos: List[Dict[str, Any]] = []  # collected for the crossing manifest

    for (src_mod, dst_mod), pairs in conn_map.items():
        cdc = cdc_map.get((src_mod, dst_mod))
        num_stages = reg_stages_map.get((src_mod, dst_mod), 0)
        num_delays = delay_cycles_map.get((src_mod, dst_mod), 0)

        # A cdc: connection gets only the synchronizer cell, never also a
        # register-stage/delay cell on the same pins — no real design
        # combines them (see write_bd_tcl's own docstring).
        if cdc:
            kind = cdc.get("kind")
            src_clk_domain = clock_of_module.get(src_mod)
            src_rst_domain = reset_of_module.get(src_mod)
            dst_clk_domain = clock_of_module.get(dst_mod)
            dst_rst_domain = reset_of_module.get(dst_mod)
            src_clk_net = (
                _domain_to_top_level_net(src_clk_domain, is_clock=True, own_nets=global_nets)
                if src_clk_domain else "ap_clk"
            )
            dst_clk_net = (
                _domain_to_top_level_net(dst_clk_domain, is_clock=True, own_nets=global_nets)
                if dst_clk_domain else "ap_clk"
            )

            def _rst_ref(domain: Optional[str]) -> str:
                """A full `[get_bd_pins ...]`/`[get_bd_ports ...]` Tcl
                reference for a resolved reset domain — routed through the
                domain's own cdc_reset_sync cell when it's a reset_sync
                destination (the same rule verilog mode's
                _reset_net_for_domain enforces), a plain top-level port
                otherwise."""
                if domain and domain in reset_sync_domains:
                    return reset_sync_output_pin[domain]
                net = (
                    _domain_to_top_level_net(domain, is_clock=False, own_nets=global_nets)
                    if domain else "ap_rst"
                )
                return f"[get_bd_ports {net}]"

            src_rst_ref = _rst_ref(src_rst_domain)
            dst_rst_ref = _rst_ref(dst_rst_domain)

            for s_pin, d_pin in pairs:
                s_pin_c = _canon_pin(ip_info, src_mod, s_pin)
                d_pin_c = _canon_pin(ip_info, dst_mod, d_pin)
                w = _pin_width(src_mod, s_pin_c)
                inst_name = f"cdc_sync_{cdc_sync_counter}"
                cdc_sync_counter += 1

                if kind == "level_sync":
                    tcl += [
                        f"# CDC crossing {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} (level_sync)",
                        f"create_bd_cell -type module -reference cdc_sync2ff {inst_name}",
                        f"set_property CONFIG.WIDTH {{{w}}} [get_bd_cells {inst_name}]",
                        f"connect_bd_net [get_bd_ports {dst_clk_net}] [get_bd_pins {inst_name}/dst_clk]",
                        f"connect_bd_net {dst_rst_ref} [get_bd_pins {inst_name}/dst_rst]",
                        f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/din]",
                        f"connect_bd_net [get_bd_pins {inst_name}/dout] [get_bd_pins {dst_mod}/{d_pin_c}]",
                    ]
                elif kind == "pulse_sync":
                    tcl += [
                        f"# CDC crossing {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} "
                        f"(pulse_sync, min_spacing_cycles={cdc.get('min_spacing_cycles')})",
                        f"create_bd_cell -type module -reference cdc_pulse_sync {inst_name}",
                        f"connect_bd_net [get_bd_ports {src_clk_net}] [get_bd_pins {inst_name}/src_clk]",
                        f"connect_bd_net {src_rst_ref} [get_bd_pins {inst_name}/src_rst]",
                        f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/pulse_in]",
                        f"connect_bd_net [get_bd_ports {dst_clk_net}] [get_bd_pins {inst_name}/dst_clk]",
                        f"connect_bd_net {dst_rst_ref} [get_bd_pins {inst_name}/dst_rst]",
                        f"connect_bd_net [get_bd_pins {inst_name}/pulse_out] [get_bd_pins {dst_mod}/{d_pin_c}]",
                    ]
                elif kind == "mailbox_transfer":
                    # dout_valid is left unconnected — no downstream valid
                    # concept yet, same as verilog mode's unused wire.
                    tcl += [
                        f"# CDC crossing {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} (mailbox_transfer)",
                        f"create_bd_cell -type module -reference cdc_mailbox {inst_name}",
                        f"set_property CONFIG.WIDTH {{{w}}} [get_bd_cells {inst_name}]",
                        f"connect_bd_net [get_bd_ports {src_clk_net}] [get_bd_pins {inst_name}/src_clk]",
                        f"connect_bd_net {src_rst_ref} [get_bd_pins {inst_name}/src_rst]",
                        f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/din]",
                        f"connect_bd_net [get_bd_ports {dst_clk_net}] [get_bd_pins {inst_name}/dst_clk]",
                        f"connect_bd_net {dst_rst_ref} [get_bd_pins {inst_name}/dst_rst]",
                        f"connect_bd_net [get_bd_pins {inst_name}/dout] [get_bd_pins {dst_mod}/{d_pin_c}]",
                    ]
                elif kind == "async_fifo":
                    depth = cdc.get("depth")
                    we_pin_raw = cdc.get("write_enable_pin")
                    tcl.append(
                        f"# CDC crossing {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} "
                        f"(async_fifo, depth={depth})"
                    )
                    tcl.append(f"create_bd_cell -type module -reference cdc_async_fifo {inst_name}")
                    tcl.append(f"set_property CONFIG.WIDTH {{{w}}} [get_bd_cells {inst_name}]")
                    tcl.append(f"set_property CONFIG.DEPTH {{{depth}}} [get_bd_cells {inst_name}]")
                    tcl.append(f"connect_bd_net [get_bd_ports {src_clk_net}] [get_bd_pins {inst_name}/wr_clk]")
                    tcl.append(f"connect_bd_net {src_rst_ref} [get_bd_pins {inst_name}/wr_rst]")
                    tcl.append(f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/din]")
                    if we_pin_raw:
                        we_pin = _canon_pin(ip_info, src_mod, we_pin_raw)
                        tcl.append(
                            f"connect_bd_net [get_bd_pins {src_mod}/{we_pin}] [get_bd_pins {inst_name}/wr_en]"
                        )
                    else:
                        # Default: continuously driven, the same
                        # "wr_en tied to 1'b1" behavior verilog mode
                        # defaults to when write_enable_pin is unset — one
                        # shared xlconstant CONST_VAL 1 cell, reused across
                        # every async_fifo needing this default.
                        if not _tie_wr_en_one_created:
                            tcl += [
                                "if {[llength [get_bd_cells -quiet tie_wr_en_one]] == 0} {",
                                "  create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 tie_wr_en_one",
                                "  set_property CONFIG.CONST_WIDTH {1} [get_bd_cells tie_wr_en_one]",
                                "  set_property CONFIG.CONST_VAL {1} [get_bd_cells tie_wr_en_one]",
                                "}",
                            ]
                            _tie_wr_en_one_created = True
                        tcl.append(
                            f"connect_bd_net [get_bd_pins tie_wr_en_one/dout] [get_bd_pins {inst_name}/wr_en]"
                        )
                    tcl.append(f"connect_bd_net [get_bd_ports {dst_clk_net}] [get_bd_pins {inst_name}/rd_clk]")
                    tcl.append(f"connect_bd_net {dst_rst_ref} [get_bd_pins {inst_name}/rd_rst]")
                    tcl.append(f"connect_bd_net [get_bd_pins {inst_name}/dout] [get_bd_pins {dst_mod}/{d_pin_c}]")
                    # full/overflow_attempt/occupancy/high_water/empty/
                    # underflow_attempt are left unconnected — real
                    # instrumentation a Tier 2 probe can consume, same as
                    # verilog mode's unused wires.
                tcl.append("")
            continue

        # A register-staged/delayed connection needs its own uniquely-named
        # stage cell per pin — never loop-grouped (a Tcl for-loop shares one
        # cell-name template across iterations, which a real per-pin cell
        # instance can't).
        if num_stages > 0 or num_delays > 0:
            tag = boundary_map.get((src_mod, dst_mod))
            for s_pin, d_pin in pairs:
                s_pin_c = _canon_pin(ip_info, src_mod, s_pin)
                d_pin_c = _canon_pin(ip_info, dst_mod, d_pin)
                w = _pin_width(src_mod, s_pin_c)

                if tag:
                    # Boundary crossing: a protected slr_crossing_delay
                    # with a stable, deterministic instance name, whatever
                    # count was declared (register_stages or delay_cycles
                    # — config.py's own load-time validation already
                    # guarantees exactly one is positive for a boundary
                    # tag). Its interface matches signal_delay's
                    # (clk/rst/din/dout), not RegisterStage's.
                    depth = num_stages or num_delays
                    inst_name = _boundary_inst_name(tag, s_pin_c, d_pin_c)
                    tcl += [
                        f"# Boundary crossing {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} "
                        f"[boundary={tag}]",
                        f"create_bd_cell -type module -reference slr_crossing_delay {inst_name}",
                        f"set_property CONFIG.WIDTH {{{w}}} [get_bd_cells {inst_name}]",
                        f"set_property CONFIG.DEPTH {{{depth}}} [get_bd_cells {inst_name}]",
                        f"connect_bd_net [get_bd_ports ap_clk] [get_bd_pins {inst_name}/clk]",
                        f"connect_bd_net [get_bd_ports ap_rst] [get_bd_pins {inst_name}/rst]",
                        f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/din]",
                        f"connect_bd_net [get_bd_pins {inst_name}/dout] [get_bd_pins {dst_mod}/{d_pin_c}]",
                    ]
                    boundary_infos.append({
                        "tag": tag,
                        "src_inst": src_mod,
                        "dst_inst": dst_mod,
                        "src_pin": s_pin_c,
                        "dst_pin": d_pin_c,
                        "depth": depth,
                        "width": w,
                        "instance_name": inst_name,
                    })
                elif num_stages > 0:
                    inst_name = f"reg_stage_{reg_stage_counter}"
                    reg_stage_counter += 1
                    tcl += [
                        f"# Pipeline {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} ({num_stages} stages)",
                        f"create_bd_cell -type module -reference RegisterStage {inst_name}",
                        f"set_property CONFIG.DATAWIDTH {{{w}}} [get_bd_cells {inst_name}]",
                        f"set_property CONFIG.STAGES {{{num_stages}}} [get_bd_cells {inst_name}]",
                        f"connect_bd_net [get_bd_ports ap_clk] [get_bd_pins {inst_name}/clk]",
                        f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/data_in]",
                        f"connect_bd_net [get_bd_pins {inst_name}/data_out] [get_bd_pins {dst_mod}/{d_pin_c}]",
                    ]
                else:
                    inst_name = f"delay_{delay_counter}"
                    delay_counter += 1
                    tcl += [
                        f"# Delay {src_mod}.{s_pin_c} -> {dst_mod}.{d_pin_c} (+{num_delays} cycles)",
                        f"create_bd_cell -type module -reference signal_delay {inst_name}",
                        f"set_property CONFIG.WIDTH {{{w}}} [get_bd_cells {inst_name}]",
                        f"set_property CONFIG.DEPTH {{{num_delays}}} [get_bd_cells {inst_name}]",
                        f"connect_bd_net [get_bd_ports ap_clk] [get_bd_pins {inst_name}/clk]",
                        f"connect_bd_net [get_bd_ports ap_rst] [get_bd_pins {inst_name}/rst]",
                        f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {inst_name}/din]",
                        f"connect_bd_net [get_bd_pins {inst_name}/dout] [get_bd_pins {dst_mod}/{d_pin_c}]",
                    ]
            continue

        buckets: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        for s_pin, d_pin in pairs:
            buckets.setdefault((s_pin.split("_")[0], d_pin.split("_")[0]), []).append(
                (s_pin, d_pin)
            )

        for (_, _), pl in buckets.items():
            s_list = [s for s, _ in pl]
            d_list = [d for _, d in pl]
            ns, bs = _pins_are_numeric(s_list)
            nd, bd = _pins_are_numeric(d_list)

            # (i) both sides numeric  → loop
            # (ii) scalar-fan-out (src scalar, dst numeric & long) → loop
            if (ns and nd) or (not ns and nd and len(pl) >= loop_threshold):
                if ns:
                    src_fmt = f"[get_bd_pins {src_mod}/{bs}_${{idx}}]"
                else:
                    src_fmt = f"[get_bd_pins {src_mod}/{s_list[0]}]"
                dst_fmt = f"[get_bd_pins {dst_mod}/{bd}_${{idx}}]"
                cmd_tpl = f"connect_bd_net {src_fmt} {dst_fmt}"
                tcl.append(_for_loop("idx", len(pl), [cmd_tpl]))
                continue

            # fallback: one line per pair
            for s_pin, d_pin in pl:
                s_pin_c = _canon_pin(ip_info, src_mod, s_pin)
                d_pin_c = _canon_pin(ip_info, dst_mod, d_pin)
                if (s_pin_c != s_pin) or (d_pin_c != d_pin):
                    tcl.append(f"# canon: {src_mod}/{s_pin}→{s_pin_c} , {dst_mod}/{d_pin}→{d_pin_c}")
                cmd = (
                    f"connect_bd_net [get_bd_pins {src_mod}/{s_pin_c}] [get_bd_pins {dst_mod}/{d_pin_c}]"
                )
                if cmd not in emitted:
                    emitted.add(cmd)
                    tcl.append(cmd)
    tcl.append("")

    # ── tie-offs for unconnected mandatory inputs ────────────
    # A BD with a real, unconnected mandatory input pin fails
    # validate_bd_design/wrapper generation — verilog mode ties these to a
    # literal `w'd0`; here one shared xlconstant IP per distinct bit-width
    # is instantiated and fanned out via connect_bd_net.
    if tied_to_zero:
        tcl.append("# -- tie-offs for unconnected mandatory inputs ----------------------------")
        by_width: Dict[int, List[Tuple[str, str]]] = {}
        for inst, pin, w in tied_to_zero:
            by_width.setdefault(w, []).append((inst, pin))
        for w in sorted(by_width):
            const_cell = f"tie_const_{w}"
            tcl += [
                f"if {{[llength [get_bd_cells -quiet {const_cell}]] == 0}} {{",
                f"  create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 {const_cell}",
                f"  set_property CONFIG.CONST_WIDTH {{{w}}} [get_bd_cells {const_cell}]",
                f"  set_property CONFIG.CONST_VAL {{0}} [get_bd_cells {const_cell}]",
                "}",
            ]
            for inst, pin in by_width[w]:
                tcl.append(
                    f"connect_bd_net [get_bd_pins {const_cell}/dout] [get_bd_pins {inst}/{pin}]"
                )
        tcl.append("")

    # ── deterministic external/debug top-level ports ─────────
    # Replaces the previous single blind `make_bd_pins_external` call
    # (whose port names/widths Vivado chose itself, unknowably to forge)
    # with one create_bd_port + connect_bd_net per resolved top-level
    # port, so top_ports below is trustworthy without running Vivado.
    _ext_debug_ports = [
        p for p in resolution.top_ports if p["origin"] in ("external", "debug")
    ]
    if _ext_debug_ports:
        tcl.append("# -- top-level ports: user-declared external / debug --------")
        for p in _ext_debug_ports:
            tcl.append(_create_port_cmd(p["direction"], p["name"], p["width"]))
            if p["direction"] == "out":
                tcl.append(
                    f"connect_bd_net [get_bd_pins {p['instance']}/{p['port']}] "
                    f"[get_bd_ports {p['name']}]"
                )
            else:
                tcl.append(
                    f"connect_bd_net [get_bd_ports {p['name']}] "
                    f"[get_bd_pins {p['instance']}/{p['port']}]"
                )
        tcl.append("")

    # ── wrap-up ──────────────────────────────────────────────
    tcl += [
        "# -- finalise & wrapper --------------------------------------",
        "save_bd_design",
        f"close_bd_design [get_bd_designs {bd_name}]",
        "",
        "# HDL wrapper (auto)",
        "make_wrapper -files [get_files *.bd] -top -inst_template -import",
        "",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(NL.join(tcl))
    print(f"✅ Block-design Tcl written to {out_path}")

    if boundary_infos:
        # The extra "/inst" level is Vivado's own convention for a
        # -type module -reference cell (confirmed against real Vivado
        # 2024.1 synthesis) — see write_crossing_manifest's docstring.
        write_crossing_manifest(
            out_path, bd_name, boundary_infos,
            hier_of=lambda inst_name: f"{bd_name}_i/{inst_name}/inst",
        )

    return {
        "open_outputs": open_outputs,
        "tied_to_zero": tied_to_zero,
        "total_modules": len(cfg.modules),
        "total_instances": sum(m.instances for m in cfg.modules),
        "total_connections": sum(len(pairs) for pairs in conn_map.values()),
        "top_ports": resolution.top_ports,
    }


# ── ad-hoc CLI for debugging --------------------------------
if __name__ == "__main__":
    import argparse, sys
    from ...contracts.matcher import auto_match_ports, load_ip_info

    ap = argparse.ArgumentParser()
    ap.add_argument("design", type=Path)
    ap.add_argument("--ip-info", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, default=Path("block_design.tcl"))
    ap.add_argument("--bd-name", default="top_bd")
    ap.add_argument(
        "--loop-threshold",
        type=int,
        default=10,
        help="minimum #repetitions before a Tcl loop is emitted",
    )
    ns = ap.parse_args()

    yd = (
        ns.design
        if ns.design.suffix in (".yaml", ".yml")
        else ns.design / "design.yaml"
    )
    cfg = DesignConfig.load(yd)
    info = load_ip_info(ns.ip_info)
    conn, gnet = auto_match_ports(cfg, info)
    write_bd_tcl(
        cfg,
        info,
        conn,
        gnet,
        ns.out,
        bd_name=ns.bd_name,
        loop_threshold=ns.loop_threshold,
        src_root=yd.parent,
        ip_root=yd.parent,
    )
    sys.exit(0)
