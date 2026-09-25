"""Direct unit coverage for forge.generation.generators._port_resolution —
the shared top-level-port/tie-off resolver both write_structural_verilog
and write_bd_tcl call. Exercises paths the higher-level bd/verilog
integration tests don't reach on their own: a real point-to-point
connection (driven_outs/incoming/_canon_pin), debug:true ports, and
system.yml framework aliasing.
"""

from __future__ import annotations

from pathlib import Path

from forge.contracts.config import DesignConfig
from forge.contracts.contract_loader import load_contracts_for_design, synthesize_ip_info
from forge.contracts.matcher import auto_match_ports
from forge.generation.generators._port_resolution import classify_connections, resolve_top_ports


def _write_two_module_design(tmp_path: Path, *, debug_on_sink: bool = False) -> tuple[Path, Path]:
    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n"
        "  ip_info_key: src\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    (tmp_path / "interfaces" / "sink.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: sink\n"
        "  ip_info_key: sink\n"
        "  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 8}\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n"
        "    kind: rtl\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: sink\n"
        "    kind: rtl\n"
        "    top: sink_top\n"
        "    src: [sink.v]\n"
        "    interface_contract: interfaces/sink.interface.yaml\n"
    )
    debug_line = "    debug: true\n" if debug_on_sink else ""
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu13p\n"
        "clock_period: 4.0\n"
        "modules:\n"
        "  - name: src\n"
        "    top: src_top\n"
        "    src: [src.v]\n"
        "  - name: sink\n"
        "    top: sink_top\n"
        "    src: [sink.v]\n"
        f"{debug_line}"
        "connections:\n"
        "  - from: src\n"
        "    to: sink\n"
        "    port_map: [[dout, din]]\n"
    )
    return design_yml, modules_yml


def _resolve(design_path: Path, modules_path: Path):
    cfg = DesignConfig.load_relaxed(design_path)
    contracts = load_contracts_for_design(modules_path, design_path.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, _match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)
    return cfg, ip_info, conn_map, global_nets


def test_classify_connections_marks_a_real_point_to_point_connection_as_driven(tmp_path: Path) -> None:
    design_yml, modules_yml = _write_two_module_design(tmp_path)
    cfg, ip_info, conn_map, global_nets = _resolve(design_yml, modules_yml)

    resolution = resolve_top_ports(cfg, ip_info, tmp_path / "ips", global_nets)
    open_outputs, tied_to_zero = classify_connections(cfg, ip_info, conn_map, global_nets, resolution)

    # src.dout drives sink.din via the declared port_map — neither pin is
    # a top-level port, and neither should show up as open/tied.
    assert open_outputs == []
    assert tied_to_zero == []


def test_resolve_top_ports_exposes_debug_ports_for_a_debug_module(tmp_path: Path) -> None:
    design_yml, modules_yml = _write_two_module_design(tmp_path, debug_on_sink=True)
    cfg, ip_info, conn_map, global_nets = _resolve(design_yml, modules_yml)

    resolution = resolve_top_ports(cfg, ip_info, tmp_path / "ips", global_nets)

    debug_ports = [p for p in resolution.top_ports if p["origin"] == "debug"]
    assert debug_ports == [{
        "name": "debug_sink_din", "direction": "out", "width": 8,
        "origin": "debug", "instance": "sink", "port": "din",
    }]

    # A debug tap doesn't substitute for the pin's real connection — din is
    # still driven by src.dout through the normal point-to-point path, not
    # left tied-to-zero just because it also got a debug port.
    open_outputs, tied_to_zero = classify_connections(cfg, ip_info, conn_map, global_nets, resolution)
    assert tied_to_zero == []
    assert open_outputs == []


def test_resolve_top_ports_applies_system_yml_framework_aliases(tmp_path: Path) -> None:
    design_yml, modules_yml = _write_two_module_design(tmp_path)
    cfg, ip_info, conn_map, global_nets = _resolve(design_yml, modules_yml)

    system_yml = tmp_path / "system.yml"
    system_yml.write_text(
        "connections:\n"
        "  - from: framework\n"
        "    to: src\n"
        "    port_map: [[fw_dummy_in, dout]]\n"
    )
    # (dout is src's own output; aliasing it as if framework-driven here is
    # purely to exercise the alias_in parsing path — real usage aliases an
    # actual input pin. Direction correctness of the alias itself is
    # covered elsewhere; this test is only about _parse_system_aliases's
    # parsing logic being reached and producing the expected mapping.)

    resolution = resolve_top_ports(
        cfg, ip_info, tmp_path / "ips", global_nets, system_yml=system_yml,
    )

    assert resolution.alias_in == {("src", "dout"): "fw_dummy_in"}
