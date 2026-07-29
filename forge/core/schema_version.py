"""
Shared schema-version parsing/compatibility policy for FORGE's user-facing
YAML schemas (release-plan §2.7).

Every user-facing schema (design topology, module registry, interface
contract, verification contract) may declare an explicit
``schema_version: "<major>.<minor>"`` string. The canonical IR and the
provenance manifest are a separate, already-shipped family with their own
``IR_SCHEMA_VERSION``/``PROVENANCE_SCHEMA_VERSION`` constants (three-part
``"0.1.0"``-shaped strings) and are not managed by this module — see
``docs/development/SCHEMA_VERSIONING.md`` for why those two families use a
different version string shape and why that's fine (each tracks a
different artifact's evolution independently, per the README's "multiple
independently-evolving version numbers" precedent).

Compatibility policy
---------------------
* Not declared → **silent**. No warning, no error — full backward
  compatibility for every file written before this field existed
  ("old-schema / new-FORGE" behavior).
* Same major, declared minor <= supported minor → silent, fully compatible.
* Same major, declared minor >  supported minor → **warning**
  ("new-schema / old-FORGE": this FORGE build may not know about optional
  fields introduced after its supported minor — it ignores them rather
  than failing).
* Different major → **error**. FORGE does not attempt to interpret a
  schema from a different major version.
* Malformed value (not an integer, or more than two dot-separated parts,
  or negative) → **error**.

Legacy bare-integer strings (e.g. ``modules.yml``'s existing
``registry_version: '1'``) parse as ``"<n>.0"`` — no existing checked-in
file needs to change to keep working.

Compatible-addition / deprecation / removal policy: within one major
version, a minor bump may only add optional fields — no field may become
required, and no field may change meaning. Deprecating a field keeps it
accepted (with a warning, at the call site) for at least one full major
version before a later major version may remove it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class SchemaVersionIssue:
    severity: str  # 'error' | 'warning'
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.severity == "error" else "⚠️ "
        return f"  {icon} {self.message}"


def parse_schema_version(value: str) -> Tuple[int, int]:
    """Parse a ``"major"`` or ``"major.minor"`` string into ``(major, minor)``.

    Raises ``ValueError`` if *value* isn't one or two non-negative integers.
    """
    if not isinstance(value, str):
        raise ValueError(f"schema_version must be a string, got: {value!r}")
    parts = value.split(".")
    if len(parts) not in (1, 2):
        raise ValueError(
            f"schema_version must be 'major' or 'major.minor', got: {value!r}"
        )
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise ValueError(
            f"schema_version components must be integers, got: {value!r}"
        ) from None
    if any(n < 0 for n in nums):
        raise ValueError(f"schema_version components must be non-negative, got: {value!r}")
    major = nums[0]
    minor = nums[1] if len(nums) == 2 else 0
    return major, minor


def check_schema_version(
    declared: Optional[str], supported: str, *, schema_name: str,
) -> List[SchemaVersionIssue]:
    """Check a declared ``schema_version`` against the version this FORGE
    build supports, for one named schema (e.g. ``"design.yml"``).

    Returns an empty list when fully compatible (including when *declared*
    is ``None`` — see module docstring).
    """
    if declared is None:
        return []

    supported_major, supported_minor = parse_schema_version(supported)

    try:
        declared_major, declared_minor = parse_schema_version(declared)
    except ValueError as exc:
        return [SchemaVersionIssue("error", f"{schema_name}: {exc}")]

    if declared_major != supported_major:
        return [SchemaVersionIssue(
            "error",
            f"{schema_name}: schema_version {declared!r} is major version "
            f"{declared_major}, but this FORGE build supports major version "
            f"{supported_major} only — incompatible schema.",
        )]

    if declared_minor > supported_minor:
        return [SchemaVersionIssue(
            "warning",
            f"{schema_name}: schema_version {declared!r} is newer than this "
            f"FORGE build supports ({supported!r}) — fields added after "
            f"{supported!r} may be ignored.",
        )]

    return []
