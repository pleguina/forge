# hls_auto/ip_parser.py

from __future__ import annotations
import re
import xml.etree.ElementTree as ET
import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Module

from ..config import resolve_declared_path

# Import minimal HDL parsing utilities
from arc.core.utils.hdl_parser import (
    _scan_ports as _scan_vhdl_ports,         # VHDL: returns {name: (dir, width)}
    _scan_verilog_ports as _scan_vlog_ports, # Verilog/SV: returns {name: (dir, width)}
    _vhdl_entity_name as _vhdl_entity_name,  # entity name from VHDL
    _verilog_module_name as _vlog_module_name, # module name from Verilog/SV
)

# the two namespaces we care about
_NS = {
    "spirit": "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009",
    "xilinx": "http://www.xilinx.com",
}


def _normalize_ports_from_map(pmap: Dict[str, Tuple[str, int]]) -> List[Dict[str, Any]]:
    """convert {'a': ('in', 8), ...} → [{'name':'a','direction':'IN','width':8}, ...]"""
    out: List[Dict[str, Any]] = []
    for n, (d, w) in pmap.items():
        dd = d.upper()
        if dd not in ("IN", "OUT", "INOUT"):
            dd = "IN"  # conservative
        out.append({"name": n, "direction": dd, "width": int(w), "type": "STD_LOGIC" if int(w) == 1 else f"STD_LOGIC_VECTOR({int(w)-1} downto 0)"})
    return out

def _extract_vhdl_generics(vhd_file: Path) -> Dict[str, Any]:
    """Extract generic parameters from a VHDL file."""
    generics: Dict[str, Any] = {}
    content = vhd_file.read_text()
    generic_section = re.search(r'generic\s*\((.*?)\);', content, re.DOTALL | re.I)
    if generic_section:
        for line in generic_section.group(1).splitlines():
            # Look for patterns like: IN_W : positive := 54;
            gen_match = re.search(r'(\w+)\s*:\s*(\w+)\s*:=\s*(.+?)\s*(?:;|$)', line)
            if gen_match:
                name = gen_match.group(1)
                gen_type = gen_match.group(2)
                default_val = gen_match.group(3).strip().strip('"').strip("'")
                generics[name] = {
                    "type": gen_type,
                    "default": default_val
                }
    return generics

def _scan_hdl_fallback(mod, src_root: Path) -> Optional[Dict[str, Any]]:
    """
    If a module doesn't have component.xml, try to parse its HDL files from mod.src.
    Returns a component-like dict or None.
    """
    # Resolve candidate HDL files from src:
    srcs: List[Path] = []
    for s in (mod.src or []):
        p = resolve_declared_path(s, src_root)
        if p.is_file():
            srcs.append(p)
        elif p.is_dir():
            # Collect common HDL names under this folder
            srcs.extend(sorted(p.rglob("*.vhd")))
            srcs.extend(sorted(p.rglob("*.v")))
            srcs.extend(sorted(p.rglob("*.sv")))

    # Collect package files from rtl_packages, vhdl_packages, etc.
    pkg_files: List[Path] = []
    pkg_fields = ("rtl_packages", "vhdl_packages", "include_packages", "packages")
    for fld in pkg_fields:
        for pkg_path_str in (getattr(mod, fld, []) or []):
            pkg_p = resolve_declared_path(pkg_path_str, src_root)
            if pkg_p.exists():
                pkg_files.append(pkg_p)

    # Prefer a file whose entity/module matches mod.top (if provided)
    def _score(f: Path) -> int:
        # higher score = better
        if mod.top and f.stem.lower() == Path(mod.top).stem.lower():
            return 100
        if mod.top and mod.top.lower() in f.name.lower():
            return 50
        if mod.name and mod.name.lower() in f.name.lower():
            return 25
        return 1

    srcs = [p for p in srcs if p.suffix.lower() in (".vhd", ".v", ".sv")]
    if not srcs:
        return None
    srcs.sort(key=_score, reverse=True)

    # Try files in priority order until we parse ports
    for f in srcs:
        try:
            if f.suffix.lower() == ".vhd":
                ports_map = _scan_vhdl_ports(f)  # {name: (dir, width)}
                if not ports_map:
                    continue
                ent = _vhdl_entity_name(f, mod.top or mod.name)
                generics = _extract_vhdl_generics(f)
                result = {
                    "vendor": "user",
                    "library": "hdl",
                    "name": ent,            # keep "name" aligned with entity
                    "entity": ent,          # explicit to help generators
                    "version": "rtl",
                    "kind": "hdl", 
                    "ports": _normalize_ports_from_map(ports_map),
                }
                # Add generics if present
                if generics:
                    result["generics"] = generics
                # Add package files if present
                if pkg_files:
                    result["packages"] = [str(p) for p in pkg_files]
                return result
            else:
                ports_map = _scan_vlog_ports(f)
                if not ports_map:
                    continue
                ent = _vlog_module_name(f, mod.top or mod.name)
                result = {
                    "vendor": "user",
                    "library": "hdl",
                    "name": ent,
                    "entity": ent,
                    "version": "rtl",
                    "kind": "hdl", 
                    "ports": _normalize_ports_from_map(ports_map),
                }
                # Add package files if present
                if pkg_files:
                    result["packages"] = [str(p) for p in pkg_files]
                return result
        except Exception:
            # try next file
            continue
    return None


def parse_component(xml_path: Path) -> Dict[str, Any]:
    """
    Parse a single HLS-exported component.xml into a dict:
      - vendor, library, name, version
      - interfaces: list of { name, direction, busType, abstractionType, portMaps }
      - ports: list of { name, direction, width, type }  <-- from the VHDL entity
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    comp: Dict[str, Any] = {
        "vendor":   root.findtext("spirit:vendor",   namespaces=_NS),
        "library":  root.findtext("spirit:library",  namespaces=_NS),
        "name":     root.findtext("spirit:name",     namespaces=_NS),
        "version":  root.findtext("spirit:version",  namespaces=_NS),
        "interfaces": [],
        "ports":     [],   # will fill from VHDL
    }

    # --- busInterfaces & portMaps (unchanged) --- (DONT ADD INTERFACES FOR THE MOMENT)
    
    # for bi in root.findall("spirit:busInterfaces/spirit:busInterface", namespaces=_NS):
    #     bt = bi.find("spirit:busType", namespaces=_NS)
    #     at = bi.find("spirit:abstractionType", namespaces=_NS)
    #     iface: Dict[str, Any] = {
    #         "name": bi.findtext("spirit:name", namespaces=_NS) or "",
    #         "direction": "master"
    #             if bi.find("spirit:master", namespaces=_NS) is not None
    #             else "slave",
    #         "busType": {
    #             "vendor":  bt.get("{%s}vendor"  % _NS["spirit"], ""),
    #             "library": bt.get("{%s}library" % _NS["spirit"], ""),
    #             "name":    bt.get("{%s}name"    % _NS["spirit"], ""),
    #             "version": bt.get("{%s}version" % _NS["spirit"], ""),
    #         },
    #         "abstractionType": {
    #             "vendor":  at.get("{%s}vendor"  % _NS["spirit"], ""),
    #             "library": at.get("{%s}library" % _NS["spirit"], ""),
    #             "name":    at.get("{%s}name"    % _NS["spirit"], ""),
    #             "version": at.get("{%s}version" % _NS["spirit"], ""),
    #         },
    #         "portMaps": [],
    #     }

    #     for pm in bi.findall("spirit:portMaps/spirit:portMap", namespaces=_NS):
    #         logical  = pm.findtext("spirit:logicalPort/spirit:name", namespaces=_NS) or ""
    #         physical = pm.findtext("spirit:physicalPort/spirit:name", namespaces=_NS) or ""
    #         iface["portMaps"].append({"logical": logical, "physical": physical})

    #     comp["interfaces"].append(iface)

    # --- now override ports[] by parsing the VHDL entity ---
    vhdl_dir = xml_path.parent / "hdl" / "vhdl"
    vhdl_file = vhdl_dir / f"{comp['name']}.vhd"
    if vhdl_file.exists():
        comp["ports"] = _parse_vhdl_entity(vhdl_file)
    else:
        # fallback: leave empty or warn
        comp["ports"] = []

    return comp


def _parse_vhdl_entity(vhdl_path: Path) -> List[Dict[str,Any]]:
    """
    Read the VHDL file, extract the entity port list.
    Returns list of { name, direction, width, type }.
    """
    text = vhdl_path.read_text()
    # strip comments
    text = re.sub(r"--.*", "", text)
    # find the entity ... port(...) block
    m = re.search(r"entity\s+\w+\s+is\s+port\s*\(\s*(.*?)\);\s*end", text, re.S | re.I)
    if not m:
        return []

    ports_block = m.group(1)
    ports = []
    for line in ports_block.split(";"):
        line = line.strip()
        if not line:
            continue
        # e.g. ap_start : IN STD_LOGIC;
        # or  data : OUT STD_LOGIC_VECTOR (31 downto 0);
        pm = re.match(
            r"^(\w+)\s*:\s*(IN|OUT)\s+(STD_LOGIC_VECTOR\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)"
            r"|STD_LOGIC)\s*$",
            line,
            re.I
        )
        if not pm:
            continue
        name = pm.group(1)
        direction = pm.group(2).upper()
        typ = pm.group(3).upper().replace(" ", "")
        if typ.startswith("STD_LOGIC_VECTOR"):
            left = int(pm.group(4))
            right = int(pm.group(5))
            width = abs(left - right) + 1
        else:
            width = 1
        ports.append({
            "name":      name,
            "direction": direction,      # "IN" or "OUT"
            "width":     width,
            "type":      typ,            # "STD_LOGIC" or "STD_LOGIC_VECTOR(…)"
        })
    return ports


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

def collect_all(
    build_root: Path,
    modules: List[Module],
    ip_root: Optional[Path] = None,
    *,
    src_root: Optional[Path] = None,          # NEW: where to resolve HDL sources from
) -> Dict[str, Any]:
    """
    Return {module.name: component-dict | None}, prioritising component.xml;
    if absent, attempt HDL scan using mod.src (VHDL/Verilog/SV).
    """
    def good(cname: str, mod: Module) -> bool:
        return (
            cname in (mod.top, mod.name)
            or cname.endswith(mod.top)
            or cname.endswith(mod.name)
        )

    summary: Dict[str, Any] = {}

    print(f"\n▶︎ build_root = {build_root}")
    if ip_root:  print(f"▶︎ ip_root    = {ip_root}")
    if src_root: print(f"▶︎ src_root   = {src_root}")

    for mod in modules:
        print(f"\n─── scanning module  '{mod.name}'  (top='{mod.top}') ───")
        accepted = None

        # 1) build/<mod.name>/… component.xml
        build_dir = build_root / mod.name
        print(f"  • build dir: {build_dir}")
        if build_dir.exists():
            for xml in build_dir.rglob("component.xml"):
                comp = parse_component(xml)
                cname = comp["name"]
                print(f"    – found {xml}  (comp_name='{cname}')")
                if good(cname, mod):
                    print("      ✓ matches — use it")
                    accepted = comp
                    break
                else:
                    print("      ✗ no match")

        # 2) explicit --ip-root repo
        if accepted is None and ip_root and ip_root.exists():
            print(f"  • ip_root scan:")
            for xml in ip_root.rglob("component.xml"):
                comp = parse_component(xml)
                cname = comp["name"]
                print(f"    – found {xml}  (comp_name='{cname}')")
                if good(cname, mod):
                    print("      ✓ matches — use it")
                    accepted = comp
                    break
                else:
                    print("      ✗ no match")

        # 3) NEW: no component.xml → scan HDL from mod.src
        if accepted is None and src_root is not None:
            if getattr(mod, "kind", "hls") == "rtl":
                print("  • no component.xml — trying HDL scan from src list …")
                accepted = _scan_hdl_fallback(mod, src_root)
            if accepted:
                print(f"      ✓ HDL module detected: entity/module '{accepted.get('entity', accepted['name'])}'")
            else:
                print("      ✗ HDL scan failed / no HDL sources")

        summary[mod.name] = accepted
        if accepted is None:
            print("  ⚠️  nothing accepted for this module")

    return summary




def write_summary(
    summary: Dict[str, Any],
    out_path: Path,
    format: str = "json",
) -> None:
    """
    Write the summary dict out in either JSON or YAML form.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if format.lower() == "json":
        text = json.dumps(summary, indent=2)
    else:
        text = yaml.safe_dump(summary, sort_keys=False)
    out_path.write_text(text)
    print(f"✅ IP summary written to {out_path} ({format.upper()})")


if __name__ == "__main__":
    import argparse
    from ..config import DesignConfig

    parser = argparse.ArgumentParser(description="Collect HLS IP component.xml info")
    parser.add_argument("config", type=Path,
                        help="Path to design.yaml (or its containing dir)")
    parser.add_argument("-b", "--build-dir", type=Path, default=Path("build"),
                        help="Root build directory")
    parser.add_argument("-f", "--format", choices=["json", "yaml"], default="json")
    parser.add_argument("-o", "--out", type=Path,
                        help="Output file (default: build/<project>/ip_info.<fmt>)")
    args = parser.parse_args()

    cfg_path = args.config if args.config.is_file() else args.config / "design.yaml"
    cfg = DesignConfig.load(cfg_path)
    project = args.config.parent.name
    build_root = args.build_dir / project
    ip_root = args.ip_root if args.ip_root else (build_root / "ips")

    out = args.out or (build_root / f"ip_info.{args.format}")

    summary = collect_all(
        build_root=build_root,
        modules=cfg.modules,                  # ← pass Module objects
        ip_root=ip_root,
        src_root=cfg_path.parent,             # ← enable RTL fallback
    )    
    write_summary(summary, out, format=args.format)
    print(f"✅ Collected {len(summary)} IP components from {build_root}")
    print(f"✅ Summary written to {out} ({args.format.upper()})")