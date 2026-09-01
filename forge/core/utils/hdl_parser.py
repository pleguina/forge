"""Minimal HDL parsing utilities for extracting entity/module names and ports."""

from __future__ import annotations

import ast
import operator
import re
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple, Type


_ARITH_BINOPS: Dict[Type[ast.AST], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,  # matches the old int(eval(...)) truncation-toward-zero
}
_ARITH_UNARYOPS: Dict[Type[ast.AST], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_arith_node(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_BINOPS:
        return _ARITH_BINOPS[type(node.op)](
            _eval_arith_node(node.left), _eval_arith_node(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ARITH_UNARYOPS:
        return _ARITH_UNARYOPS[type(node.op)](_eval_arith_node(node.operand))
    raise ValueError(f"Unsupported arithmetic expression node: {ast.dump(node)}")


def _safe_eval_arith(expr: str) -> int:
    """Evaluate a simple integer arithmetic expression (+, -, *, /, parens) without eval()."""
    tree = ast.parse(expr, mode="eval")
    return int(_eval_arith_node(tree.body))


# ==================== VHDL Parsing ====================

_VHDL_PORT_RE = re.compile(
    r"^\s*(?P<name>\w+)\s*:\s*(?P<dir>in|out|inout)\s+"
    r"(?P<type>std_logic(?:_vector\s*\([^)]+\))?)"
    r"\s*;?\s*(?:--.*)?$",
    re.IGNORECASE,
)

_FW_ENTITY_RE = re.compile(r"^\s*entity\s+(?P<name>\w+)\s+is", re.I)


def _vhdl_entity_name(vhdl_path: Path, fallback: str = "") -> str:
    """Extract entity name from VHDL file."""
    for ln in vhdl_path.read_text().splitlines():
        m = _FW_ENTITY_RE.match(ln)
        if m:
            return m.group("name")
    return fallback


def _eval_vhdl_expr(expr: str, generics: Dict[str, int]) -> int:
    """Evaluate simple VHDL expressions like 'N-1', 'WIDTH+2', or just '15'."""
    # Replace known generics with their values
    for name, val in generics.items():
        expr = re.sub(r'\b' + name + r'\b', str(val), expr)

    # Try to evaluate as a simple arithmetic expression
    try:
        # Only allow safe characters: digits, +, -, *, /, (, ), spaces
        if re.match(r'^[\d\s\+\-\*/\(\)]+$', expr):
            return _safe_eval_arith(expr)
    except (SyntaxError, ValueError, ZeroDivisionError):
        pass

    # If it's just a number, return it
    if expr.isdigit():
        return int(expr)

    raise ValueError(f"Cannot evaluate VHDL expression: {expr}")


def _scan_ports(vhdl_path: Path) -> Dict[str, Tuple[str, int]]:
    """
    Scan VHDL entity ports.
    
    Returns:
        Dict mapping port name to (direction, width as int)
        direction: 'in', 'out', 'inout'
        width: integer bit width
    """
    ports: Dict[str, Tuple[str, int]] = {}
    inside = False
    generics: Dict[str, int] = {}  # store generic values if we can find them

    # First pass: try to extract generic values
    content = vhdl_path.read_text()
    generic_section = re.search(r'generic\s*\((.*?)\);', content, re.DOTALL | re.I)
    if generic_section:
        for line in generic_section.group(1).splitlines():
            # Look for patterns like: IN_W : positive := 54;
            gen_match = re.search(r'(\w+)\s*:\s*\w+\s*:=\s*(\d+)', line)
            if gen_match:
                generics[gen_match.group(1)] = int(gen_match.group(2))

    for ln in content.splitlines():
        if re.match(r"^\s*port\s*\(", ln, re.I):
            inside = True
            continue
        if inside and re.match(r"^\s*\)\s*;", ln):
            break
        if not inside:
            continue

        ln_clean = ln.split("--", 1)[0].rstrip()
        if not ln_clean:
            continue

        m = _VHDL_PORT_RE.match(ln_clean)
        if not m:
            continue

        name = m["name"]
        direction = m["dir"].lower()
        width = 1
        if "vector" in m["type"].lower():
            # Look for patterns like (N-1 downto 0) or (66 downto 0)
            range_match = re.search(r'\(([^)]+)\s+(?:downto|to)\s+([^)]+)\)', m["type"], re.I)
            if range_match:
                hi_expr, lo_expr = range_match.groups()
                hi_expr = hi_expr.strip()
                lo_expr = lo_expr.strip()

                try:
                    # Try to evaluate using known generics
                    hi_val = _eval_vhdl_expr(hi_expr, generics)
                    lo_val = _eval_vhdl_expr(lo_expr, generics)
                    width = abs(hi_val - lo_val) + 1
                except ValueError:
                    # Can't determine width, use a safe default
                    # Check if it might be from a package constant
                    if any(const in m["type"].upper() for const in ['CSP_BUS_W', 'BUS_W', 'DATA_W']):
                        width = 64
                    else:
                        width = 64  # Reasonable default for unknown widths
            else:
                width = 64  # Fallback default
        ports[name] = (direction, width)
    return ports


# ==================== Verilog/SystemVerilog Parsing ====================

_VLOG_MOD_RE = re.compile(
    r'^\s*module\s+(?P<name>\w+)\s*(?:#\s*\(.*?\)\s*)?\(', re.M | re.S
)
_VLOG_HEADER_RE = re.compile(
    r'^\s*module\s+(?P<name>\w+)\s*(?:#\s*\(.*?\)\s*)?\((?P<ports>.*?)\)\s*;',
    re.M | re.S
)
_VLOG_DECL_RE = re.compile(
    r'^\s*(?P<dir>input|output|inout)\s+'
    r'(?P<attrs>(?:wire|reg|logic|signed|unsigned|\s)*)'
    r'(?:(?P<w0>\[\s*\d+\s*:\s*\d+\s*\])\s*)?'
    r'(?P<names>[^;]+);',
    re.M
)
_VLOG_NAME_RE = re.compile(
    r'^\s*(?:(?P<w>\[\s*\d+\s*:\s*\d+\s*\])\s*)?(?P<name>\w+)\s*$'
)


def _strip_sv_comments(text: str) -> str:
    """Remove comments from Verilog/SystemVerilog code."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'//.*?$', '', text, flags=re.M)
    return text


def _eval_verilog_expr(expr: str, params: Dict[str, int]) -> int:
    """Evaluate simple Verilog expressions like 'N-1', 'WIDTH+2', or just '15'."""
    # Replace known parameters with their values
    for name, val in params.items():
        expr = re.sub(r'\b' + name + r'\b', str(val), expr)

    # Try to evaluate as a simple arithmetic expression
    try:
        # Only allow safe characters: digits, +, -, *, /, (, ), spaces
        if re.match(r'^[\d\s\+\-\*/\(\)]+$', expr):
            return _safe_eval_arith(expr)
    except (SyntaxError, ValueError, ZeroDivisionError):
        pass

    # If it's just a number, return it
    if expr.isdigit():
        return int(expr)

    # If we still can't evaluate, return a safe default
    return 64


def _width_from_slice(slice_txt: str | None, params: Optional[Dict[str, int]] = None) -> int:
    """Calculate width from Verilog range like [7:0] or [IN_W-1:0]."""
    if not slice_txt:
        return 1
    if params is None:
        params = {}
    
    # Extract the range bounds
    range_match = re.search(r'\[(.+):(.+)\]', slice_txt)
    if not range_match:
        return 1
    
    msb_expr = range_match.group(1).strip()
    lsb_expr = range_match.group(2).strip()
    
    # Try to evaluate using parameters
    try:
        msb = _eval_verilog_expr(msb_expr, params)
        lsb = _eval_verilog_expr(lsb_expr, params)
        return abs(msb - lsb) + 1
    except ValueError:
        # Fallback to old behavior for simple numeric ranges
        nums = list(map(int, re.findall(r'\d+', slice_txt)))
        if len(nums) != 2:
            return 1
        return abs(nums[0] - nums[1]) + 1


def _verilog_module_name(vlog_path: Path, fallback: str = "") -> str:
    """Extract module name from Verilog/SystemVerilog file."""
    txt = _strip_sv_comments(vlog_path.read_text())
    m = _VLOG_MOD_RE.search(txt)
    return m.group('name') if m else fallback


def _scan_verilog_ports(vlog_path: Path) -> Dict[str, Tuple[str, int]]:
    """
    Parse ports of the first module in a Verilog/SystemVerilog file.
    Returns: {port_name: (dir, width)} with dir in {'in','out','inout'}.
    Supports:
      - ANSI-style: module m(input [7:0] a, output logic b, ...);
      - Non-ANSI:   module m(a,b); ... input [7:0] a; output b;
      - Parameterized widths: module m #(parameter IN_W=54) (input [IN_W-1:0] din, ...);
    """
    txt = _strip_sv_comments(vlog_path.read_text())

    # First, extract parameter values
    params: Dict[str, int] = {}
    # Look for parameter declarations with default values
    # Matches: parameter IN_W = 54, parameter WIDTH = 32, etc.
    param_pattern = re.compile(r'parameter\s+(?:\w+\s+)?(\w+)\s*=\s*(\d+)', re.M)
    for match in param_pattern.finditer(txt):
        param_name = match.group(1)
        param_value = int(match.group(2))
        params[param_name] = param_value

    # Find module declaration - handle parameters and ports more robustly
    # Match: module name #(params) (ports);
    # We need to find the module keyword, then find matching parentheses for ports
    mod_match = re.search(r'^\s*module\s+(\w+)', txt, re.M)
    if not mod_match:
        return {}
    
    mod_name = mod_match.group(1)
    start_pos = mod_match.end()
    
    # Skip optional parameter section: #( ... )
    param_section_match = re.match(r'\s*#\s*\(', txt[start_pos:])
    if param_section_match:
        # Find the matching closing paren for parameters
        paren_count = 1
        i = start_pos + param_section_match.end()
        while i < len(txt) and paren_count > 0:
            if txt[i] == '(':
                paren_count += 1
            elif txt[i] == ')':
                paren_count -= 1
            i += 1
        start_pos = i
    
    # Now find the port list: ( ... );
    port_section_match = re.match(r'\s*\(', txt[start_pos:])
    if not port_section_match:
        return {}
    
    # Find the matching closing paren and semicolon for ports
    paren_count = 1
    i = start_pos + port_section_match.end()
    port_start = i
    while i < len(txt) and paren_count > 0:
        if txt[i] == '(':
            paren_count += 1
        elif txt[i] == ')':
            paren_count -= 1
        i += 1
    
    header_ports_blob = txt[port_start:i-1]  # Exclude the closing paren
    mod_start = i
    
    # Find semicolon after closing paren
    semi_match = re.match(r'\s*;', txt[mod_start:])
    if semi_match:
        mod_start += semi_match.end()
    
    end_pos = txt.find('endmodule', mod_start)
    body = txt[mod_start:end_pos if end_pos != -1 else len(txt)]

    ports: Dict[str, Tuple[str, int]] = {}

    # Enhanced regex to capture parameterized widths
    _VLOG_DECL_PARAM_RE = re.compile(
        r'^\s*(?P<dir>input|output|inout)\s+'
        r'(?P<attrs>(?:wire|reg|logic|signed|unsigned|\s)*)'
        r'(?:(?P<w0>\[[^\]]+\])\s*)?'
        r'(?P<names>[^;]+);',
        re.M
    )
    _VLOG_NAME_PARAM_RE = re.compile(
        r'^\s*(?:(?P<w>\[[^\]]+\])\s*)?(?P<name>\w+)\s*$'
    )

    # Body decls (works for both styles)
    decls: Dict[str, Tuple[str, int]] = {}
    for dm in _VLOG_DECL_PARAM_RE.finditer(body):
        base_w = _width_from_slice(dm.group('w0'), params)
        direction = dm.group('dir').lower()
        direction = {'input': 'in', 'output': 'out'}.get(direction, 'inout')
        names_blob = dm.group('names')
        for raw in names_blob.split(','):
            nm = raw.strip()
            if not nm:
                continue
            nm_m = _VLOG_NAME_PARAM_RE.match(nm)
            if not nm_m:
                continue
            per_w = _width_from_slice(nm_m.group('w'), params)
            name = nm_m.group('name')
            w = per_w if nm_m.group('w') else base_w
            if name not in decls:
                decls[name] = (direction, w)

    # Parse header port list - handle ANSI-style declarations
    # Split by comma, but be careful with nested brackets
    port_entries = []
    current_entry = []
    paren_depth = 0
    bracket_depth = 0
    
    for char in header_ports_blob:
        if char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth -= 1
        elif char == '[':
            bracket_depth += 1
        elif char == ']':
            bracket_depth -= 1
        elif char == ',' and paren_depth == 0 and bracket_depth == 0:
            port_entries.append(''.join(current_entry).strip())
            current_entry = []
            continue
        current_entry.append(char)
    
    if current_entry:
        port_entries.append(''.join(current_entry).strip())

    # Process each port entry.
    #
    # The blob was split on commas above, which also splits a single
    # multi-name declaration -- `output wire [W-1:0] a, b, c` arrives here as
    # three entries, only the first of which carries the direction and width.
    # In an ANSI port list Verilog says the bare names inherit from the
    # preceding item, so carry it forward. Without this they fell through to
    # the non-ANSI branch and silently became ('in', 1): a real corruption
    # that turned 8-bit outputs into 1-bit inputs, invisible wherever a
    # hand-written contract happened to restate the correct values.
    last_ansi: Optional[Tuple[str, int]] = None
    for entry in port_entries:
        if not entry:
            continue
        
        # Try to match ANSI-style: input/output [width] name
        trial = entry + ';'
        dm = _VLOG_DECL_PARAM_RE.match(trial)
        if dm:
            base_w = _width_from_slice(dm.group('w0'), params)
            direction = dm.group('dir').lower()
            direction = {'input': 'in', 'output': 'out'}.get(direction, 'inout')
            names_blob = dm.group('names')
            for raw in names_blob.split(','):
                nm = raw.strip()
                nm_m = _VLOG_NAME_PARAM_RE.match(nm)
                if not nm_m:
                    continue
                per_w = _width_from_slice(nm_m.group('w'), params)
                name = nm_m.group('name')
                w = per_w if nm_m.group('w') else base_w
                last_ansi = (direction, base_w)
                if name not in ports:
                    ports[name] = (direction, w)
            continue

        # A bare name: either the continuation of the ANSI declaration above,
        # or a non-ANSI header name whose direction/width comes from a body
        # declaration.
        nm_m = _VLOG_NAME_PARAM_RE.match(entry)
        if nm_m:
            name = nm_m.group('name')
            if name in decls:
                ports[name] = decls[name]
            elif last_ansi is not None:
                per_w = _width_from_slice(nm_m.group('w'), params)
                direction, base_w = last_ansi
                ports[name] = (direction, per_w if nm_m.group('w') else base_w)
            else:
                ports[name] = ('in', 1)

    return ports
