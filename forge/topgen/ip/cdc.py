"""
Clock-domain-crossing (CDC) structural validation (release-plan §3.2,
expanded to the full 5-kind primitive family in §10.0B).

A ``design.yml`` ``connections:`` entry may declare an approved adapter
for a *data* crossing:

    connections:
      - from: producer
        to: consumer
        cdc:
          kind: level_sync   # or pulse_sync, mailbox_transfer, async_fifo
                             # ('2ff_sync' is a backwards-compatible alias
                             # for 'level_sync')
          depth: 2           # required for async_fifo; must be a power of two
          min_spacing_cycles: 8   # required for pulse_sync

A *reset* crossing is declared differently, since it's a property of a
destination reset *domain*, not a data connection between two modules —
see the top-level ``reset_domains:`` block instead:

    reset_domains:
      rst_slow:
        derived_from: ap_rst
        sync: reset_sync

``verify_cdc`` checks every wired connection between two modules resolved
to *different*, both-known clock (or reset) domains
(``forge.topgen.ip.domains.resolve_domain_nets``) and flags any that has
no matching ``cdc:`` declaration on its originating ``Connection`` — an
undeclared cross-domain wire. Only ``connections:`` entries are checked
(not ``topology_groups:``), consistent with ``register_stages``/
``delay_cycles``/``boundary`` already being ``Connection``-only fields,
not a new asymmetry. It does not itself validate ``reset_domains.*.sync``
(that's schema-level work done at ``DesignConfig.load`` time in
``forge.topgen.config``) — reset *synchronization* is a docs/generation
concern, not a structural crossing to flag as undeclared.

Following the exact existing convention of
``forge.topgen.ip.cardinality.verify_cardinality``/
``forge.topgen.ip.contract_verifier.verify_topology_groups``: this
function only ever produces ``"error"``-severity issues (no warning
tier). It is called both from ``gen-top --strict`` (as an error) and,
new in release-plan §10.0B, from ``forge topgen validate`` (surfaced as
a warning, whenever contracts/ip_info are available) — see
``forge/core/cli/groups/topgen.py``.

A single ``cdc:`` declaration approves *both* clock- and reset-crossing
for its connection — a real synchronizer/FIFO handles the signal, not two
independent declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from ..config import DesignConfig
from .contract_loader import LoadedContract
from .domains import resolve_domain_nets

KNOWN_CDC_KINDS = frozenset({"level_sync", "2ff_sync", "pulse_sync", "mailbox_transfer", "async_fifo"})

CODE_UNDECLARED_CLOCK_CROSSING = "ATG023"
CODE_UNDECLARED_RESET_CROSSING = "ATG024"


@dataclass
class CdcIssue:
    severity: str  # 'error' (no warning tier — see module docstring)
    connection: str
    message: str
    code: str = ""  # 'ATG023'/'ATG024' (release-plan §10.0B) — see forge.core.diagnostics

    def __str__(self) -> str:
        icon = "❌" if self.severity == "error" else "⚠️ "
        prefix = f"[{self.code}] " if self.code else ""
        return f"  {icon} [{self.connection}] {prefix}{self.message}"


@dataclass
class _PairDomains:
    src_mod: str
    dst_mod: str
    kind: "str | None"
    clock_a: "str | None"
    clock_b: "str | None"
    reset_a: "str | None"
    reset_b: "str | None"
    approved: bool


def _resolve_pairs(
    design_cfg: DesignConfig,
    contracts: Dict[str, LoadedContract],
    match_report: Any,
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],
    global_nets: Dict[str, Any],
) -> "List[_PairDomains]":
    """Resolve clock/reset domains and the declared ``cdc:`` kind (if any)
    for every distinct wired module-pair connection — the shared
    traversal both :func:`verify_cdc` and :func:`report_all_crossings`
    build on, so they can never disagree about what a pair's domains or
    approval status are."""
    mod_of_instance: Dict[str, str] = {}
    for mod in design_cfg.modules:
        for idx in range(max(1, mod.instances)):
            inst_id = mod.name if mod.instances == 1 else f"{mod.name}_{idx}"
            mod_of_instance[inst_id] = mod.name

    clock_of_module, reset_of_module, _unresolved = resolve_domain_nets(
        design_cfg, contracts, match_report, global_nets, mod_of_instance,
    )

    cdc_of_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in design_cfg.connections:
        if c.cdc is not None:
            cdc_of_pair[(c.from_, c.to)] = c.cdc

    pairs: "List[_PairDomains]" = []
    seen_pairs: set = set()
    for (src_inst, dst_inst) in conn_map:
        src_mod = mod_of_instance.get(src_inst)
        dst_mod = mod_of_instance.get(dst_inst)
        if src_mod is None or dst_mod is None or (src_mod, dst_mod) in seen_pairs:
            continue
        seen_pairs.add((src_mod, dst_mod))

        cdc = cdc_of_pair.get((src_mod, dst_mod))
        pairs.append(_PairDomains(
            src_mod=src_mod, dst_mod=dst_mod,
            kind=cdc.get("kind") if cdc else None,
            clock_a=clock_of_module.get(src_mod), clock_b=clock_of_module.get(dst_mod),
            reset_a=reset_of_module.get(src_mod), reset_b=reset_of_module.get(dst_mod),
            approved=cdc is not None,
        ))
    return pairs


def verify_cdc(
    design_cfg: DesignConfig,
    contracts: Dict[str, LoadedContract],
    match_report: Any,
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],
    global_nets: Dict[str, Any],
) -> List[CdcIssue]:
    """Flag every wired module-pair connection that crosses clock or reset
    domains without an approved ``cdc:`` declaration."""
    issues: List[CdcIssue] = []

    for pair in _resolve_pairs(design_cfg, contracts, match_report, conn_map, global_nets):
        if (
            pair.clock_a is not None and pair.clock_b is not None
            and pair.clock_a != pair.clock_b and not pair.approved
        ):
            issues.append(CdcIssue(
                "error", f"{pair.src_mod}->{pair.dst_mod}",
                f"undeclared clock-domain crossing: '{pair.clock_a}' -> '{pair.clock_b}' "
                f"— add a 'cdc:' block ({sorted(KNOWN_CDC_KINDS)}) to this connection "
                "or remove --strict.",
                code=CODE_UNDECLARED_CLOCK_CROSSING,
            ))

        if (
            pair.reset_a is not None and pair.reset_b is not None
            and pair.reset_a != pair.reset_b and not pair.approved
        ):
            issues.append(CdcIssue(
                "error", f"{pair.src_mod}->{pair.dst_mod}",
                f"undeclared reset-domain crossing: '{pair.reset_a}' -> '{pair.reset_b}' "
                f"— add a 'cdc:' block ({sorted(KNOWN_CDC_KINDS)}) to this connection "
                "or remove --strict.",
                code=CODE_UNDECLARED_RESET_CROSSING,
            ))

    return issues


def report_all_crossings(
    design_cfg: DesignConfig,
    contracts: Dict[str, LoadedContract],
    match_report: Any,
    conn_map: Dict[Tuple[str, str], List[Tuple[str, str]]],
    global_nets: Dict[str, Any],
) -> "List[Dict[str, Any]]":
    """Return one plain dict (the same field shape
    ``forge.verify.cdc_verification_result.CdcCrossingResult`` uses) per
    module-pair connection that is actually a clock- or reset-domain
    crossing (both domains known and differing), or that explicitly
    declares a ``cdc:`` adapter regardless — passing entries included,
    not just :func:`verify_cdc`'s failures (release-plan Phase 10, slice
    10.0C).

    A same-domain pair with no ``cdc:`` declared is not a crossing at
    all and is skipped entirely — there is nothing to verify.

    Returns plain dicts, not a ``forge.verify`` dataclass — ``forge.topgen``
    and ``forge.verify`` must never cross-import each other
    (``ci/import_direction_check.sh`` enforces this; they're independent
    subsystems composed by the CLI layer, not by each other). A caller in
    the CLI layer (e.g. ``forge/core/cli/groups/topgen.py``) passes this
    return value into
    ``forge.verify.cdc_verification_result.build_cdc_verification_result``.
    """
    results: "List[Dict[str, Any]]" = []
    for pair in _resolve_pairs(design_cfg, contracts, match_report, conn_map, global_nets):
        clock_crosses = (
            pair.clock_a is not None and pair.clock_b is not None and pair.clock_a != pair.clock_b
        )
        reset_crosses = (
            pair.reset_a is not None and pair.reset_b is not None and pair.reset_a != pair.reset_b
        )
        if not clock_crosses and not reset_crosses and not pair.approved:
            continue

        connection = f"{pair.src_mod}->{pair.dst_mod}"
        if pair.approved:
            passed = True
            message = f"approved via cdc: {{kind: {pair.kind}}}"
        else:
            # Only reachable when clock_crosses or reset_crosses is True
            # (the skip-check above already filtered out the
            # not-approved-and-no-crossing case, which needs no entry).
            passed = False
            parts = []
            if clock_crosses:
                parts.append(f"clock '{pair.clock_a}' -> '{pair.clock_b}'")
            if reset_crosses:
                parts.append(f"reset '{pair.reset_a}' -> '{pair.reset_b}'")
            message = f"undeclared crossing ({', '.join(parts)}) — no 'cdc:' block declared"

        results.append({
            "connection": connection,
            "kind": pair.kind,
            "source_clock_domain": pair.clock_a, "destination_clock_domain": pair.clock_b,
            "source_reset_domain": pair.reset_a, "destination_reset_domain": pair.reset_b,
            "property_checked": "clock/reset-domain crossing declared with an approved "
                                 "cdc:/reset_domains.*.sync adapter",
            "passed": passed,
            "message": message,
        })
    return results
