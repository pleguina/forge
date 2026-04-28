from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, List, Tuple, Set

import yaml  # To read system.yml

from ..config import DesignConfig, Module, resolve_declared_path
from ..ip.matcher import load_ip_info, auto_match_ports
from arc.core.utils.hdl_parser import _scan_ports as scan_vhdl_ports
from arc.core.utils.hdl_parser import _scan_verilog_ports as scan_vlog_ports
from arc.core.utils.hdl_parser import _vhdl_entity_name, _verilog_module_name

# ----------------------------- Verilog helpers ------------------------------

_VERILOG_KEYWORDS = {
    "always", "and", "assign", "automatic", "begin", "buf", "bufif0", "bufif1",
    "case", "casex", "casez", "cell", "cmos", "config", "deassign", "default",
    "defparam", "design", "disable", "edge", "else", "end", "endcase", "endconfig",
    "endfunction", "endgenerate", "endmodule", "endprimitive", "endspecify",
    "endtable", "endtask", "event", "for", "force", "forever", "fork", "function",
    "generate", "genvar", "highz0", "highz1", "if", "ifnone", "incdir", "include",
    "initial", "inout", "input", "instance", "integer", "join", "large", "liblist",
    "library", "localparam", "macromodule", "medium", "module", "nand", "negedge",
    "nmos", "nor", "noshowcancelled", "not", "notif0", "notif1", "or", "output",
    "parameter", "pmos", "posedge", "primitive", "pull0", "pull1", "pulldown",
    "pullup", "pulsestyle_onevent", "pulsestyle_ondetect", "rcmos", "real",
    "realtime", "reg", "release", "repeat", "rnmos", "rpmos", "rtran", "rtranif0",
    "rtranif1", "scalared", "showcancelled", "signed", "small", "specify",
    "specparam", "strong0", "strong1", "supply0", "supply1", "table", "task",
    "time", "tran", "tranif0", "tranif1", "tri", "tri0", "tri1", "triand",
    "trior", "trireg", "unsigned", "use", "uwire", "vectored", "wait", "wand",
    "weak0", "weak1", "while", "wire", "wor", "xnor", "xor"
}

_id_re = re.compile(r"[^A-Za-z0-9_]+")
_re_trailing_num = re.compile(r"^(.*?)(?:_(\d+)|(\d+))$")

def _verilog_ident(s: str) -> str:
    """
    Make a legal Verilog identifier:
      - replace non [A-Za-z0-9_] with '_'
      - collapse multiple underscores
      - strip leading/trailing underscores
      - ensure first char is a letter by prefixing 'v_'
      - avoid reserved words by prefixing 'v_'
    """
    if not s:
        return "v_"
    s = _id_re.sub("_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    if not s or not s[0].isalpha():
        s = "v_" + s
    if s.lower() in _VERILOG_KEYWORDS:
        s = "v_" + s
    return s

def _base_of(name: str) -> str:
    m = _re_trailing_num.match(name)
    return m.group(1) if m else name

def _inst(mod: Module, idx: int) -> str:
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"

def _vtype(width: int) -> str:
    """Return Verilog type string: wire for 1-bit, wire [N-1:0] for multi-bit."""
    return "wire" if width == 1 else f"wire [{width-1}:0]"

def _port_decl(name: str, direction: str, width: int) -> str:
    """Return Verilog port declaration."""
    if width == 1:
        return f"{direction} {name}"
    else:
        return f"{direction} [{width-1}:0] {name}"

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
        # Verilog uses 'input'/'output' instead of 'in'/'out'
        if dirn == "in":
            dirn = "input"
        elif dirn == "out":
            dirn = "output"
        out.append({"name": name, "dir": dirn, "width": width})
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
        # Convert direction format
        result = []
        for n, (d, w) in scanned.items():
            if d == "in":
                d = "input"
            elif d == "out":
                d = "output"
            result.append({"name": n, "dir": d, "width": int(w)})
        return result

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
    # Add packages FIRST
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

    # Add package files FIRST
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
    If it's an HLS IP, use mod.top (the actual Verilog module name).
    If it's a catalog IP, use a safe entity name that differs from the
    catalog 'name' (to avoid Vivado Common 17-69).
    """
    if _is_hdl(meta):  # pure RTL
        return meta.get("entity") or mod.top or mod.name
    # For HLS modules, use mod.top directly (the actual Verilog module name)
    if mod.kind == 'hls':
        return mod.top
    return _safe_ip_entity_name(mod, meta)

# ------------------------------ generator ---------------------------------

def write_structural_verilog(
    cfg: DesignConfig,
    ip_info: Dict[str, Dict],
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],   # (src_inst, dst_inst) → [(s_pin, d_pin)]
    global_nets: Dict[str, List[Tuple[str, str]]],
    ip_root: Path,
    out_path: Path,
    *,
    top_name: str = "algo_top",
    system_yml: Path | None = None,          # framework awareness
    contracts: Dict | None = None,           # loaded interface contracts (for clock_free)
) -> Dict[str, any]:
    """
    Generate a structural Verilog top that wires algorithm modules together.

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
    external_output_bindings: Dict[Tuple[str, str], str] = {}

    # Build maps for register_stages and delay_cycles from cfg.connections
    reg_stages_map: Dict[Tuple[str, str], int] = {}
    delay_cycles_map: Dict[Tuple[str, str], int] = {}
    for conn in cfg.connections:
        src_mod = next(m for m in cfg.modules if m.name == conn.from_)
        dst_mod = next(m for m in cfg.modules if m.name == conn.to)
        # For each instance combination, store register stages and delay cycles
        for src_idx in range(src_mod.instances):
            for dst_idx in range(dst_mod.instances):
                src_inst = _inst(src_mod, src_idx)
                dst_inst = _inst(dst_mod, dst_idx)
                if conn.register_stages > 0:
                    reg_stages_map[(src_inst, dst_inst)] = conn.register_stages
                if conn.delay_cycles > 0:
                    delay_cycles_map[(src_inst, dst_inst)] = conn.delay_cycles

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

    def _is_user_external(mod: Module, pname: str) -> str | None:
        """
        Check user-declared external_in_ports / external_out_ports on a *name or prefix* basis.
        Returns 'input' / 'output' / None.
        """
        user_in  = tuple(mod.external_in_ports or [])
        user_out = tuple(mod.external_out_ports or [])
        if any(pname == x or pname.startswith(x + "_") for x in user_in):
            return "input"
        if any(pname == x or pname.startswith(x + "_") for x in user_out):
            return "output"
        return None

    # ---------- read system.yml → alias maps ------------------------------
    alias_in, alias_out = _parse_system_aliases(system_yml)

    # ---------- tracking for report ---------------------------------------
    unconnected_inputs = []   # [(instance, port, width)]
    unconnected_outputs = []  # [(instance, port, width)]
    open_outputs = []         # [(instance, port, width)]
    tied_to_zero = []        # [(instance, port, width)]

    emit("// ------------------------------------------------------------")
    emit("//  Auto-generated by topgen – structural Verilog top")
    emit("//  DO NOT EDIT BY HAND")
    emit("// ------------------------------------------------------------")
    emit("")

    # ====== Precompute instance ↔ module maps, and per-module ports =======
    ports_by_mod: Dict[str, List[Dict]] = {}
    for mod in cfg.modules:
        ports_by_mod[mod.name] = _fetch_module_ports(mod, ip_info[mod.name], ip_root)

    inst_to_mod: Dict[str, str] = {}
    inst_to_params: Dict[str, Dict[str, Any]] = {}  # Track parameters per instance
    for mod in cfg.modules:
        for i in range(mod.instances):
            ilabel = _inst(mod, i)
            inst_to_mod[ilabel] = mod.name
            inst_to_params[ilabel] = mod.parameters  # Store parameters for this instance

    def _pin_width_for_inst(inst: str, pin: str) -> int:
        """
        Return the width of a pin for a given instance, considering module parameters.
        For parameterized modules like signal_delay, uses WIDTH parameter if available.
        """
        mod = inst_to_mod[inst]
        params = inst_to_params.get(inst, {})
        
        # For parameterized width ports (din/dout in signal_delay), use WIDTH parameter
        if pin in ("din", "dout") and "WIDTH" in params:
            return int(params["WIDTH"])
        
        # Otherwise look up in port info
        for p in ports_by_mod[mod]:
            if p["name"] == pin:
                return int(p["width"])
        return 1

    # ====== Module declaration =============================================
    module_ports: List[str] = []
    declared_names: Set[str] = set()

    if cfg.connect_clock:
        module_ports.append("input ap_clk")
        declared_names.add("ap_clk")
    if cfg.connect_reset:
        module_ports.append("input ap_rst")
        declared_names.add("ap_rst")

    # Add control signals as inputs (only if not generated internally)
    # Check if any module outputs this control signal
    control_signal_sources = {}  # sig_name -> module that outputs it
    for mod in cfg.modules:
        for p in ports_by_mod[mod.name]:
            if p["dir"] == "output" and p["name"] in cfg.control_signals:
                control_signal_sources[p["name"]] = mod.name
    
    for sig_name, sig_config in cfg.control_signals.items():
        # Skip if this signal is generated by a module (not a top-level input)
        if sig_name in control_signal_sources:
            continue
        if sig_name not in declared_names:
            if sig_config.width == 1:
                module_ports.append(f"input {sig_name}")
            else:
                module_ports.append(f"input [{sig_config.width-1}:0] {sig_name}")
            declared_names.add(sig_name)

    # Add ports for any other global nets (besides clock/reset)
    for gnet_name, binds in global_nets.items():
        # Skip clock and reset as they're handled above
        if gnet_name in ("ap_clk", "clk", "clock", "ap_rst", "rst", "reset", "rst_n"):
            continue
        if gnet_name not in declared_names and binds:
            # All instances of this signal should be inputs (global signals drive modules)
            module_ports.append(f"input {gnet_name}")
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
                    ext_dir = "input"
                    top_port_name = alias_in[(ilabel, pname)]
                elif (ilabel, pname) in alias_out:
                    ext_dir = "output"
                    top_port_name = alias_out[(ilabel, pname)]
                else:
                    # fall back to user-declared externals
                    ext_dir = _is_user_external(mod, pname)
                    if ext_dir:
                        top_port_name = f"{ilabel}_{pname}"

                if not ext_dir:
                    continue

                if ext_dir == "output":
                    external_output_bindings[(ilabel, pname)] = top_port_name

                # avoid duplicates if a name somehow repeats
                if top_port_name in declared_names:
                    continue
                module_ports.append(_port_decl(top_port_name, ext_dir, w))
                declared_names.add(top_port_name)

    # Add DEBUG ports - expose ALL ports of modules marked with debug: true
    for mod in cfg.modules:
        if not mod.debug:
            continue
        
        for p in ports_by_mod[mod.name]:
            pname, pdir, w = p["name"], p["dir"], int(p["width"])
            
            # Skip clock/reset
            if cfg.connect_clock and pname in ("clk", "clock", "ap_clk"):
                continue
            if cfg.connect_reset and pname in ("rst", "reset", "ap_rst", "rst_n"):
                continue
            
            # Expose each instance's port as debug port
            for i in range(mod.instances):
                ilabel = _inst(mod, i)
                debug_port_name = f"debug_{ilabel}_{pname}"
                
                if debug_port_name in declared_names:
                    continue
                
                # Debug ports are outputs (so we can monitor them)
                module_ports.append(_port_decl(debug_port_name, "output", w))
                declared_names.add(debug_port_name)

    emit(f"module {top_name} (")
    if module_ports:
        for i, port in enumerate(module_ports):
            if i < len(module_ports) - 1:
                emit(f"  {port},")
            else:
                emit(f"  {port}")
    emit(");")
    emit("")

    # ====== Internal wire declarations ====================================
    emit("  // Internal wires (one per driving output pin per instance)")
    driver_net_set: Set[str] = set()
    reg_stage_nets: Set[str] = set()  # Wires for register stage outputs
    delay_nets: Set[str] = set()  # Wires for signal_delay outputs

    for (src_i, dst_i), pairs in conn_map.items():
        src_mod = inst_to_mod[src_i]
        num_stages = reg_stages_map.get((src_i, dst_i), 0)
        num_delays = delay_cycles_map.get((src_i, dst_i), 0)

        for (s_pin_raw, _d_pin_raw) in pairs:
            s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
            w = _pin_width_for_inst(src_i, s_pin)

            # Create wire for source output
            raw_net = f"net_{src_i}_{s_pin}"
            net = _verilog_ident(raw_net)
            if net not in driver_net_set:
                if w == 1:
                    emit(f"  wire {net};")
                else:
                    emit(f"  wire [{w-1}:0] {net};")
                driver_net_set.add(net)

            # If register stages needed, create intermediate wire for registered output
            if num_stages > 0:
                reg_net = _verilog_ident(f"reg_net_{src_i}_{dst_i}_{s_pin}")
                if reg_net not in reg_stage_nets:
                    if w == 1:
                        emit(f"  wire {reg_net};")
                    else:
                        emit(f"  wire [{w-1}:0] {reg_net};")
                    reg_stage_nets.add(reg_net)

            # If delay cycles needed, create intermediate wire for delayed output
            if num_delays > 0:
                delay_net = _verilog_ident(f"delay_net_{src_i}_{dst_i}_{s_pin}")
                if delay_net not in delay_nets:
                    if w == 1:
                        emit(f"  wire {delay_net};")
                    else:
                        emit(f"  wire [{w-1}:0] {delay_net};")
                    delay_nets.add(delay_net)

    for (ilabel, pname), _top_port_name in external_output_bindings.items():
        raw_net = f"net_{ilabel}_{pname}"
        net = _verilog_ident(raw_net)
        if net in driver_net_set:
            continue
        w = _pin_width_for_inst(ilabel, pname)
        if w == 1:
            emit(f"  wire {net};")
        else:
            emit(f"  wire [{w-1}:0] {net};")
        driver_net_set.add(net)
    emit("")

    # ====== Instances =====================================================
    for mod in cfg.modules:
        meta = ip_info[mod.name]
        entity_name = _entity_name_for_component(mod, meta)

        for i in range(mod.instances):
            ilabel = _inst(mod, i)
            emit(f"  // {mod.name} instance {i+1}/{mod.instances}")
            
            # Add parameter instantiation if module has parameters
            if mod.parameters:
                param_strs = [f".{k}({v})" for k, v in mod.parameters.items()]
                param_list = ", ".join(param_strs)
                emit(f"  {entity_name} #({param_list}) {ilabel} (")
            else:
                emit(f"  {entity_name} {ilabel} (")

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
                    # Skip ap_clk connection for clock-free (combinatorial) modules
                    _contract = (contracts or {}).get(mod.name)
                    if _contract and _contract.clock_free:
                        continue
                    pm.append(f"    .{pname}(ap_clk)")
                    continue
                if cfg.connect_reset and (pname in ("rst", "reset", "ap_rst", "rst_n") or pname.endswith("_rst") or pname.endswith("_ap_rst") or pname.endswith("_rst_n")):
                    pm.append(f"    .{pname}(ap_rst)")
                    continue

                # other global nets (e.g., new_event) - use delayed version if control signal
                if pdir != "output" and pname in global_nets:
                    # Check if this is a control signal with delayed distribution
                    signal_name = None
                    for sig_name, sig_config in cfg.control_signals.items():
                        if sig_name == pname:
                            # Find if this module has a delayed version
                            for target in sig_config.distribution:
                                if target.module == mod.name:
                                    signal_name = _verilog_ident(f"{sig_name}_{target.module}")
                                    break
                            break
                    if signal_name:
                        pm.append(f"    .{pname}({signal_name})")
                    else:
                        pm.append(f"    .{pname}({pname})")
                    continue

                # External (system alias or user-declared)?
                if (ilabel, pname) in alias_in:
                    pm.append(f"    .{pname}({alias_in[(ilabel, pname)]})")
                    continue
                if (ilabel, pname) in alias_out:
                    pm.append(f"    .{pname}({_verilog_ident(f'net_{ilabel}_{pname}')})")
                    continue
                user_ext_dir = _is_user_external(mod, pname)
                if user_ext_dir:
                    if user_ext_dir == "output":
                        pm.append(f"    .{pname}({_verilog_ident(f'net_{ilabel}_{pname}')})")
                    else:
                        pm.append(f"    .{pname}({ilabel}_{pname})")
                    continue

                if pdir == "output":
                    if pname in driven_outs:
                        pm.append(f"    .{pname}({_verilog_ident(f'net_{ilabel}_{pname}')})")
                    else:
                        pm.append(f"    .{pname}()")  # leave unconnected
                        open_outputs.append((ilabel, pname, w))
                else:  # input
                    src_i = incoming.get(pname)
                    if src_i:
                        src_mod = inst_to_mod[src_i]
                        src_pin = None
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

                        # Check if delay cycles or register stages exist between src and dst
                        num_delays = delay_cycles_map.get((src_i, ilabel), 0)
                        num_stages = reg_stages_map.get((src_i, ilabel), 0)
                        if num_delays > 0:
                            # Connect to delayed output
                            delay_net = _verilog_ident(f"delay_net_{src_i}_{ilabel}_{src_pin}")
                            pm.append(f"    .{pname}({delay_net})")
                        elif num_stages > 0:
                            # Connect to registered output
                            reg_net = _verilog_ident(f"reg_net_{src_i}_{ilabel}_{src_pin}")
                            pm.append(f"    .{pname}({reg_net})")
                        else:
                            # Direct connection
                            pm.append(f"    .{pname}({_verilog_ident(f'net_{src_i}_{src_pin}')})")
                    else:
                        # Tie to zero
                        if w > 1:
                            pm.append(f"    .{pname}({w}'d0)")
                        else:
                            pm.append(f"    .{pname}(1'b0)")
                        tied_to_zero.append((ilabel, pname, w))

            # Join port mappings with commas
            for j, port_map in enumerate(pm):
                if j < len(pm) - 1:
                    emit(f"{port_map},")
                else:
                    emit(f"{port_map}")
            emit("  );")
            emit("")
            
            # Add DEBUG port assignments if this module has debug enabled
            if mod.debug:
                for p in ports_by_mod[mod.name]:
                    pname, pdir, w = p["name"], p["dir"], int(p["width"])
                    
                    # Skip clock/reset
                    if cfg.connect_clock and (pname in ("clk", "clock", "ap_clk") or pname.endswith("_clk") or pname.endswith("_ap_clk")):
                        continue
                    if cfg.connect_reset and (pname in ("rst", "reset", "ap_rst", "rst_n") or pname.endswith("_rst") or pname.endswith("_ap_rst") or pname.endswith("_rst_n")):
                        continue
                    
                    # Assign debug port from this instance's port
                    debug_port_name = f"debug_{ilabel}_{pname}"
                    emit(f"  assign {debug_port_name} = {ilabel}.{pname};")
                emit("")  # Blank line after debug assignments

    if external_output_bindings:
        emit("  // Top-level bindings for exported producer outputs")
        for (ilabel, pname), top_port_name in external_output_bindings.items():
            emit(f"  assign {top_port_name} = {_verilog_ident(f'net_{ilabel}_{pname}')};")
        emit("")

    # ====== Register Stage Instances ======================================
    emit("  // Register stages for pipelined connections")
    reg_stage_counter = 0
    for (src_i, dst_i), pairs in conn_map.items():
        num_stages = reg_stages_map.get((src_i, dst_i), 0)
        if num_stages > 0:
            src_mod = inst_to_mod[src_i]
            for s_pin_raw, _d_pin_raw in pairs:
                s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                w = _pin_width_for_inst(src_i, s_pin)

                src_net = _verilog_ident(f"net_{src_i}_{s_pin}")
                dst_net = _verilog_ident(f"reg_net_{src_i}_{dst_i}_{s_pin}")
                inst_name = f"reg_stage_{reg_stage_counter}"

                emit(f"  // Pipeline {src_i}.{s_pin} → {dst_i} ({num_stages} stages)")
                emit(f"  RegisterStage #(")
                emit(f"    .DATAWIDTH({w}),")
                emit(f"    .STAGES({num_stages})")
                emit(f"  ) {inst_name} (")
                emit(f"    .clk(ap_clk),")
                emit(f"    .data_in({src_net}),")
                emit(f"    .data_out({dst_net})")
                emit(f"  );")
                emit("")

                reg_stage_counter += 1

    # ====== Signal Delay Instances (connection delays) ====================
    emit("  // Signal delays for connection timing alignment")
    delay_counter = 0
    for (src_i, dst_i), pairs in conn_map.items():
        num_delays = delay_cycles_map.get((src_i, dst_i), 0)
        if num_delays > 0:
            src_mod = inst_to_mod[src_i]
            for s_pin_raw, _d_pin_raw in pairs:
                s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                w = _pin_width_for_inst(src_i, s_pin)

                src_net = _verilog_ident(f"net_{src_i}_{s_pin}")
                dst_net = _verilog_ident(f"delay_net_{src_i}_{dst_i}_{s_pin}")
                inst_name = f"delay_{delay_counter}"

                emit(f"  // Delay {src_i}.{s_pin} → {dst_i} (+{num_delays} cycles)")
                emit(f"  signal_delay #(")
                emit(f"    .WIDTH({w}),")
                emit(f"    .DEPTH({num_delays})")
                emit(f"  ) {inst_name} (")
                emit(f"    .clk(ap_clk),")
                emit(f"    .rst(ap_rst),")
                emit(f"    .din({src_net}),")
                emit(f"    .dout({dst_net})")
                emit(f"  );")
                emit("")

                delay_counter += 1

    # ====== Control Signal Distribution (NEW) =============================
    emit("  // Control signal distribution with delays")
    for sig_name, sig_config in cfg.control_signals.items():
        for target in sig_config.distribution:
            delayed_sig = _verilog_ident(f"{sig_name}_{target.module}")
            inst_name = f"delay_{delay_counter}"

            emit(f"  // {sig_name} → {target.module} (+{target.delay_cycles} cycles)")
            emit(f"  signal_delay #(")
            emit(f"    .WIDTH({sig_config.width}),")
            emit(f"    .DEPTH({target.delay_cycles})")
            emit(f"  ) {inst_name} (")
            emit(f"    .clk(ap_clk),")
            emit(f"    .rst(ap_rst),")
            emit(f"    .din({sig_name}),")
            emit(f"    .dout({delayed_sig})")
            emit(f"  );")
            emit("")

            delay_counter += 1

    # ====== Output Alignment Delays (NEW) =================================
    emit("  // Output alignment delays")
    for mod in cfg.modules:
        if mod.output_delays:
            for port_name, cycles in mod.output_delays.items():
                for i in range(mod.instances):
                    ilabel = _inst(mod, i)
                    src_net = _verilog_ident(f"net_{ilabel}_{port_name}")
                    dst_port = f"{ilabel}_{port_name}"
                    inst_name = f"delay_{delay_counter}"

                    # Try to get port width
                    w = 1
                    for p in ports_by_mod.get(mod.name, []):
                        if p["name"] == port_name:
                            w = int(p["width"])
                            break

                    emit(f"  // Align {ilabel}.{port_name} output (+{cycles} cycles)")
                    emit(f"  signal_delay #(")
                    emit(f"    .WIDTH({w}),")
                    emit(f"    .DEPTH({cycles})")
                    emit(f"  ) {inst_name} (")
                    emit(f"    .clk(ap_clk),")
                    emit(f"    .rst(ap_rst),")
                    emit(f"    .din({src_net}),")
                    emit(f"    .dout({dst_port})")
                    emit(f"  );")
                    emit("")

                    delay_counter += 1

    emit("endmodule")

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
