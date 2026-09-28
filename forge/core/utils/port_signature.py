"""port_signature — single implementation of DUT interface fingerprinting.

Rules (from the architecture decision):
  * The signature MUST change when a port is added, removed, renamed,
    resized, or changes direction.
  * The signature MUST NOT change for comments, formatting, or YAML key order.
  * This is the ONLY place the canonicalization and hash computation live.

Public API
----------
  compute(ports)         -> str (16-char hex digest)
  canonical_entries(ports) -> list[dict]   (sorted, normalized)
  from_port_map(port_map_dict) -> str      (re-derive from loaded port_map.yaml)
  write_artifact(hash, path, *, source_file, top_module)
  read_artifact(path)    -> PortSignatureArtifact

Canonical form
--------------
Each port is reduced to three fields: name, direction, width.
Direction is normalised to "input" | "output".  Width is an integer.
Entries are sorted by name.  The hash input string is:

    <name>:<direction>:<width>\n
    ...

SHA-256 truncated to 16 hex characters (64-bit depth, sufficient for
drift detection, matches the existing stored format).

Artifact schema (port_signature.json)
--------------------------------------
{
  "scheme": "sha256-trunc16",
  "version": 1,
  "hash": "<16 hex chars>",
  "top_module": "<string>",
  "source_file": "<string>",
  "port_count": <int>,
  "generated_by": "forge topgen gen-top"
}
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEME = "sha256-trunc16"
ARTIFACT_VERSION = 1


# ── Canonical form ─────────────────────────────────────────────────────────

def canonical_entries(ports: dict[str, tuple[str, int]]) -> list[dict[str, Any]]:
    """Return a stable, normalised list of port dicts sorted by name.

    *ports* is the dict returned by hdl_parser._scan_verilog_ports:
        { port_name: (direction_str, width_int), ... }
    direction_str is "in" or "out" (hdl_parser convention) and is preserved
    verbatim so that the hash is bit-for-bit compatible with existing stored
    artifacts.
    """
    result = []
    for name in sorted(ports):
        dir_raw, width = ports[name]
        result.append({"name": name, "direction": dir_raw, "width": int(width)})
    return result


def _sig_string(entries: list[dict[str, Any]]) -> str:
    """Build the canonical hash-input string from normalised port entries."""
    return "\n".join(f"{e['name']}:{e['direction']}:{e['width']}" for e in entries)


def compute(ports: dict[str, tuple[str, int]]) -> str:
    """Compute the 16-char hex port-signature hash from raw parser output."""
    entries = canonical_entries(ports)
    return hashlib.sha256(_sig_string(entries).encode()).hexdigest()[:16]


def from_port_map(port_map: dict[str, Any]) -> str:
    """Re-derive the hash from a loaded port_map.yaml dict.

    Iterates port_groups to collect all port entries (name, direction, width),
    then runs the same canonical hash.  Use this to verify that a stored
    port_map.yaml hash has not drifted.
    """
    return compute(ports_from_port_map(port_map))


def ports_from_port_map(port_map: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """Every port a loaded ``port_map.yaml`` declares, as
    ``{name: (direction, width)}`` in the ``hdl_parser`` convention
    (``in``/``out``) — the shape ``forge.ir.verification_plan.port_divergences``
    and the hash below both take.

    Public because it is the one traversal of ``port_groups``' four shapes
    (flat list, ``channels:``, ``ports:``, nested ``groups:``); a second
    caller wanting the ports rather than the hash should not write a fifth
    partial version of it.
    """
    ports_flat: dict[str, tuple[str, int]] = {}

    def _ingest(entry: dict[str, Any]) -> None:
        name = entry.get("name")
        direction = entry.get("direction", "input")
        width = int(entry.get("width", 1))
        if name:
            # Reverse-normalise port_map direction ("input"/"output") back to the
            # hdl_parser convention ("in"/"out") used by the original hash formula.
            dir_hash = "in" if direction in ("input", "in") else "out"
            ports_flat[name] = (dir_hash, width)

    groups = port_map.get("port_groups", {})
    for group_name, group_data in groups.items():
        if isinstance(group_data, list):
            for entry in group_data:
                _ingest(entry)
        elif isinstance(group_data, dict):
            # channel-style group
            for ch in group_data.get("channels", []):
                _ingest(ch)
            # config-style group
            for p in group_data.get("ports", []):
                _ingest(p)
            # grouped (rpc-style) group
            for sub in group_data.get("groups", []):
                for p in sub.get("ports", []):
                    _ingest(p)

    return ports_flat


# ── Artifact ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PortSignatureArtifact:
    """Contents of port_signature.json."""
    scheme: str
    version: int
    hash: str
    top_module: str
    source_file: str
    port_count: int
    generated_by: str = "forge topgen gen-top"

    def matches(self, other_hash: str) -> bool:
        return self.hash == other_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "version": self.version,
            "hash": self.hash,
            "top_module": self.top_module,
            "source_file": self.source_file,
            "port_count": self.port_count,
            "generated_by": self.generated_by,
        }


def write_artifact(
    sig_hash: str,
    output_path: Path,
    *,
    source_file: str = "",
    top_module: str = "algo_top",
    port_count: int = 0,
) -> PortSignatureArtifact:
    """Write port_signature.json and return the artifact object."""
    artifact = PortSignatureArtifact(
        scheme=SCHEME,
        version=ARTIFACT_VERSION,
        hash=sig_hash,
        top_module=top_module,
        source_file=source_file,
        port_count=port_count,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact.to_dict(), indent=2) + "\n")
    return artifact


def read_artifact(path: Path) -> PortSignatureArtifact:
    """Load port_signature.json and return a PortSignatureArtifact.

    Raises FileNotFoundError if the artifact does not exist.
    Raises ValueError if the artifact is malformed or uses an unknown scheme.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"port_signature.json not found at {path}. "
            "Regenerate the DUT with: forge topgen gen-top ..."
        )
    data = json.loads(path.read_text())
    scheme = data.get("scheme", "")
    if scheme != SCHEME:
        raise ValueError(
            f"Unsupported port signature scheme: {scheme!r}. Expected {SCHEME!r}."
        )
    return PortSignatureArtifact(
        scheme=scheme,
        version=int(data.get("version", ARTIFACT_VERSION)),
        hash=data["hash"],
        top_module=data.get("top_module", "algo_top"),
        source_file=data.get("source_file", ""),
        port_count=int(data.get("port_count", 0)),
        generated_by=data.get("generated_by", "forge topgen gen-top"),
    )
