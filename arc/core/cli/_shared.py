"""Shared helpers used across all arc CLI group modules."""

from __future__ import annotations

import copy
import os
import shutil
import sys
import traceback
from pathlib import Path


# ---------------------------------------------------------------------------
# Debug flag (set by main() from --debug arg)
# ---------------------------------------------------------------------------

_TOPGEN_DEBUG = False


def debug_enabled() -> bool:
    return _TOPGEN_DEBUG or os.environ.get("TOPGEN_DEBUG", "0") == "1"


def set_debug(value: bool) -> None:
    global _TOPGEN_DEBUG
    _TOPGEN_DEBUG = value


# ---------------------------------------------------------------------------
# Error printing
# ---------------------------------------------------------------------------

def print_cli_error(prefix: str, exc: Exception, *, hint: str | None = None) -> None:
    print(f"❌ {prefix}: {exc}", file=sys.stderr)
    if hint:
        print(f"   → {hint}", file=sys.stderr)
    if debug_enabled():
        traceback.print_exc(file=sys.stderr)
    else:
        print("   Re-run with --debug for traceback details.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def consumer_root(anchor_path: Path | None = None, explicit_root: Path | None = None) -> Path:
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()
    env_root = os.environ.get("TOPGEN_CONSUMER_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if anchor_path is not None:
        resolved_anchor = Path(anchor_path).expanduser().resolve()
        return resolved_anchor if resolved_anchor.is_dir() else resolved_anchor.parent
    raise ValueError(
        "Cannot determine consumer root. Pass --consumer-root, set "
        "TOPGEN_CONSUMER_ROOT, or provide an input path that can anchor "
        "relative defaults."
    )


def resolve_path(
    path_value: Path | str | None,
    base_root: Path,
    default: Path | str | None = None,
) -> Path | None:
    candidate = path_value if path_value is not None else default
    if candidate is None:
        return None
    path = Path(candidate).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_root / path).resolve()


def remove_path(path: Path, removed: list[Path]) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    removed.append(path)


# ---------------------------------------------------------------------------
# Pattern helpers (used by topgen port filtering and port-map)
# ---------------------------------------------------------------------------

def match_patterns(name: str, patterns: list[str]) -> bool:
    import re as _re
    return any(_re.search(pattern, name) for pattern in patterns or [])


def numeric_suffix_key(name: str) -> tuple[int, str]:
    import re as _re
    match = _re.search(r'_(\d+)(?:_|$)', name)
    return (int(match.group(1)), name) if match else (0, name)


def sort_entries(entries: list[dict], sort_mode: str | None) -> list[dict]:
    if sort_mode == "numeric_suffix":
        return sorted(entries, key=lambda e: numeric_suffix_key(e["name"]))
    return sorted(entries, key=lambda e: e["name"])


def resolve_interface_metadata(interface_metadata: dict | None) -> dict:
    return copy.deepcopy(interface_metadata or {})


# ---------------------------------------------------------------------------
# Verify-flow helpers
# ---------------------------------------------------------------------------

def load_verify_flow_entries(verify_design_path: Path) -> list[tuple[str, str | None]]:
    import yaml as _yaml
    raw = _yaml.safe_load(verify_design_path.read_text()) or {}
    entries: list[tuple[str, str | None]] = []
    for flow in raw.get("flows", []) or []:
        if not isinstance(flow, dict):
            continue
        flow_name = flow.get("name")
        if not flow_name:
            continue
        flow_kind = flow.get("kind")
        entries.append((str(flow_name), str(flow_kind) if flow_kind else None))
    return entries
