from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from ...contracts.config import DesignConfig, Module, resolve_declared_path
from ._port_resolution import classify_connections, resolve_top_ports

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


def _unsupported_bd_features(cfg: DesignConfig) -> List[str]:
    """Design features write_bd_tcl still doesn't implement.

    ``register_stages``/``delay_cycles`` are handled (see
    ``_register_stage_map``/``_delay_cycles_map`` and the intermediate-stage
    insertion in ``write_bd_tcl``'s point-to-point loop) — real
    ``RegisterStage``/``signal_delay`` cell instantiation, the same modules
    verilog mode uses. Still rejected outright, rather than silently
    generating a BD missing real logic:

    - A ``boundary:`` tag needs the *protected* ``slr_crossing_delay``
      module (``KEEP_HIERARCHY``/``DONT_TOUCH`` on every stage register) —
      a different module from plain ``signal_delay``, not implemented here.
    - CDC synchronizers (``cdc:``) and ``reset_domains.*.sync: reset_sync``
      both need destination-domain clock/reset resolution
      (``forge.contracts.domains.resolve_domain_nets``) this generator
      doesn't perform, and real synchronizer/FIFO cell instantiation this
      generator has no code for at all.
    """
    problems: List[str] = []
    for conn in cfg.connections:
        if conn.boundary:
            problems.append(f"connection {conn.from_}->{conn.to} declares boundary={conn.boundary!r}")
        if conn.cdc:
            problems.append(f"connection {conn.from_}->{conn.to} declares cdc: {conn.cdc}")
    for name, rel in (cfg.reset_domains or {}).items():
        if rel.get("sync") == "reset_sync":
            problems.append(f"reset_domains.{name} declares sync: reset_sync")
    return problems


def _resolve_support_rtl_path(target_name: str, search_roots: List[Path]) -> Optional[Path]:
    """Locate a framework support-RTL file (``RegisterStage.v``,
    ``signal_delay.v``) by filename under any of *search_roots*.

    Same rglob-search strategy ``forge.core.cli.groups.topgen.
    generate_build_manifest`` already uses to find these files for the
    verilog-mode compile list (*where* the checkout keeps them is a
    filesystem question, answered the same way regardless of output mode)
    — a second, small, self-contained copy here rather than importing from
    the CLI layer (``topgen.py`` already imports `from` this module).
    """
    for root in search_roots:
        if root is None or not Path(root).is_dir():
            continue
        matches = sorted(Path(root).rglob(target_name))
        if matches:
            return matches[0].resolve()
    return None


#: Which framework support RTL a register_stages/delay_cycles connection
#: needs — same filenames forge.core.cli.groups.topgen's
#: _SUPPORT_RTL_BY_TRANSFORMATION maps for verilog mode.
_REGISTER_STAGE_RTL = "RegisterStage.v"
_SIGNAL_DELAY_RTL = "signal_delay.v"


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
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate a Vivado Block Design Tcl script wiring algorithm modules
    together — the ``--mode bd`` counterpart to
    ``write_structural_verilog``.

    Returns a report dict with exactly the same shape
    ``write_structural_verilog`` returns (``open_outputs``, ``tied_to_zero``,
    ``total_modules``, ``total_instances``, ``total_connections``,
    ``top_ports``), so ``forge topgen gen-top``'s reporting/manifest
    pipeline works unmodified against either mode.

    Raises ``ValueError`` if the design declares a boundary-tagged delay,
    a CDC synchronizer, or a reset_sync domain — see
    ``_unsupported_bd_features``. Plain ``register_stages``/
    ``delay_cycles`` (no ``boundary:`` tag) are supported: a real
    ``RegisterStage``/``signal_delay`` cell is instantiated between the
    source and destination pins, the same modules verilog mode uses.
    """
    unsupported = _unsupported_bd_features(cfg)
    if unsupported:
        raise ValueError(
            "write_bd_tcl (--mode bd) does not yet support a boundary-tagged "
            "delay, CDC synchronizers, or reset_domains.*.sync: reset_sync "
            "— generating a Block Design for this design would silently drop "
            "real logic verilog mode includes:\n  - " + "\n  - ".join(unsupported) +
            "\nUse --mode verilog for this design instead."
        )

    # register_stages/delay_cycles, expanded per-instance — same
    # construction write_structural_verilog uses (minus cdc_map/boundary,
    # already rejected above).
    reg_stages_map: Dict[Tuple[str, str], int] = {}
    delay_cycles_map: Dict[Tuple[str, str], int] = {}
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

    # Single implementation of "which pins become top-level ports and what
    # they're named" — shared with write_structural_verilog. See
    # forge.ir.model.ResolvedTopLevelPort's docstring.
    resolution = resolve_top_ports(
        cfg, ip_info, ip_root or Path("."), global_nets, system_yml=system_yml,
    )
    open_outputs, tied_to_zero = classify_connections(
        cfg, ip_info, conn_map, global_nets, resolution,
    )

     # Collect HDL sources for -type module references (pairs: file, lang)
    hdl_sources_flat: List[str] = []
    for mod in cfg.modules:
        meta = ip_info.get(mod.name, {}) or {}
        if _is_hdl(meta):
            for f, lang in _gather_hdl_sources_for_mod(mod, meta, src_root or Path(".")):
                hdl_sources_flat += [f, lang]

    # Framework support RTL for register_stages/delay_cycles — same
    # filenames verilog mode's build_manifest.json resolves, found the same
    # way (rglob under src_root/ip_root).
    # project_root first: for a real plugin layout (e.g. trigger_demo),
    # RegisterStage.v/signal_delay.v live under <plugin>/algo/rtl/, outside
    # both src_root (the design.yml's own directory) and ip_root — the
    # same reason generate_build_manifest searches project_root first too.
    _support_search_roots = [r for r in (project_root, src_root, ip_root) if r]
    if reg_stages_map:
        found = _resolve_support_rtl_path(_REGISTER_STAGE_RTL, _support_search_roots)
        if found is None:
            raise ValueError(
                f"{_REGISTER_STAGE_RTL} not found under {_support_search_roots} — "
                "this design's register_stages connections need it"
            )
        hdl_sources_flat += [found.as_posix(), "verilog"]
    if delay_cycles_map:
        found = _resolve_support_rtl_path(_SIGNAL_DELAY_RTL, _support_search_roots)
        if found is None:
            raise ValueError(
                f"{_SIGNAL_DELAY_RTL} not found under {_support_search_roots} — "
                "this design's delay_cycles connections need it"
            )
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
            else:
                cmd_tpl = f"create_bd_cell -type module -reference {ref} {mod.name}_${{idx}}"
                tcl.append(_for_loop("idx", mod.instances, [cmd_tpl]))
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
            else:
                cmd_tpl = f"create_bd_cell -type ip -vlnv {vlnv} {mod.name}_${{idx}}"
                tcl.append(_for_loop("idx", mod.instances, [cmd_tpl]))
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

    for (src_mod, dst_mod), pairs in conn_map.items():
        num_stages = reg_stages_map.get((src_mod, dst_mod), 0)
        num_delays = delay_cycles_map.get((src_mod, dst_mod), 0)

        # A register-staged/delayed connection needs its own uniquely-named
        # stage cell per pin — never loop-grouped (a Tcl for-loop shares one
        # cell-name template across iterations, which a real per-pin cell
        # instance can't).
        if num_stages > 0 or num_delays > 0:
            for s_pin, d_pin in pairs:
                s_pin_c = _canon_pin(ip_info, src_mod, s_pin)
                d_pin_c = _canon_pin(ip_info, dst_mod, d_pin)
                w = _pin_width(src_mod, s_pin_c)

                if num_stages > 0:
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
