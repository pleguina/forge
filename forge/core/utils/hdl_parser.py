"""Minimal HDL parsing utilities for extracting entity/module names and ports."""

from __future__ import annotations

import ast
import operator
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple, Type


def _trunc_div(a: float, b: float) -> float:
    # Verilog and VHDL integer division truncates toward zero.
    if isinstance(a, int) and isinstance(b, int):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q
    return a / b


def _clog2(value: float) -> int:
    n = int(value)
    return 0 if n <= 1 else (n - 1).bit_length()


_ARITH_BINOPS: Dict[Type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: _trunc_div,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}
_ARITH_UNARYOPS: Dict[Type[ast.AST], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ARITH_FUNCS: Dict[str, Callable[[Any], Any]] = {"clog2": _clog2}


def _eval_arith_node(node: ast.AST, names: Optional[Dict[str, int]] = None):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and names is not None and node.id in names:
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_BINOPS:
        return _ARITH_BINOPS[type(node.op)](
            _eval_arith_node(node.left, names), _eval_arith_node(node.right, names)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ARITH_UNARYOPS:
        return _ARITH_UNARYOPS[type(node.op)](_eval_arith_node(node.operand, names))
    if (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in _ARITH_FUNCS and len(node.args) == 1 and not node.keywords
    ):
        return _ARITH_FUNCS[node.func.id](_eval_arith_node(node.args[0], names))
    raise ValueError(f"Unsupported arithmetic expression node: {ast.dump(node)}")


def _safe_eval_arith(expr: str, names: Optional[Dict[str, int]] = None) -> int:
    """Evaluate an integer constant expression without eval().

    Accepts integer literals, ``+ - * / % ** << >> & | ^``, parentheses,
    ``clog2(x)`` and, when *names* is given, the identifiers it defines.
    """
    tree = ast.parse(expr.strip(), mode="eval")
    return int(_eval_arith_node(tree.body, names))


class UnresolvedWidthError(ValueError):
    """A port's width depends on something the source scan cannot evaluate.

    ``unresolved`` maps each such port to its declared range, so the message
    can name what the user has to declare instead of a guessed width.
    """

    def __init__(self, unresolved: Dict[str, str], where: str = "") -> None:
        self.unresolved = dict(unresolved)
        self.where = where
        listed = ", ".join(f"{name} {rng}" for name, rng in sorted(self.unresolved.items()))
        prefix = f"{where}: " if where else ""
        super().__init__(
            f"{prefix}cannot determine the width of port(s) {listed} from the "
            f"source (the range uses a macro, package constant or function this "
            f"scanner does not evaluate); declare them under `widths:` for the "
            f"module in forge.yml, or `port_widths:` in a hand-written module "
            f"registry"
        )


class PortDecl(NamedTuple):
    """One scanned port. ``width`` is None when its range could not be evaluated;
    ``range_text`` then holds the declared range."""

    direction: str
    width: Optional[int]
    range_text: str = ""


def _strict_ports(decls: Dict[str, PortDecl], where: str = "") -> Dict[str, Tuple[str, int]]:
    unresolved = {n: d.range_text for n, d in decls.items() if d.width is None}
    if unresolved:
        raise UnresolvedWidthError(unresolved, where)
    return {n: (d.direction, d.width) for n, d in decls.items() if d.width is not None}


def apply_declared_widths(
    decls: Dict[str, PortDecl], declared: Optional[Mapping[str, Any]], where: str = "",
) -> Dict[str, PortDecl]:
    """Fill unresolved widths from user *declared* ``{port: width}``.

    A declaration for a port whose width the source does state must agree
    with it, and one for a port that does not exist is refused: either is a
    declaration that has drifted from the HDL.
    """
    if not declared:
        return decls
    prefix = f"{where}: " if where else ""
    out = dict(decls)
    for port, value in declared.items():
        if port not in out:
            raise ValueError(f"{prefix}declared width for unknown port {port!r}")
        width = int(value)
        decl = out[port]
        if decl.width is not None and decl.width != width:
            raise ValueError(
                f"{prefix}declared width {width} for port {port!r} contradicts "
                f"the source, which gives {decl.width}"
            )
        out[port] = PortDecl(decl.direction, width)
    return out


def parse_verilog_defines(entries: Iterable[str]) -> Dict[str, str]:
    """``["NAME=VALUE", "FLAG"]`` (a registry's ``verilog_defines``) as a dict."""
    out: Dict[str, str] = {}
    for entry in entries or ():
        name, _, value = str(entry).partition("=")
        if name.strip():
            out[name.strip()] = value.strip()
    return out


def scan_hdl_port_decls(
    path: Path,
    parameters: Optional[Mapping[str, Any]] = None,
    defines: Optional[Mapping[str, str]] = None,
) -> Dict[str, PortDecl]:
    """:func:`scan_verilog_port_decls` or :func:`scan_vhdl_port_decls` by suffix."""
    text = path.read_text()
    if path.suffix.lower() in (".vhd", ".vhdl"):
        return scan_vhdl_port_decls(text, parameters)
    return scan_verilog_port_decls(text, parameters, defines)


def scan_module_ports(
    path: Path,
    parameters: Optional[Mapping[str, Any]] = None,
    defines: Optional[Mapping[str, str]] = None,
    declared_widths: Optional[Mapping[str, Any]] = None,
    where: str = "",
) -> Dict[str, Tuple[str, int]]:
    """Ports of the unit in *path* as an instance with *parameters* sees them.

    Raises UnresolvedWidthError for any width neither the source nor
    *declared_widths* determines.
    """
    where = where or str(path)
    decls = apply_declared_widths(scan_hdl_port_decls(path, parameters, defines), declared_widths, where)
    return _strict_ports(decls, where)


# ==================== VHDL Parsing ====================

_VHDL_PORT_RE = re.compile(
    r"^\s*(?P<name>\w+)\s*:\s*(?P<dir>in|out|inout)\s+"
    r"(?P<type>(?:std_u?logic|signed|unsigned)(?:_vector)?(?:\s*\([^)]+\))?)"
    r"\s*;?\s*(?:--.*)?$",
    re.IGNORECASE,
)
_VHDL_GENERIC_RE = re.compile(r"(\w+)\s*:\s*\w+(?:\s+range\s+[^:;]+)?\s*:=\s*([^;]+)", re.I)

_FW_ENTITY_RE = re.compile(r"^\s*entity\s+(?P<name>\w+)\s+is", re.I)


def _vhdl_entity_name(vhdl_path: Path, fallback: str = "") -> str:
    """Extract entity name from VHDL file."""
    for ln in vhdl_path.read_text().splitlines():
        m = _FW_ENTITY_RE.match(ln)
        if m:
            return m.group("name")
    return fallback


def _eval_vhdl_expr(expr: str, generics: Dict[str, int]) -> int:
    """Evaluate simple VHDL expressions like 'N-1', 'WIDTH+2', or just '15'.

    Raises ValueError when *expr* uses anything other than integers, the
    known *generics* and arithmetic.
    """
    try:
        return _safe_eval_arith(expr, generics)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError):
        raise ValueError(f"Cannot evaluate VHDL expression: {expr}") from None


def _scan_ports(vhdl_path: Path) -> Dict[str, Tuple[str, int]]:
    """
    Scan VHDL entity ports.
    
    Returns:
        Dict mapping port name to (direction, width as int)
        direction: 'in', 'out', 'inout'
        width: integer bit width

    Raises UnresolvedWidthError when a range cannot be evaluated.
    """
    return _strict_ports(scan_vhdl_port_decls(vhdl_path.read_text()), str(vhdl_path))


def _scan_ports_text(content: str) -> Dict[str, Tuple[str, int]]:
    """:func:`_scan_ports` against already-read VHDL text.

    Split out so a caller holding one entity's slice of a multi-entity file
    (``forge.core.project.hdl_scan``) can scan that slice directly instead of
    writing it back to a temporary file first.
    """
    return _strict_ports(scan_vhdl_port_decls(content))


def scan_vhdl_port_decls(
    content: str, generic_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, PortDecl]:
    """Every port of the first entity in *content*, unresolved widths included.

    *generic_overrides* are the instance's generic map and replace defaults.
    """
    ports: Dict[str, PortDecl] = {}
    inside = False
    generics: Dict[str, int] = {}
    overrides = generic_overrides or {}

    generic_section = re.search(r'generic\s*\((.*?)\);', content, re.DOTALL | re.I)
    if generic_section:
        generic_txt = re.sub(r"--.*$", "", generic_section.group(1), flags=re.M)
        for line in generic_txt.split(";"):
            gen_match = _VHDL_GENERIC_RE.search(line)
            if gen_match:
                name = gen_match.group(1)
                value = str(overrides[name]) if name in overrides else gen_match.group(2)
                try:
                    generics[name] = _eval_vhdl_expr(value, generics)
                except ValueError:
                    pass

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
        type_txt = m["type"]
        width: Optional[int] = 1
        range_txt = ""
        if "(" in type_txt or type_txt.lower().endswith("_vector") \
                or type_txt.lower() in ("signed", "unsigned"):
            range_txt = type_txt[type_txt.find("("):] if "(" in type_txt else type_txt
            range_match = re.search(r'\(([^)]+?)\s+(?:downto|to)\s+([^)]+)\)', type_txt, re.I)
            width = None
            if range_match:
                try:
                    hi_val = _eval_vhdl_expr(range_match.group(1), generics)
                    lo_val = _eval_vhdl_expr(range_match.group(2), generics)
                    width = abs(hi_val - lo_val) + 1
                except ValueError:
                    width = None
        ports[name] = PortDecl(direction, width, "" if width is not None else range_txt)
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


_VLOG_BASED_LITERAL_RE = re.compile(
    r"(?:\b\d[\d_]*\s*)?'[sS]?(?P<base>[bBoOdDhH])\s*(?P<digits>[0-9a-fA-F_xXzZ?]+)"
)
_VLOG_BASES = {"b": 2, "o": 8, "d": 10, "h": 16}


def _verilog_expr_to_python(expr: str, defines: Optional[Dict[str, str]] = None) -> str:
    """Rewrite a Verilog constant expression into the syntax _safe_eval_arith reads."""

    def _macro(m: "re.Match[str]") -> str:
        name = m.group(1)
        if defines is None or name not in defines:
            raise ValueError(f"unknown macro `{name}")
        return f"({defines[name]})"

    def _based(m: "re.Match[str]") -> str:
        digits = m.group("digits").replace("_", "")
        if re.search(r"[xXzZ?]", digits):
            raise ValueError(f"x/z literal {m.group(0)!r}")
        return str(int(digits, _VLOG_BASES[m.group("base").lower()]))

    for _ in range(8):
        if "`" not in expr:
            break
        expr = re.sub(r"`(\w+)", _macro, expr)
    expr = _VLOG_BASED_LITERAL_RE.sub(_based, expr)
    expr = re.sub(r"(?<=\d)_(?=\d)", "", expr)
    expr = expr.replace("<<<", "<<").replace(">>>", ">>")
    if "$" in expr:
        expr = re.sub(r"\$clog2\s*\(", "clog2(", expr)
        if "$" in expr:
            raise ValueError(f"unsupported system function in {expr!r}")
    return expr


def _eval_verilog_expr(
    expr: str, params: Dict[str, int], defines: Optional[Dict[str, str]] = None,
) -> int:
    """Evaluate a Verilog constant expression: literals, *params*, arithmetic,
    shifts, ``$clog2`` and macros from *defines*.

    Raises ValueError when anything else is involved.
    """
    try:
        return _safe_eval_arith(_verilog_expr_to_python(expr, defines), params)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise ValueError(f"Cannot evaluate Verilog expression {expr!r}: {exc}") from None


def _width_from_slice(
    slice_txt: str | None,
    params: Optional[Dict[str, int]] = None,
    defines: Optional[Dict[str, str]] = None,
) -> int:
    """Calculate width from Verilog range like [7:0] or [IN_W-1:0].

    Raises ValueError when the range cannot be evaluated.
    """
    if not slice_txt:
        return 1
    range_match = re.fullmatch(r'\s*\[(.+):(.+)\]\s*', slice_txt, re.S)
    if not range_match:
        raise ValueError(f"not a packed range: {slice_txt!r}")
    msb = _eval_verilog_expr(range_match.group(1), params or {}, defines)
    lsb = _eval_verilog_expr(range_match.group(2), params or {}, defines)
    return abs(msb - lsb) + 1


def _optional_width(
    slice_txt: str | None, params: Dict[str, int], defines: Dict[str, str],
) -> Optional[int]:
    try:
        return _width_from_slice(slice_txt, params, defines)
    except ValueError:
        return None


_VLOG_DEFINE_RE = re.compile(r"^[ \t]*`define[ \t]+(\w+)(?:[ \t]+(.*?))?[ \t]*$", re.M)
_VLOG_PARAM_KEYWORD_RE = re.compile(r"\b(?:parameter|localparam)\b")


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """Split *text* on *sep* outside (), [] and {}."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _verilog_parameters(
    txt: str, defines: Dict[str, str], overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Values of every ``parameter``/``localparam`` in *txt* that evaluates, in
    declaration order so later ones may use earlier ones. *overrides* are the
    instance's parameter assignments and replace the declared defaults."""
    params: Dict[str, int] = {}
    overrides = overrides or {}
    for kw in _VLOG_PARAM_KEYWORD_RE.finditer(txt):
        # The declaration list runs to ';' or to the ')' closing a #( ... ).
        depth = 0
        end = kw.end()
        while end < len(txt):
            ch = txt[end]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            end += 1
        for piece in _split_top_level(txt[kw.end():end]):
            if _VLOG_PARAM_KEYWORD_RE.search(piece):
                break  # the next declaration has its own keyword match
            m = re.match(r"^(.*?)\b(\w+)\s*(?:\[[^\]]*\]\s*)*=(?!=)\s*(.+)$", piece.strip(), re.S)
            if not m:
                continue
            name = m.group(2)
            value = str(overrides[name]) if name in overrides else m.group(3)
            try:
                params[name] = _eval_verilog_expr(value, params, defines)
            except ValueError:
                params.pop(name, None)
    return params


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
    return _scan_verilog_ports_text(vlog_path.read_text())


def _scan_verilog_ports_text(source: str) -> Dict[str, Tuple[str, int]]:
    """:func:`_scan_verilog_ports` against already-read Verilog text.

    Split out so a caller holding one module's slice of a multi-module file
    (``forge.core.project.hdl_scan``) can scan that slice directly instead of
    writing it back to a temporary file first.

    Raises UnresolvedWidthError when a port's range cannot be evaluated.
    """
    return _strict_ports(scan_verilog_port_decls(source))


def scan_verilog_port_decls(
    source: str,
    parameters: Optional[Mapping[str, Any]] = None,
    defines: Optional[Mapping[str, str]] = None,
) -> Dict[str, PortDecl]:
    """Every port of the first module in *source*, unresolved widths included.

    *parameters* are instance overrides of the module's parameters; *defines*
    are macros set outside the file (``+define+``), which the file's own
    ```define`` lines take precedence over.
    """
    txt = _strip_sv_comments(source)
    defines = dict(defines or {})
    defines.update({m.group(1): (m.group(2) or "") for m in _VLOG_DEFINE_RE.finditer(txt)})

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
    params = _verilog_parameters(txt[mod_match.start():mod_start] + body, defines, parameters)

    def _decl(direction: str, slice_txt: Optional[str]) -> PortDecl:
        width = _optional_width(slice_txt, params, defines)
        return PortDecl(direction, width, "" if width is not None else (slice_txt or "").strip())

    ports: Dict[str, PortDecl] = {}

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
    decls: Dict[str, PortDecl] = {}
    for dm in _VLOG_DECL_PARAM_RE.finditer(body):
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
            name = nm_m.group('name')
            if name not in decls:
                decls[name] = _decl(direction, nm_m.group('w') or dm.group('w0'))

    # Parse header port list - handle ANSI-style declarations
    port_entries = [entry.strip() for entry in _split_top_level(header_ports_blob)]

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
    last_ansi: Optional[Tuple[str, Optional[str]]] = None
    for entry in port_entries:
        if not entry:
            continue
        
        # Try to match ANSI-style: input/output [width] name
        trial = entry + ';'
        decl = _VLOG_DECL_PARAM_RE.match(trial)
        if decl:
            direction = decl.group('dir').lower()
            direction = {'input': 'in', 'output': 'out'}.get(direction, 'inout')
            names_blob = decl.group('names')
            for raw in names_blob.split(','):
                nm = raw.strip()
                nm_m = _VLOG_NAME_PARAM_RE.match(nm)
                if not nm_m:
                    continue
                name = nm_m.group('name')
                last_ansi = (direction, decl.group('w0'))
                if name not in ports:
                    ports[name] = _decl(direction, nm_m.group('w') or decl.group('w0'))
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
                direction, base_slice = last_ansi
                ports[name] = _decl(direction, nm_m.group('w') or base_slice)
            else:
                ports[name] = PortDecl('in', 1)

    return ports
