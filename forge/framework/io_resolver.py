"""
FORGE Detector I/O Resolver

Loads detector_io.yml from a plugin and resolves each detector input /
trigger output against a FrameworkImport, producing a validated
detector_io.resolved.json.

The resolved JSON forms the single source of truth for:
  - payload wrapper Verilog generation
  - testbench stimulus mapping
  - simulation bindings

Validation rules (all must pass before writing output):
  1.  Every blobfish_endpoint referenced must exist in the FrameworkImport.
  2.  Referenced RX endpoints must have direction == 'rx'.
  3.  Referenced TX endpoints must have direction == 'tx'.
  4.  The endpoint's endpoint_role must be compatible with the detector type.
  5.  No endpoint is consumed twice unless explicit fanout is declared.
  6.  Every frontend module must exist in the FORGE module registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError as exc:
    raise ImportError("PyYAML is required: pip install pyyaml") from exc

from forge.framework.importer import Endpoint, FrameworkImport


# ---------------------------------------------------------------------------
# Compatibility maps
# ---------------------------------------------------------------------------

# Maps detector type (upper-case) to the set of endpoint roles that are
# compatible for *input* connections.
_DETECTOR_INPUT_ROLES: Dict[str, set] = {
    "DT":  {"detector_input_dt"},
    "CSC": {"detector_input_csc"},
    "RPC": {"detector_input_rpc"},
    # Accept any input role for UNKNOWN to allow placeholder entries
    "UNKNOWN": {
        "detector_input_dt", "detector_input_csc", "detector_input_rpc",
    },
}

_TRIGGER_OUTPUT_ROLES: set = {
    "trigger_output_gmt",
    "trigger_output_spare",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ResolvedInput:
    name:              str
    detector:          str
    blobfish_endpoint: str
    endpoint:          Endpoint             # fully resolved
    detector_object:   Dict[str, Any]
    frontend_module:   str
    frontend_instance: str
    frontend_params:   Dict[str, Any]
    output_wiring:     Dict[str, Any]


@dataclass
class ResolvedOutput:
    name:              str
    blobfish_endpoint: str
    endpoint:          Endpoint
    source_instance:   str
    source_port:       str
    wiring_kind:       str


@dataclass
class DetectorIOResolved:
    inputs:  List[ResolvedInput]
    outputs: List[ResolvedOutput]


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class DetectorIOError(ValueError):
    pass


def resolve(
    detector_io_path: Path,
    framework:        FrameworkImport,
    registry_modules: Optional[set] = None,
) -> DetectorIOResolved:
    """Resolve detector_io.yml against a FrameworkImport.

    Parameters
    ----------
    detector_io_path:
        Path to the plugin's detector_io.yml.
    framework:
        Loaded FrameworkImport (from forge.framework.importer.load).
    registry_modules:
        Optional set of known module names (from modules.yml).
        When provided, frontend modules are validated against it.

    Returns
    -------
    A DetectorIOResolved with fully resolved inputs and outputs.

    Raises
    ------
    DetectorIOError   on any validation failure.
    FileNotFoundError if detector_io.yml is missing.
    """
    if not Path(detector_io_path).exists():
        raise FileNotFoundError(f"detector_io.yml not found: {detector_io_path}")

    with open(detector_io_path) as fh:
        raw = yaml.safe_load(fh)

    errors: List[str] = []
    consumed_endpoints: Dict[str, str] = {}   # endpoint_id → first consumer name

    resolved_inputs:  List[ResolvedInput]  = []
    resolved_outputs: List[ResolvedOutput] = []

    # -------------------------------------------------------------------
    # Resolve detector inputs
    # -------------------------------------------------------------------
    for entry in raw.get("detector_inputs", []):
        name    = entry.get("name", "<unnamed>")
        ep_id   = entry.get("blobfish_endpoint", "")
        det_typ = entry.get("detector", "UNKNOWN").upper()

        # Rule 1: endpoint must exist
        ep = framework.get_endpoint(ep_id)
        if ep is None:
            errors.append(
                f"Detector input '{name}': endpoint '{ep_id}' not found in framework manifest"
            )
            continue

        # Rule 2: must be an RX endpoint
        if ep.direction != "rx":
            errors.append(
                f"Detector input '{name}': endpoint '{ep_id}' has direction "
                f"'{ep.direction}' (expected 'rx')"
            )

        # Rule 4: role compatibility
        allowed_roles = _DETECTOR_INPUT_ROLES.get(det_typ, set())
        if allowed_roles and ep.endpoint_role not in allowed_roles:
            errors.append(
                f"Detector input '{name}': endpoint '{ep_id}' has role "
                f"'{ep.endpoint_role}' which is incompatible with detector type '{det_typ}'"
            )

        # Rule 5: no duplicate consumption
        if ep_id in consumed_endpoints:
            errors.append(
                f"Detector input '{name}': endpoint '{ep_id}' already consumed by "
                f"'{consumed_endpoints[ep_id]}' (fanout not declared)"
            )
        consumed_endpoints[ep_id] = name

        # Rule 6: frontend module in registry
        frontend    = entry.get("frontend", {})
        mod_name    = frontend.get("module", "")
        if registry_modules is not None and mod_name and mod_name not in registry_modules:
            errors.append(
                f"Detector input '{name}': frontend module '{mod_name}' not found in registry"
            )

        resolved_inputs.append(ResolvedInput(
            name              = name,
            detector          = det_typ,
            blobfish_endpoint = ep_id,
            endpoint          = ep,
            detector_object   = entry.get("detector_object", {}),
            frontend_module   = mod_name,
            frontend_instance = frontend.get("instance", ""),
            frontend_params   = frontend.get("parameters", {}),
            output_wiring     = entry.get("output", {}),
        ))

    # -------------------------------------------------------------------
    # Resolve trigger outputs
    # -------------------------------------------------------------------
    for entry in raw.get("trigger_outputs", []):
        name   = entry.get("name", "<unnamed>")
        ep_id  = entry.get("blobfish_endpoint", "")

        ep = framework.get_endpoint(ep_id)
        if ep is None:
            errors.append(
                f"Trigger output '{name}': endpoint '{ep_id}' not found in framework manifest"
            )
            continue

        # Rule 3: must be a TX endpoint
        if ep.direction != "tx":
            errors.append(
                f"Trigger output '{name}': endpoint '{ep_id}' has direction "
                f"'{ep.direction}' (expected 'tx')"
            )

        # Role check
        if ep.endpoint_role not in _TRIGGER_OUTPUT_ROLES and ep.endpoint_role != "unused":
            errors.append(
                f"Trigger output '{name}': endpoint '{ep_id}' has role "
                f"'{ep.endpoint_role}' which is not a recognised trigger output role"
            )

        if ep_id in consumed_endpoints:
            errors.append(
                f"Trigger output '{name}': endpoint '{ep_id}' already consumed by "
                f"'{consumed_endpoints[ep_id]}' (fanout not declared)"
            )
        consumed_endpoints[ep_id] = name

        source = entry.get("source", {})
        resolved_outputs.append(ResolvedOutput(
            name              = name,
            blobfish_endpoint = ep_id,
            endpoint          = ep,
            source_instance   = source.get("instance", ""),
            source_port       = source.get("port", ""),
            wiring_kind       = entry.get("wiring_kind", ""),
        ))

    if errors:
        msg = "Detector I/O resolution failed:\n" + "\n".join(f"  {e}" for e in errors)
        raise DetectorIOError(msg)

    return DetectorIOResolved(inputs=resolved_inputs, outputs=resolved_outputs)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _endpoint_to_dict(ep: Endpoint) -> dict:
    return {
        "endpoint_id":    ep.endpoint_id,
        "direction":      ep.direction,
        "slr":            ep.slr,
        "gt_site":        ep.gt_site,
        "lane":           ep.lane,
        "lane_width":     ep.lane_width,
        "link_function":  ep.link_function,
        "endpoint_role":  ep.endpoint_role,
        "abi_port_prefix": ep.abi_port_prefix,
    }


def to_json(resolved: DetectorIOResolved) -> dict:
    """Serialise a DetectorIOResolved to a plain dict (JSON-serialisable)."""
    return {
        "detector_inputs": [
            {
                "name":              ri.name,
                "detector":          ri.detector,
                "blobfish_endpoint": ri.blobfish_endpoint,
                "endpoint":          _endpoint_to_dict(ri.endpoint),
                "detector_object":   ri.detector_object,
                "frontend": {
                    "module":    ri.frontend_module,
                    "instance":  ri.frontend_instance,
                    "parameters": ri.frontend_params,
                },
                "output": ri.output_wiring,
            }
            for ri in resolved.inputs
        ],
        "trigger_outputs": [
            {
                "name":              ro.name,
                "blobfish_endpoint": ro.blobfish_endpoint,
                "endpoint":          _endpoint_to_dict(ro.endpoint),
                "source": {
                    "instance": ro.source_instance,
                    "port":     ro.source_port,
                },
                "wiring_kind": ro.wiring_kind,
            }
            for ro in resolved.outputs
        ],
    }


def write_resolved(resolved: DetectorIOResolved, output_path: Path) -> None:
    """Write detector_io.resolved.json to disk."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    data = to_json(resolved)
    with open(output_path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
