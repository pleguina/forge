"""Shared output-writing discipline for every ``forge.docsgen`` generator.

Every page this package produces is written atomically and only ever
inside the requested output directory — the same absolute-path-leakage
discipline already established for
``forge.core.utils.portable_path``/the visual design explorer (reused as
precedent, not code — the data shapes differ).
"""
from __future__ import annotations

import os
from pathlib import Path


def write_page(output_dir: "str | Path", relative_name: str, content: str) -> Path:
    """Atomically write *content* to ``output_dir / relative_name``.

    Raises:
        ValueError: *relative_name* would resolve outside *output_dir*
            (e.g. via ``..`` segments) — a generator bug, never a valid
            request.
    """
    output_dir = Path(output_dir).resolve()
    target = (output_dir / relative_name).resolve()
    if output_dir not in target.parents and target != output_dir:
        raise ValueError(f"refusing to write outside output_dir: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, target)
    return target
