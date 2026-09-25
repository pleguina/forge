"""Real-Vivado round-trip smoke test for --mode bd's generated Tcl.

Everything else in the bd-mode test suite (test_block_design.py,
test_topgen_cli_commands.py's bd tests, test_generation_ir_equivalence.py)
checks Python-side report/artifact correctness without ever running the
Tcl. This is the one test that actually sources the generated
block_design.tcl into a real Vivado project and runs validate_bd_design —
the check that would have caught a BD with a genuinely unconnected
mandatory pin (the class of problem --mode bd used to have before the
deterministic-port/tie-off work). Skipped where Vivado isn't on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge.contracts.config import DesignConfig
from forge.contracts.contract_loader import load_contracts_for_design, synthesize_ip_info
from forge.contracts.matcher import auto_match_ports
from forge.generation.generators.block_design import write_bd_tcl

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSTHROUGH_DESIGN = REPO_ROOT / "plugins/passthrough_demo/forge/designs/design.yml"
PASSTHROUGH_MODULES = REPO_ROOT / "plugins/passthrough_demo/forge/modules.yml"

_VIVADO = shutil.which("vivado")


@pytest.mark.skipif(_VIVADO is None, reason="Vivado not on PATH")
def test_bd_tcl_round_trips_through_real_vivado(tmp_path: Path) -> None:
    # No pytest-timeout plugin in this repo's test deps — the timeout=580
    # on subprocess.run below is the real guard against a hung Vivado batch
    # run (startup alone can take a couple minutes on a loaded host).
    cfg = DesignConfig.load_relaxed(PASSTHROUGH_DESIGN)
    contracts = load_contracts_for_design(PASSTHROUGH_MODULES, PASSTHROUGH_DESIGN.parent)
    mapped = {}
    for m in cfg.modules:
        c = contracts.get(m.name) or (m.ip_info_key and contracts.get(m.ip_info_key))
        if c:
            mapped[m.name] = c
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, _match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    bd_tcl = tmp_path / "block_design.tcl"
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=bd_tcl, bd_name="smoke_bd",
        src_root=PASSTHROUGH_DESIGN.parent, ip_root=tmp_path / "ips",
    )

    driver_tcl = tmp_path / "driver.tcl"
    driver_tcl.write_text(
        f"create_project smoke ./proj -part xcvu9p-flga2104-2L-e -force\n"
        f"source {bd_tcl}\n"
        f"open_bd_design [get_files *.bd]\n"
        f"validate_bd_design\n"
    )

    log_path = tmp_path / "vivado.log"
    result = subprocess.run(
        [_VIVADO, "-mode", "batch", "-source", str(driver_tcl), "-nolog", "-log", str(log_path), "-nojournal"],
        cwd=tmp_path, capture_output=True, text=True, timeout=580,
    )

    log_text = log_path.read_text() if log_path.exists() else ""
    combined = result.stdout + result.stderr + log_text

    assert result.returncode == 0, combined
    assert "ERROR" not in combined, combined
    # BD-specific unconnected/DRC problems surface as "CRITICAL WARNING:
    # [BD ...]" — a clean run has none. (Ordinary, unrelated Vivado
    # CRITICAL WARNINGs, e.g. about license features, are not "[BD ".)
    assert "CRITICAL WARNING: [BD" not in combined, combined
    assert "make_wrapper" in bd_tcl.read_text()
    wrapper_candidates = list(tmp_path.rglob("*_wrapper.v"))
    assert wrapper_candidates, "make_wrapper should have produced an HDL wrapper file"


@pytest.mark.skipif(_VIVADO is None, reason="Vivado not on PATH")
def test_bd_reset_sync_round_trips_through_real_vivado(tmp_path: Path) -> None:
    """A reset_domains.*.sync: reset_sync domain's cdc_reset_sync cell and
    its sync_rst_out fan-out validate cleanly in a real BD — the same
    round-trip test_bd_tcl_round_trips_through_real_vivado runs for plain
    designs, here for the reset-synchronizer path specifically."""
    from forge.contracts.config import DesignConfig as _DesignConfig

    (tmp_path / "interfaces").mkdir()
    (tmp_path / "interfaces" / "src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n  ip_info_key: src\n  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    dout: {raw_port: dout, direction: output, width: 8}\n"
    )
    (tmp_path / "interfaces" / "dst.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: dst\n  ip_info_key: dst\n  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: clk_dst, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: rst_dst, direction: input, width: 1}\n"
        "    din: {raw_port: din, direction: input, width: 8}\n"
    )
    # The interface contract's module_name must equal the RTL module's
    # literal name — synthesize_ip_info uses it directly as the BD cell's
    # -reference (see forge.contracts.contract_loader).
    (tmp_path / "src_top.v").write_text(
        "module src(input wire ap_clk, input wire ap_rst, output reg [7:0] dout);\n"
        "  always @(posedge ap_clk) if (ap_rst) dout <= 8'h0; else dout <= dout + 1'b1;\n"
        "endmodule\n"
    )
    (tmp_path / "dst_top.v").write_text(
        "module dst(input wire clk_dst, input wire rst_dst, input wire [7:0] din);\n"
        "  reg [7:0] captured;\n"
        "  always @(posedge clk_dst) if (rst_dst) captured <= 8'h0; else captured <= din;\n"
        "endmodule\n"
    )
    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n    kind: rtl\n    top: src\n    src: [src_top.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        "  - name: dst\n    kind: rtl\n    top: dst\n    src: [dst_top.v]\n"
        "    interface_contract: interfaces/dst.interface.yaml\n"
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu9p-flga2104-2L-e\nclock_period: 4.0\nblock_protocol: none\n"
        "reset_domains:\n"
        "  rst_dst:\n    derived_from: ap_rst\n    sync: reset_sync\n"
        "modules:\n"
        "  - name: src\n    top: src\n    src: [src_top.v]\n"
        "  - name: dst\n    top: dst\n    src: [dst_top.v]\n"
        "connections:\n"
        "  - from: src\n    to: dst\n    port_map: [[dout, din]]\n"
    )

    cfg = _DesignConfig.load_relaxed(design_yml)
    contracts = load_contracts_for_design(modules_yml, design_yml.parent)
    mapped = {m.name: contracts[m.name] for m in cfg.modules if m.name in contracts}
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    bd_tcl = tmp_path / "block_design.tcl"
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=bd_tcl, bd_name="smoke_bd", src_root=design_yml.parent,
        ip_root=tmp_path / "ips", contracts=contracts, match_report=match_report,
    )

    driver_tcl = tmp_path / "driver.tcl"
    driver_tcl.write_text(
        f"create_project smoke ./proj -part xcvu9p-flga2104-2L-e -force\n"
        f"source {bd_tcl}\n"
        f"open_bd_design [get_files *.bd]\n"
        f"validate_bd_design\n"
    )

    log_path = tmp_path / "vivado.log"
    result = subprocess.run(
        [_VIVADO, "-mode", "batch", "-source", str(driver_tcl), "-nolog", "-log", str(log_path), "-nojournal"],
        cwd=tmp_path, capture_output=True, text=True, timeout=580,
    )

    log_text = log_path.read_text() if log_path.exists() else ""
    combined = result.stdout + result.stderr + log_text

    assert result.returncode == 0, combined
    assert "ERROR" not in combined, combined
    assert "CRITICAL WARNING: [BD" not in combined, combined


@pytest.mark.skipif(_VIVADO is None, reason="Vivado not on PATH")
def test_bd_cdc_round_trips_through_real_vivado(tmp_path: Path) -> None:
    """All four cdc: kinds (level_sync, pulse_sync, mailbox_transfer,
    async_fifo) validate cleanly in a real BD, crossing from one ap_clk
    source into four clk_dst destinations whose reset is itself a
    reset_domains.*.sync: reset_sync domain — the combination
    design_cdc.yml's real reference design uses, and the one case where a
    CDC synchronizer's own dst_rst must fan out from another cell's pin
    rather than a plain top-level port."""
    from forge.contracts.config import DesignConfig as _DesignConfig

    ifdir = tmp_path / "interfaces"
    ifdir.mkdir()
    ifdir.joinpath("src.interface.yaml").write_text(
        "ip_interface:\n"
        "  module_name: src\n  ip_info_key: src\n  source_type: rtl\n"
        "  roles:\n"
        "    clock_primary: {raw_port: ap_clk, direction: input, width: 1}\n"
        "    reset_primary: {raw_port: ap_rst, direction: input, width: 1}\n"
        "    level_out: {raw_port: level_out, direction: output, width: 1}\n"
        "    pulse_out: {raw_port: pulse_out, direction: output, width: 1}\n"
        "    mbox_out: {raw_port: mbox_out, direction: output, width: 8}\n"
        "    fifo_out: {raw_port: fifo_out, direction: output, width: 8}\n"
    )
    for name, width in (("dst_level", 1), ("dst_pulse", 1), ("dst_mbox", 8), ("dst_fifo", 8)):
        ifdir.joinpath(f"{name}.interface.yaml").write_text(
            "ip_interface:\n"
            f"  module_name: {name}\n  ip_info_key: {name}\n  source_type: rtl\n"
            "  roles:\n"
            "    clock_primary: {raw_port: clk_dst, direction: input, width: 1}\n"
            "    reset_primary: {raw_port: rst_dst, direction: input, width: 1}\n"
            f"    din: {{raw_port: din, direction: input, width: {width}}}\n"
        )

    (tmp_path / "src.v").write_text(
        "module src(input wire ap_clk, input wire ap_rst,\n"
        "           output reg level_out, output reg pulse_out,\n"
        "           output reg [7:0] mbox_out, output reg [7:0] fifo_out);\n"
        "  always @(posedge ap_clk) begin\n"
        "    if (ap_rst) begin\n"
        "      level_out <= 1'b0; pulse_out <= 1'b0; mbox_out <= 8'h0; fifo_out <= 8'h0;\n"
        "    end else begin\n"
        "      level_out <= ~level_out; pulse_out <= ~pulse_out;\n"
        "      mbox_out <= mbox_out + 1'b1; fifo_out <= fifo_out + 1'b1;\n"
        "    end\n"
        "  end\n"
        "endmodule\n"
    )
    for name, width in (("dst_level", 1), ("dst_pulse", 1), ("dst_mbox", 8), ("dst_fifo", 8)):
        wtype = "wire" if width == 1 else f"wire [{width - 1}:0]"
        rtype = "reg" if width == 1 else f"reg [{width - 1}:0]"
        (tmp_path / f"{name}.v").write_text(
            f"module {name}(input wire clk_dst, input wire rst_dst, input {wtype} din);\n"
            f"  {rtype} captured;\n"
            f"  always @(posedge clk_dst) if (rst_dst) captured <= {width}'h0; else captured <= din;\n"
            "endmodule\n"
        )

    modules_yml = tmp_path / "modules.yml"
    modules_yml.write_text(
        "registry_version: '1'\n"
        "modules:\n"
        "  - name: src\n    kind: rtl\n    top: src\n    src: [src.v]\n"
        "    interface_contract: interfaces/src.interface.yaml\n"
        + "".join(
            f"  - name: {name}\n    kind: rtl\n    top: {name}\n    src: [{name}.v]\n"
            f"    interface_contract: interfaces/{name}.interface.yaml\n"
            for name in ("dst_level", "dst_pulse", "dst_mbox", "dst_fifo")
        )
    )
    design_yml = tmp_path / "design.yml"
    design_yml.write_text(
        "part: xcvu9p-flga2104-2L-e\nclock_period: 4.0\nblock_protocol: none\n"
        "reset_domains:\n  rst_dst:\n    derived_from: ap_rst\n    sync: reset_sync\n"
        "modules:\n"
        "  - name: src\n    top: src\n    src: [src.v]\n"
        "  - name: dst_level\n    top: dst_level\n    src: [dst_level.v]\n"
        "  - name: dst_pulse\n    top: dst_pulse\n    src: [dst_pulse.v]\n"
        "  - name: dst_mbox\n    top: dst_mbox\n    src: [dst_mbox.v]\n"
        "  - name: dst_fifo\n    top: dst_fifo\n    src: [dst_fifo.v]\n"
        "connections:\n"
        "  - from: src\n    to: dst_level\n    cdc: {kind: level_sync}\n"
        "    port_map: [[level_out, din]]\n"
        "  - from: src\n    to: dst_pulse\n    cdc: {kind: pulse_sync, min_spacing_cycles: 4}\n"
        "    port_map: [[pulse_out, din]]\n"
        "  - from: src\n    to: dst_mbox\n    cdc: {kind: mailbox_transfer}\n"
        "    port_map: [[mbox_out, din]]\n"
        "  - from: src\n    to: dst_fifo\n    cdc: {kind: async_fifo, depth: 8}\n"
        "    port_map: [[fifo_out, din]]\n"
    )

    cfg = _DesignConfig.load_relaxed(design_yml)
    contracts = load_contracts_for_design(modules_yml, design_yml.parent)
    mapped = {m.name: contracts[m.name] for m in cfg.modules if m.name in contracts}
    ip_info = synthesize_ip_info(mapped)
    conn_map, global_nets, match_report = auto_match_ports(cfg, ip_info, contracts=contracts or None)

    bd_tcl = tmp_path / "block_design.tcl"
    write_bd_tcl(
        cfg=cfg, ip_info=ip_info, conn_map=conn_map, global_nets=global_nets,
        out_path=bd_tcl, bd_name="smoke_bd", src_root=design_yml.parent,
        ip_root=tmp_path / "ips", contracts=contracts, match_report=match_report,
    )

    driver_tcl = tmp_path / "driver.tcl"
    driver_tcl.write_text(
        f"create_project smoke ./proj -part xcvu9p-flga2104-2L-e -force\n"
        f"source {bd_tcl}\n"
        f"open_bd_design [get_files *.bd]\n"
        f"validate_bd_design\n"
    )

    log_path = tmp_path / "vivado.log"
    result = subprocess.run(
        [_VIVADO, "-mode", "batch", "-source", str(driver_tcl), "-nolog", "-log", str(log_path), "-nojournal"],
        cwd=tmp_path, capture_output=True, text=True, timeout=580,
    )

    log_text = log_path.read_text() if log_path.exists() else ""
    combined = result.stdout + result.stderr + log_text

    assert result.returncode == 0, combined
    assert "ERROR" not in combined, combined
    assert "CRITICAL WARNING: [BD" not in combined, combined
