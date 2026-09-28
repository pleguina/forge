"""
Migration tooling.

Pure, CLI-independent functions — one per migration kind — used by
``forge topgen migrate`` (``forge/core/cli/groups/topgen.py::cmd_migrate``).

Every text-rewriting function here does a **targeted line-level edit**,
never a full ``yaml.safe_load`` → mutate → ``yaml.dump`` round-trip —
PyYAML's `safe_dump` silently strips comments and reflows formatting,
which is unacceptable for hand-authored, comment-heavy files like
``design.yml`` or an interface contract. Each function returns the new
text (or a description of planned filesystem actions) without touching
disk; the CLI layer decides whether to actually write, based on
``--dry-run``.

See ``docs/development/MIGRATION_TOOLING.md`` for the full policy,
including what each kind deliberately does NOT attempt to do.
"""

from __future__ import annotations

import difflib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..contracts.config import DESIGN_SCHEMA_VERSION, MODULE_REGISTRY_SCHEMA_VERSION
from ..contracts.contract_loader import INTERFACE_CONTRACT_SCHEMA_VERSION

# forge.generation must not depend on forge.verification (no such import exists
# anywhere else in the codebase — the two subsystems are kept
# independent). This value is kept manually
# in sync with forge.verification.design_contract.VERIFY_CONTRACT_SCHEMA_VERSION
# rather than importing it.
_VERIFY_CONTRACT_SCHEMA_VERSION = "1.0"


def unified_diff_text(before: str, after: str, path: Path) -> str:
    """A readable unified diff between *before* and *after*, for any
    text-content migration."""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=str(path), tofile=f"{path} (migrated)",
    ))


# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema-version insertion
# ─────────────────────────────────────────────────────────────────────────────

_TOP_LEVEL_SCHEMA_KEY: Dict[str, Tuple[str, str]] = {
    "design": ("schema_version", f'"{DESIGN_SCHEMA_VERSION}"'),
    "registry": ("registry_version", f'"{MODULE_REGISTRY_SCHEMA_VERSION}"'),
    "verify_contract": ("schema_version", f'"{_VERIFY_CONTRACT_SCHEMA_VERSION}"'),
}


@dataclass
class MigrationResult:
    changed: bool
    diff: str
    message: str
    new_content: Optional[str] = None


def detect_schema_kind(path: Path) -> str:
    """Infer which of the four user-authored schemas *path* is, from its
    filename. Raises ValueError if it can't be determined — callers should
    let the user override via an explicit kind in that case."""
    name = path.name
    if name == "design.yml":
        return "design"
    if name == "modules.yml":
        return "registry"
    if name in ("design.verification.yml", "verify.design.yml"):
        return "verify_contract"
    if name.endswith(".interface.yaml"):
        return "interface"
    raise ValueError(
        f"cannot auto-detect schema kind for {path.name!r} — pass an explicit schema kind"
    )


def _insert_top_level_key(text: str, key: str, value: str) -> Tuple[str, bool]:
    if re.search(rf"^{re.escape(key)}\s*:", text, re.MULTILINE):
        return text, False

    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[0] not in (" ", "\t"):
            insert_at = i
            break
    lines.insert(insert_at, f"{key}: {value}\n")
    return "".join(lines), True


def _insert_interface_schema_version(text: str, value: str) -> Tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    header_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^ip_interface\s*:\s*$", line):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("no top-level 'ip_interface:' block found")

    indent = None
    for j in range(header_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped:
            continue
        if lines[j][0] not in (" ", "\t"):
            break  # block ended
        if stripped.startswith("schema_version:"):
            return text, False
        if indent is None:
            indent = lines[j][: len(lines[j]) - len(lines[j].lstrip(" \t"))]

    if indent is None:
        indent = "  "
    lines.insert(header_idx + 1, f"{indent}schema_version: {value}\n")
    return "".join(lines), True


def migrate_schema_version(path: Path, *, schema_kind: Optional[str] = None) -> MigrationResult:
    """Insert the current supported schema_version/registry_version into
    *path* if it doesn't already declare one. A no-op (changed=False) is
    not an error — most files won't need this."""
    kind = schema_kind or detect_schema_kind(path)
    text = path.read_text()

    if kind == "interface":
        new_text, changed = _insert_interface_schema_version(text, f'"{INTERFACE_CONTRACT_SCHEMA_VERSION}"')
    elif kind in _TOP_LEVEL_SCHEMA_KEY:
        key, value = _TOP_LEVEL_SCHEMA_KEY[kind]
        new_text, changed = _insert_top_level_key(text, key, value)
    else:
        raise ValueError(f"unknown schema kind: {kind!r}")

    if not changed:
        return MigrationResult(
            changed=False, diff="",
            message=f"{path.name}: schema version already declared — nothing to do",
            new_content=text,
        )
    return MigrationResult(
        changed=True,
        diff=unified_diff_text(text, new_text, path),
        message=f"{path.name}: inserted schema version",
        new_content=new_text,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Partition strings -> structured coordinates
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_HEADER_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z0-9_]+):\s*$")
_PARTITION_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)partition:\s*(?P<value>.+?)\s*$")


def _eligible_partition_roles(spec: Dict[str, Any]) -> List[str]:
    roles = ((spec.get("ip_interface") or {}).get("roles")) or {}
    return [
        name for name, body in roles.items()
        if isinstance(body, dict) and "partition" in body and "coordinates" not in body
    ]


def partition_to_coordinates(
    text: str, *, axis: str, role: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Wrap `partition: X` as a single-axis `coordinates: {axis: X}` for
    one role (*role*) or all eligible roles (*role* is None).

    This is a **1-axis wrap only** — there is no safe, general way to
    decompose an arbitrary partition string into multiple named axes
    without a user-supplied mapping (see
    docs/development/MIGRATION_TOOLING.md). `partition:` remains fully
    supported either way; this is opt-in convenience, not a required step.

    Raises ValueError if *role* is given but doesn't exist or isn't
    eligible (missing `partition`, or already has `coordinates`) — never
    guesses.
    """
    spec = yaml.safe_load(text) or {}
    roles = ((spec.get("ip_interface") or {}).get("roles")) or {}
    eligible = _eligible_partition_roles(spec)

    if role is not None:
        if role not in roles:
            raise ValueError(f"role {role!r} not found in contract")
        if role not in eligible:
            raise ValueError(
                f"role {role!r} is not eligible — it has no 'partition' field, "
                "or already has 'coordinates'"
            )
        targets = [role]
    else:
        targets = eligible

    if not targets:
        return text, []

    lines = text.splitlines(keepends=True)
    changed_roles: List[str] = []
    i = 0
    while i < len(lines):
        m = _ROLE_HEADER_RE.match(lines[i])
        if m and m.group("name") in targets:
            role_indent = len(m.group("indent"))
            j = i + 1
            while j < len(lines):
                stripped = lines[j].strip()
                if stripped:
                    this_indent = len(lines[j]) - len(lines[j].lstrip(" \t"))
                    if this_indent <= role_indent:
                        break
                    pm = _PARTITION_LINE_RE.match(lines[j])
                    if pm:
                        value = pm.group("value")
                        lines[j] = f'{pm.group("indent")}coordinates: {{{axis}: {value}}}\n'
                        changed_roles.append(m.group("name"))
                        break
                j += 1
            i = j
        else:
            i += 1

    return "".join(lines), changed_roles


# ─────────────────────────────────────────────────────────────────────────────
# 3. Legacy plugin layout (<plugin>/verify/ -> <plugin>/forge/verify/,
#    dead _FW_PYTHON sys.path block removal) — see MIGRATION.md.
# ─────────────────────────────────────────────────────────────────────────────

_FW_PYTHON_SYS_PATH_MARKERS = ("_FW_PYTHON", "framework/verify/python")


@dataclass
class LayoutIssue:
    move_needed: bool
    old_verify_dir: Path
    new_verify_dir: Path
    fw_python_files: List[Path] = field(default_factory=list)
    manual_files: List[Path] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.move_needed and not self.fw_python_files and not self.manual_files


def _scan_for_fw_python_block(f: Path) -> Optional[bool]:
    """Return True if a confident, contiguous _FW_PYTHON block was found
    (safe to remove), False if the marker is present but the pattern isn't
    confidently recognized (needs manual removal), or None if absent."""
    text = f.read_text()
    if "_FW_PYTHON" not in text:
        return None
    lines = text.splitlines(keepends=True)
    removable = [
        i for i, l in enumerate(lines)
        if "_FW_PYTHON" in l or ("sys.path" in l and "framework/verify/python" in l)
    ]
    if removable and removable == list(range(removable[0], removable[0] + len(removable))):
        return True
    return False


def find_legacy_plugin_layout(plugin_root: Path) -> LayoutIssue:
    old_dir = plugin_root / "verify"
    new_dir = plugin_root / "forge" / "verify"
    move_needed = old_dir.is_dir() and not new_dir.exists()
    scan_dir = old_dir if move_needed else new_dir

    fw_python_files: List[Path] = []
    manual_files: List[Path] = []
    if scan_dir.is_dir():
        for name in ("bootstrap.py", "gen_stimulus.py"):
            f = scan_dir / name
            if not f.is_file():
                continue
            confident = _scan_for_fw_python_block(f)
            if confident is True:
                fw_python_files.append(f)
            elif confident is False:
                manual_files.append(f)

    return LayoutIssue(move_needed, old_dir, new_dir, fw_python_files, manual_files)


def apply_legacy_plugin_layout(issue: LayoutIssue) -> List[str]:
    actions: List[str] = []
    if issue.move_needed:
        shutil.move(str(issue.old_verify_dir), str(issue.new_verify_dir))
        actions.append(f"moved {issue.old_verify_dir} -> {issue.new_verify_dir}")

    for f in issue.fw_python_files:
        target = issue.new_verify_dir / f.name if issue.move_needed else f
        text = target.read_text()
        lines = text.splitlines(keepends=True)
        kept = [
            line for line in lines
            if "_FW_PYTHON" not in line
            and not ("sys.path" in line and "framework/verify/python" in line)
        ]
        target.write_text("".join(kept))
        actions.append(f"removed _FW_PYTHON sys.path block from {target}")

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# 4. Deprecated verify.design.yml filename -> design.verification.yml
# ─────────────────────────────────────────────────────────────────────────────

def find_legacy_verify_contract_name(plugin_root: Path) -> Optional[Path]:
    verify_dir = plugin_root / "forge" / "verify"
    legacy = verify_dir / "verify.design.yml"
    current = verify_dir / "design.verification.yml"
    if legacy.is_file() and not current.exists():
        return legacy
    return None


def apply_rename_verify_contract(legacy_path: Path) -> Path:
    new_path = legacy_path.with_name("design.verification.yml")
    legacy_path.rename(new_path)
    return new_path


# ─────────────────────────────────────────────────────────────────────────────
# 5. Compatibility-mode contract inference
# ─────────────────────────────────────────────────────────────────────────────

# Same conservative name lists forge.contracts.matcher.auto_match_ports uses
# for compat-mode clock/reset heuristics (matcher.py, local to that
# function) — duplicated rather than imported since they're function-local
# there, not a public module constant.
_CLOCK_NAMES = ("ap_clk", "clk", "clock")
_RESET_NAMES = ("ap_rst", "rst", "reset", "rst_n")


def infer_contract_skeleton(
    module_name: str, ip_info_entry: Dict[str, Any], *, source_type: str = "rtl",
) -> str:
    """Generate a *.interface.yaml skeleton for a module with no contract.

    Emits every observed port as a real role, rather than listing the
    non-clock/reset ones as TODO comments the author then has to retype as
    YAML. Across this repo's contracts, 308 of 324 roles carry no semantic
    field at all — they restate the port's name, direction and width — so
    handing that back as commentary made the generator useless for exactly
    the bulk of the work.

    What is emitted depends on where the port facts can come from later:

    * ``rtl`` — the role name alone. ``contract_loader`` derives
      raw_port/direction/width from the module's own Verilog/VHDL at load
      time and verifies anything the contract does declare.
    * ``hls`` — direction and width are written out, because an HLS module
      has no HDL to scan until its IP is built and the contract is itself
      the pre-build stand-in for the port list.

    What is *not* guessed: ``wiring_kind``, ``protocol``, ``partition``,
    ``coordinates``. Those are genuine integration decisions and can never
    be inferred from a port name. The contract is emitted as
    ``normalization_status: draft`` so it is flagged as unreviewed until a
    human promotes it to ``ready``.
    """
    ports = ip_info_entry.get("ports", []) or []
    port_names = {p["name"] for p in ports}
    port_by_name = {p["name"]: p for p in ports}
    is_rtl = source_type == "rtl"

    clock_port = next((n for n in _CLOCK_NAMES if n in port_names), None)
    reset_port = next((n for n in _RESET_NAMES if n in port_names), None)

    lines = [
        f"# Inferred integration contract skeleton for {module_name!r}.",
        "# Generated by `forge contract infer` from the module's observed ports.",
        "#",
        "# Every port is emitted as a role below. Review the role names, then",
        "# add the integration semantics that cannot be inferred from a port",
        "# name -- wiring_kind, protocol, partition, coordinates -- to the roles",
        "# that take part in contract-driven wiring. See",
        "# docs/IP_INTERFACE_POLICY.md.",
        "#",
        "# Set normalization_status to 'ready' once reviewed: 'draft' is",
        "# reported by `forge topgen validate` and fails under --strict.",
        "",
        "ip_interface:",
        f'  schema_version: "{INTERFACE_CONTRACT_SCHEMA_VERSION}"',
        f"  module_name: {module_name}",
        f"  ip_info_key: {module_name}",
        f"  source_type: {source_type}",
        "  normalization_status: draft",
        "",
        "  roles:",
        "",
    ]

    def _emit(role: str, port_name: str, *, extra: List[str] = ()) -> None:
        p = port_by_name.get(port_name, {})
        body = [f"    {role}:"]
        if role != port_name:
            body.append(f"      raw_port: {port_name}")
        if not is_rtl:
            direction = str(p.get("direction", "IN")).upper()
            body.append(f"      direction: {'input' if direction.startswith('IN') else 'output'}")
            body.append(f"      width: {p.get('width', 1)}")
        body.extend(extra)
        if len(body) == 1:
            # Nothing but the name: valid, and resolved from the source.
            body = [f"    {role}:"]
        lines.extend(body + [""])

    if clock_port:
        _emit("clock_primary", clock_port)
    if reset_port:
        _emit("reset_primary", reset_port,
              extra=[] if is_rtl else ["      active_level: high"])

    consumed = {n for n in (clock_port, reset_port) if n}
    for name in sorted(port_names - consumed):
        _emit(name, name)

    if not port_names:
        lines.append("    # No ports observed for this module.")

    return "\n".join(lines) + "\n"
