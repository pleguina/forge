from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

import yaml  # NEW: to read system.yml

from ..config import DesignConfig, Module, resolve_declared_path
from ..ip.matcher import load_ip_info, auto_match_ports
from forge.core.utils.hdl_parser import _scan_ports as scan_vhdl_ports
from forge.core.utils.hdl_parser import _scan_verilog_ports as scan_vlog_ports
from forge.core.utils.hdl_parser import _vhdl_entity_name, _verilog_module_name

# ----------------------------- VHDL helpers ------------------------------

_VHDL_KEYWORDS = {
    "abs","access","after","alias","all","and","architecture","array","assert","attribute",
    "begin","block","body","buffer","bus","case","component","configuration","constant",
    "disconnect","downto","else","elsif","end","entity","exit","file","for","function",
    "generate","generic","group","guarded","if","impure","in","inertial","inout","is",
    "label","library","linkage","literal","loop","map","mod","nand","new","next","nor",
    "not","null","of","on","open","or","others","out","package","port","postponed",
    "procedure","process","pure","range","record","register","reject","rem","report",
    "return","rol","ror","select","severity","shared","signal","sla","sll","sra","srl",
    "subtype","then","to","transport","type","units","until","use","variable","wait",
    "when","while","with","xnor","xor"
}

_id_re = re.compile(r"[^A-Za-z0-9_]+")
_re_trailing_num = re.compile(r"^(.*?)(?:_(\d+)|(\d+))$")

def _vhdl_ident(s: str) -> str:
    """
    Make a legal VHDL identifier:
      - replace non [A-Za-z0-9_] with '_'
      - collapse multiple underscores
      - strip leading/trailing underscores
      - ensure first char is a letter by prefixing 'n_'
      - avoid reserved words by prefixing 'n_'
    """
    if not s:
        return "n_"
    s = _id_re.sub("_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    if not s or not s[0].isalpha():
        s = "n_" + s
    if s.lower() in _VHDL_KEYWORDS:
        s = "n_" + s
    return s

def _base_of(name: str) -> str:
    m = _re_trailing_num.match(name)
    return m.group(1) if m else name

def _inst(mod: Module, idx: int) -> str:
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"

def _vtype(width: int) -> str:
    return "std_logic" if width == 1 else f"std_logic_vector({width-1} downto 0)"

def _canon_pin(ip_info: Dict[str, Dict], mod: str, pin: str) -> str:
    """Map e.g. cfg_vec15 → cfg_vec if only the base pin exists."""
    def _port_exists(ip_info, mod, pin):
        try:
            return any(p["name"] == pin for p in ip_info[mod]["ports"])
        except KeyError:
            return False
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

# ------------------------- system.yml awareness --------------------------

def _parse_system_reservations(system_yml: Path | None) -> tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """
    Read system.yml (if provided) and return:
      - fw_to_mod_in  : {module -> set(base_port_names)}  framework → algorithm inputs
      - mod_to_fw_out : {module -> set(base_port_names)}  algorithm → framework outputs
    These bases will be treated as 'external' (top-level I/O) during generation.
    """
    fw_to_mod_in: Dict[str, Set[str]]  = {}
    mod_to_fw_out: Dict[str, Set[str]] = {}
    if not system_yml:
        return fw_to_mod_in, mod_to_fw_out

    cfg = yaml.safe_load(system_yml.read_text()) or {}
    conns = cfg.get("connections", []) or []

    def _is_fw(name: str) -> bool:
        return str(name).lower() in ("framework", "links")

    for c in conns:
        src = c.get("from")
        dst = c.get("to")
        for fw_sig, alg_sig in c.get("port_map", []):
            if _is_fw(src) and isinstance(dst, str) and not _is_fw(dst):
                fw_to_mod_in.setdefault(dst, set()).add(_base_of(str(alg_sig)))
            elif _is_fw(dst) and isinstance(src, str) and not _is_fw(src):
                mod_to_fw_out.setdefault(src, set()).add(_base_of(str(fw_sig)))
    return fw_to_mod_in, mod_to_fw_out

def _name_is_reserved(name: str, bases: Set[str]) -> bool:
    b = _base_of(name)
    if b in bases:
        return True
    # also allow base prefixes like base_<idx>
    return any(name == x or name.startswith(x + "_") for x in bases)

# ------------------------- port inventory helpers ------------------------

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
        out.append({"name": name, "dir": dirn.lower(), "width": width})
    return out

def _fetch_module_ports(mod: Module, meta: Dict, ip_root: Path) -> List[Dict]:
    """
    Return [{name,dir,width}], prefer ip_info. If missing, scan RTL under ip_root/<mod>/…
    """
    ports = meta.get("ports", [])
    ports = _normalize_ports(ports)
    if ports and all(("name" in p and "dir" in p and "width" in p) for p in ports):
        return ports

    # Try to scan RTL around ip_root/<mod>
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
        return [{"name": n, "dir": d, "width": int(w)} for n, (d, w) in scanned.items()]

    return ports  # whatever we had, normalized

def _is_hdl(meta: Dict) -> bool:
    if str(meta.get("kind", "")).lower() == "hdl": return True
    if str(meta.get("library", "")).lower() == "hdl": return True
    if str(meta.get("version", "")).lower() in ("rtl", "hdl"): return True
    if meta.get("entity") and not meta.get("vendor"): return True
    return False

def _resolve_paths(paths: List[str] | None, root: Path) -> List[Path]:
    out: List[Path] = []
    for s in (paths or []):
        p = resolve_declared_path(s, root)
        if p.exists():
            out.append(p)
    return out

def _guess_lang(path: Path) -> str:
    return "vhdl" if path.suffix.lower() == ".vhd" else "verilog"

def _gather_hdl_sources_for_mod(mod: Module, meta: Dict, src_root: Path) -> list[tuple[str,str]]:
    """
    Returns [(file,lang)] and now also includes any *package* files declared
    in either ip_info['packages'] or the YAML module (rtl_packages / vhdl_packages / include_packages / packages).
    """
    out: list[tuple[str,str]] = []

    # Prefer ip_info lists when present
    ip_sources  = meta.get("sources")  or []
    ip_packages = meta.get("packages") or []
    # Add packages FIRST (VHDL compilation order requirement)
    for p in ip_packages:
        f = Path(p)
        out.append((f.as_posix(), _guess_lang(f)))
    for p in ip_sources:
        f = Path(p)
        out.append((f.as_posix(), _guess_lang(f)))
    if out:
        return out

    # YAML-provided
    src_files = _resolve_paths(getattr(mod, "src", []) or [], src_root)

    pkg_fields = ("rtl_packages", "vhdl_packages", "include_packages", "packages")
    pkg_files: List[Path] = []
    for fld in pkg_fields:
        pkg_files += _resolve_paths(getattr(mod, fld, []) or [], src_root)

    cand: list[Path] = []
    for p in src_files:
        if p.is_file():
            cand.append(p)
        elif p.is_dir():
            cand += sorted(p.rglob("*.vhd"))
            cand += sorted(p.rglob("*.v"))
            cand += sorted(p.rglob("*.sv"))

    want = meta.get("entity") or mod.top or mod.name
    def score(f: Path) -> int:
        if f.stem == want: return 100
        if want.lower() in f.name.lower(): return 50
        return 1

    cand = [c for c in cand if c.suffix.lower() in (".vhd",".v",".sv")]
    cand.sort(key=score, reverse=True)

    # Add package files FIRST - VHDL requires packages be compiled before entities
    for f in pkg_files:
        out.append((f.as_posix(), _guess_lang(f)))

    if cand:
        f = cand[0]
        out.append((f.as_posix(), _guess_lang(f)))

    return out

def collect_hdl_sources(cfg: DesignConfig, ip_info: Dict[str, Dict], design_dir: Path) -> list[tuple[str,str]]:
    pairs: list[tuple[str,str]] = []
    for mod in cfg.modules:
        meta = ip_info.get(mod.name, {}) or {}
        if _is_hdl(meta):
            pairs += _gather_hdl_sources_for_mod(mod, meta, design_dir)
    # de-dup while preserving order
    seen = set()
    unique = []
    for f,lang in pairs:
        if (f,lang) not in seen:
            unique.append((f,lang)); seen.add((f,lang))
    return unique

def _safe_ip_entity_name(mod: Module, meta: Dict) -> str:
    """
    Choose a module/entity name that is NOT the same as the catalog 'name'
    (Vivado error Common 17-69 if they match). Reuse meta['entity'] when
    it already differs; otherwise append '_ip'.
    """
    base = (meta.get("entity") or meta.get("name") or mod.top or mod.name)
    core = (meta.get("name")   or base)
    return base if base != core else f"{base}_ip"

def _entity_name_for_component(mod: Module, meta: Dict) -> str:
    """
    If this module is plain HDL (no vendor), use its true entity/top/name.
    If it’s a catalog IP, use a safe entity name that differs from the
    catalog 'name' (to avoid Vivado Common 17-69).
    """
    if _is_hdl(meta):  # pure RTL
        return meta.get("entity") or mod.top or mod.name
    return _safe_ip_entity_name(mod, meta)

# ------------------------------ generator ---------------------------------

def write_structural_vhdl(
    cfg: DesignConfig,
    ip_info: Dict[str, Dict],
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],   # (src_inst, dst_inst) → [(s_pin, d_pin)]
    global_nets: Dict[str, List[Tuple[str, str]]],
    ip_root: Path,
    out_path: Path,
    *,
    top_name: str = "algo_top",
    system_yml: Path | None = None,          # NEW: framework awareness
) -> Dict[str, Any]:
    """
    Generate a structural RTL top that wires algorithm modules together.

    If system_yml is provided, any framework↔algorithm connections listed there
    are lifted to the top and the *top-level* port names are the framework
    signal names (e.g. SLxQyCHz_rx_data / _tx_data / _cfg_data). Those top
    ports are then wired to the algorithm pins according to system.yml.

    If system_yml is None, behavior matches the previous implementation: only
    user-declared externals are lifted as <inst>_<pin>.
    """
    NL = "\n"
    lines: List[str] = []
    emit = lines.append

    # ---------- helpers (local to this function) --------------------------
    def _parse_system_aliases(system_yml: Path | None) -> tuple[Dict[Tuple[str, str], str], Dict[Tuple[str, str], str]]:
        """
        Returns:
          alias_in [(inst_label, algo_input_pin)]  -> top_port_name (framework RX/CFG)
          alias_out[(inst_label, algo_output_pin)] -> top_port_name (framework TX)
        """
        alias_in: Dict[Tuple[str, str], str] = {}
        alias_out: Dict[Tuple[str, str], str] = {}
        if not system_yml:
            return alias_in, alias_out

        import yaml
        sys_cfg = yaml.safe_load(system_yml.read_text()) or {}
        name_to_mod = {m.name: m for m in cfg.modules}

        def _inst_label(mod_name: str, idx: int | None) -> str:
            m = name_to_mod.get(mod_name)
            n = int(idx or 0)
            return mod_name if (m and m.instances == 1) else f"{mod_name}_{n}"

        def _is_fw(x: str | None) -> bool:
            return str(x).lower() in ("framework", "links")

        for conn in sys_cfg.get("connections", []):
            src  = conn.get("from")
            dst  = conn.get("to")
            sidx = conn.get("from_instance")
            didx = conn.get("to_instance")
            for a, b in conn.get("port_map", []):
                # framework → algorithm
                if _is_fw(src):
                    ilab = _inst_label(dst, didx)
                    # a = framework signal, b = algorithm input pin
                    alias_in[(ilab, b)] = a
                # algorithm → framework
                elif _is_fw(dst):
                    ilab = _inst_label(src, sidx)
                    # a = algorithm output pin, b = framework signal
                    alias_out[(ilab, a)] = b
        return alias_in, alias_out

    def _is_user_external(mod: Module, pname: str, pdir: str) -> str | None:
        """
        Check user-declared external_in_ports / external_out_ports on a
        *name or prefix* basis — cross-checked against *pname*'s own real
        direction (see the identical fix + rationale in
        forge/topgen/generators/structural_verilog.py's own
        ``_is_user_external`` — this
        VHDL generator carries the same bug, unexercised by any existing
        test).
        Returns 'in' / 'out' / None.
        """
        user_in  = tuple(mod.external_in_ports or [])
        user_out = tuple(mod.external_out_ports or [])
        if pdir == "in" and any(pname == x or pname.startswith(x + "_") for x in user_in):
            return "in"
        if pdir == "out" and any(pname == x or pname.startswith(x + "_") for x in user_out):
            return "out"
        return None

    # ---------- read system.yml → alias maps ------------------------------
    alias_in, alias_out = _parse_system_aliases(system_yml)

    # ---------- tracking for report ---------------------------------------
    unconnected_inputs: List[Tuple[str, str, int]] = []   # [(instance, port, width)]
    unconnected_outputs: List[Tuple[str, str, int]] = []  # [(instance, port, width)]
    open_outputs: List[Tuple[str, str, int]] = []         # [(instance, port, width)]
    tied_to_zero: List[Tuple[str, str, int]] = []         # [(instance, port, width)]

    emit("-- ------------------------------------------------------------")
    emit("--  Auto-generated by hls-auto – structural top (no BD)")
    emit("--  DO NOT EDIT BY HAND")
    emit("-- ------------------------------------------------------------")
    emit("library ieee;")
    emit("use ieee.std_logic_1164.all;")
    emit("")

    # ====== Precompute instance ↔ module maps, and per-module ports =======
    ports_by_mod: Dict[str, List[Dict]] = {}
    for mod in cfg.modules:
        ports_by_mod[mod.name] = _fetch_module_ports(mod, ip_info[mod.name], ip_root)

    inst_to_mod: Dict[str, str] = {}
    for mod in cfg.modules:
        for i in range(mod.instances):
            inst_to_mod[_inst(mod, i)] = mod.name

    def _pin_width_for_inst(inst: str, pin: str) -> int:
        mod = inst_to_mod[inst]
        for p in ports_by_mod[mod]:
            if p["name"] == pin:
                return int(p["width"])
        return 1

    # ====== Entity =========================================================
    entity_ports: List[str] = []
    declared_names: Set[str] = set()

    if cfg.connect_clock:
        entity_ports.append("ap_clk : in std_logic")
        declared_names.add("ap_clk")
    if cfg.connect_reset:
        entity_ports.append("ap_rst : in std_logic")
        declared_names.add("ap_rst")

    # Add ports for any other global nets (besides clock/reset)
    for gnet_name, binds in global_nets.items():
        # Skip clock and reset as they're handled above
        if gnet_name in ("ap_clk", "clk", "clock", "ap_rst", "rst", "reset", "rst_n"):
            continue
        if gnet_name not in declared_names and binds:
            # All instances of this signal should be inputs (global signals drive modules)
            entity_ports.append(f"{gnet_name} : in std_logic")
            declared_names.add(gnet_name)

    # Emit top-level ports.
    # If (inst,pin) has an alias from system.yml, use the framework name; otherwise use <inst>_<pin>.
    for mod in cfg.modules:
        for p in ports_by_mod[mod.name]:
            pname, pdir, w = p["name"], p["dir"], int(p["width"])

            # skip any clock/reset-ish externals if top provides global clk/rst
            if cfg.connect_clock and (pname in ("clk", "clock", "ap_clk") or pname.endswith("_clk") or pname.endswith("_ap_clk")):
                continue
            if cfg.connect_reset and (pname in ("rst", "reset", "ap_rst", "rst_n") or pname.endswith("_rst") or pname.endswith("_ap_rst") or pname.endswith("_rst_n")):
                continue
            # skip any other global nets (e.g., new_event)
            if pname in global_nets:
                continue

            for i in range(mod.instances):
                ilabel = _inst(mod, i)

                # system.yml alias takes precedence for externalization
                ext_dir = None
                if (ilabel, pname) in alias_in:
                    ext_dir = "in"
                    top_port_name = alias_in[(ilabel, pname)]
                elif (ilabel, pname) in alias_out:
                    ext_dir = "out"
                    top_port_name = alias_out[(ilabel, pname)]
                else:
                    # fall back to user-declared externals
                    ext_dir = _is_user_external(mod, pname, pdir)
                    if ext_dir:
                        top_port_name = f"{ilabel}_{pname}"

                if not ext_dir:
                    continue

                # avoid duplicates if a name somehow repeats
                if top_port_name in declared_names:
                    continue
                entity_ports.append(f"{top_port_name} : {ext_dir} {_vtype(w)}")
                declared_names.add(top_port_name)

    emit(f"entity {top_name} is")
    if entity_ports:
        emit("  port (")
        emit("    " + (";" + NL + "    ").join(entity_ports))
        emit("  );")
    else:
        emit("  -- no external data ports")
    emit(f"end entity {top_name};")
    emit("")

    emit(f"architecture structural of {top_name} is")
    emit("")

    # ====== Component decls (per module kind) =============================
    declared: Set[str] = set()
    for mod in cfg.modules:
        if mod.name in declared:
            continue
        meta = ip_info[mod.name]
        entity_name = _entity_name_for_component(mod, meta)
        emit(f"  component {entity_name} is")
        emit("    port (")
        arr = [f"      {p['name']} : {p['dir']} {_vtype(int(p['width']))}"
               for p in ports_by_mod[mod.name]]
        emit(";\n".join(arr))
        emit("    );")
        emit(f"  end component;")
        emit("")
        declared.add(mod.name)

    # ====== Internal driver nets (one per (src_inst, s_pin) that drives) ==
    emit("  -- driver nets (one per driving output pin per instance)")
    driver_net_set: Set[str] = set()
    for (src_i, _dst_i), pairs in conn_map.items():
        src_mod = inst_to_mod[src_i]
        for (s_pin_raw, _d_pin_raw) in pairs:
            s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
            w = _pin_width_for_inst(src_i, s_pin)
            raw_net = f"net_{src_i}_{s_pin}"
            net = _vhdl_ident(raw_net)
            if net not in driver_net_set:
                emit(f"  signal {net} : {_vtype(w)};")
                driver_net_set.add(net)
    emit("")
    emit("begin")
    emit("")

    # ====== Instances + port maps ========================================
    for mod in cfg.modules:
        meta = ip_info[mod.name]
        entity_name = _entity_name_for_component(mod, meta)
        has_generics = "generics" in meta and meta["generics"]
        use_entity_inst = has_generics and _is_hdl(meta)  # Use entity instantiation for HDL with generics

        for i in range(mod.instances):
            ilabel = _inst(mod, i)
            emit(f"  -- {mod.name} instance {i+1}/{mod.instances}")
            
            # Determine if we need to compute generic values (e.g., input width)
            generic_map: Dict[str, str] = {}
            if use_entity_inst and has_generics:
                # For each generic, try to determine its value from connections
                for gen_name, gen_info in meta["generics"].items():
                    default = gen_info.get("default", "")
                    
                    # Special handling for width generics like IN_W
                    if gen_name in ("IN_W", "INPUT_WIDTH", "DATA_WIDTH") and gen_info.get("type") in ("positive", "natural", "integer"):
                        # Find the actual width of the connected input signal
                        actual_width = None
                        for (src_i, dst_i), pairs in conn_map.items():
                            if dst_i == ilabel:
                                for s_pin_raw, d_pin_raw in pairs:
                                    d_pin = _canon_pin(ip_info, mod.name, d_pin_raw)
                                    if d_pin in ("din", "data_in", "din_0"):  # Common input port names
                                        src_mod = inst_to_mod[src_i]
                                        s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                                        actual_width = _pin_width_for_inst(src_i, s_pin)
                                        break
                            if actual_width:
                                break
                        if actual_width:
                            generic_map[gen_name] = str(actual_width)
                        else:
                            generic_map[gen_name] = default
                    else:
                        # Use default for other generics
                        generic_map[gen_name] = default
            
            # Generate instance statement
            if use_entity_inst:
                emit(f"  {ilabel} : entity work.{entity_name}")
                if generic_map:
                    emit("    generic map (")
                    gm_lines = []
                    for gname, gval in generic_map.items():
                        gen_info = meta["generics"][gname]
                        # Quote string values, keep numbers and booleans unquoted
                        if gen_info.get("type") == "string":
                            gm_lines.append(f'      {gname} => "{gval}"')
                        else:
                            gm_lines.append(f"      {gname} => {gval}")
                    emit(",\n".join(gm_lines))
                    emit("    )")
                emit("    port map (")
            else:
                emit(f"  {ilabel} : {entity_name}")
                emit("    port map (")
            
            pm = []

            # Build maps from pins to their driver instance for *this* sink instance
            incoming: Dict[str, str] = {}
            for (src_i, dst_i), pairs in conn_map.items():
                if dst_i != ilabel:
                    continue
                src_mod = inst_to_mod[src_i]
                dst_mod = mod.name
                for s_pin_raw, d_pin_raw in pairs:
                    d_pin = _canon_pin(ip_info, dst_mod, d_pin_raw)
                    if d_pin not in incoming:   # first-driver-wins ensured upstream
                        incoming[d_pin] = src_i

            # Build a set of driven source pins for this instance
            driven_outs: Set[str] = set()
            for (src_i, _dst_i), pairs in conn_map.items():
                if src_i != ilabel:
                    continue
                src_mod = mod.name
                for s_pin_raw, _ in pairs:
                    s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                    driven_outs.add(s_pin)

            for p in ports_by_mod[mod.name]:
                pname, pdir, w = p["name"], p["dir"], int(p["width"])

                # clock/reset auto-map (support various naming conventions)
                if cfg.connect_clock and (pname in ("clk", "clock", "ap_clk") or pname.endswith("_clk") or pname.endswith("_ap_clk")):
                    pm.append("      " + pname + " => ap_clk"); continue
                if cfg.connect_reset and (pname in ("rst", "reset", "ap_rst", "rst_n") or pname.endswith("_rst") or pname.endswith("_ap_rst") or pname.endswith("_rst_n")):
                    pm.append("      " + pname + " => ap_rst"); continue

                # other global nets (e.g., new_event)
                if pname in global_nets:
                    pm.append(f"      {pname} => {pname}"); continue

                # External (system alias or user-declared)?
                if (ilabel, pname) in alias_in:
                    pm.append(f"      {pname} => {alias_in[(ilabel, pname)]}")
                    continue
                if (ilabel, pname) in alias_out:
                    pm.append(f"      {pname} => {alias_out[(ilabel, pname)]}")
                    continue
                user_ext_dir = _is_user_external(mod, pname, pdir)
                if user_ext_dir:
                    pm.append(f"      {pname} => {ilabel}_{pname}")
                    continue

                if pdir == "out":
                    if pname in driven_outs:
                        pm.append(f"      {pname} => {_vhdl_ident(f'net_{ilabel}_{pname}')}")
                    else:
                        pm.append(f"      {pname} => open")
                        open_outputs.append((ilabel, pname, w))
                else:  # input
                    src_i = incoming.get(pname)
                    if src_i:
                        src_mod = inst_to_mod[src_i]
                        src_pin: Optional[str] = None
                        for (s_i, d_i), pairs in conn_map.items():
                            if s_i == src_i and d_i == ilabel:
                                for s_raw, d_raw in pairs:
                                    if _canon_pin(ip_info, mod.name, d_raw) == pname:
                                        src_pin = _canon_pin(ip_info, src_mod, s_raw)
                                        break
                                if src_pin:
                                    break
                        if not src_pin:
                            src_pin = pname
                        pm.append(f"      {pname} => {_vhdl_ident(f'net_{src_i}_{src_pin}')}")
                    else:
                        pm.append(f"      {pname} => (others=>'0')" if w > 1 else f"      {pname} => '0'")
                        tied_to_zero.append((ilabel, pname, w))

            emit(",\n".join(pm))
            emit("    );")
            emit("")

    emit(f"end architecture structural;")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(NL.join(lines))

    # ========== Generate Report ==========
    report = {
        "open_outputs": open_outputs,
        "tied_to_zero": tied_to_zero,
        "total_modules": len(cfg.modules),
        "total_instances": sum(m.instances for m in cfg.modules),
        "total_connections": sum(len(pairs) for pairs in conn_map.values()),
    }
    
    return report
