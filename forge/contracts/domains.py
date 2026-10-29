"""
Clock/reset domain resolution.

Resolves which top-level net drives each module's clock/reset —
the shared algorithm both the canonical IR (``forge/ir/build.py``, for
``forge inspect``/``forge.ir.build.assemble_project_ir``) and the CDC
checker (``forge/contracts/cdc.py``) need, kept in one place so they can
never disagree about what a module's domain is.

A domain's identity is the top-level net the generators wire the port to
(:func:`top_level_net`), not the module's port name: under
``connect_clock`` a module's ``clk`` and another's ``ap_clk`` both end up
on ``ap_clk`` and are one domain. Contract-driven modules are resolved
authoritatively via ``MatchReport.contract_wired_roles`` (no
re-matching). A module is correctly excluded (``None``) when its contract
declares ``clock_free``/``reset_free`` — it was never wired to any net and
must not appear to belong to one. Modules with no contract at all fall
back to the same conservative name heuristics
``forge.contracts.matcher.auto_match_ports`` itself uses for compat-mode
wiring. A module that needs a clock/reset (no contract,
``connect_clock``/``connect_reset`` is on) but resolves to nothing is
reported in ``unresolved`` — the "unknown domain" case this module
handles. The "contract declares a role but its raw_port wasn't found in
ip_info" case already produces a warning in ``MatchReport.warnings``
(surfaced as a diagnostic by callers) — not duplicated here.
"""

from __future__ import annotations

from typing import Any, Container, Dict, List, Optional, Set, Tuple

from .config import DesignConfig
from .contract_loader import LoadedContract
from forge.core.utils.signal_names import is_active_low_reset, is_clock_name, is_reset_name

# Same conservative name lists forge.contracts.matcher.auto_match_ports
# uses for compat-mode clock/reset heuristics (function-local there, not a
# public constant — duplicated here rather than imported, same choice
# already made in forge/generation/migrate.py's infer_contract_skeleton).
CLOCK_HEURISTIC_NAMES = ("ap_clk", "clk", "clock")
RESET_HEURISTIC_NAMES = ("ap_rst", "rst", "reset", "rst_n")


#: The clock/reset names collapsed onto the single top-level ``ap_clk``/
#: ``ap_rst`` net. A name *outside* this that the design declared as its own
#: domain (``clk_b``) must keep its own net — that distinction is
#: load-bearing for CDC, handled entirely by the caller (not here).
def is_standard_clock(name: str) -> bool:
    return (
        name in ("clk", "clock", "ap_clk")
        or name.endswith("_clk") or name.endswith("_ap_clk")
    )


def is_standard_reset(name: str) -> bool:
    return (
        name in ("rst", "reset", "ap_rst", "rst_n")
        or name.endswith("_rst") or name.endswith("_ap_rst")
        or name.endswith("_rst_n")
    )


def collapses_to_global(name: str, *, is_clock: bool, own_nets: Container[str]) -> bool:
    """Whether this clock/reset pin is driven by the single global net.

    Three cases:
    1. A **standard** name (``clk``, ``ap_clk``, ``*_clk``) always collapses
       onto ``ap_clk``/``ap_rst``, even when the design also lists it as a
       domain — that is what ``connect_clock``/``connect_reset`` mean.
    2. A **non-standard** name the design declared as its own domain
       (``clk_b``) keeps its own top-level net.
    3. A name that reads as a clock or reset by the shared convention but is
       **neither** standard nor a declared domain (``pclk``, ``presetn``,
       ``s_axi_aclk``) collapses onto the global net.
    """
    standard = is_standard_clock(name) if is_clock else is_standard_reset(name)
    if standard:
        return True
    reads_as = is_clock_name(name) if is_clock else is_reset_name(name)
    return reads_as and name not in (own_nets or ())


def reset_is_active_low(port: str, contract: Optional[LoadedContract] = None) -> bool:
    """Whether a module's reset pin asserts low.

    The global ``ap_rst`` is active-high, so a generator drives such a pin
    with the inverted net. A contract role for this port that declares
    ``active_level`` decides; otherwise the name does (``rst_n``,
    ``aresetn``).
    """
    if contract is not None:
        for role in ("reset_primary", port):
            spec = contract.get_role(role) or {}
            if spec.get("raw_port", role) == port and spec.get("active_level") in ("low", "high"):
                return spec["active_level"] == "low"
    return is_active_low_reset(port)


def top_level_net(
    domain_name: str, *, is_clock: bool, own_nets: Container[str] = (),
) -> str:
    """Translate a resolved clock/reset domain identity (a module's raw
    clock/reset port name) into the actual
    top-level net a generator wires it to.

    Both generators' own clock/reset auto-map (``forge.generation.generators._port_resolution._auto_mapped_global``) collapses every *standard*-name variant onto the single literal
    ``ap_clk``/``ap_rst`` top-level port, regardless of which specific
    variant string a module's contract declared. A domain with its own
    declared global net (``clk_b``, a named second clock domain) keeps that
    net instead — pass those names as *own_nets*, and they win over the
    collapse.

    Used by CDC synchronizer / reset-synchronizer emission to reference the
    net that actually exists in the generated output, not the raw domain
    identity string, so it must resolve exactly as the per-instance port
    mapping does.
    """
    if not collapses_to_global(domain_name, is_clock=is_clock, own_nets=own_nets):
        return domain_name
    return "ap_clk" if is_clock else "ap_rst"


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

    def _net(port: Optional[str], is_clock: bool) -> Optional[str]:
        return None if port is None else top_level_net(port, is_clock=is_clock, own_nets=global_nets)

    clock_of_module = {m: _net(port, True) for m, port in clock_of_module.items()}
    reset_of_module = {m: _net(port, False) for m, port in reset_of_module.items()}
    return clock_of_module, reset_of_module, unresolved
