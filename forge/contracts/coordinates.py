"""
Structured coordinates — an optional, backward-compatible generalization of
the legacy free-form ``partition:`` string label used to pair multi-instance
producers with partitioned consumers in ``topology_groups``.

A role or ``instance_assign`` entry may declare either:

- the legacy scalar label:   ``partition: lower_pair``
- or a structured mapping:   ``coordinates: {sector: 2, station: 1}``

Both normalize to the same canonical, hashable, order-independent key via
``coordinate_key()``, so matching, duplicate detection, and diagnostics work
uniformly regardless of which form a given contract or design.yml uses.
Existing designs that only use ``partition:`` strings keep working unchanged
— this is a superset, not a replacement.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

CoordinateKey = Tuple[Tuple[str, Any], ...]


def coordinate_key(spec: Mapping[str, Any]) -> Optional[CoordinateKey]:
    """
    Return a canonical, hashable, order-independent coordinate key for a role
    or ``instance_assign`` mapping that declares either ``coordinates`` (a
    dict of axis -> value) or the legacy scalar ``partition`` (a bare
    string).

    Returns ``None`` when neither field is present (i.e. the role/entry is
    unpartitioned).

    Raises ``ValueError`` if ``coordinates`` is present but not a mapping, or
    if both ``coordinates`` and a conflicting legacy ``partition`` are given.
    """
    coords = spec.get("coordinates")
    partition = spec.get("partition")

    if coords is not None:
        if not isinstance(coords, Mapping):
            raise ValueError(
                "'coordinates' must be a mapping of axis to value, got "
                f"{type(coords).__name__}: {coords!r}"
            )
        key: CoordinateKey = tuple(sorted((str(k), v) for k, v in coords.items()))
        if partition is not None and key != (("partition", partition),):
            raise ValueError(
                f"declares both 'coordinates' ({dict(coords)!r}) and a "
                f"conflicting legacy 'partition' ({partition!r}) — declare only one"
            )
        return key

    if partition is not None:
        return (("partition", partition),)

    return None


def coordinate_label(key: Optional[CoordinateKey]) -> str:
    """Human-readable rendering of a coordinate_key for diagnostics."""
    if key is None:
        return "<none>"
    if len(key) == 1 and key[0][0] == "partition":
        return str(key[0][1])
    return ", ".join(f"{axis}={value}" for axis, value in key)
