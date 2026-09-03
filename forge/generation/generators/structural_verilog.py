from __future__ import annotations

from forge.core.utils.signal_names import is_clock_name, is_reset_name
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

import yaml  # To read system.yml

from ...contracts.config import DesignConfig, Module, resolve_declared_path
from ...contracts.domains import resolve_domain_nets
from ...contracts.matcher import load_ip_info, auto_match_ports
from forge.core.utils.hdl_parser import _scan_ports as scan_vhdl_ports
from forge.core.utils.hdl_parser import _scan_verilog_ports as scan_vlog_ports
from forge.core.utils.hdl_parser import _vhdl_entity_name, _verilog_module_name

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

#: The clock/reset names this generator has always collapsed onto the single
#: ``ap_clk``/``ap_rst`` top-level net. Kept exactly as it was, because a
#: name *outside* it that the design declared as its own domain (``clk_b``)
#: must keep its own net — that distinction is load-bearing for CDC.
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

    Three cases, and the third is why this exists:

    1. A **standard** name (``clk``, ``ap_clk``, ``*_clk``) always collapses
       onto ``ap_clk``/``ap_rst``, even when the design also lists it as a
       domain — that is what ``connect_clock``/``connect_reset`` mean.
    2. A **non-standard** name the design declared as its own domain
       (``clk_b``) keeps its own top-level net. Collapsing it would merge
       two domains the design went out of its way to separate, and CDC
       synchronizer emission resolves through this same rule so the
       synchronizer and the instance pin can never disagree.
    3. A name that reads as a clock or reset by the shared convention but
       is **neither** standard nor a declared domain — ``pclk``,
       ``presetn``, ``s_axi_aclk`` — collapses onto the global net. Before
       this case existed it matched nothing at all and fell through to the
       unconnected-input path: an APB peripheral's clock was tied to
       ``1'b0``, in a top level that elaborates perfectly and does nothing.
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
    — see forge.contracts.domains.resolve_domain_nets) into the actual
    top-level net name this generator wires it to.

    This generator's own clock/reset auto-map (the per-instance port-
    mapping loop below) collapses every *standard*-name variant
    (``clk``/``clock``/``ap_clk``, or anything ending ``_clk``/``_ap_clk``
    for clocks; ``rst``/``reset``/``ap_rst``/``rst_n``, or anything ending
    ``_rst``/``_ap_rst``/``_rst_n`` for resets) onto the single literal
    ``ap_clk``/``ap_rst`` top-level port, regardless of which specific
    variant string a module's contract declared. A domain with its own
    declared global net (``clk_b``, a named second clock domain) keeps that
    net instead — pass those names as *own_nets*, and they win over the
    collapse.

    Used by CDC synchronizer emission to reference the net that actually
    exists in the generated file, not the raw domain identity string, so it
    must resolve exactly as the per-instance port mapping does.

    What counts as a clock or reset name is
    ``forge.core.utils.signal_names``' shared convention, which recognises
    the fused bus spellings (``pclk``, ``aclk``, ``presetn``, ``aresetn``)
    the hand-written rule this replaced did not — a peripheral design's
    ``pclk`` matched nothing and was tied to ``1'b0``.
    """
    if not _collapses_to_global(domain_name, is_clock=is_clock, own_nets=own_nets):
        return domain_name
    return "ap_clk" if is_clock else "ap_rst"

def _reset_net_for_domain(
    domain_name: "str | None",
    reset_sync_domains: Dict[str, Dict[str, Any]],
    own_nets: "Set[str] | Tuple[str, ...]" = (),
) -> str:
    """Resolve a module's reset domain to the net that actually carries a
    real, synchronized reset.

    A `reset_domains.<name>.sync: reset_sync` domain's real reset signal
    is `rst_sync_<name>` — the cdc_reset_sync instance's own output — not
    the raw top-level `<name>` port (which is never driven by anything
    once a real synchronizer exists for it; see design_cdc.yml's own
    header for why that top-level port is intentionally left
    unconnected/dangling). Every OTHER consumer of a resolved reset
    domain (a CDC synchronizer's own src_rst/dst_rst, and a member
    instance's own reset pin) must resolve through this same rule, not
    just `_domain_to_top_level_net` alone — using the raw domain net
    instead is a real, silent miscompile (a module/synchronizer reads a
    permanently-unasserted reset and its registers stay X forever),
    found empirically wiring vision_pipeline_demo's design_cdc.yml.
    """
    if domain_name and domain_name in reset_sync_domains:
        return _verilog_ident(f"rst_sync_{domain_name}")
    if not domain_name:
        return "ap_rst"
    return _domain_to_top_level_net(domain_name, is_clock=False, own_nets=own_nets)

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


# ---------------------------------------------------------------------------
# SLR boundary crossing helpers
# ---------------------------------------------------------------------------

def _verilog_id_frag(text: str) -> str:
    """Return a deterministic Verilog-safe identifier fragment from *text*."""
    out = []
    for ch in text:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    clean = "".join(out).strip("_")
    if not clean:
        clean = "unnamed"
    if clean[0].isdigit():
        clean = "n_" + clean
    return clean


def _boundary_inst_name(tag: str, src_pin: str, dst_pin: str) -> str:
    """Stable, deterministic Verilog instance name for a boundary crossing FF."""
    return f"bdry_{_verilog_id_frag(tag)}_{_verilog_id_frag(src_pin)}_to_{_verilog_id_frag(dst_pin)}"


def _stage_patterns_for_instance(hier: str, depth: int) -> List[Dict[str, Any]]:
    """Return the stage-level cell-pattern entries for a given hierarchy and depth.

    Vivado synthesis flattens Verilog generate blocks: a named generate block
    ``begin : gen_depth2`` does NOT create a ``/gen_depth2/`` sub-hierarchy.
    Instead the generate-block name becomes a dot-prefix on the signal name, and
    the tool appends ``_reg`` to every synthesised register.  So a register
    declared as ``stage0_reg`` inside ``begin : gen_depth2`` becomes the cell
    ``<inst>/gen_depth2.stage0_reg_reg[*]`` (same hierarchy level as the
    parent module, not a child level).
    """
    if depth == 1:
        return [{
            "index": 0,
            "role": "boundary",
            "cell_pattern": f"{hier}/gen_depth1.stage0_reg_reg*",
        }]
    if depth == 2:
        return [
            {
                "index": 0,
                "role": "source_side",
                "cell_pattern": f"{hier}/gen_depth2.stage0_reg_reg*",
            },
            {
                "index": 1,
                "role": "destination_boundary",
                "cell_pattern": f"{hier}/gen_depth2.stage1_reg_reg*",
            },
        ]
    # DEPTH > 2: same dot-prefix rule applies.
    return [{
        "index": i,
        "role": f"stage{i}",
        "cell_pattern": f"{hier}/gen_depth_general.stage_reg_{i}_reg*",
    } for i in range(depth)]


def _write_crossing_manifest(
    out_verilog_path: Path,
    top_name: str,
    boundary_infos: List[Dict[str, Any]],
) -> None:
    """Write algo_top.crossings.json alongside the generated Verilog."""
    grouped: Dict[Tuple, List[Dict]] = defaultdict(list)
    for b in boundary_infos:
        key = (b["tag"], b["src_inst"], b["dst_inst"], b["depth"])
        grouped[key].append(b)

    crossings = []
    for (tag, src_inst, dst_inst, depth), infos in grouped.items():
        instances = []
        for b in infos:
            hier = f"u_{top_name}/{b['instance_name']}"
            instances.append({
                "name": b["instance_name"],
                "width": b["width"],
                "hier": hier,
                "src_pin": b["src_pin"],
                "dst_pin": b["dst_pin"],
                "stages": _stage_patterns_for_instance(hier, depth),
            })
        crossings.append({
            "tag": tag,
            "src_module": src_inst,
            "dst_module": dst_inst,
            "depth": depth,
            "kind": "slr_crossing_delay",
            "instances": instances,
        })

    manifest = {
        "schema": 2,
        "top": top_name,
        "crossings": crossings,
    }

    manifest_path = out_verilog_path.with_suffix(".crossings.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

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
    match_report: Any = None,                # auto_match_ports's MatchReport (for CDC domain resolution)
) -> Dict[str, Any]:
    """
    Generate a structural Verilog top that wires algorithm modules together.

    If system_yml is provided, any framework↔algorithm connections listed there
    are lifted to the top and the *top-level* port names are the framework
    signal names (e.g. SLxQyCHz_rx_data / _tx_data / _cfg_data). Those top
    ports are then wired to the algorithm pins according to system.yml.

    If system_yml is None, behavior matches the previous implementation: only
    user-declared externals are lifted as <inst>_<pin>.

    ``match_report`` is required only
    when a connection declares ``cdc: {kind: 2ff_sync}`` — it's what lets
    this function resolve the *destination* instance's own clock/reset net
    (which may differ from the design's default ``ap_clk``/``ap_rst`` in a
    multi-domain design) via ``forge.contracts.domains.resolve_domain_nets``,
    the same resolver the IR and ``forge.contracts.cdc.verify_cdc`` use.
    ``cdc: {kind: async_fifo}`` emits a real ``cdc_async_fifo`` instance
    (depth, occupancy, and write-enable-gating handled below), not a
    direct wire-through.
    """
    NL = "\n"
    lines: List[str] = []
    emit = lines.append
    external_output_bindings: Dict[Tuple[str, str], str] = {}

    # Build maps for register_stages, delay_cycles, boundary tags, and CDC
    # adapter declarations.
    reg_stages_map: Dict[Tuple[str, str], int] = {}
    delay_cycles_map: Dict[Tuple[str, str], int] = {}
    boundary_map: Dict[Tuple[str, str], str] = {}  # (src_inst, dst_inst) -> tag
    cdc_map: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (src_inst, dst_inst) -> cdc dict
    for conn in cfg.connections:
        src_mod_obj = next(m for m in cfg.modules if m.name == conn.from_)
        dst_mod_obj = next(m for m in cfg.modules if m.name == conn.to)
        # For each instance combination, store register stages and delay cycles
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
                    # Normalize the '2ff_sync' backwards-compatible alias to
                    # 'level_sync' here, once, so every dispatch site below
                    # only ever has to recognize one spelling — callers may
                    # construct Connection objects directly (bypassing
                    # DesignConfig.load's own normalization, e.g. in tests),
                    # so this generator cannot assume the alias was already
                    # resolved upstream.
                    cdc = conn.cdc
                    if cdc.get("kind") == "2ff_sync":
                        cdc = {**cdc, "kind": "level_sync"}
                    cdc_map[(src_inst, dst_inst)] = cdc

    _reset_sync_declared = any(
        rel.get("sync") == "reset_sync" for rel in cfg.reset_domains.values()
    )
    if (cdc_map or _reset_sync_declared) and match_report is None:
        raise ValueError(
            "one or more connections declare 'cdc:' (or a reset_domains.*.sync: "
            "reset_sync entry is present) but write_structural_verilog was not "
            "given a match_report — pass the MatchReport auto_match_ports "
            "returned so the destination instance's real clock/reset net can be "
            "resolved (forge.contracts.domains.resolve_domain_nets)."
        )

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
        direction, so an unrelated, oppositely-directioned port whose
        name happens to share a prefix with a declared external can never
        match (e.g. a real output port ``x_out`` must never match a
        declared external *input* ``x``, even though
        ``"x_out".startswith("x_")`` is true). Found via real testing —
        vision_pipeline_demo's quickstart declared
        ``external_in_ports: [x, y, ...]`` for its
        real scalar pixel-stream inputs, and the prefix match (with no
        direction check) silently misrouted the module's real, unrelated
        ``x_out``/``y_out``/etc. *output* pins to a bogus, wrong-direction
        top-level port instead of the internal net wire the consuming
        instance actually reads from — no existing test exercised this
        interaction.
        Returns 'input' / 'output' / None.
        """
        user_in  = tuple(mod.external_in_ports or [])
        user_out = tuple(mod.external_out_ports or [])
        if pdir == "input" and any(pname == x or pname.startswith(x + "_") for x in user_in):
            return "input"
        if pdir == "output" and any(pname == x or pname.startswith(x + "_") for x in user_out):
            return "output"
        return None

    def _auto_mapped_global(mod: Module, pname: str, pdir: str) -> str | None:
        """Whether this port is swallowed by the global clock/reset fan-out.

        ``connect_clock``/``connect_reset`` collapse every clock- and
        reset-named pin — by the shared convention in
        ``forge.core.utils.signal_names``, which recognises the fused bus
        spellings (``pclk``, ``aclk``, ``presetn``, ``aresetn``) this
        generator's own hand-written test missed — onto the single top-level
        ``ap_clk``/``ap_rst`` net.
        That is right for the ordinary case and wrong for one real case: a
        module with more than one functional clock domain. Tying its three
        clocks to one net shorts them together silently.

        An explicit ``external_in_ports`` entry is the user saying "this pin
        is driven from above, not from FORGE's global net", and it wins —
        an explicit declaration must always beat an implicit name
        convention. That is what makes a multi-clock module integrable at
        all: its extra domains surface at the generated top for the
        enclosing design to drive.

        Both the top-level port declaration loop and the per-instance port
        mapping loop consult this one predicate, because a port declared by
        one and not connected by the other is a generated top that does not
        elaborate.
        """
        if _is_user_external(mod, pname, pdir):
            return None
        if cfg.connect_clock and _collapses_to_global(
            pname, is_clock=True, own_nets=global_nets,
        ):
            return "clock"
        if cfg.connect_reset and _collapses_to_global(
            pname, is_clock=False, own_nets=global_nets,
        ):
            return "reset"
        return None

    # ---------- read system.yml → alias maps ------------------------------
    alias_in, alias_out = _parse_system_aliases(system_yml)

    # ---------- tracking for report ---------------------------------------
    unconnected_inputs: List[Tuple[str, str, int]] = []   # [(instance, port, width)]
    unconnected_outputs: List[Tuple[str, str, int]] = []  # [(instance, port, width)]
    open_outputs: List[Tuple[str, str, int]] = []         # [(instance, port, width)]
    tied_to_zero: List[Tuple[str, str, int]] = []         # [(instance, port, width)]

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

    # Resolve each module's real
    # clock/reset net so a CDC synchronizer instance can be clocked/reset
    # by the *destination* domain, not always assumed to be ap_clk/ap_rst.
    clock_of_module: Dict[str, Optional[str]] = {}
    reset_of_module: Dict[str, Optional[str]] = {}
    if cdc_map or _reset_sync_declared:
        clock_of_module, reset_of_module, _unresolved = resolve_domain_nets(
            cfg, contracts or {}, match_report, global_nets, inst_to_mod,
        )

    # Which reset_domains declare a real
    # `sync: reset_sync` synchronizer — hoisted here (not just where the
    # cdc_reset_sync instances themselves are emitted, later in this
    # function) so the per-instance port-binding loop below can route a
    # member instance's own reset pin to that synchronizer's real
    # `sync_rst_out` net instead of the raw (unsynchronized) domain net.
    reset_sync_domains = {
        name: rel for name, rel in cfg.reset_domains.items() if rel.get("sync") == "reset_sync"
    }

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
    # Structured mirror of module_ports: recorded alongside the
    # text declarations below so callers (generate_port_map, the canonical
    # IR) can consume the resolved top-level port list directly instead of
    # re-parsing it back out of the generated Verilog file. This list has
    # zero influence on `lines`/`emit()` — it only records what's already
    # being decided.
    #
    # Each entry carries `origin` (which of this function's six lifting
    # rules put the port on the top level) and, for the two rules that lift
    # one specific pin, the `instance`/`port` it reaches. This loop is the
    # only place that binding is known — the generated Verilog states the
    # top-level name and the instance connection separately, and nothing
    # downstream could rejoin them without re-implementing the naming rule
    # (`<instance>_<pin>`, or a system.yml alias, which follows no rule at
    # all). Verification planning needs exactly this join: a testbench
    # drives `ctrl_level_enable_in`, and only this says that pin is
    # `ctrl_level.enable_in`.
    top_ports: List[Dict[str, Any]] = []

    if cfg.connect_clock:
        module_ports.append("input ap_clk")
        declared_names.add("ap_clk")
        top_ports.append({"name": "ap_clk", "direction": "in", "width": 1, "origin": "clock"})
    if cfg.connect_reset:
        module_ports.append("input ap_rst")
        declared_names.add("ap_rst")
        top_ports.append({"name": "ap_rst", "direction": "in", "width": 1, "origin": "reset"})

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
            top_ports.append({
                "name": sig_name, "direction": "in", "width": sig_config.width,
                "origin": "control_signal",
            })

    # Add ports for any other global nets (besides clock/reset)
    for gnet_name, binds in global_nets.items():
        # Skip clock and reset as they're handled above
        if gnet_name in ("ap_clk", "clk", "clock", "ap_rst", "rst", "reset", "rst_n"):
            continue
        if gnet_name not in declared_names and binds:
            # All instances of this signal should be inputs (global signals drive modules)
            module_ports.append(f"input {gnet_name}")
            declared_names.add(gnet_name)
            top_ports.append({
                "name": gnet_name, "direction": "in", "width": 1, "origin": "global_net",
            })

    # Emit top-level ports.
    # If (inst,pin) has an alias from system.yml, use the framework name; otherwise use <inst>_<pin>.
    for mod in cfg.modules:
        for p in ports_by_mod[mod.name]:
            pname, pdir, w = p["name"], p["dir"], int(p["width"])

            # Skip any clock/reset-ish pin the global clk/rst fan-out will
            # drive — unless it was explicitly declared external, in which
            # case it needs a top-level port of its own.
            if _auto_mapped_global(mod, pname, pdir):
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
                    ext_dir = _is_user_external(mod, pname, pdir)
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
                top_ports.append({
                    "name": top_port_name,
                    "direction": "in" if ext_dir == "input" else "out",
                    "width": w,
                    "origin": "external",
                    "instance": ilabel,
                    "port": pname,
                })

    # Add DEBUG ports - expose ALL ports of modules marked with debug: true
    for mod in cfg.modules:
        if not mod.debug:
            continue
        
        for p in ports_by_mod[mod.name]:
            pname, pdir, w = p["name"], p["dir"], int(p["width"])
            
            # Skip clock/reset
            if cfg.connect_clock and is_clock_name(pname):
                continue
            if cfg.connect_reset and is_reset_name(pname):
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
                top_ports.append({
                    "name": debug_port_name, "direction": "out", "width": w,
                    "origin": "debug", "instance": ilabel, "port": pname,
                })

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
    sync_nets: Set[str] = set()  # Wires for cdc_sync2ff outputs

    for (src_i, dst_i), pairs in conn_map.items():
        src_mod = inst_to_mod[src_i]
        num_stages = reg_stages_map.get((src_i, dst_i), 0)
        num_delays = delay_cycles_map.get((src_i, dst_i), 0)
        cdc_kind = cdc_map.get((src_i, dst_i), {}).get("kind")

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

            # cdc: {kind: ...} — intermediate wire for the synchronizer/
            # FIFO output (every kind now emits real
            # RTL and therefore a real intermediate net, including
            # async_fifo, which used to be wired straight through).
            if cdc_kind in ("level_sync", "pulse_sync", "mailbox_transfer", "async_fifo"):
                sync_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}")
                if sync_net not in sync_nets:
                    if w == 1:
                        emit(f"  wire {sync_net};")
                    else:
                        emit(f"  wire [{w-1}:0] {sync_net};")
                    sync_nets.add(sync_net)

        # cdc.write_enable_pin: an
        # async_fifo connection may name one of the *source* instance's
        # other own output pins as a real write-enable, gating
        # cdc_async_fifo's write so it stops writing a fresh FIFO entry
        # every single write-domain cycle regardless of whether the
        # payload actually changed (its documented default, "continuously
        # driven... relying on its own internal full/empty guards" —
        # accurate for a level-style status value, but it silently floods
        # a real record-producing FIFO with duplicate entries whenever the
        # write clock is faster than the read clock, found empirically
        # wiring a real packetizer design: occupancy/high-water/
        # overflow telemetry saturated almost immediately even though only
        # 64 real records were ever produced). Not part of any port_map
        # pair, so it needs its own pre-declared driver net here, exactly
        # like a regular driving output pin gets above.
        if cdc_kind == "async_fifo":
            we_pin_raw = (cdc_map.get((src_i, dst_i)) or {}).get("write_enable_pin")
            if we_pin_raw:
                we_pin = _canon_pin(ip_info, src_mod, we_pin_raw)
                we_w = _pin_width_for_inst(src_i, we_pin)
                we_net = _verilog_ident(f"net_{src_i}_{we_pin}")
                if we_net not in driver_net_set:
                    if we_w == 1:
                        emit(f"  wire {we_net};")
                    else:
                        emit(f"  wire [{we_w-1}:0] {we_net};")
                    driver_net_set.add(we_net)

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

    # Pre-declare each reset_sync
    # domain's own sync_rst_out net here too, up front, alongside every
    # other intermediate net this function pre-declares (sync_net_* for
    # CDC data crossings, above) — a member instance's reset pin
    # (the "Instances" loop just below) references this net by name, and
    # if it's only declared later (in the "Reset Synchronizer Instances"
    # section, after every module instance), Xilinx xvlog implicitly
    # declares a *separate*, undriven 1-bit net for the earlier reference
    # ("already implicitly declared" warning) — an instance's reset pin
    # then reads that phantom net (permanently X) instead of the real
    # synchronizer output. Found empirically: the second reset_sync
    # domain wired this way (rst_output) silently stayed X for an entire
    # xsim run while the first (rst_pixel) happened to still resolve
    # correctly — a real, order-dependent miscompile, not a hypothetical.
    for _name in sorted(reset_sync_domains):
        emit(f"  wire {_verilog_ident(f'rst_sync_{_name}')};")
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
            for (src_i, dst_i), pairs in conn_map.items():
                if src_i != ilabel:
                    continue
                src_mod = mod.name
                for s_pin_raw, _ in pairs:
                    s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                    driven_outs.add(s_pin)

                # cdc.write_enable_pin:
                # this instance's own write-enable output pin (see the
                # matching pre-declaration above) also counts as driven,
                # even though it's never a port_map pair.
                if cdc_map.get((src_i, dst_i), {}).get("kind") == "async_fifo":
                    we_pin_raw = cdc_map.get((src_i, dst_i), {}).get("write_enable_pin")
                    if we_pin_raw:
                        driven_outs.add(_canon_pin(ip_info, src_mod, we_pin_raw))

            for p in ports_by_mod[mod.name]:
                pname, pdir, w = p["name"], p["dir"], int(p["width"])

                # clock/reset auto-map (support various naming conventions)
                _global = _auto_mapped_global(mod, pname, pdir)
                if _global == "clock":
                    # Skip ap_clk connection for clock-free (combinatorial) modules
                    _contract = (contracts or {}).get(mod.name)
                    if _contract and _contract.clock_free:
                        continue
                    pm.append(f"    .{pname}(ap_clk)")
                    continue
                if _global == "reset":
                    pm.append(f"    .{pname}(ap_rst)")
                    continue

                # A non-standard-named
                # reset pin (doesn't match the pattern above, so it's this
                # module's own genuine domain-specific reset net) that
                # belongs to a `reset_domains.<name>.sync: reset_sync`
                # domain must bind to that synchronizer's real
                # `sync_rst_out` net, not the raw (unsynchronized) domain
                # net the "other global nets" fallback below would use —
                # otherwise the whole point of declaring reset_sync (an
                # asynchronously-asserted, synchronously-deasserted reset
                # in *this* destination domain) is silently lost, and the
                # instance sees whatever raw external signal drives the
                # domain net directly instead.
                if cfg.connect_reset and reset_sync_domains and pname == reset_of_module.get(mod.name):
                    domain_name = reset_of_module[mod.name]
                    if domain_name in reset_sync_domains:
                        rst_sync_net = _verilog_ident(f"rst_sync_{domain_name}")
                        pm.append(f"    .{pname}({rst_sync_net})")
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
                user_ext_dir = _is_user_external(mod, pname, pdir)
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

                        # Check if a CDC synchronizer, delay cycles, or register
                        # stages exist between src and dst — CDC takes priority:
                        # a domain crossing needs the synchronizer regardless of
                        # any also-declared delay/register on the same connection.
                        cdc_kind = cdc_map.get((src_i, ilabel), {}).get("kind")
                        num_delays = delay_cycles_map.get((src_i, ilabel), 0)
                        num_stages = reg_stages_map.get((src_i, ilabel), 0)
                        if cdc_kind in ("level_sync", "pulse_sync", "mailbox_transfer", "async_fifo"):
                            # Connect to the synchronized/buffered output
                            sync_net = _verilog_ident(f"sync_net_{src_i}_{ilabel}_{src_pin}")
                            pm.append(f"    .{pname}({sync_net})")
                        elif num_delays > 0:
                            # Connect to delayed output
                            delay_net = _verilog_ident(f"delay_net_{src_i}_{ilabel}_{src_pin}")
                            pm.append(f"    .{pname}({delay_net})")
                        elif num_stages > 0:
                            # Connect to registered output
                            reg_net = _verilog_ident(f"reg_net_{src_i}_{ilabel}_{src_pin}")
                            pm.append(f"    .{pname}({reg_net})")
                        else:
                            # Direct connection (also covers cdc: {kind: async_fifo}
                            # — approved structurally, but no FIFO RTL this release,
                            # see this function's docstring)
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
                    if cfg.connect_clock and is_clock_name(pname):
                        continue
                    if cfg.connect_reset and is_reset_name(pname):
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
    boundary_infos: List[Dict[str, Any]] = []  # collected for crossing manifest

    for (src_i, dst_i), pairs in conn_map.items():
        num_delays = delay_cycles_map.get((src_i, dst_i), 0)
        if num_delays > 0:
            src_mod = inst_to_mod[src_i]
            tag = boundary_map.get((src_i, dst_i))

            for s_pin_raw, d_pin_raw in pairs:
                s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                dst_mod_name = inst_to_mod[dst_i]
                d_pin = _canon_pin(ip_info, dst_mod_name, d_pin_raw)
                w = _pin_width_for_inst(src_i, s_pin)

                src_net = _verilog_ident(f"net_{src_i}_{s_pin}")
                dst_net = _verilog_ident(f"delay_net_{src_i}_{dst_i}_{s_pin}")

                if tag:
                    # Boundary crossing: emit a protected slr_crossing_delay with
                    # a stable, deterministic instance name.
                    inst_name = _boundary_inst_name(tag, s_pin, d_pin)
                    emit(f"  // Boundary crossing {src_i}.{s_pin} → {dst_i}.{d_pin} [boundary={tag}]")
                    emit(f'  (* KEEP_HIERARCHY = "TRUE" *)')
                    emit(f"  slr_crossing_delay #(")
                    emit(f"    .WIDTH({w}),")
                    emit(f"    .DEPTH({num_delays})")
                    emit(f"  ) {inst_name} (")
                    emit(f"    .clk(ap_clk),")
                    emit(f"    .rst(ap_rst),")
                    emit(f"    .din({src_net}),")
                    emit(f"    .dout({dst_net})")
                    emit(f"  );")
                    emit("")

                    boundary_infos.append({
                        "tag": tag,
                        "src_inst": src_i,
                        "dst_inst": dst_i,
                        "src_pin": s_pin,
                        "dst_pin": d_pin,
                        "depth": num_delays,
                        "width": w,
                        "instance_name": inst_name,
                    })
                else:
                    # Normal delay: use signal_delay with a positional name.
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

    # ====== CDC Synchronizer Instances (expanded to the full 5-kind ========
    # ====== primitive family) ===============================================
    # A real synchronizer/FIFO is emitted for every declared cdc: {kind: ...}
    # connection, clocked/reset by each side's own resolved clock/reset net
    # (which may differ from ap_clk/ap_rst in a multi-domain design —
    # though no plugin in this repo declares a real second domain yet, see
    # forge.contracts.domains; the physical top-level port is still always
    # a single ap_clk/ap_rst pair this release).
    if cdc_map:
        emit("  // CDC synchronizers for declared clock-domain-crossing connections")
        cdc_sync_counter = 0
        for (src_i, dst_i), pairs in conn_map.items():
            cdc = cdc_map.get((src_i, dst_i))
            if not cdc:
                continue
            kind = cdc.get("kind")
            src_mod = inst_to_mod[src_i]
            dst_mod = inst_to_mod[dst_i]

            # resolve_domain_nets's domain identity is the raw port name
            # verbatim (e.g. "rst") — but this generator's own clock/reset
            # auto-map (above) aliases every standard-name variant onto the
            # single literal ap_clk/ap_rst top-level port regardless of
            # which variant a given module's contract used. Translate
            # through the same rule here, or the synchronizer would
            # reference a top-level net that doesn't exist.
            src_clk_domain = clock_of_module.get(src_mod)
            src_rst_domain = reset_of_module.get(src_mod)
            dst_clk_domain = clock_of_module.get(dst_mod)
            dst_rst_domain = reset_of_module.get(dst_mod)
            src_clk_net = _domain_to_top_level_net(src_clk_domain, is_clock=True, own_nets=global_nets) if src_clk_domain else "ap_clk"
            src_rst_net = _reset_net_for_domain(src_rst_domain, reset_sync_domains, global_nets)
            dst_clk_net = _domain_to_top_level_net(dst_clk_domain, is_clock=True, own_nets=global_nets) if dst_clk_domain else "ap_clk"
            dst_rst_net = _reset_net_for_domain(dst_rst_domain, reset_sync_domains, global_nets)

            for s_pin_raw, d_pin_raw in pairs:
                s_pin = _canon_pin(ip_info, src_mod, s_pin_raw)
                d_pin = _canon_pin(ip_info, dst_mod, d_pin_raw)
                w = _pin_width_for_inst(src_i, s_pin)

                src_net = _verilog_ident(f"net_{src_i}_{s_pin}")
                sync_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}")
                inst_name = f"cdc_sync_{cdc_sync_counter}"

                if kind == "level_sync":
                    emit(f"  // CDC crossing {src_i}.{s_pin} -> {dst_i}.{d_pin} (level_sync)")
                    emit(f"  cdc_sync2ff #(")
                    emit(f"    .WIDTH({w})")
                    emit(f"  ) {inst_name} (")
                    emit(f"    .dst_clk({dst_clk_net}),")
                    emit(f"    .dst_rst({dst_rst_net}),")
                    emit(f"    .din({src_net}),")
                    emit(f"    .dout({sync_net})")
                    emit(f"  );")
                    emit("")
                elif kind == "pulse_sync":
                    emit(f"  // CDC crossing {src_i}.{s_pin} -> {dst_i}.{d_pin} (pulse_sync, "
                         f"min_spacing_cycles={cdc.get('min_spacing_cycles')})")
                    emit(f"  cdc_pulse_sync {inst_name} (")
                    emit(f"    .src_clk({src_clk_net}),")
                    emit(f"    .src_rst({src_rst_net}),")
                    emit(f"    .pulse_in({src_net}),")
                    emit(f"    .dst_clk({dst_clk_net}),")
                    emit(f"    .dst_rst({dst_rst_net}),")
                    emit(f"    .pulse_out({sync_net})")
                    emit(f"  );")
                    emit("")
                elif kind == "mailbox_transfer":
                    valid_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_valid")
                    emit(f"  wire {valid_net};  // unused: mailbox dout_valid, no downstream valid concept yet")
                    emit(f"  // CDC crossing {src_i}.{s_pin} -> {dst_i}.{d_pin} (mailbox_transfer)")
                    emit(f"  cdc_mailbox #(")
                    emit(f"    .WIDTH({w})")
                    emit(f"  ) {inst_name} (")
                    emit(f"    .src_clk({src_clk_net}),")
                    emit(f"    .src_rst({src_rst_net}),")
                    emit(f"    .din({src_net}),")
                    emit(f"    .dst_clk({dst_clk_net}),")
                    emit(f"    .dst_rst({dst_rst_net}),")
                    emit(f"    .dout({sync_net}),")
                    emit(f"    .dout_valid({valid_net})")
                    emit(f"  );")
                    emit("")
                elif kind == "async_fifo":
                    depth = cdc.get("depth")
                    # $clog2(depth) — depth is validated as a positive power of
                    # two at design.yml load time (forge.contracts.config).
                    addr_width = (depth - 1).bit_length() if depth else 0
                    full_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_full")
                    empty_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_empty")
                    ovf_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_overflow")
                    unf_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_underflow")
                    occ_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_occupancy")
                    hw_net = _verilog_ident(f"sync_net_{src_i}_{dst_i}_{s_pin}_high_water")
                    # cdc.write_enable_pin: real write-enable gating,
                    # opt-in per connection — see the pre-declaration
                    # above for why.
                    # Defaults to 1'b1 (every prior async_fifo usage's
                    # unchanged "continuously driven" behavior) when unset.
                    we_pin_raw = cdc.get("write_enable_pin")
                    if we_pin_raw:
                        we_pin = _canon_pin(ip_info, src_mod, we_pin_raw)
                        wr_en_expr = _verilog_ident(f"net_{src_i}_{we_pin}")
                    else:
                        wr_en_expr = "1'b1"
                    emit(
                        "  // Occupancy/backpressure telemetry — "
                        "consume via a Tier 2 probe declaration."
                    )
                    emit(f"  wire {full_net}, {empty_net}, {ovf_net}, {unf_net};")
                    emit(f"  wire [{addr_width}:0] {occ_net}, {hw_net};")
                    emit(f"  // CDC crossing {src_i}.{s_pin} -> {dst_i}.{d_pin} (async_fifo, depth={depth}, wr_en={wr_en_expr})")
                    emit(f"  cdc_async_fifo #(")
                    emit(f"    .WIDTH({w}),")
                    emit(f"    .DEPTH({depth})")
                    emit(f"  ) {inst_name} (")
                    emit(f"    .wr_clk({src_clk_net}),")
                    emit(f"    .wr_rst({src_rst_net}),")
                    emit(f"    .din({src_net}),")
                    emit(f"    .wr_en({wr_en_expr}),")
                    emit(f"    .full({full_net}),")
                    emit(f"    .overflow_attempt({ovf_net}),")
                    emit(f"    .occupancy({occ_net}),")
                    emit(f"    .high_water({hw_net}),")
                    emit(f"    .rd_clk({dst_clk_net}),")
                    emit(f"    .rd_rst({dst_rst_net}),")
                    emit(f"    .dout({sync_net}),")
                    emit(f"    .empty({empty_net}),")
                    emit(f"    .underflow_attempt({unf_net})")
                    emit(f"  );")
                    emit("")
                else:
                    continue

                cdc_sync_counter += 1

    # ====== Reset Synchronizer Instances ====================================
    # ====== reset_domains.<name>.sync: reset_sync ===========================
    # A reset crossing is a property of a destination reset *domain*, not
    # a connections:-level data crossing (see forge.contracts.cdc's module
    # docstring), so this is driven directly off cfg.reset_domains rather
    # than cdc_map. NOTE: the synchronized reset net produced here is not
    # yet threaded into any instance's actual reset pin binding — every
    # instance's reset pin is still bound to the literal ap_rst
    # unconditionally elsewhere in this generator (the same single-
    # top-level-clock/reset-port limitation the CDC data synchronizers
    # above already have). That gap has since been closed
    # (see the per-instance port-binding loop earlier in this
    # function): a member instance's reset pin is now bound to this
    # synchronizer's own `sync_rst_out` net below, not the raw domain net.
    if reset_sync_domains:
        emit("  // Reset synchronizers for reset_domains.*.sync: reset_sync declarations")
        for rst_sync_counter, (name, rel) in enumerate(sorted(reset_sync_domains.items())):
            derived_from = rel.get("derived_from")
            async_rst_net = _domain_to_top_level_net(derived_from, is_clock=False, own_nets=global_nets)

            # The synchronizer's own destination clock: whichever clock
            # domain this reset domain's member instances actually use
            # (a reset domain and its instances are assumed to share one
            # clock domain). Falls back to ap_clk if unresolvable.
            member_mods = [m for m, dom in reset_of_module.items() if dom == name]
            dst_clk_domain = clock_of_module.get(member_mods[0]) if member_mods else None
            dst_clk_net = _domain_to_top_level_net(dst_clk_domain, is_clock=True, own_nets=global_nets) if dst_clk_domain else "ap_clk"

            rst_sync_net = _verilog_ident(f"rst_sync_{name}")
            emit(f"  // Reset domain {name!r}: real member instances bound to sync_rst_out")
            emit(f"  // (net pre-declared earlier, alongside every other intermediate net)")
            emit(f"  cdc_reset_sync rst_sync_{rst_sync_counter} (")
            emit(f"    .dst_clk({dst_clk_net}),")
            emit(f"    .async_rst_in({async_rst_net}),")
            emit(f"    .sync_rst_out({rst_sync_net})")
            emit(f"  );")
            emit("")

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

    # Write SLR crossing manifest if any boundary connections were emitted.
    if boundary_infos:
        _write_crossing_manifest(out_path, top_name, boundary_infos)

    # ========== Generate Report ==========
    report = {
        "open_outputs": open_outputs,
        "tied_to_zero": tied_to_zero,
        "total_modules": len(cfg.modules),
        "total_instances": sum(m.instances for m in cfg.modules),
        "total_connections": sum(len(pairs) for pairs in conn_map.values()),
        "top_ports": top_ports,
    }
    
    return report
