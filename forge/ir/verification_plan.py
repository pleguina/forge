"""
Verification planning on the canonical IR — Phase G3.

Verification and generation used to answer "what are this design's ports,
and which instance is behind each one?" independently: generation from its
own lifting rules, verification by reading back ``port_map.yaml`` (rendered
from those rules) and, for the flow's entry point, by string-matching a
module name against the design configuration. Two readings of one design
can disagree, and when they do the disagreement surfaces as a testbench
that binds a port the DUT doesn't have.

This module builds the plan from the IR instead: the DUT's stimulus and
observation points are ``ResolvedTopLevelPort``s, carrying the instance pin
each one reaches (the generator's own join, recorded at generation time —
see ``forge.generation.generators.structural_verilog``), and each declared
flow's entry point is resolved against real ``ResolvedModuleDefinition``s.

Deliberately kept in ``forge/ir/`` and free of any CLI/backend import, the
same way ``plan.py`` and ``serialize.py`` are — enforced by
``ci/import_direction_check.sh``. It imports the verification *contract
loader* only as a type at check time; the caller passes an
already-loaded contract.

Nothing here decides whether a flow passes, what stimulus it applies, or
how a backend is invoked. It states what the design offers a testbench and
which of the declared flows this design can account for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import (
    ResolvedProject,
    ResolvedVerificationBinding,
    ResolvedVerificationFlow,
    ResolvedVerificationPlan,
)

# The top-level ports a testbench treats as its clock and reset rather than
# as stimulus — the generator's own two origins for them, so this never
# guesses from a port's name.
_CLOCK_ORIGIN = "clock"
_RESET_ORIGIN = "reset"

# The `dut_rtl_source` prefix a plugin uses for generated top levels
# ("gen-top", or "gen-top/<design>" when one plugin generates several).
# It is a path, relative to the consumer root — which design it points at is
# settled by comparing directories, not by parsing the string.
_GENERATED_TOP_PREFIX = "gen-top"


def _binding(port: Any) -> ResolvedVerificationBinding:
    return ResolvedVerificationBinding(
        top_port=port.name,
        direction=port.direction,
        width=port.width,
        origin=port.origin,
        instance_id=port.instance_id,
        instance_port=port.instance_port,
    )


def resolve_entry_point_module(project: ResolvedProject, ref_or_name: str) -> Optional[str]:
    """The IR module name a flow's declared ``top_module`` refers to.

    Matched against the design's own module name first, then its
    ``ip_info_key`` — a flow declares the ``modules.yml`` ref, which is not
    always the same string as the design.yml module name. Public because
    ``forge.analysis.design_explorer.verification_join`` needs the same
    lookup for the explorer's overlay and there should be one of it.
    """
    for mod in project.design.modules:
        if mod.name == ref_or_name:
            return mod.name
    for mod in project.design.modules:
        if mod.ip_info_key == ref_or_name:
            return mod.name
    return None


def _same_directory(a: Optional[Path], b: Optional[Path]) -> bool:
    if a is None or b is None:
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _resolve_flow(
    project: ResolvedProject,
    flow: Any,
    *,
    dut_dir: Optional[Path],
    consumer_root: Optional[Path],
) -> ResolvedVerificationFlow:
    dut_source = str(getattr(flow, "dut_rtl_source", "") or "")
    resolved = ResolvedVerificationFlow(
        name=flow.name,
        kind=flow.kind,
        backend=flow.backend,
        declared_top_module=flow.top_module,
        dut_rtl_source=dut_source,
        dataset=getattr(flow, "dataset", None),
        stimulus_mode=getattr(flow, "stimulus_mode", None),
    )

    declares_generated_top = (
        dut_source == _GENERATED_TOP_PREFIX
        or dut_source.startswith(_GENERATED_TOP_PREFIX + "/")
    )
    flow_dut_dir = (
        (consumer_root / dut_source) if (consumer_root and dut_source) else None
    )

    if declares_generated_top:
        resolved.dut_kind = "generated_top"
        # Which generated top level? A plugin may generate several from one
        # verification contract, so the answer is the directory this run
        # actually wrote to — never the flow's or the design's name, which
        # are free-form and routinely differ.
        if _same_directory(flow_dut_dir, dut_dir):
            resolved.targets_this_design = True
            if project.design.top_module and flow.top_module != project.design.top_module:
                resolved.unresolved_reason = (
                    f"elaborates top_module '{flow.top_module}', but this "
                    f"design's generated top level is "
                    f"'{project.design.top_module}'"
                )
        else:
            resolved.note = (
                f"targets the generated top level under '{dut_source}', not "
                "this design's"
            )
        return resolved

    module_name = resolve_entry_point_module(project, flow.top_module)
    if module_name is not None:
        resolved.dut_kind = "module"
        resolved.targets_this_design = True
        resolved.entry_point_module = module_name
        resolved.entry_point_instances = [
            inst.id for inst in project.design.instances if inst.module == module_name
        ]
        return resolved

    resolved.note = (
        f"declared top_module '{flow.top_module}' is not a module in this "
        "design (checked both the design's module names and their "
        "modules.yml refs)"
    )
    return resolved


def build_verification_plan(
    project: ResolvedProject,
    contract: Optional[Any] = None,
    *,
    dut_dir: Optional[Path] = None,
    consumer_root: Optional[Path] = None,
) -> ResolvedVerificationPlan:
    """Resolve *contract*'s flows and this design's testbench-facing ports
    into a ``ResolvedVerificationPlan``.

    *contract* is a ``forge.verification.design_contract.VerifyDesignContract``
    (or ``None`` when the project has no verification contract). It is read,
    never loaded here — this module stays free of file I/O and of any
    verification import.

    *dut_dir* is the directory this generation run wrote its top level to,
    and *consumer_root* the root a flow's ``dut_rtl_source`` is relative to.
    Together they answer "is this flow about this design?" by comparing
    directories rather than names. Both absent (``forge inspect``, a test
    building a plan by hand) simply means no flow can be attributed to this
    design's generated top level — the flows are still resolved and
    recorded.

    The plan is ``populated`` whenever there was anything to state: a
    contract with flows, or a post-generation IR that knows its top-level
    ports. A pre-generation IR (``forge inspect``, which never runs the
    generator and so has no ``top_ports``) with no contract in reach yields
    the unpopulated plan, exactly as before.
    """
    flows = [
        _resolve_flow(project, fl, dut_dir=dut_dir, consumer_root=consumer_root)
        for fl in getattr(contract, "flows", ())
    ]

    stimulus: List[ResolvedVerificationBinding] = []
    observation: List[ResolvedVerificationBinding] = []
    clocks: List[str] = []
    resets: List[str] = []

    for port in project.design.top_ports:
        if port.origin == _CLOCK_ORIGIN:
            clocks.append(port.name)
        elif port.origin == _RESET_ORIGIN:
            resets.append(port.name)
        elif port.direction == "in":
            stimulus.append(_binding(port))
        else:
            observation.append(_binding(port))

    populated = bool(flows or project.design.top_ports)
    return ResolvedVerificationPlan(
        populated=populated,
        note=(
            "Resolved from the canonical IR: top-level ports as stimulus/"
            "observation points, declared flows resolved to real modules."
            if populated else
            ResolvedVerificationPlan().note
        ),
        flows=flows,
        stimulus=stimulus,
        observation=observation,
        clocks=clocks,
        resets=resets,
    )


def port_divergences(
    plan: ResolvedVerificationPlan,
    dut_ports: Dict[str, Any],
) -> List[str]:
    """Compare a DUT's own port list against the plan's, and report every
    difference in both directions.

    *dut_ports* is ``{name: (direction, width)}`` — the shape
    ``forge.core.utils.hdl_parser._scan_verilog_ports`` returns and the one
    ``port_map.yaml`` is built from, so a caller can pass either a freshly
    scanned DUT or a loaded port map.

    Returns one human-readable line per divergence, empty when the two
    agree. Direction strings are compared after normalising ``input``/
    ``output`` to the IR's ``in``/``out`` — the two conventions coexist in
    generated artifacts and a spelling difference is not a divergence.

    This is a *comparison*, not a check with a policy: the caller decides
    whether a difference is a warning or an error. Clocks and resets
    participate — they are ports of the DUT like any other, and a testbench
    that can't find its clock fails as hard as one that can't find its data.
    """
    def _dir(value: str) -> str:
        return {"input": "in", "output": "out"}.get(value, value)

    planned: Dict[str, ResolvedVerificationBinding] = {
        b.top_port: b for b in (*plan.stimulus, *plan.observation)
    }
    for name in plan.clocks:
        planned[name] = ResolvedVerificationBinding(
            top_port=name, direction="in", width=1, origin=_CLOCK_ORIGIN,
        )
    for name in plan.resets:
        planned[name] = ResolvedVerificationBinding(
            top_port=name, direction="in", width=1, origin=_RESET_ORIGIN,
        )

    divergences: List[str] = []
    for name in sorted(set(planned) - set(dut_ports)):
        divergences.append(
            f"{name}: the resolved design has this port, the DUT does not"
        )
    for name in sorted(set(dut_ports) - set(planned)):
        divergences.append(
            f"{name}: the DUT has this port, the resolved design does not"
        )
    for name in sorted(set(planned) & set(dut_ports)):
        direction, width = dut_ports[name]
        binding = planned[name]
        if _dir(direction) != _dir(binding.direction):
            divergences.append(
                f"{name}: direction {binding.direction} in the resolved "
                f"design, {direction} in the DUT"
            )
        if int(width) != int(binding.width):
            divergences.append(
                f"{name}: width {binding.width} in the resolved design, "
                f"{width} in the DUT"
            )
    return divergences
