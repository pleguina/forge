"""Extract an HLS top function's interface from its C++ source.

`forge.hls.port_prediction` predicts RTL ports from a resolved argument
list — name, element width, array dimensions, interface mode. This module
produces that list from the source, so the prediction can run on a module
that has never been synthesised.

It is deliberately not a C++ parser. It resolves the narrow slice HLS
interfaces actually depend on:

* ``#define`` integer constants, including simple arithmetic
* ``typedef`` chains down to ``ap_uint``/``ap_int``/plain integer types
* array typedefs and inline array parameters, including multi-dimensional
* ``struct`` definitions, recorded as structs rather than width-summed
  (HLS pads members; see the port-matrix fixture)
* the top function's signature — parameter names, const/reference, return
* ``#pragma HLS INTERFACE`` and ``ARRAY_PARTITION`` directives

What it cannot resolve it reports. A width it could not determine is None,
and the caller is told which argument and why, rather than being handed a
plausible-looking guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from forge.hls.port_prediction import Argument

#: Widths of the plain C types HLS maps onto fixed-width ports.
_BUILTIN_WIDTHS: Dict[str, int] = {
    "bool": 1, "char": 8, "signed char": 8, "unsigned char": 8,
    "short": 16, "unsigned short": 16, "int": 32, "unsigned": 32,
    "unsigned int": 32, "long": 64, "unsigned long": 64,
    "long long": 64, "unsigned long long": 64,
    "uint8_t": 8, "int8_t": 8, "uint16_t": 16, "int16_t": 16,
    "uint32_t": 32, "int32_t": 32, "uint64_t": 64, "int64_t": 64,
    "float": 32, "double": 64,
}

_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.S | re.M)
_DEFINE_RE = re.compile(r"^\s*#define\s+(\w+)\s+(.+?)\s*$", re.M)
#: `static const int W = 32;` / `constexpr unsigned W = 32;` — just as common
#: as #define in HLS headers, and previously invisible to the resolver.
_CONST_RE = re.compile(
    r"^\s*(?:static\s+)?(?:const|constexpr)\s+(?:unsigned\s+|signed\s+)?"
    r"(?:int|short|long|char|size_t|unsigned)\s+(\w+)\s*=\s*([^;]+);", re.M)
_AP_INT_RE = re.compile(r"^\s*ap_(u?)(?:int|fixed)\s*<\s*([^,>]+?)\s*(?:,[^>]*)?>\s*$")
_TYPEDEF_SCALAR_RE = re.compile(r"^\s*typedef\s+(.+?)\s+(\w+)\s*;", re.M)
_TYPEDEF_ARRAY_RE = re.compile(r"^\s*typedef\s+(.+?)\s+(\w+)\s*((?:\[[^\]]+\])+)\s*;", re.M)
_STRUCT_RE = re.compile(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+)\s*;", re.S)
_PRAGMA_IFACE_RE = re.compile(
    r"#pragma\s+HLS\s+INTERFACE\s+(?P<mode>\w+)(?P<rest>[^\n]*)", re.I)
_PRAGMA_PARTITION_RE = re.compile(
    r"#pragma\s+HLS\s+ARRAY_PARTITION\s+(?P<rest>[^\n]*)", re.I)
_DIMS_RE = re.compile(r"\[([^\]]+)\]")


@dataclass
class ParsedSignature:
    """What a static read of the source could determine."""
    top: str
    args: List[Argument] = field(default_factory=list)
    block_protocol: str = "ap_ctrl_hs"
    returns_value: bool = False
    return_width: Optional[int] = None
    return_is_struct: bool = False
    warnings: List[str] = field(default_factory=list)


class _Types:
    """Resolves #define constants and typedef chains to widths and dims."""

    def __init__(self, text: str) -> None:
        self.defines: Dict[str, int] = {}
        self.scalars: Dict[str, str] = {}          # name -> underlying type text
        self.arrays: Dict[str, Tuple[str, List[str]]] = {}  # name -> (elem, dim exprs)
        self.structs: Dict[str, List[str]] = {}   # name -> member type texts
        self._load(text)

    # ── loading ──────────────────────────────────────────────────────────
    def _load(self, text: str) -> None:
        for name, body in _DEFINE_RE.findall(text):
            value = self.eval_int(body)
            if value is not None:
                self.defines[name] = value
        for name, body in _CONST_RE.findall(text):
            value = self.eval_int(body)
            if value is not None:
                self.defines.setdefault(name, value)
        for body, name in _STRUCT_RE.findall(text):
            members = []
            for line in body.split(";"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens = line.split()
                if len(tokens) >= 2:
                    members.append(" ".join(tokens[:-1]))
            self.structs[name] = members
        for elem, name, dims in _TYPEDEF_ARRAY_RE.findall(text):
            self.arrays[name] = (elem.strip(), _DIMS_RE.findall(dims))
        for elem, name in _TYPEDEF_SCALAR_RE.findall(text):
            if name not in self.arrays and name not in self.structs:
                self.scalars[name] = elem.strip()

    # ── evaluation ───────────────────────────────────────────────────────
    def eval_int(self, expr: str) -> Optional[int]:
        """Evaluate a constant integer expression, or None.

        Handles the `#define N ( 18 )` and `WIDTH-1` forms real HLS headers
        use. Restricted to digits, known constants and arithmetic — never
        evaluates arbitrary text.
        """
        expr = expr.strip().rstrip(";").strip()
        if not expr:
            return None
        substituted = re.sub(
            r"\b([A-Za-z_]\w*)\b",
            lambda m: str(self.defines[m.group(1)]) if m.group(1) in self.defines else m.group(1),
            expr,
        )
        if not re.fullmatch(r"[\d\s()+\-*/<>]+", substituted):
            return None
        try:
            value = eval(substituted, {"__builtins__": {}}, {})  # noqa: S307 - guarded above
        except Exception:
            return None
        return int(value) if isinstance(value, (int, float)) else None

    def width_of(self, type_text: str) -> Optional[int]:
        """Bit width of a scalar type, following typedef chains."""
        t = type_text.strip().replace("const", "").strip()
        for _ in range(16):                       # chain-depth guard
            t = t.strip()
            m = _AP_INT_RE.match(t)
            if m:
                return self.eval_int(m.group(2))
            if t in _BUILTIN_WIDTHS:
                return _BUILTIN_WIDTHS[t]
            if t in self.scalars:
                t = self.scalars[t]
                continue
            return None
        return None

    def struct_width(self, name: str) -> Optional[int]:
        """Sum of a struct's member widths, or None if any is unresolvable.

        Only meaningful where HLS *bit-packs* the struct — see ``resolve``.
        """
        total = 0
        for member in self.structs.get(name, []):
            width = self.width_of(member)
            if width is None:
                return None
            total += width
        return total or None

    def resolve(self, type_text: str) -> Tuple[Optional[int], List[int], bool]:
        """(element width, dims, is_struct) for a parameter's declared type."""
        t = type_text.strip().replace("const", "").strip()
        dims: List[int] = []
        for _ in range(16):
            if t in self.arrays:
                elem, dim_exprs = self.arrays[t]
                for d in dim_exprs:
                    value = self.eval_int(d)
                    if value is None:
                        return (None, dims, False)
                    dims.append(value)
                t = elem.strip()
                continue
            break
        if t in self.structs:
            # Where the struct is an *array element*, HLS bit-packs it and the
            # width is exactly the sum of its members. As a scalar argument or
            # a return value it pads instead. Established by synthesis: the
            # same packed_t is 16 bits as an array element and 48 as a scalar
            # (see m_structs in the port-matrix fixture), which is also why
            # T_GP_HIT resolves to 1+11+9=21 in a real design.
            return (self.struct_width(t) if dims else None, dims, True)
        return (self.width_of(t), dims, False)


def _split_params(text: str) -> List[str]:
    """Split a parameter list on commas that aren't inside <> or ()."""
    out, depth, current = [], 0, []
    for ch in text:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(current)); current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        out.append("".join(current))
    return [p.strip() for p in out if p.strip()]


def _find_top(text: str, top: str) -> Optional[re.Match]:
    return re.search(
        rf"(?P<ret>[\w:<>,\s\*&]+?)\s+{re.escape(top)}\s*\((?P<params>[^)]*)\)\s*\{{",
        text, re.S)


def parse_signature(source: Path | str, top: str) -> ParsedSignature:
    """Read *top*'s interface out of an HLS C++ source (plus its includes)."""
    source = Path(source)
    text = _COMMENT_RE.sub("", source.read_text(errors="replace"))

    # Pull in headers so typedefs and constants resolve. Follows local quoted
    # includes *transitively* — a top's .cpp typically includes its own
    # header, which includes the project's shared types header, and the
    # widths live in that third file. Only quoted includes, only within the
    # project tree, with a visited set and a depth cap.
    combined = [text]
    seen = {source.resolve()}
    search_roots = [source.parent, source.parent.parent]
    pending = [(text, 0)]
    while pending:
        current, depth = pending.pop()
        if depth >= 4:
            continue
        for inc in re.findall(r'#include\s+"([^"]+)"', current):
            candidate = None
            for base in search_roots:
                direct = (base / inc)
                if direct.is_file():
                    candidate = direct.resolve()
                    break
            if candidate is None and source.parent.parent.is_dir():
                # Shared headers often sit in a sibling directory the build
                # passes with -I; find by basename rather than guessing.
                matches = sorted(source.parent.parent.rglob(Path(inc).name))
                if matches:
                    candidate = matches[0].resolve()
            if candidate is None or candidate in seen:
                continue
            seen.add(candidate)
            body = _COMMENT_RE.sub("", candidate.read_text(errors="replace"))
            combined.append(body)
            search_roots.append(candidate.parent)
            pending.append((body, depth + 1))
    all_text = "\n".join(combined)

    types = _Types(all_text)
    result = ParsedSignature(top=top)

    match = _find_top(text, top) or _find_top(all_text, top)
    if match is None:
        result.warnings.append(f"top function {top!r} not found in {source.name}")
        return result

    body = text[match.end():]
    body = body[:body.find("\n}")] if "\n}" in body else body

    # ── pragmas ──────────────────────────────────────────────────────────
    modes: Dict[str, str] = {}
    for m in _PRAGMA_IFACE_RE.finditer(body):
        mode = m.group("mode").lower()
        rest = m.group("rest")
        port = re.search(r"port\s*=\s*(\w+)", rest)
        if mode == "ap_ctrl_none" or (port and port.group(1) == "return"):
            if mode.startswith("ap_ctrl"):
                result.block_protocol = mode
                continue
            if mode in ("s_axilite", "axis", "m_axi"):
                result.block_protocol = "ap_ctrl_hs"
        if port:
            modes[port.group(1)] = mode

    partitions: Dict[str, int] = {}
    for m in _PRAGMA_PARTITION_RE.finditer(body):
        rest = m.group("rest")
        var = re.search(r"variable\s*=\s*(\w+)", rest)
        if not var or "complete" not in rest.lower():
            continue
        dim = re.search(r"dim\s*=\s*(\d+)", rest)
        # A bare `complete` means dim=1, not dim=0. Confirmed by synthesis:
        # see the m_dimdefault case in the port-matrix fixture.
        partitions[var.group(1)] = int(dim.group(1)) if dim else 1

    # ── return type ──────────────────────────────────────────────────────
    ret = match.group("ret").strip().split()[-1] if match.group("ret").strip() else "void"
    if ret != "void":
        result.returns_value = True
        width, dims, is_struct = types.resolve(ret)
        result.return_is_struct = is_struct
        result.return_width = width
        if is_struct:
            result.warnings.append(
                f"return type {ret!r} is a struct — HLS pads members rather "
                f"than concatenating them, so ap_return's width is not "
                f"predicted; read it from the built IP"
            )
        elif width is None:
            result.warnings.append(f"could not resolve the width of return type {ret!r}")

    # ── parameters ───────────────────────────────────────────────────────
    for param in _split_params(match.group("params")):
        inline_dims = _DIMS_RE.findall(param)
        cleaned = _DIMS_RE.sub("", param).strip()
        is_output = "&" in cleaned or "*" in cleaned
        cleaned = cleaned.replace("&", " ").replace("*", " ")
        tokens = cleaned.split()
        if len(tokens) < 2:
            result.warnings.append(f"could not read parameter {param.strip()!r}")
            continue
        name = tokens[-1]
        type_text = " ".join(tokens[:-1])

        width, dims, is_struct = types.resolve(type_text)
        for d in inline_dims:
            value = types.eval_int(d)
            if value is None:
                result.warnings.append(
                    f"{name}: could not evaluate array dimension {d!r}")
                break
            dims.append(value)

        # An array parameter is written to unless declared const.
        if dims and "const" in param:
            is_output = False
        elif dims and not is_output:
            is_output = "const" not in param and False

        if width is None and not is_struct:
            result.warnings.append(
                f"{name}: could not resolve the width of type {type_text!r}")
        if is_struct:
            result.warnings.append(
                f"{name}: struct width is not predicted (HLS pads members)")

        result.args.append(Argument(
            name=name,
            width=width or 0,
            is_output=is_output,
            dims=tuple(dims),
            mode=modes.get(name),
            partition_dim=partitions.get(name),
            is_struct=is_struct,
        ))

    return result
