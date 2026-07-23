"""
FORGE Framework Import

Loads an external framework's ABI manifest and endpoint manifest, validates
them internally, and produces a normalised FrameworkImport object that the
rest of FORGE (topgen, io-resolve, payload generation) can consume without
knowing which framework provider generated the artifacts.

Currently supported providers: blobfish
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AbiPort:
    name:      str
    direction: str          # "input" | "output"
    width:     int
    kind:      str
    extra:     Dict         = field(default_factory=dict)

    def slr(self) -> Optional[int]:
        return self.extra.get("slr")

    def gt_site(self) -> Optional[int]:
        return self.extra.get("gt_site")

    def lanes(self) -> Optional[int]:
        return self.extra.get("lanes")

    def lane_width(self) -> Optional[int]:
        return self.extra.get("lane_width")


@dataclass
class Endpoint:
    endpoint_id:          str
    direction:            str           # "rx" | "tx"
    slr:                  int
    gt_site:              int
    lane:                 int
    lane_width:           int
    protocol:             str
    quad_type:            str
    cage:                 Optional[int]
    fiber:                Optional[int]
    polarity:             int
    link_function:        str
    endpoint_role:        str
    include_in_framework: bool
    logical_id:           Optional[int] = None
    logical_name:         Optional[str] = None

    # resolved at load time
    abi_port_prefix: Optional[str] = field(default=None, repr=False)


@dataclass
class FrameworkImport:
    """Normalised result of a successful framework import."""
    provider:   str
    project:    str
    board:      str
    abi_path:   Path
    ep_path:    Path

    module_name: str                        # e.g. "payload"
    ports:       List[AbiPort]
    endpoints:   List[Endpoint]

    # Fast lookup structures (built during load)
    _port_by_name:     Dict[str, AbiPort]   = field(default_factory=dict, repr=False)
    _ep_by_id:         Dict[str, Endpoint]  = field(default_factory=dict, repr=False)
    # (slr, gt_site, direction) → list of AbiPort for the tdata group
    _abi_index:        Dict[tuple, AbiPort] = field(default_factory=dict, repr=False)

    def get_port(self, name: str) -> Optional[AbiPort]:
        return self._port_by_name.get(name)

    def get_endpoint(self, endpoint_id: str) -> Optional[Endpoint]:
        return self._ep_by_id.get(endpoint_id)

    def endpoint_ids(self) -> List[str]:
        return list(self._ep_by_id)

    def rx_endpoints(self) -> List[Endpoint]:
        return [e for e in self.endpoints if e.direction == "rx"]

    def tx_endpoints(self) -> List[Endpoint]:
        return [e for e in self.endpoints if e.direction == "tx"]

    def abi_port_for_endpoint(self, ep: Endpoint) -> Optional[AbiPort]:
        """Return the tdata ABI port that covers this endpoint's lane."""
        return self._abi_index.get((ep.slr, ep.gt_site, ep.direction))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class FrameworkImportError(ValueError):
    pass


def load(
    provider: str,
    abi_path: Path,
    ep_path:  Path,
) -> FrameworkImport:
    """Load and validate a framework import from ABI + endpoint manifest files.

    Parameters
    ----------
    provider:  Provider name (currently only "blobfish").
    abi_path:  Path to the payload_abi.json produced by the framework.
    ep_path:   Path to the payload_endpoints.json produced by the framework.

    Returns
    -------
    A validated FrameworkImport object.

    Raises
    ------
    FrameworkImportError  on any validation failure.
    FileNotFoundError     if either input file is missing.
    """
    supported = ("blobfish",)
    if provider not in supported:
        raise FrameworkImportError(
            f"Unknown provider '{provider}'. Supported: {supported}"
        )

    for p, label in ((abi_path, "ABI"), (ep_path, "Endpoints")):
        if not Path(p).exists():
            raise FileNotFoundError(f"{label} manifest not found: {p}")

    with open(abi_path) as fh:
        raw_abi = json.load(fh)
    with open(ep_path) as fh:
        raw_ep = json.load(fh)

    errors: List[str] = []

    # --- Parse ABI ---
    ports: List[AbiPort] = []
    port_by_name: Dict[str, AbiPort] = {}
    abi_index: Dict[tuple, AbiPort] = {}

    for raw_p in raw_abi.get("ports", []):
        name = raw_p.get("name", "")
        extra = {k: v for k, v in raw_p.items()
                 if k not in ("name", "direction", "width", "kind")}
        p_obj = AbiPort(
            name      = name,
            direction = raw_p.get("direction", ""),
            width     = int(raw_p.get("width", 0)),
            kind      = raw_p.get("kind", ""),
            extra     = extra,
        )
        if name in port_by_name:
            errors.append(f"ABI: duplicate port name '{name}'")
        port_by_name[name] = p_obj
        ports.append(p_obj)

        if p_obj.kind in ("gt_rx_tdata", "gt_tx_tdata"):
            dir_pfx = "rx" if p_obj.kind == "gt_rx_tdata" else "tx"
            abi_index[(p_obj.slr(), p_obj.gt_site(), dir_pfx)] = p_obj

    # --- Parse endpoints ---
    endpoints: List[Endpoint] = []
    ep_by_id:  Dict[str, Endpoint] = {}

    for raw_e in raw_ep.get("endpoints", []):
        ep_id = raw_e.get("endpoint_id", "")

        # Locate matching ABI port prefix for this endpoint
        slr     = raw_e.get("slr")
        gt_site = raw_e.get("gt_site")
        lane    = raw_e.get("lane")
        dir_    = raw_e.get("direction", "")

        abi_tdata = abi_index.get((slr, gt_site, dir_))
        is_selected = bool(raw_e.get("include_in_framework", False))
        if abi_tdata is None:
            if is_selected:
                errors.append(
                    f"Endpoint '{ep_id}': no ABI port group for "
                    f"(slr={slr}, gt_site={gt_site}, direction={dir_})"
                )
            abi_prefix = None
        else:
            abi_lanes = abi_tdata.lanes() or 0
            if is_selected and (lane is None or lane >= abi_lanes):
                errors.append(
                    f"Endpoint '{ep_id}': lane {lane} out of range "
                    f"(ABI port '{abi_tdata.name}' has {abi_lanes} lanes)"
                )
            abi_prefix = abi_tdata.name.replace("_rx_tdata", "").replace("_tx_tdata", "")

        ep_obj = Endpoint(
            endpoint_id          = ep_id,
            direction            = dir_,
            slr                  = slr,
            gt_site              = gt_site,
            lane                 = lane,
            lane_width           = raw_e.get("lane_width", 64),
            protocol             = raw_e.get("protocol", ""),
            quad_type            = raw_e.get("quad_type", ""),
            cage                 = raw_e.get("cage"),
            fiber                = raw_e.get("fiber"),
            polarity             = raw_e.get("polarity", 0),
            link_function        = raw_e.get("link_function", ""),
            endpoint_role        = raw_e.get("endpoint_role", ""),
            include_in_framework = bool(raw_e.get("include_in_framework", False)),
            logical_id           = raw_e.get("logical_id"),
            logical_name         = raw_e.get("logical_name"),
            abi_port_prefix      = abi_prefix,
        )

        if ep_id in ep_by_id:
            errors.append(f"Endpoint manifest: duplicate endpoint_id '{ep_id}'")
        ep_by_id[ep_id] = ep_obj
        endpoints.append(ep_obj)

    if errors:
        msg = "Framework import validation failed:\n" + "\n".join(f"  {e}" for e in errors)
        raise FrameworkImportError(msg)

    fi = FrameworkImport(
        provider    = provider,
        project     = raw_abi.get("project", "unknown"),
        board       = raw_ep.get("board", raw_abi.get("board", "unknown")),
        abi_path    = Path(abi_path),
        ep_path     = Path(ep_path),
        module_name = raw_abi.get("module", "payload"),
        ports       = ports,
        endpoints   = endpoints,
    )
    fi._port_by_name = port_by_name
    fi._ep_by_id     = ep_by_id
    fi._abi_index    = abi_index
    return fi
