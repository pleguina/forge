"""
Tests for forge.ir.build.build_project_ir against real checked-in plugins
(audit gap #1: "No canonical IR" — this is the first slice, migration steps
1-3: topology/config loading, interface-contract loading, IP/RTL port
metadata, plus read-only consumption of the existing matcher's output).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.ir.build import build_project_ir
from forge.ir.serialize import content_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"
TRIGGER_DESIGN = REPO_ROOT / "plugins/trigger_demo/forge/designs/design.yml"
TRIGGER_MODULES = REPO_ROOT / "plugins/trigger_demo/forge/modules.yml"


def test_build_never_writes_ip_info(tmp_path):
    """forge inspect / build_project_ir must be read-only, mirroring the
    --dry-run invariant fixed in topgen.py — regression test for the same
    class of bug (an in-memory ip_info collection must never be persisted)."""
    build_dir = tmp_path / "build"
    project = build_project_ir(
        PASSTHROUGH_DESIGN,
        contracts_from=PASSTHROUGH_MODULES,
        build_dir=build_dir,
    )
    assert project.design.modules
    assert not (tmp_path / "ip_info.yaml").exists()
    assert not build_dir.exists()  # build_dir is only ever read, never created


def test_passthrough_demo_resolves_one_module_with_interfaces():
    project = build_project_ir(PASSTHROUGH_DESIGN, contracts_from=PASSTHROUGH_MODULES)
    d = project.design
    assert len(d.modules) == 1
    mod = d.modules[0]
    assert mod.ports_resolved is True
    assert mod.contract_path is not None
    assert mod.interfaces  # at least clock/reset/data roles resolved
    assert len(d.instances) == 1
    assert d.connections  # global-net (clock/reset) fan-out at minimum
    assert d.clock_domains == [d.clock_domains[0]]
    # Phase 3.1: domain name is the resolved net's raw port name, not a
    # hardcoded "default" — passthrough's contract declares clock_primary
    # on ap_clk.
    assert d.clock_domains[0].name == "ap_clk"
    assert d.reset_domains[0].name == "ap_rst"
    assert d.instances[0].id in d.clock_domains[0].instances


def test_trigger_demo_resolves_all_seven_modules_from_contracts_alone():
    """No HLS build artifacts are available in this environment — this
    proves contracts-only synthesis (the same fallback gen-top itself
    supports) is sufficient to resolve the whole design, not just modules
    with prebuilt IP."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    d = project.design
    assert len(d.modules) == 7
    assert all(m.ports_resolved for m in d.modules)
    assert len(d.instances) >= 7
    assert len(d.connections) > 0


def test_unresolvable_module_gets_diagnostic_not_a_crash(tmp_path):
    """A module with neither a contract nor a build artifact must degrade
    to ports_resolved=False plus a diagnostic, not raise."""
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: orphan\n"
        "    top: orphan_top\n"
        "    src: []\n"
        "    kind: rtl\n"
        "    instances: 1\n"
    )
    project = build_project_ir(design_yml, build_dir=tmp_path / "build")
    d = project.design
    assert len(d.modules) == 1
    mod = d.modules[0]
    assert mod.ports_resolved is False
    assert mod.interfaces == []
    assert d.connections == []
    assert any(
        "orphan" in diag.message and "no resolvable IP/RTL port metadata" in diag.message
        for diag in d.diagnostics
    )
    # forge.topgen.ip.matcher.auto_match_ports (fixed this session — see
    # test_matcher_handles_unresolved_module_without_crashing in
    # test_matcher_unresolved_ip_info.py) itself degrades gracefully for
    # global-net wiring and surfaces its own warning, forwarded here.
    assert any(
        "skipped for global-net" in diag.message and "orphan" in diag.message
        for diag in d.diagnostics
    )


def test_build_is_deterministic():
    """Identical semantic input must produce an identical IR hash — the
    determinism property required for provenance (Phase 5) to eventually
    build on."""
    a = build_project_ir(PASSTHROUGH_DESIGN, contracts_from=PASSTHROUGH_MODULES)
    b = build_project_ir(PASSTHROUGH_DESIGN, contracts_from=PASSTHROUGH_MODULES)
    assert content_hash(a) == content_hash(b)


def test_content_hash_is_independent_of_yaml_top_level_key_order(tmp_path):
    """Release-plan Phase 5 slice 5.4 determinism test: two design.yml
    fixtures that are byte-different only in top-level key order must
    still produce an identical content_hash(). Architecturally guaranteed
    already (every loader parses YAML into a plain dict, and
    ``_canonical_design_json``/``content_hash`` always re-serializes via
    ``json.dumps(..., sort_keys=True)`` — see forge/ir/serialize.py — so
    key order in the original dict, YAML or otherwise, never survives
    into the hash) but previously never proven with a real, reordered-keys
    YAML fixture rather than left an implicit, unverified assumption.
    """
    module_block = (
        "  - name: pt\n"
        "    top: passthrough\n"
        "    src: [passthrough.v]\n"
    )
    # Both files declare the exact same 3 top-level keys with the exact
    # same values — only their order in the file differs.
    ordered = (
        f"part: xcvu13p\n"
        f"clock_period: 4.0\n"
        f"modules:\n{module_block}"
    )
    reordered = (
        f"modules:\n{module_block}"
        f"clock_period: 4.0\n"
        f"part: xcvu13p\n"
    )
    assert ordered != reordered  # sanity: genuinely different bytes

    design_a = tmp_path / "a" / "design.yml"
    design_b = tmp_path / "b" / "design.yml"
    design_a.parent.mkdir()
    design_b.parent.mkdir()
    design_a.write_text(ordered)
    design_b.write_text(reordered)

    (tmp_path / "a" / "passthrough.v").write_text("module passthrough; endmodule")
    (tmp_path / "b" / "passthrough.v").write_text("module passthrough; endmodule")

    project_a = build_project_ir(design_a)
    project_b = build_project_ir(design_b)
    assert content_hash(project_a) == content_hash(project_b)


def test_connections_carry_wiring_method_evidence():
    """Real designs exercise multiple wiring methods (contract_wiring,
    topology_group, port_map) — trigger_demo's should all be populated
    with a known method, not left None (release-plan §3.5 "matching
    evidence", first slice)."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    module_connections = [
        c for c in project.design.connections
        if c.producer.instance_id != "$external"
    ]
    assert module_connections
    methods = {c.wiring_method for c in module_connections}
    assert methods <= {"contract_wiring", "topology_group", "port_map", "port_map_ranges", "auto_match"}
    assert None not in methods

    external_connections = [
        c for c in project.design.connections if c.producer.instance_id == "$external"
    ]
    assert external_connections
    assert all(c.wiring_method in ("contract_wiring", "heuristic") for c in external_connections)


def test_reserved_role_surfaces_as_ir_diagnostic(tmp_path):
    """clock_secondary/reset_secondary (marked status: reserved in
    canonical_roles.yaml) must surface as an IR diagnostic when a contract
    declares them, same as ContractVerifier's own warning."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "forge/interfaces").mkdir(parents=True)
    (plugin_root / "forge/designs").mkdir(parents=True)

    contract_path = plugin_root / "forge/interfaces/orphan.interface.yaml"
    contract_path.write_text(
        "ip_interface:\n"
        "  module_name: orphan\n"
        "  ip_info_key: orphan\n"
        "  clock_free: true\n"
        "  reset_free: true\n"
        "  roles:\n"
        "    clock_secondary:\n"
        "      raw_port: clk2\n"
        "      direction: input\n"
        "      width: 1\n"
    )
    modules_yml = plugin_root / "forge/modules.yml"
    modules_yml.write_text(
        "modules:\n"
        "  - name: orphan\n"
        "    interface_contract: interfaces/orphan.interface.yaml\n"
    )
    design_yml = plugin_root / "forge/designs/design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: orphan\n"
        "    top: orphan_top\n"
        "    src: []\n"
        "    kind: rtl\n"
        "    instances: 1\n"
    )

    project = build_project_ir(design_yml, contracts_from=modules_yml)
    assert any(
        "clock_secondary" in diag.message and "RESERVED" in diag.message
        for diag in project.design.diagnostics
    )


def test_modules_carry_latency_metadata_from_the_registry():
    """Migration step 7: ResolvedModuleDefinition.latency_cycles/latency_hint/
    is_variable_latency are populated from Module.timing (the shared
    DesignConfig loader), not re-derived — trigger_demo's modules.yml
    declares latency_hint throughout (Phase 4 slice 2: 'trig' itself was
    migrated to the newer structured latency: {kind: fixed, cycles: 3}
    spelling, real-design-proving the new syntax — see
    ResolvedModuleDefinition.latency below)."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    trig = next(m for m in project.design.modules if m.name == "trig")
    assert trig.latency_hint is None
    assert trig.latency_cycles is None
    assert trig.is_variable_latency is False
    assert trig.ip_info_key == "trigger_logic"
    assert trig.latency.kind == "fixed"
    assert trig.latency.cycles == 3


def test_module_matching_is_unaffected_by_latency_metadata():
    """Adding Module.timing must be inert to auto_match_ports's matching —
    connections should be identical in count/content to before this field
    existed (proxy check: connection count matches the known-good value
    from earlier sessions' manual verification)."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    assert len(project.design.connections) == 45


def test_interfaces_carry_protocol_from_the_contract():
    """Phase 2.2: ResolvedLogicalInterface.protocol is populated from the
    interface contract's protocol: field (dec's raw_hit/decoded_hit
    declare protocol: valid-only in the real trigger_demo contract)."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    dec = next(m for m in project.design.modules if m.name == "dec")
    raw_hit = next(i for i in dec.interfaces if i.name == "raw_hit")
    assert raw_hit.protocol == "valid-only"

    # A role that doesn't declare protocol stays None — no false positives.
    clock_primary = next(i for i in dec.interfaces if i.name == "clock_primary")
    assert clock_primary.protocol is None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2.5 — interface members
# ─────────────────────────────────────────────────────────────────────────────

from forge.ir.build import _build_interfaces
from forge.topgen.ip.contract_loader import LoadedContract


def _synthetic_contract(roles: dict) -> LoadedContract:
    spec = {
        "ip_interface": {
            "module_name": "synthetic",
            "ip_info_key": "synthetic",
            "source_type": "hls",
            "roles": roles,
        }
    }
    return LoadedContract(path=Path("/fake/synthetic.interface.yaml"), spec=spec)


def test_roles_without_interface_field_stay_1to1():
    """No `interface:` declared anywhere — identical to pre-2.5 behavior."""
    contract = _synthetic_contract({
        "raw_hit": {"raw_port": "raw_hit", "direction": "input", "width": 32},
    })
    interfaces = _build_interfaces(contract, vocab={}, module_name="synthetic", diagnostics=[])
    assert [i.name for i in interfaces] == ["raw_hit"]
    assert [m.name for m in interfaces[0].members] == ["raw_hit"]
    assert interfaces[0].members[0].direction is None


def test_grouped_roles_merge_into_one_interface_with_multiple_members():
    contract = _synthetic_contract({
        "raw_hit": {
            "raw_port": "raw_hit", "direction": "input", "width": 32,
            "wiring_kind": "raw_detector_hit", "protocol": "valid-only",
            "interface": "raw_detector_hit", "member": "data",
        },
        "raw_valid": {
            "raw_port": "raw_valid", "direction": "input", "width": 1,
            "wiring_kind": "raw_hit_valid",
            "interface": "raw_detector_hit", "member": "valid",
        },
    })
    interfaces = _build_interfaces(contract, vocab={}, module_name="synthetic", diagnostics=[])
    assert [i.name for i in interfaces] == ["raw_detector_hit"]

    grp = interfaces[0]
    assert grp.direction == "input"
    # Group-level metadata comes from the `data` member.
    assert grp.wiring_kind == "raw_detector_hit"
    assert grp.protocol == "valid-only"
    assert {m.name for m in grp.members} == {"data", "valid"}
    assert all(m.direction is None for m in grp.members)  # same direction as group


def test_reverse_direction_member_records_its_own_direction():
    """A `ready` member flowing opposite `data`/`valid` must not force the
    whole group onto one direction — its own physical direction is
    recorded on the member instead."""
    contract = _synthetic_contract({
        "in_data": {
            "raw_port": "in_data", "direction": "input", "width": 8,
            "interface": "in_stream", "member": "data",
        },
        "in_valid": {
            "raw_port": "in_valid", "direction": "input", "width": 1,
            "interface": "in_stream", "member": "valid",
        },
        "in_ready": {
            "raw_port": "in_ready", "direction": "output", "width": 1,
            "interface": "in_stream", "member": "ready",
        },
    })
    interfaces = _build_interfaces(contract, vocab={}, module_name="synthetic", diagnostics=[])
    grp = next(i for i in interfaces if i.name == "in_stream")
    assert grp.direction == "input"

    data = next(m for m in grp.members if m.name == "data")
    valid = next(m for m in grp.members if m.name == "valid")
    ready = next(m for m in grp.members if m.name == "ready")
    assert data.direction is None
    assert valid.direction is None
    assert ready.direction == "output"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2.4 — declarative cardinality (IR representation)
# ─────────────────────────────────────────────────────────────────────────────

def test_cardinality_is_resolved_from_the_data_members_declaration():
    contract = _synthetic_contract({
        "raw_hit": {
            "raw_port": "raw_hit", "direction": "input", "width": 32,
            "interface": "raw_detector_hit", "member": "data",
            "cardinality": {"producers": {"min": 1, "max": 1}},
        },
        "raw_valid": {
            "raw_port": "raw_valid", "direction": "input", "width": 1,
            "interface": "raw_detector_hit", "member": "valid",
        },
    })
    interfaces = _build_interfaces(contract, vocab={}, module_name="synthetic", diagnostics=[])
    grp = next(i for i in interfaces if i.name == "raw_detector_hit")
    assert grp.cardinality == {"producers": {"min": 1, "max": 1}}


def test_no_cardinality_declared_stays_none():
    contract = _synthetic_contract({
        "raw_hit": {"raw_port": "raw_hit", "direction": "input", "width": 32},
    })
    interfaces = _build_interfaces(contract, vocab={}, module_name="synthetic", diagnostics=[])
    assert interfaces[0].cardinality is None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.1 — clock/reset domain resolution
# ─────────────────────────────────────────────────────────────────────────────

from forge.ir.build import assemble_project_ir
from forge.topgen.config import Connection, DesignConfig, Module
from forge.topgen.ip.matcher import auto_match_ports


def _ip_info_entry(ports):
    return {"ports": ports}


def _port(name, direction, width=1):
    return {"name": name, "direction": direction, "width": width, "type": "wire"}


def test_real_designs_resolve_to_named_not_default_domains():
    """passthrough_demo's contract declares clock_primary=ap_clk/
    reset_primary=ap_rst — the domain name must be the real net, never
    the old hardcoded 'default' literal."""
    project = build_project_ir(PASSTHROUGH_DESIGN, contracts_from=PASSTHROUGH_MODULES)
    d = project.design
    assert [c.name for c in d.clock_domains] == ["ap_clk"]
    assert [c.name for c in d.reset_domains] == ["ap_rst"]
    assert d.instances[0].clock_domain == "ap_clk"
    assert d.instances[0].reset_domain == "ap_rst"


def test_clock_free_module_has_no_clock_domain_but_keeps_reset():
    """trigger_demo's hit_decoder_ip contract declares clock_free: true —
    its instances must not appear in any clock domain, while still
    resolving a reset domain normally."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    dec = next(i for i in project.design.instances if i.id == "dec_0")
    assert dec.clock_domain is None
    assert dec.reset_domain == "ap_rst"
    assert "dec_0" not in [
        inst for dom in project.design.clock_domains for inst in dom.instances
    ]
    assert "dec_0" in [
        inst for dom in project.design.reset_domains for inst in dom.instances
    ]


def test_no_contract_heuristic_clock_name_resolves_a_domain():
    """A module with no interface contract at all still resolves a domain
    via the same conservative name heuristics auto_match_ports uses."""
    mod = Module(name="m1", top="m1_top", src=["x.v"])
    cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
    ip_info = {"m1": _ip_info_entry([_port("clk", "IN"), _port("rst", "IN")])}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)

    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    inst = project.design.instances[0]
    assert inst.clock_domain == "clk"
    assert inst.reset_domain == "rst"
    assert not any("domain" in d.message.lower() for d in project.design.diagnostics)


def test_unresolvable_domain_produces_a_diagnostic():
    """A module with no contract and no recognizable clock/reset port name
    must get a diagnostic, not a silent 'default' domain membership."""
    mod = Module(name="weird", top="weird_top", src=["x.v"])
    cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
    ip_info = {"weird": _ip_info_entry([_port("totally_custom_clk", "IN")])}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)

    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    inst = project.design.instances[0]
    assert inst.clock_domain is None
    assert inst.reset_domain is None
    messages = [d.message for d in project.design.diagnostics]
    assert any("no clock domain could be resolved" in m for m in messages)
    assert any("no reset domain could be resolved" in m for m in messages)
    assert project.design.clock_domains == []
    assert project.design.reset_domains == []


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.2 — named domain relationships, CDC-crossing IR fields
# ─────────────────────────────────────────────────────────────────────────────

def _domain_contract(name, clock_port, reset_port="rst"):
    spec = {
        "ip_interface": {
            "module_name": name, "ip_info_key": name, "source_type": "rtl",
            "roles": {
                "clock_primary": {"raw_port": clock_port, "direction": "input", "width": 1},
                "reset_primary": {"raw_port": reset_port, "direction": "input", "width": 1},
            },
        }
    }
    return LoadedContract(path=Path(f"/fake/{name}.interface.yaml"), spec=spec)


def test_clock_domain_relationship_and_crossing_flag_are_populated():
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")], cdc={"kind": "2ff_sync"})],
        clock_domains={"clk_a": {}, "clk_b": {"derived_from": "clk_a", "ratio": 2}},
    )
    ip_info = {
        "src": _ip_info_entry([_port("clk_a", "IN"), _port("rst", "IN"), _port("dout", "OUT", 8)]),
        "dst": _ip_info_entry([_port("clk_b", "IN"), _port("rst", "IN"), _port("din", "IN", 8)]),
    }
    contracts = {"src": _domain_contract("src", "clk_a"), "dst": _domain_contract("dst", "clk_b")}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)

    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    clk_b = next(d for d in project.design.clock_domains if d.name == "clk_b")
    assert clk_b.derived_from == "clk_a"
    assert clk_b.ratio == 2

    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert conn.crosses_clock_domain is True
    assert conn.crosses_reset_domain is False  # both share the "rst" net


def test_same_domain_connection_does_not_cross():
    project = build_project_ir(PASSTHROUGH_DESIGN, contracts_from=PASSTHROUGH_MODULES)
    assert all(not c.crosses_clock_domain and not c.crosses_reset_domain for c in project.design.connections)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.3 — transformation-kind taxonomy
# ─────────────────────────────────────────────────────────────────────────────

def test_register_stages_is_pipeline_register_kind():
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")], register_stages=2)],
    )
    ip_info = {
        "src": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("dout", "OUT", 8)]),
        "dst": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("din", "IN", 8)]),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["pipeline_register"]
    assert conn.transformations[0].cycles == 2
    assert conn.transformations[0].tag is None


def test_delay_without_boundary_is_latency_delay_kind():
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")], delay_cycles=3)],
    )
    ip_info = {
        "src": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("dout", "OUT", 8)]),
        "dst": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("din", "IN", 8)]),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["latency_delay"]
    assert conn.transformations[0].cycles == 3


def test_delay_with_boundary_is_slr_crossing_kind_not_latency_delay():
    """A boundary-tagged delay emits ONE RTL instance (slr_crossing_delay,
    not signal_delay) — the IR must carry one transformation, not both."""
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(
            from_="src", to="dst", port_map=[("dout", "din")],
            delay_cycles=5, boundary="slr_a_to_b",
        )],
    )
    ip_info = {
        "src": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("dout", "OUT", 8)]),
        "dst": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("din", "IN", 8)]),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["slr_crossing"]
    assert conn.transformations[0].cycles == 5
    assert conn.transformations[0].tag == "slr_a_to_b"


def test_cdc_declaration_produces_cdc_synchronizer_or_async_fifo_kind():
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")], cdc={"kind": "2ff_sync"})],
    )
    ip_info = {
        "src": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("dout", "OUT", 8)]),
        "dst": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("din", "IN", 8)]),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["cdc_synchronizer"]

    cfg.connections[0].cdc = {"kind": "async_fifo", "depth": 8}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["async_fifo"]

    # release-plan §10.0B: 'level_sync' (the canonical spelling) maps to
    # the same 'cdc_synchronizer' kind as its '2ff_sync' alias.
    cfg.connections[0].cdc = {"kind": "level_sync"}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["cdc_synchronizer"]

    cfg.connections[0].cdc = {"kind": "pulse_sync", "min_spacing_cycles": 8}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["pulse_sync"]

    cfg.connections[0].cdc = {"kind": "mailbox_transfer"}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.producer.instance_id == "src")
    assert [x.kind for x in conn.transformations] == ["mailbox_transfer"]


def test_reset_domains_sync_produces_reset_synchronizer_kind():
    """release-plan §10.0B: reset_domains.<name>.sync: reset_sync is a
    domain-keyed transformation (ResolvedResetDomain.transformations),
    not a connection-keyed one."""
    # A custom-named reset domain needs a contract declaring its
    # reset_primary role — resolve_domain_nets' no-contract heuristic
    # only recognizes the fixed names ap_rst/rst/reset/rst_n.
    mod = Module(name="mod", top="mod_top", src=["x.v"])
    cfg = DesignConfig(part="xcvu13p", clock_period=4.0, modules=[mod], connections=[])
    cfg.reset_domains = {"slow_rst": {"derived_from": "ap_rst", "ratio": None, "sync": "reset_sync"}}
    ip_info = {"mod": _ip_info_entry([_port("ap_clk", "IN"), _port("slow_rst", "IN")])}
    contracts = {"mod": _domain_contract("mod", "ap_clk", "slow_rst")}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    domain = next(d for d in project.design.reset_domains if d.name == "slow_rst")
    assert [x.kind for x in domain.transformations] == ["reset_synchronizer"]


def test_fanout_synthetic_one_producer_two_consumers():
    src = Module(name="src", top="src_top", src=["x.v"])
    dst_a = Module(name="dst_a", top="dst_top", src=["x.v"])
    dst_b = Module(name="dst_b", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst_a, dst_b],
        connections=[
            Connection(from_="src", to="dst_a", port_map=[("dout", "din")]),
            Connection(from_="src", to="dst_b", port_map=[("dout", "din")]),
        ],
    )
    ip_info = {
        "src": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("dout", "OUT", 8)]),
        "dst_a": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("din", "IN", 8)]),
        "dst_b": _ip_info_entry([_port("ap_clk", "IN"), _port("ap_rst", "IN"), _port("din", "IN", 8)]),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    data_conns = [c for c in project.design.connections if c.producer.instance_id == "src"]
    assert len(data_conns) == 2
    assert all(any(x.kind == "fanout" for x in c.transformations) for c in data_conns)

    # Clock/reset global-net fan-out (every instance) must never be tagged.
    ext_conns = [c for c in project.design.connections if c.producer.instance_id == "$external"]
    assert ext_conns
    assert not any(any(x.kind == "fanout" for x in c.transformations) for c in ext_conns)


def test_trigger_demo_real_fanout_producers_are_tagged():
    """Real-design validation: trigger_demo's dec_0..dec_3 decoded_hit/
    decoded_valid outputs each drive two consumer connections today."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    fanout_conns = [
        c for c in project.design.connections
        if any(x.kind == "fanout" for x in c.transformations)
    ]
    producers = {(c.producer.instance_id, c.producer.port) for c in fanout_conns}
    expected = {
        (f"dec_{i}", port) for i in range(4) for port in ("decoded_hit", "decoded_valid")
    }
    assert producers == expected
    assert len(fanout_conns) == 16  # 8 producer groups x 2 consumers each


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.5 — matching evidence expansion
# ─────────────────────────────────────────────────────────────────────────────

def test_trigger_demo_real_gather_scatter_evidence():
    """Real-design validation (release-plan §3.5): trigger_demo's
    decoder_to_collector topology group is a real gather pattern (scalar
    dec_i.decoded_hit/decoded_valid -> col's prefix-array in_hit_i/
    in_valid_i) — the exact 8-connection set computed independently before
    writing this assertion, same discipline as the fanout proof above.
    There is no real scatter example in either reference design; scatter
    is proven only synthetically below."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    gs_conns = [
        c for c in project.design.connections
        if c.matching_evidence and c.matching_evidence.gather_scatter_pattern
    ]
    assert len(gs_conns) == 8
    assert all(c.matching_evidence.gather_scatter_pattern == "gather" for c in gs_conns)
    expected_ids = {
        f"dec_{i}.decoded_{kind}->col.in_{kind}_{i}"
        for i in range(4) for kind in ("hit", "valid")
    }
    assert {c.id for c in gs_conns} == expected_ids
    for c in gs_conns:
        assert any(x.kind == "gather_scatter" and x.tag == "gather" for x in c.transformations)


def test_trigger_demo_matching_evidence_iterates_without_crashing():
    """No-crash smoke test over the real design's full 45-connection set —
    matching_evidence must be well-formed (None or a valid MatchingEvidence)
    for every connection, contract-driven or not."""
    project = build_project_ir(TRIGGER_DESIGN, contracts_from=TRIGGER_MODULES)
    assert len(project.design.connections) == 45
    for c in project.design.connections:
        if c.matching_evidence is not None:
            assert isinstance(c.matching_evidence.rejected_candidates, list)


def test_matching_evidence_surfaces_width_mismatch():
    """No width_adapter exists (release-plan §3.3) — a producer/consumer
    width mismatch would otherwise be completely silent. matching_evidence
    surfaces the two different widths instead of hiding the fact."""
    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[Connection(from_="src", to="dst", port_map=[("dout", "din")])],
    )
    ip_info = {
        "src": _ip_info_entry([_port("dout", "OUT", width=32)]),
        "dst": _ip_info_entry([_port("din", "IN", width=8)]),
    }
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts={}, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conn = next(c for c in project.design.connections if c.id == "src.dout->dst.din")
    assert conn.matching_evidence is not None
    assert conn.matching_evidence.producer_width == 32
    assert conn.matching_evidence.consumer_width == 8


def test_matching_evidence_cardinality_result_reflects_unsatisfied_bound():
    """Release-plan §3.5's 'cardinality result' — the same bound
    forge.topgen.ip.cardinality.verify_cardinality enforces design-wide,
    reflected per-connection as a descriptive fact. Two producers wire to
    a max=1 sink; the first-driver-wins guard keeps only one, but the
    cardinality result must still count both (same rule verify_cardinality
    itself uses via rejected_fanin)."""
    src1 = Module(name="src1", top="s1_top", src=["x.v"])
    src2 = Module(name="src2", top="s2_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"])
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src1, src2, dst],
        connections=[
            Connection(from_="src1", to="dst", port_map=[("dout", "din")]),
            Connection(from_="src2", to="dst", port_map=[("dout", "din")]),
        ],
    )
    ip_info = {
        "src1": _ip_info_entry([_port("dout", "OUT", width=8)]),
        "src2": _ip_info_entry([_port("dout", "OUT", width=8)]),
        "dst": _ip_info_entry([_port("din", "IN", width=8)]),
    }
    dst_contract = _synthetic_contract({
        "din": {
            "raw_port": "din", "direction": "input", "width": 8,
            "cardinality": {"producers": {"min": 1, "max": 1}},
        },
    })
    contracts = {"dst": dst_contract}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    conns = [c for c in project.design.connections if c.consumer.port == "din"]
    assert len(conns) == 1  # first-driver-wins: only one candidate actually wired
    ev = conns[0].matching_evidence
    assert ev.consumer_cardinality is not None
    assert ev.consumer_cardinality.bound == {"min": 1, "max": 1}
    assert ev.consumer_cardinality.actual_count == 2
    assert ev.consumer_cardinality.satisfied is False
    assert len(ev.rejected_candidates) == 1


def test_matching_evidence_synthetic_scatter():
    """Scatter (prefix_array producer -> scalar consumer instances) has no
    real occurrence in either reference design — proven synthetically
    here, unlike gather above."""
    from forge.topgen.config import TopologyGroup

    src = Module(name="src", top="src_top", src=["x.v"])
    dst = Module(name="dst", top="dst_top", src=["x.v"], instances=3)
    cfg = DesignConfig(
        part="xcvu13p", clock_period=4.0, modules=[src, dst],
        connections=[],
        topology_groups=[TopologyGroup(
            name="scatter_tg", family="wk", from_="src", to="dst",
            role_pairs=[("a_out", "b_in")],
        )],
    )
    ip_info = {
        "src": _ip_info_entry([_port(f"a_out_{i}", "OUT") for i in range(3)]),
        "dst": _ip_info_entry([_port("b_in", "IN")]),
    }
    src_contract = _synthetic_contract({
        "a_out": {
            "direction": "output", "wiring_kind": "wk",
            "raw_port_prefix": "a_out_", "count": 3, "width": 32,
        },
    })
    dst_contract = _synthetic_contract({
        "b_in": {"raw_port": "b_in", "direction": "input", "width": 32, "wiring_kind": "wk"},
    })
    contracts = {"src": src_contract, "dst": dst_contract}
    conn_map, global_nets, report = auto_match_ports(cfg, ip_info, contracts=contracts)
    project = assemble_project_ir(
        cfg, "/tmp/design.yml", contracts=contracts, ip_info_data=ip_info,
        conn_map=conn_map, global_nets=global_nets, match_report=report,
    )
    scatter_conns = [
        c for c in project.design.connections
        if c.matching_evidence and c.matching_evidence.gather_scatter_pattern == "scatter"
    ]
    assert len(scatter_conns) == 3
    for c in scatter_conns:
        assert any(x.kind == "gather_scatter" and x.tag == "scatter" for x in c.transformations)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.3 — tie_off connections
# ─────────────────────────────────────────────────────────────────────────────

from forge.ir.build import build_tie_off_connections
from forge.ir.project import project_to_conn_map
from forge.ir.model import ResolvedDesign, ResolvedProject


def test_build_tie_off_connections_shape():
    conns = build_tie_off_connections([("dst", "unused_in", 8), ("dst", "another_in", 1)])
    assert len(conns) == 2
    assert conns[0].producer.instance_id == "$tie_off"
    assert conns[0].producer.port is None
    assert conns[0].consumer.instance_id == "dst"
    assert conns[0].consumer.port == "unused_in"
    assert [x.kind for x in conns[0].transformations] == ["tie_off"]
    assert conns[0].id == "tie_off:dst.unused_in"


def test_build_tie_off_connections_empty_list():
    assert build_tie_off_connections([]) == []


def test_project_to_conn_map_skips_tie_off_connections():
    tie_off_conns = build_tie_off_connections([("dst", "unused_in", 8)])
    design = ResolvedDesign(name="t", connections=tie_off_conns)
    project = ResolvedProject(design=design, forge_version="0.0.0")
    conn_map, global_nets = project_to_conn_map(project)
    assert conn_map == {}
    assert global_nets == {}
