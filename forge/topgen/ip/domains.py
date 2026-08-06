"""
Clock/reset domain resolution.

Resolves which net (raw port name) drives each module's clock/reset —
the shared algorithm both the canonical IR (``forge/ir/build.py``, for
``forge inspect``/``forge.ir.build.assemble_project_ir``) and the CDC
checker (``forge/topgen/ip/cdc.py``) need, kept in one place so they can
never disagree about what a module's domain is.

A domain's identity is the resolved net name itself (e.g. ``"ap_clk"``),
not a hardcoded literal — contract-driven modules are resolved
authoritatively via ``MatchReport.contract_wired_roles`` (no
re-matching). A module is correctly excluded (``None``) when its contract
declares ``clock_free``/``reset_free`` — it was never wired to any net and
must not appear to belong to one. Modules with no contract at all fall
back to the same conservative name heuristics
``forge.topgen.ip.matcher.auto_match_ports`` itself uses for compat-mode
wiring. A module that needs a clock/reset (no contract,
``connect_clock``/``connect_reset`` is on) but resolves to nothing is
reported in ``unresolved`` — the "unknown domain" case this module
handles. The "contract declares a role but its raw_port wasn't found in
ip_info" case already produces a warning in ``MatchReport.warnings``
(surfaced as a diagnostic by callers) — not duplicated here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import DesignConfig
from .contract_loader import LoadedContract

# Same conservative name lists forge.topgen.ip.matcher.auto_match_ports
# uses for compat-mode clock/reset heuristics (function-local there, not a
# public constant — duplicated here rather than imported, same choice
# already made in forge/topgen/migrate.py's infer_contract_skeleton).
CLOCK_HEURISTIC_NAMES = ("ap_clk", "clk", "clock")
RESET_HEURISTIC_NAMES = ("ap_rst", "rst", "reset", "rst_n")


def resolve_domain_nets(
    cfg: DesignConfig,
    contracts: Dict[str, LoadedContract],
    match_report: Any,
    global_nets: Dict[str, Any],
    mod_of_instance: Dict[str, str],
) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]], List[Tuple[str, str]]]:
    """Resolve each module's clock/reset domain.

    Returns ``(clock_of_module, reset_of_module, unresolved)`` —
    ``unresolved`` is a list of ``(module_name, "clock"|"reset")`` pairs
    for modules that needed a domain but none could be resolved.
    """
    clock_of_module: Dict[str, Optional[str]] = {}
    reset_of_module: Dict[str, Optional[str]] = {}
    unresolved: List[Tuple[str, str]] = []

    for mod_name, role, raw_port in match_report.contract_wired_roles:
        if role == "clock_primary":
            clock_of_module[mod_name] = raw_port
        elif role == "reset_primary":
            reset_of_module[mod_name] = raw_port

    modules_in_net: Dict[str, Set[str]] = {}
    for net_name, binds in global_nets.items():
        for inst, _port in binds:
            m = mod_of_instance.get(inst)
            if m:
                modules_in_net.setdefault(net_name, set()).add(m)

    def _heuristic_match(mod_name: str, candidates: Tuple[str, ...]) -> Optional[str]:
        return next((n for n in candidates if mod_name in modules_in_net.get(n, ())), None)

    for mod in cfg.modules:
        key = mod.ip_info_key or mod.name
        contract = contracts.get(mod.name) or contracts.get(key)

        if mod.name not in clock_of_module:
            if contract is not None and contract.clock_free:
                clock_of_module[mod.name] = None
            else:
                found = _heuristic_match(mod.name, CLOCK_HEURISTIC_NAMES)
                clock_of_module[mod.name] = found
                if found is None and contract is None and cfg.connect_clock:
                    unresolved.append((mod.name, "clock"))

        if mod.name not in reset_of_module:
            if contract is not None and contract.reset_free:
                reset_of_module[mod.name] = None
            else:
                found = _heuristic_match(mod.name, RESET_HEURISTIC_NAMES)
                reset_of_module[mod.name] = found
                if found is None and contract is None and cfg.connect_reset:
                    unresolved.append((mod.name, "reset"))

    return clock_of_module, reset_of_module, unresolved
