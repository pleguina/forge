"""Link map and build identity (forge.integration.link_map)."""

from __future__ import annotations

import json
import subprocess

from forge.core.cli.main import build_parser
from forge.integration.link_map import build_identity, link_map
from forge.integration.registers import RegisterMap

_EP = {"endpoint_id": "rx_slr2_gt0_lane1", "direction": "rx", "slr": 2, "gt_site": 0, "lane": 1,
       "lane_width": 64, "protocol": "csp_64b", "quad_type": "gty25", "cage": 16, "fiber": 2,
       "polarity": 0, "logical_id": 3, "logical_name": "dt_rx_03", "link_function": "DT_BMT",
       "endpoint_role": "detector_input_dt", "abi_port_prefix": "slr2_gt_0"}
_RESOLVED = {
    "detector_inputs": [{"name": "dt_3", "detector": "DT", "blobfish_endpoint": _EP["endpoint_id"],
                         "endpoint": _EP, "frontend": {"instance": "dt_if_3"},
                         "output": {"algo_port": "dt_3_csp_in"}}],
    "trigger_outputs": [{"name": "gmt_0", "blobfish_endpoint": "tx_slr0_gt1_lane0",
                         "endpoint": dict(_EP, endpoint_id="tx_slr0_gt1_lane0", direction="tx", slr=0),
                         "source": {"instance": "out", "port": "csp_out"}}],
}


def test_links_carry_fibre_lane_logical_input_and_algorithm_port(tmp_path):
    (tmp_path / "ir.json").write_text("{}")
    doc = link_map(_RESOLVED, build_identity({"ir": tmp_path / "ir.json"}), clock="logic_clk")
    rx, tx = doc["links"]
    assert rx["fiber"] == 2 and rx["cage"] == 16 and rx["lane"] == 1 and rx["logical_name"] == "dt_rx_03"
    assert rx["algo_port"] == "dt_3_csp_in" and rx["frontend_instance"] == "dt_if_3"
    assert tx["source"] == "out.csp_out" and tx["direction"] == "tx"
    assert doc["link_clock"] == "logic_clk" and doc["schema"] == "forge.link_map/1"


def test_the_build_id_follows_the_content_of_its_files(tmp_path):
    f = tmp_path / "payload.v"
    f.write_text("module a; endmodule\n")
    first = build_identity({"payload": f})
    assert first == build_identity({"payload": f})
    assert first["build_id32"] == "0x" + first["build_id"][:8]
    f.write_text("module b; endmodule\n")
    assert build_identity({"payload": f})["build_id"] != first["build_id"]


def test_the_build_id_includes_the_commit_and_whether_it_is_dirty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    run("init", "-q")
    (repo / "x").write_text("1")
    run("add", "x")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    f = tmp_path / "ir.json"
    f.write_text("{}")
    clean = build_identity({"ir": f}, repo)
    assert clean["source"]["dirty"] is False and len(clean["source"]["commit"]) == 40
    (repo / "x").write_text("2")
    dirty = build_identity({"ir": f}, repo)
    assert dirty["source"]["dirty"] is True and dirty["build_id"] != clean["build_id"]


def test_the_register_layout_is_included(tmp_path):
    (tmp_path / "ir.json").write_text("{}")
    regs = RegisterMap.from_dict({"registers": [{"name": "c", "kind": "config", "width": 64, "port": "p",
                                                 "fields": [{"name": "en", "bits": 0}]}]})
    doc = link_map(_RESOLVED, build_identity({"ir": tmp_path / "ir.json"}), registers=regs)
    assert doc["registers"]["layout"] == [{"name": "c", "kind": "config", "first_word": 0, "words": 2, "width": 64,
                                           "access": "rw", "reset": 0, "fields": [{"name": "en", "bits": [0, 0]}]}]


def test_the_command_writes_the_map(tmp_path, capsys):
    resolved = tmp_path / "resolved.json"
    resolved.write_text(json.dumps(_RESOLVED))
    (tmp_path / "payload.v").write_text("module p; endmodule\n")
    out = tmp_path / "link_map.json"
    args = build_parser().parse_args(["dev", "framework", "link-map", "--detector-io", str(resolved),
                                      "--file", f"payload={tmp_path / 'payload.v'}", "--out", str(out)])
    args.func(args)
    doc = json.loads(out.read_text())
    assert set(doc["build"]["files"]) == {"payload", "detector_io"} and len(doc["links"]) == 2
    assert "2 links, build 0x" in capsys.readouterr().out
