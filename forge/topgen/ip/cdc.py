"""
Clock-domain-crossing (CDC) structural validation (release-plan §3.2).

A ``design.yml`` ``connections:`` entry may declare an approved adapter:

    connections:
      - from: producer
        to: consumer
        cdc:
          kind: 2ff_sync   # or async_fifo
          depth: 2         # optional

``verify_cdc`` checks every wired connection between two modules resolved
to *different*, both-known clock (or reset) domains
(``forge.topgen.ip.domains.resolve_domain_nets``) and flags any that has
no matching ``cdc:`` declaration on its originating ``Connection`` — an
undeclared cross-domain wire. Only ``connections:`` entries are checked
(not ``topology_groups:``), consistent with ``register_stages``/
``delay_cycles``/``boundary`` already being ``Connection``-only fields,
not a new asymmetry.

Following the exact existing convention of
``forge.topgen.ip.cardinality.verify_cardinality``/
``forge.topgen.ip.contract_verifier.verify_topology_groups``: this
function only ever produces ``"error"``-severity issues (no warning
tier), and — like those two — is only ever *called* under
``gen-top --strict`` (see ``forge/core/cli/groups/topgen.py``); a
non-strict run does not check CDC at all today, matching cardinality/
topology-group verification's existing behavior exactly.

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

KNOWN_CDC_KINDS = frozenset({"2ff_sync", "async_fifo"})


@dataclass
class CdcIssue:
    severity: str  # 'error' (no warning tier — see module docstring)
    connection: str
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.severity == "error" else "⚠️ "
        return f"  {icon} [{self.connection}] {self.message}"


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

    seen_pairs: set = set()
    for (src_inst, dst_inst) in conn_map:
        src_mod = mod_of_instance.get(src_inst)
        dst_mod = mod_of_instance.get(dst_inst)
        if src_mod is None or dst_mod is None or (src_mod, dst_mod) in seen_pairs:
            continue
        seen_pairs.add((src_mod, dst_mod))

        approved = (src_mod, dst_mod) in cdc_of_pair

        clock_a, clock_b = clock_of_module.get(src_mod), clock_of_module.get(dst_mod)
        if clock_a is not None and clock_b is not None and clock_a != clock_b and not approved:
            issues.append(CdcIssue(
                "error", f"{src_mod}->{dst_mod}",
                f"undeclared clock-domain crossing: '{clock_a}' -> '{clock_b}' "
                f"— add a 'cdc:' block ({sorted(KNOWN_CDC_KINDS)}) to this connection "
                "or remove --strict.",
            ))

        reset_a, reset_b = reset_of_module.get(src_mod), reset_of_module.get(dst_mod)
        if reset_a is not None and reset_b is not None and reset_a != reset_b and not approved:
            issues.append(CdcIssue(
                "error", f"{src_mod}->{dst_mod}",
                f"undeclared reset-domain crossing: '{reset_a}' -> '{reset_b}' "
                f"— add a 'cdc:' block ({sorted(KNOWN_CDC_KINDS)}) to this connection "
                "or remove --strict.",
            ))

    return issues
