from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from ..config import DesignConfig, Module, resolve_declared_path

NL = "\n"


# ───────────────────────── helpers ──────────────────────────

def _inst(mod: Module, idx: int) -> str:
    """Instance label inside the BD."""
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"


def _declared_external(mod: Module, pin: str) -> bool:
    """True if *pin* was listed in external_in/out_ports."""
    for pref in (mod.external_in_ports + mod.external_out_ports):
        if pin == pref or pin.startswith(pref):
            return True
    return False


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
    src_root: Optional[Path]=None
) -> None:
     # Collect HDL sources for -type module references (pairs: file, lang)
    hdl_sources_flat: List[str] = []
    for mod in cfg.modules:
        meta = ip_info.get(mod.name, {}) or {}
        if _is_hdl(meta):
            for f, lang in _gather_hdl_sources_for_mod(mod, meta, src_root or Path(".")):
                hdl_sources_flat += [f, lang]

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

    # ── point-to-point nets (loops where possible) ───────────
    tcl.append("# -- auto-matched intra-design nets -------------------------")
    emitted: set[str] = set()

    for (src_mod, dst_mod), pairs in conn_map.items():
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

    # ── external pins declared in YAML ───────────────────────
    tcl.append("# -- user-declared external pins ----------------------------")
    ext: List[str] = []
    for mod in cfg.modules:
        for p in ip_info[mod.name]["ports"]:
            if _declared_external(mod, p["name"]):
                for i in range(mod.instances):
                    ext.append(f"[get_bd_pins {_inst(mod, i)}/{p['name']}]")
    if ext:
        tcl.append("make_bd_pins_external " + " ".join(ext))
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


# ── ad-hoc CLI for debugging --------------------------------
if __name__ == "__main__":
    import argparse, sys
    from ..ip.matcher import auto_match_ports, load_ip_info

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
    )
    sys.exit(0)
