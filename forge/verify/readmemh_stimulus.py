#!/usr/bin/env python3
"""Fixed-shape, non-recompiling stimulus mechanism — the ``.mem``-file half.

Explicitly scoped conservative: this covers **fixed-width,
fixed-shape event records only**. Datasets with variable-length events,
multiple input interfaces, or per-event arrays of differing size are not
covered here and stay on the default ``svh_include`` mechanism.

Responsibility boundary
------------------------
This module owns the framework-generic half: writing a real
``$readmemh``-compatible ``.mem`` file from a
:class:`~forge.verify.dataset_adapter.CanonicalDataset`, one fixed-width
hex word per event, and returning the real ``event_index → event_id``
mapping (never fabricated — it's the actual position each event landed at
in the file). It does **not** know how to pack an arbitrary event dict
into a fixed-width word — that bit layout is inherently per-plugin (a
DUT's port widths are project-specific), so packing is a plugin-supplied
callback, matching the framework/plugin ownership split already
established for layer B (``dataset_adapter.py``).

Designed extension point, not built
------------------------------------
A more general ``stimulus/`` package model — ``manifest.json`` +
``event_index.mem`` + ``event_payload.mem`` + ``event_offsets.mem`` +
``control_schedule.mem``, with each event carrying ``offset``/``length``/
``event_id`` — would support variable-length events without
recompilation. This is a documented future design, reserved under the
schema name ``forge.stimulus_package`` (unversioned, unimplemented) for
when a real reference plugin genuinely needs variable-sized events. It is
explicitly **not implemented** here.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict

if TYPE_CHECKING:
    from forge.verify.dataset_adapter import CanonicalDataset


def write_event_memory_file(
    canonical: "CanonicalDataset",
    pack_event: "Callable[[Dict[str, object]], str]",
    out_path: Path,
) -> "Dict[int, str]":
    """Write one ``$readmemh``-compatible hex word per event, in
    ``event_index`` order, to *out_path*.

    Args:
        canonical:   The flow's materialized dataset.
        pack_event:  Plugin-supplied callback: one event dict →  one
                     fixed-width hex string (no ``0x``/``'h`` prefix,
                     e.g. ``"03a01"``). Every returned string must be the
                     same width — this function does not validate that
                     (the plugin owns the bit layout) but a mismatched
                     width will make ``$readmemh`` mis-load later words.
        out_path:    Destination ``.mem`` file (parent dir created as
                     needed).

    Returns:
        The real ``event_index → event_id`` mapping — ``event_index`` is
        the word's line position (0-based), ``event_id`` is the real
        string id from ``canonical.metadata.event_ids`` at that position.
        This is the mapping ``EventResult``/``FlowResult``
        report from, and the mapping ``forge test run`` resolves a
        user-given ``--event-id`` back into a ``+EVENT_INDEX=N`` plusarg
        through (never the reverse — the index is purely an internal
        memory-layout detail, never something a user supplies directly).
    """
    lines: "list[str]" = []
    index_to_id: "Dict[int, str]" = {}
    for index, (event_id, event) in enumerate(
        zip(canonical.metadata.event_ids, canonical.events)
    ):
        lines.append(pack_event(event))
        index_to_id[index] = event_id

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    return index_to_id
