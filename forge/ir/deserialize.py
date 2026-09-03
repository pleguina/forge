"""
Reading a ``design.ir.json`` back: schema compatibility, migration, and a
deserializer that cannot drift from the model — Phase G4.

Writing the IR has always been one line (``dataclasses.asdict``); reading it
back was a hand-written constructor per dataclass, living in the CLI. Every
field added to ``model.py`` had to be added there too, and one that wasn't
came back as a *silently different design*: the field vanished on the
round-trip, so the content hash changed and ``forge inspect --diff`` said a
design had changed when nothing had. This module reads the model's own field
definitions instead, so a new IR field needs no second implementation.

Three separate concerns, deliberately not merged:

* **Compatibility** (``check_ir_schema_version``) — is this build willing to
  read that schema version at all? Same policy the four user-authored
  schemas follow (``forge.core.schema_version``), applied to the IR's
  three-part version string: a different major is refused, a newer minor is
  read with a warning and its unknown fields ignored, an older minor is
  migrated.
* **Migration** (``migrate_ir_payload``) — bring an older payload up to the
  current schema *as data*, one documented step per version boundary. A step
  exists only where a real semantic change happened; the additive
  boundaries need none, because a missing field is exactly what the
  dataclass default already means.
* **Deserialization** (``from_json_dict``) — the generic, model-driven
  reconstruction.

A migrated IR does not hash equal to a freshly built one, and shouldn't:
its schema really did change. ``diff_projects`` reports the version
difference alongside the hash so the two are never confused.
"""

from __future__ import annotations

import dataclasses
import typing
from typing import Any, Dict, List, Optional, Tuple

from . import model as _model
from .model import IR_SCHEMA_VERSION, ResolvedProject

# The version an IR payload that predates the ``schema_version`` field is
# treated as. No such file should exist (the field has been written since
# the first slice), but a payload that lost it must not silently read as
# "current".
_OLDEST_KNOWN_VERSION = "0.1.0"


class IrSchemaError(ValueError):
    """A ``design.ir.json`` this build will not interpret — a different
    major version, or a malformed one. Its own class so a caller can tell
    "the file is from an incompatible FORGE" from "the file is corrupt"
    without string-matching a message."""


def parse_ir_version(value: str) -> Tuple[int, int, int]:
    """Parse a three-part ``"major.minor.patch"`` IR/provenance version.

    Deliberately *not* ``forge.core.schema_version.parse_schema_version``:
    that one parses the two-part strings the user-authored schemas use and
    rejects a third component. The two shapes are documented as
    intentionally different (``docs/development/SCHEMA_VERSIONING.md``), so
    they get one parser each rather than one parser with a mode flag.
    """
    if not isinstance(value, str):
        raise IrSchemaError(f"IR schema_version must be a string, got: {value!r}")
    parts = value.split(".")
    if len(parts) != 3:
        raise IrSchemaError(
            f"IR schema_version must be 'major.minor.patch', got: {value!r}"
        )
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise IrSchemaError(
            f"IR schema_version components must be integers, got: {value!r}"
        ) from None
    if any(n < 0 for n in nums):
        raise IrSchemaError(
            f"IR schema_version components must be non-negative, got: {value!r}"
        )
    return nums[0], nums[1], nums[2]


def check_ir_schema_version(value: str) -> Optional[str]:
    """Return a warning string, or ``None`` when the version is fully
    compatible. Raises ``IrSchemaError`` for a version this build refuses.

    Same shape as the user-schema policy: a *newer minor* is readable
    (unknown fields are ignored by ``from_json_dict``), a *different major*
    is not.
    """
    major, minor, _patch = parse_ir_version(value)
    cur_major, cur_minor, _ = parse_ir_version(IR_SCHEMA_VERSION)
    if major != cur_major:
        raise IrSchemaError(
            f"IR schema version {value} is incompatible with this FORGE build "
            f"(supports {cur_major}.x, currently {IR_SCHEMA_VERSION}). "
            "Regenerate the IR with `forge topgen gen-top`, or use the FORGE "
            "version that wrote it."
        )
    if minor > cur_minor:
        return (
            f"IR schema version {value} is newer than this build's "
            f"{IR_SCHEMA_VERSION} — fields added after {IR_SCHEMA_VERSION} are "
            "ignored, not interpreted."
        )
    return None


# ── Migrations ───────────────────────────────────────────────────────────
# One entry per version boundary that changed the *meaning* of existing
# data. A purely additive boundary has no entry: a field absent from an
# older payload already reads as its dataclass default, which is what the
# addition defined it to mean.

# The 0.1.0 transformation-kind vocabulary, renamed in 0.2.0 when the kind
# set was expanded (see ResolvedTransformation's docstring).
_KIND_RENAMES_0_2_0 = {
    "register": "pipeline_register",
    "delay": "latency_delay",
}


def _migrate_0_1_0_to_0_2_0(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Rename the two transformation kinds 0.2.0 replaced.

    A 0.1.0 IR called a pipeline register ``register`` and a latency delay
    ``delay``. Left alone, they read as kinds nothing in the current
    vocabulary recognises — the build manifest would not compile
    ``RegisterStage.v`` for a design that has one.
    """
    for conn in payload.get("design", {}).get("connections", []):
        for xform in conn.get("transformations", []):
            kind = xform.get("kind")
            if kind in _KIND_RENAMES_0_2_0:
                xform["kind"] = _KIND_RENAMES_0_2_0[kind]
    return payload


# 0.2.0 → 0.3.0 is additive only (a module's resolved compile set and
# declaration order, a top-level port's origin and the instance pin behind
# it, and the verification plan's own fields) — no step needed.
_MIGRATIONS = [
    ("0.1.0", "0.2.0", _migrate_0_1_0_to_0_2_0),
]


def migrate_ir_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Bring *payload* up to ``IR_SCHEMA_VERSION``, returning it together
    with a note per migration applied.

    Mutates and returns *payload* rather than deep-copying it: every caller
    here has just parsed the JSON for this purpose. A payload already at
    the current version, or at a newer minor, comes back untouched.
    """
    version = str(payload.get("schema_version") or _OLDEST_KNOWN_VERSION)
    warning = check_ir_schema_version(version)
    notes: List[str] = [warning] if warning else []

    for from_version, to_version, step in _MIGRATIONS:
        if parse_ir_version(version) < parse_ir_version(to_version):
            payload = step(payload)
            notes.append(f"migrated IR schema {from_version} → {to_version}")
            version = to_version

    if parse_ir_version(version) < parse_ir_version(IR_SCHEMA_VERSION):
        # Remaining boundaries are additive: nothing to rewrite, but the
        # payload is now readable as the current schema.
        version = IR_SCHEMA_VERSION
    payload["schema_version"] = version
    return payload, notes


# ── Model-driven deserialization ─────────────────────────────────────────

def _hints(cls: type) -> Dict[str, Any]:
    """Resolved type hints for *cls*, evaluated against ``model``'s own
    namespace so ``from __future__ import annotations`` strings and the
    one forward reference (``LatencyDeclaration``) resolve."""
    return typing.get_type_hints(cls, vars(_model))


def _build(annotation: Any, value: Any) -> Any:
    """Reconstruct *value* as *annotation*.

    Handles exactly the shapes the IR model uses: dataclasses, ``Optional``,
    ``List[...]``, ``Dict[...]``, and primitives. Anything else is passed
    through unchanged — the model has no such field today, and inventing a
    conversion for a shape that doesn't exist would be guesswork.
    """
    if value is None:
        return None

    origin = typing.get_origin(annotation)
    if origin is typing.Union:  # includes Optional[X]
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _build(args[0], value) if len(args) == 1 else value
    if origin in (list, List):
        (item_type,) = typing.get_args(annotation) or (Any,)
        return [_build(item_type, v) for v in value]
    if origin in (dict, Dict):
        return dict(value)
    if dataclasses.is_dataclass(annotation) and isinstance(value, dict):
        return _from_dataclass_dict(annotation, value)
    return value


def _from_dataclass_dict(cls: type, payload: Dict[str, Any]) -> Any:
    """Construct one dataclass from a dict, field by field.

    A field the payload doesn't carry keeps its default — that is how an
    older schema's missing field reads. A key the model doesn't define is
    ignored — that is how a *newer* schema's extra field reads. Both are the
    documented compatibility behaviour, and both fall out of iterating the
    model's fields rather than the payload's keys.
    """
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if not f.init or f.name not in payload:
            continue
        kwargs[f.name] = _build(hints.get(f.name, Any), payload[f.name])
    return cls(**kwargs)


def from_json_dict(payload: Dict[str, Any]) -> Tuple[ResolvedProject, List[str]]:
    """Reconstruct a ``ResolvedProject`` from an emitted ``design.ir.json``
    dict, migrating it first.

    Returns the project and the migration/compatibility notes, so a caller
    can report what it had to do to read the file. Raises ``IrSchemaError``
    for a payload this build won't interpret.
    """
    if not isinstance(payload, dict) or "design" not in payload:
        raise IrSchemaError(
            "not a FORGE IR document: no 'design' object at the top level"
        )
    payload, notes = migrate_ir_payload(payload)
    return _from_dataclass_dict(ResolvedProject, payload), notes
