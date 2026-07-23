#!/usr/bin/env python3
"""RTL port introspection — layered parser for port extraction.

This module replaces ad-hoc regex port parsing with a layered approach:

  Mode A — structured Verilog parser
      Uses ``pyverilog`` if available in the environment.  Handles full
      Verilog module port declarations robustly, including parameters,
      multi-dimensional arrays, and SV-style port lists.

  Mode B — controlled HLS regex fallback
      The existing HLS-output regex from ``port_parser.py``, scoped to
      known HLS port forms.  Activated when pyverilog is absent or when the
      caller requests it explicitly via ``mode="regex"``.

Both modes produce the same output: a list of ``PortInfo`` objects.

The public API is intentionally backward-compatible with the original
``port_parser`` module.  Callers previously using ``port_parser`` should
switch to this module; the old module now delegates here.

Public API
----------
  PortInfo                      dataclass — name, direction, width
  extract_ports(path, *, mode)  → list[PortInfo]
  write_port_map_yaml(ports, out_path)        → None
  write_port_signature(ports, out_path)       → None
  introspection_mode(verilog_path)            → str  ("parser" | "regex")

Usage
-----
::

    from forge.verify.rtl_introspection import extract_ports, write_port_map_yaml
    ports = extract_ports(Path("build_hls/my_module/my_module.v"))
    write_port_map_yaml(ports, Path("my_flow/port_map.yaml"))
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ── Introspection error hierarchy ──────────────────────────────────────────

class IntrospectionError(RuntimeError):
    """Base class for RTL introspection failures.

    Always includes an ``action`` string telling the caller exactly what
    to do next, so the error bubbles up as an actionable diagnostic.
    """
    def __init__(self, message: str, *, action: str) -> None:
        super().__init__(message)
        self.action = action

    def __str__(self) -> str:
        return f"{super().__str__()}\n  → {self.action}"


class RTLFileNotFound(IntrospectionError):
    """The Verilog source file does not exist on disk."""
    def __init__(self, verilog_path: Path) -> None:
        super().__init__(
            f"RTL file not found: {verilog_path}",
            action=(
                "Run HLS synthesis first, then re-run forge verify generate.\n"
                f"  Expected: {verilog_path}"
            ),
        )
        self.verilog_path = verilog_path


class ParserDependencyMissing(IntrospectionError):
    """mode='parser' was requested but pyverilog is not installed."""
    def __init__(self) -> None:
        super().__init__(
            "Structured parser mode requires pyverilog, which is not installed.",
            action=(
                "Install the parser extra:\n"
                "    pip install pyverilog\n"
                "  or:\n"
                "    pip install 'fw-verify[parser]'\n"
                "  Alternatively, use mode='regex' for HLS-generated RTL, "
                "or provide a hand-authored port_map.yaml."
            ),
        )


class ParserModeFailed(IntrospectionError):
    """pyverilog was available but failed to parse the given file."""
    def __init__(self, verilog_path: Path, reason: str) -> None:
        super().__init__(
            f"pyverilog parser failed on {verilog_path.name}: {reason}",
            action=(
                "Check that the file is valid Verilog.  "
                "If it uses SV-specific syntax, install pyverilog >=1.3 or "
                "write a manual port_map.yaml in the flow directory."
            ),
        )
        self.verilog_path = verilog_path
        self.reason = reason


class RegexFallbackUnsupported(IntrospectionError):
    """The regex fallback cannot handle this RTL style."""
    def __init__(self, verilog_path: Path, reason: str) -> None:
        super().__init__(
            f"HLS-regex port extraction not suited for {verilog_path.name}: {reason}",
            action=(
                "Options:\n"
                "  1. Install pyverilog for full Verilog support:\n"
                "       pip install pyverilog\n"
                "  2. Set  rtl_source_type: rtl  in design.verification.yml.\n"
                "  3. Write port_map.yaml by hand in the flow directory "
                "     (the framework will use it and skip extraction).\n"
                "  4. If this file is HLS output, ensure it uses ap_ctrl_none "
                "     port style (scalar / bus input/output statements on "
                "     separate lines ending with ';')."
            ),
        )
        self.verilog_path = verilog_path
        self.reason = reason


class ManualPortMapRequired(IntrospectionError):
    """No extraction mode can handle this RTL; a manual port_map.yaml is needed."""
    def __init__(self, verilog_path: Path, reason: str) -> None:
        super().__init__(
            f"Automatic port extraction failed for {verilog_path.name}: {reason}",
            action=(
                "Write port_map.yaml by hand in the flow directory.\n"
                "  The framework will use the existing file and skip auto-extraction.\n"
                "  Schema:\n"
                "    ports:\n"
                "      - name:      my_signal\n"
                "        direction: input\n"
                "        width:     32"
            ),
        )


# ── Port data model ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PortInfo:
    """Description of one RTL port."""
    name:      str
    direction: str   # "input" | "output" | "inout"
    width:     int   # bit width; 1 for scalars


# ── Mode detection ─────────────────────────────────────────────────────────

def _pyverilog_available() -> bool:
    """Return True if pyverilog is importable."""
    try:
        import importlib
        importlib.import_module("pyverilog")
        return True
    except ImportError:
        return False


def introspection_mode(verilog_path: Path | None = None) -> str:
    """Return the active introspection mode: ``"parser"`` or ``"regex"``.

    ``"parser"`` is returned when pyverilog is available in the environment.
    ``"regex"`` is returned as a controlled HLS-scoped fallback.

    The *verilog_path* argument is accepted for API symmetry but currently
    unused — mode selection is environment-level, not file-level.
    """
    return "parser" if _pyverilog_available() else "regex"


# ── Mode A: pyverilog-based parser ─────────────────────────────────────────

def _extract_ports_pyverilog(verilog_path: Path) -> list[PortInfo]:
    """Extract ports using the pyverilog structured Verilog parser."""
    import pyverilog.vparser.parser as pvparser  # type: ignore[import]
    import pyverilog.vparser.ast as pvast        # type: ignore[import]

    ast, _ = pvparser.parse([str(verilog_path)])
    ports: list[PortInfo] = []

    def _visit(node: Any) -> None:
        if isinstance(node, pvast.Ioport):
            io = node.first
            if isinstance(io, (pvast.Input, pvast.Output, pvast.Inout)):
                direction = (
                    "input"  if isinstance(io, pvast.Input)  else
                    "output" if isinstance(io, pvast.Output) else
                    "inout"
                )
                width = 1
                if io.width is not None:
                    # width is a Range node: msb:lsb
                    try:
                        msb = int(io.width.msb.value)
                        lsb = int(io.width.lsb.value)
                        width = msb - lsb + 1
                    except (AttributeError, ValueError):
                        width = 1
                name = io.name if isinstance(io.name, str) else str(io.name)
                ports.append(PortInfo(name=name, direction=direction, width=width))
        for child in node.children():
            _visit(child)

    _visit(ast)
    return ports


# ── Mode B: controlled HLS regex fallback ─────────────────────────────────

_HLS_PORT_RE = re.compile(
    r"^\s*(input|output)\s+"
    r"(?:\[(\d+):(\d+)\]\s*)?"
    r"(\w+)\s*;",
    re.MULTILINE,
)


def _extract_ports_regex(verilog_path: Path) -> list[PortInfo]:
    """Extract port declarations using the controlled HLS-output regex.

    Handles:
      ``input  [W:0] name;``   — bus input
      ``input  name;``          — scalar input
      ``output [W:0] name;``   — bus output
      ``output name;``          — scalar output

    Does NOT handle ``inout`` or SV-style port lists by design — those
    require the pyverilog mode.
    """
    text = verilog_path.read_text(errors="replace")
    ports: list[PortInfo] = []
    for m in _HLS_PORT_RE.finditer(text):
        direction         = m.group(1)
        hi, lo, name      = m.group(2), m.group(3), m.group(4)
        width = (int(hi) - int(lo) + 1) if hi is not None else 1
        ports.append(PortInfo(name=name, direction=direction, width=width))

    # If we found zero ports, the file may use ANSI-style or SV port lists
    # which the HLS regex cannot handle — signal this explicitly.
    if not ports:
        raise RegexFallbackUnsupported(
            verilog_path,
            reason=(
                "No ports matched the HLS-output pattern.  "
                "The file may use ANSI-style port lists or SystemVerilog syntax."
            ),
        )
    return ports


# ── Main public extraction entry point ────────────────────────────────────

def extract_ports(
    verilog_path: Path,
    *,
    mode: Literal["auto", "parser", "regex"] = "auto",
) -> list[PortInfo]:
    """Extract RTL port declarations from *verilog_path*.

    Args:
        verilog_path: Path to the ``.v`` or ``.sv`` file.
        mode:         Extraction mode:
                      ``"auto"``   — Mode A (pyverilog) if available, else Mode B.
                      ``"parser"`` — Force Mode A (pyverilog); raises if not installed.
                      ``"regex"``  — Force Mode B (HLS regex fallback).

    Returns:
        List of ``PortInfo`` objects in declaration order.

    Raises:
        RTLFileNotFound:          if *verilog_path* does not exist.
        ParserDependencyMissing:  if mode ``"parser"`` requested but pyverilog absent.
        ParserModeFailed:         if pyverilog is present but fails to parse the file.
        RegexFallbackUnsupported: if regex mode finds no ports (wrong RTL style).
        ValueError:               if *mode* is not a recognised value.
    """
    verilog_path = Path(verilog_path)
    if not verilog_path.exists():
        raise RTLFileNotFound(verilog_path)

    if mode == "auto":
        if _pyverilog_available():
            try:
                return _extract_ports_pyverilog(verilog_path)
            except Exception as exc:  # noqa: BLE001
                # pyverilog failed; try regex as emergency fallback and let
                # RegexFallbackUnsupported propagate if that also yields nothing.
                try:
                    return _extract_ports_regex(verilog_path)
                except RegexFallbackUnsupported:
                    raise ParserModeFailed(verilog_path, str(exc)) from exc
        return _extract_ports_regex(verilog_path)

    if mode == "parser":
        if not _pyverilog_available():
            raise ParserDependencyMissing()
        try:
            return _extract_ports_pyverilog(verilog_path)
        except Exception as exc:  # noqa: BLE001
            raise ParserModeFailed(verilog_path, str(exc)) from exc

    if mode == "regex":
        return _extract_ports_regex(verilog_path)

    raise ValueError(
        f"Unknown extraction mode {mode!r}. "
        f"Valid values: 'auto', 'parser', 'regex'"
    )


# ── File writers ───────────────────────────────────────────────────────────

def write_port_map_yaml(
    ports: list[PortInfo] | list[dict[str, Any]],
    out_path: Path,
    *,
    signature_hash: str | None = None,
) -> None:
    """Write a ``port_map.yaml`` from *ports*.

    The output schema is consumed by ``forge.verify.gen_sim._read_port_map()``.

    .. code-block:: yaml

        port_signature_hash: <sha256>   # present when signature_hash is given
        ports:
          - name:      ap_clk
            direction: input
            width:     1
          ...

    Args:
        ports:           ``PortInfo`` objects **or** legacy port dicts
                         (``{"name": ..., "direction": ..., "width": ...}``).
        out_path:        Destination path.
        signature_hash:  Optional SHA-256 string to embed as
                         ``port_signature_hash`` (enables preflight cross-check).
    """
    lines: list[str] = []
    if signature_hash:
        lines.append(f"port_signature_hash: {signature_hash}")
    lines.append("ports:")
    for p in ports:
        if isinstance(p, PortInfo):
            name, direction, width = p.name, p.direction, p.width
        else:
            name      = p["name"]
            direction = p["direction"]
            width     = int(p["width"])
        lines.append(f"  - name:      {name}")
        lines.append(f"    direction: {direction}")
        lines.append(f"    width:     {width}")
    Path(out_path).write_text("\n".join(lines) + "\n")


def write_port_signature(
    ports: list[PortInfo] | list[dict[str, Any]],
    out_path: Path,
) -> None:
    """Write a ``port_signature.json`` interface fingerprint from *ports*.

    The JSON contains an ordered list of ports and a SHA-256 hash of the
    interface.  The same hash is embedded into ``port_map.yaml`` by
    :func:`write_port_map_yaml` when ``signature_hash`` is provided.

    Schema:

    .. code-block:: json

        {
          "hash": "<sha256>",
          "ports": [{"name": ..., "direction": ..., "width": ...}, ...]
        }

    Args:
        ports:    ``PortInfo`` objects or legacy port dicts.
        out_path: Destination path.

    Returns:
        The computed SHA-256 hash string (also embedded in the file).
    """
    normalized: list[dict[str, Any]] = []
    for p in ports:
        if isinstance(p, PortInfo):
            normalized.append({"name": p.name, "direction": p.direction, "width": p.width})
        else:
            normalized.append({"name": p["name"], "direction": p["direction"], "width": int(p["width"])})

    # Stable hash over sorted port list (sorted by name for position-independence)
    hashable = json.dumps(
        sorted(normalized, key=lambda x: x["name"]),
        sort_keys=True,
    ).encode()
    sha = hashlib.sha256(hashable).hexdigest()

    sig = {"hash": sha, "ports": normalized}
    Path(out_path).write_text(json.dumps(sig, indent=2) + "\n")
    return sha  # type: ignore[return-value]


def extract_and_write(
    verilog_path: Path,
    port_map_out: Path,
    signature_out: Path | None = None,
    *,
    mode: Literal["auto", "parser", "regex"] = "auto",
) -> list[PortInfo]:
    """Extract ports and write ``port_map.yaml`` (and optionally ``port_signature.json``).

    Convenience wrapper combining :func:`extract_ports`, :func:`write_port_map_yaml`,
    and optionally :func:`write_port_signature`.

    Args:
        verilog_path:  Source RTL file.
        port_map_out:  Destination for ``port_map.yaml``.
        signature_out: Destination for ``port_signature.json`` (skipped if ``None``).
        mode:          Extraction mode (``"auto"`` | ``"parser"`` | ``"regex"``).

    Returns:
        The extracted ``PortInfo`` list (useful for callers that also need the data).
    """
    ports = extract_ports(verilog_path, mode=mode)

    sig_hash: str | None = None
    if signature_out is not None:
        sig_hash = write_port_signature(ports, signature_out)

    port_map_out.parent.mkdir(parents=True, exist_ok=True)
    write_port_map_yaml(ports, port_map_out, signature_hash=sig_hash)

    return ports
