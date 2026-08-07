"""
forge.core.toolchain_versions — best-effort toolchain version capture
(provenance-manifest toolchain versions).

``forge doctor`` (``forge/core/cli/groups/doctor.py``) already checks tool
*presence* on ``PATH`` via ``shutil.which`` for the same 5 tools — this
module reuses that exact tool list and adds a *version* string on top,
for ``forge.ir.provenance.build_provenance`` to attach to a provenance
manifest. Deliberately its own module rather than importing from
``doctor.py`` — ``forge/ir`` (which ``provenance.py`` lives in) may not
import from ``forge/core/cli`` (a CLI group module), per
``ci/import_direction_check.sh``.

Never raises and never blocks: a missing tool, an unrecognized version
flag, a timeout, or any other failure all collapse to a graceful result
(tool omitted, or ``"unknown"``) — same "advisory, optional extras"
philosophy ``doctor.py`` already established for tool presence.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Dict, Iterable, Optional

# Same 5 tools forge doctor already checks for presence. Xilinx tools
# (xvlog/xelab/xsim) use `-version` (single dash); ghdl/verilator use the
# conventional `--version`.
_VERSION_FLAGS: Dict[str, str] = {
    "xvlog": "-version",
    "xelab": "-version",
    "xsim": "-version",
    "ghdl": "--version",
    "verilator": "--version",
}

DEFAULT_TOOLS = tuple(_VERSION_FLAGS)


def _first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return "unknown"


def tool_present(name: str) -> bool:
    """Whether *name* resolves on ``PATH`` — the single shared presence
    check both ``forge doctor`` (``core/cli/groups/doctor.py``) and
    ``forge verify doctor`` (``verify/__main__.py``) now call, instead of
    each independently invoking ``shutil.which`` for the same 5 tools
    (closes a duplication that could
    previously make the two doctor commands disagree if one's check list
    or invocation ever drifted from the other's)."""
    return shutil.which(name) is not None


def tool_version(name: str, *, timeout: float = 5.0) -> Optional[str]:
    """Best-effort version string for the tool *name*, or ``None`` if it's
    not on ``PATH`` at all. Returns ``"unknown"`` (never raises) when the
    tool exists but its version output couldn't be parsed — a wrong
    version flag, a nonzero exit, a hang past *timeout*, or empty output.
    """
    resolved = shutil.which(name)
    if resolved is None:
        return None

    flag = _VERSION_FLAGS.get(name, "--version")
    try:
        result = subprocess.run(
            [resolved, flag],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return "unknown"

    output = (result.stdout or "") + (result.stderr or "")
    if not output.strip():
        return "unknown"
    return _first_nonblank_line(output)


def collect_toolchain_versions(tools: Iterable[str] = DEFAULT_TOOLS) -> Dict[str, str]:
    """Version string for every tool in *tools* that's actually present on
    ``PATH``. Tools not found are omitted entirely — ``forge doctor``
    already owns *presence* reporting; this module only reports versions
    of what's actually there, never fabricates a "missing" entry."""
    versions: Dict[str, str] = {}
    for name in tools:
        version = tool_version(name)
        if version is not None:
            versions[name] = version
    return versions
