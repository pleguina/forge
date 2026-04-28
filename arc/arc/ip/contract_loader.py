"""
Contract loader for contract-driven generation.

Loads *.interface.yaml contracts from the source tree and makes them available
to the generator pipeline.

The key abstraction is `LoadedContract`, which exposes the raw_port name for
each canonical role so the generator can directly use the physical port name
without any name-similarity heuristics.

Contracts are indexed by *ip_info_key* — the same key used to look up port
metadata in ip_info.yaml and the same name used for module instances in the
design.yml connections (``from:`` / ``to:``).

Usage::

    from arc.ip.contract_loader import load_contracts_for_design

    # Load all contracts referenced by modules referenced in design.yml:
    contracts = load_contracts_for_design(modules_yml_path, repo_root)

    # Retrieve a contract by design-module name (= ip_info_key):
    c = contracts.get("dt")
    if c:
        clk_port = c.get_raw_port("clock_primary")   # "ap_clk"
        rst_port = c.get_raw_port("reset_primary")    # "ap_rst"

Policy: framework/IP_INTERFACE_POLICY.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

# ──────────────────────────────────────────────────────────────────────────────
# Generation-authoritative role set (Phase 3 of CONTRACT_DRIVEN_GENERATION_PLAN)
# ──────────────────────────────────────────────────────────────────────────────

GENERATION_AUTHORITATIVE_ROLES: frozenset[str] = frozenset({
    "clock_primary",
    "reset_primary",
    "cfg_word",
    "cfg_valid",
})
"""
Roles for which the generator uses contracts directly and must NOT fall back
to heuristic port-name matching on contract-supported modules.
See framework/IP_INTERFACE_POLICY.md §4.
"""


# ──────────────────────────────────────────────────────────────────────────────
# LoadedContract
# ──────────────────────────────────────────────────────────────────────────────

class LoadedContract:
    """
    A parsed ``*.interface.yaml`` contract for a single module.

    Provides typed access to canonical role mappings without requiring callers
    to understand the YAML schema.
    """

    def __init__(self, path: Path, spec: dict) -> None:
        self.path = path
        self._spec = spec
        iface = spec.get("ip_interface", {})
        self._iface = iface
        self._roles: Dict[str, dict] = iface.get("roles", {})

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def module_name(self) -> str:
        """Logical module name (matches modules.yml canonical name)."""
        return self._iface.get("module_name", "")

    @property
    def ip_info_key(self) -> str:
        """Key used in ip_info.yaml — also used as the design-instance name."""
        return self._iface.get("ip_info_key", "")

    @property
    def source_type(self) -> str:
        return self._iface.get("source_type", "hls")

    # ── Combinatorial / clock-free flags ──────────────────────────────────────

    @property
    def clock_free(self) -> bool:
        """True if the module has no clock port."""
        return (
            self._iface.get("clock_free", False)
            or self._iface.get("combinatorial", False)
        )

    @property
    def reset_free(self) -> bool:
        """True if the module has no reset port."""
        return (
            self._iface.get("reset_free", False)
            or self._iface.get("combinatorial", False)
        )

    # ── Role access ───────────────────────────────────────────────────────────

    def has_role(self, role: str) -> bool:
        """Return True if the contract declares this role."""
        return role in self._roles

    def get_role(self, role: str) -> Optional[dict]:
        """Return the full role spec dict, or None."""
        return self._roles.get(role)

    def get_raw_port(self, role: str) -> Optional[str]:
        """
        Return the ``raw_port`` name for *role*, or None if the role is absent
        or is an array role (which has ``raw_port_prefix`` instead).
        """
        spec = self._roles.get(role)
        if spec is None:
            return None
        return spec.get("raw_port")

    def get_authoritative_ports(self) -> Dict[str, str]:
        """
        Return a dict of {role → raw_port} for all generation-authoritative
        roles that are present in this contract and have a scalar raw_port.
        """
        result: Dict[str, str] = {}
        for role in GENERATION_AUTHORITATIVE_ROLES:
            port = self.get_raw_port(role)
            if port:
                result[role] = port
        return result

    def get_connection_roles(
        self, direction: str, *, require_wiring_kind: bool = True,
    ) -> List[Dict]:
        """
        Return all roles in *direction* (``'input'`` or ``'output'``) that can
        participate in contract-driven data-path wiring.

        When *require_wiring_kind* is True (default), only roles with a
        ``wiring_kind`` field are returned.  Set to False to include roles
        that lack ``wiring_kind`` (needed for explicit ``role_pairs`` on
        generic modules like signal_delay).

        Each entry is a dict with:
          - ``role_name``      : str
          - ``wiring_kind``    : str or None
          - ``kind``           : ``'nd_tpl'`` | ``'prefix_array'`` | ``'scalar'``
          - ``raw_port_tpl``   : str   (only when kind == 'nd_tpl')
          - ``dims``           : list  (only when kind == 'nd_tpl')
          - ``raw_port_prefix``: str   (only when kind == 'prefix_array')
          - ``count``          : int   (only when kind == 'prefix_array')
          - ``raw_port``       : str   (only when kind == 'scalar')
          - ``width``          : int or None
        """
        result = []
        for role_name, spec in self._roles.items():
            if spec.get("direction", "") != direction:
                continue
            sk = spec.get("wiring_kind")
            if require_wiring_kind and not sk:
                continue
            width = spec.get("width")
            if "raw_port_tpl" in spec:
                result.append({
                    "role_name":    role_name,
                    "wiring_kind":  sk,
                    "kind":         "nd_tpl",
                    "raw_port_tpl": spec["raw_port_tpl"],
                    "dims":         spec["dims"],
                    "width":        width,
                    "partition":    spec.get("partition"),
                })
            elif "raw_port_prefix" in spec:
                result.append({
                    "role_name":      role_name,
                    "wiring_kind":    sk,
                    "kind":           "prefix_array",
                    "raw_port_prefix": spec["raw_port_prefix"],
                    "count":          spec["count"],
                    "width":          width,
                    "partition":      spec.get("partition"),
                })
            elif "raw_port" in spec:
                result.append({
                    "role_name":  role_name,
                    "wiring_kind": sk,
                    "kind":       "scalar",
                    "raw_port":   spec["raw_port"],
                    "width":      width,
                    "partition":  spec.get("partition"),
                })
        return result

    def __repr__(self) -> str:
        return (
            f"<LoadedContract module_name={self.module_name!r} "
            f"ip_info_key={self.ip_info_key!r} path={self.path.name!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────────────────────

def load_contracts_for_design(
    modules_yml: Path,
    repo_root: Path,
) -> Dict[str, LoadedContract]:
    """
    Load all contracts referenced via ``interface_contract:`` in *modules_yml*.

    Returns a dict keyed by **ip_info_key** so downstream code can look
    contracts up using the same key used to access ip_info.yaml.

    Missing contract files are silently skipped (the module falls back to
    heuristic compatibility mode).

    Parameters
    ----------
    modules_yml:
        Path to the plugin's ``modules.yml`` registry.
    repo_root:
        Repository root; ``interface_contract`` paths are relative to this.
    """
    contracts: Dict[str, LoadedContract] = {}

    if not modules_yml.exists():
        return contracts

    data = yaml.safe_load(modules_yml.read_text()) or {}
    for mod_entry in data.get("modules", []):
        contract_path_str = mod_entry.get("interface_contract")
        if not contract_path_str:
            continue

        contract_path = repo_root / contract_path_str
        if not contract_path.exists():
            continue

        spec = yaml.safe_load(contract_path.read_text()) or {}
        contract = LoadedContract(contract_path, spec)

        # Key by the modules.yml module name — this is the value stored in
        # Module.ip_info_key (= the ref: name from design.yml) so the matcher
        # can do a single-hop dict lookup by m.ip_info_key or m.name.
        # Fall back to contract.ip_info_key / contract.module_name if needed.
        key = mod_entry.get("name") or contract.ip_info_key or contract.module_name
        if key:
            contracts[key] = contract
            # Also register under contract.ip_info_key if different, for
            # direct ip_info-keyed lookups (batch verification scans etc.)
            alt = contract.ip_info_key
            if alt and alt != key and alt not in contracts:
                contracts[alt] = contract

    return contracts


def load_contracts_from_dirs(
    search_dirs: List[Path],
) -> Dict[str, LoadedContract]:
    """
    Load all ``*.interface.yaml`` files found recursively under *search_dirs*.

    Keyed by ip_info_key (then falls back to module_name).  Useful for batch
    verification scans; prefer :func:`load_contracts_for_design` for the
    generation pipeline.
    """
    contracts: Dict[str, LoadedContract] = {}
    for d in search_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.interface.yaml")):
            spec = yaml.safe_load(p.read_text()) or {}
            contract = LoadedContract(p, spec)
            key = contract.ip_info_key or contract.module_name
            if key:
                contracts[key] = contract
    return contracts


# ──────────────────────────────────────────────────────────────────────────────
# Support-tier utilities
# ──────────────────────────────────────────────────────────────────────────────

def classify_modules(
    module_names: List[str],
    contracts: Dict[str, LoadedContract],
) -> tuple[List[str], List[str]]:
    """
    Split *module_names* into:

    * **supported** — have a loaded contract
    * **legacy** — no contract; will run in heuristic compatibility mode

    Returns ``(supported_names, legacy_names)``.
    """
    supported = [n for n in module_names if n in contracts]
    legacy    = [n for n in module_names if n not in contracts]
    return supported, legacy


# ──────────────────────────────────────────────────────────────────────────────
# Contract → ip_info projection
#
# IMPORTANT ARCHITECTURAL DISTINCTION:
#
#   Observed ip_info  –  from built/exported IP (ip-summary / collect_all).
#                        Independent physical truth; authoritative for
#                        verification and CI.
#
#   Projected ip_info –  from contracts (this code).
#                        A contract projection; tells the generator what the
#                        contract *claims* the module exports, not what the
#                        built module actually contains.  Useful for early
#                        structural generation before IPs are built.
#
# The projected path CANNOT replace observed ip_info for production
# verification, because the check becomes self-referential.
# ──────────────────────────────────────────────────────────────────────────────

def _width_to_type(w: int) -> str:
    """Convert a port width to a VHDL-style type string (matches ip_info format)."""
    if w == 1:
        return "STD_LOGIC"
    return f"STD_LOGIC_VECTOR({w - 1} downto 0)"


def synthesize_ip_info_from_contract(contract: LoadedContract) -> dict:
    """
    Project an ip_info-compatible dict from a single interface contract.

    .. warning:: This produces **projected** ip_info — what the contract
       *claims* the module exports.  It is NOT observed physical truth.
       Use ``ip-summary`` / ``collect_all`` for production verification.

    The returned dict has the same schema as entries in ip_info.yaml:
    ``{vendor, library, name, entity, version, kind, ports: [...]}``.

    Array roles (``prefix_array``) are expanded into individual indexed
    ports (``prefix_0``, ``prefix_1``, …).
    """
    ports = []
    seen_ports: set[tuple[str, str]] = set()

    def _add_port(name: str, direction: str, width: int) -> None:
        key = (name, direction)
        if key in seen_ports:
            return
        seen_ports.add(key)
        ports.append({
            "name": name,
            "direction": direction,
            "width": width,
            "type": _width_to_type(width),
        })

    for role_name, spec in contract._roles.items():
        direction = spec.get("direction", "input").upper()
        width = spec.get("width", 1)

        if spec.get("array") or "raw_port_prefix" in spec:
            # Expand array role to indexed ports
            prefix = spec.get("raw_port_prefix", role_name + "_")
            count = spec.get("count", 1)
            for i in range(count):
                _add_port(f"{prefix}{i}", direction, width)
        elif "raw_port_tpl" in spec:
            # N-D template roles — expand all combinations
            tpl = spec["raw_port_tpl"]
            dims = spec.get("dims", [])
            if dims:
                import itertools
                ranges = [range(d) for d in dims]
                for combo in itertools.product(*ranges):
                    _add_port(tpl.format(*combo), direction, width)
        elif "raw_port" in spec:
            _add_port(spec["raw_port"], direction, width)

    source_type = contract.source_type
    kind = "hls" if source_type == "hls" else "hdl"

    return {
        "vendor": "contract",
        "library": kind,
        "name": contract.module_name,
        "entity": contract.module_name,
        "version": "contract",
        "kind": kind,
        "ports": ports,
    }


def synthesize_ip_info(
    contracts: Dict[str, LoadedContract],
    module_names: Optional[List[str]] = None,
) -> Dict[str, dict]:
    """
    Build a **projected** ip_info dict from loaded contracts.

    This is a development convenience for structural generation before
    IPs are built.  It produces what the contracts *claim* the modules
    export, not what the built modules actually contain.

    For production verification and CI, use observed ip_info from
    ``ip-summary`` / ``collect_all`` instead.

    Parameters
    ----------
    contracts:
        Dict of ip_info_key → LoadedContract.
    module_names:
        If provided, only synthesize for these keys.  Keys without a contract
        get ``None`` (same as a missing HLS build in the traditional flow).

    Returns
    -------
    A dict compatible with ``load_ip_info()``'s return value.
    """
    keys = module_names if module_names is not None else list(contracts.keys())
    result: Dict[str, dict] = {}
    for key in keys:
        c = contracts.get(key)
        result[key] = synthesize_ip_info_from_contract(c) if c else None
    return result
