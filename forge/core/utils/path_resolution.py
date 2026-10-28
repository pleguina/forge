"""Where a command's relative paths are anchored, and how they resolve against it."""

from __future__ import annotations

import os
from pathlib import Path


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
