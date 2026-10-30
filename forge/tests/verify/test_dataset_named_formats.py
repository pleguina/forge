"""Datasets in any registered format, named by the flow or recognised by content, not only XML."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forge.integration.emp import LinkWord, packet, write_pattern_file
from forge.verification.dataset_format import EmpPatternDatasetLoader, get_format_loader, known_formats
from forge.verification.run import XmlRunSelection, resolve_event_ids


def _emp(tmp_path: Path, name: str = "patterns.txt") -> Path:
    path = tmp_path / name
    frames = [{0: packet(0xA), 1: LinkWord(0xB, valid=True, start=True, strobe=False)},
              {1: LinkWord(0xC, valid=True, last=True)}, {}, {0: packet(0xD)}]
    write_pattern_file(path, frames, [0, 1], board="omtf_p1")
    return path


def test_formats_are_named_and_txt_is_known_by_content(tmp_path):
    assert {"xml", "json", "empv2"} <= set(known_formats())
    emp = _emp(tmp_path)
    assert isinstance(get_format_loader(emp), EmpPatternDatasetLoader)
    plain = tmp_path / "notes.txt"
    plain.write_text("hello\n")
    with pytest.raises(ValueError, match="name the format"):
        get_format_loader(plain)
    # CMSSW's X2O codec is unimplemented; there is no format to read.
    with pytest.raises(ValueError, match="unknown dataset format 'x2o'"):
        get_format_loader(emp, "x2o")


def test_an_emp_event_is_the_packets_that_start_together(tmp_path):
    data = EmpPatternDatasetLoader().load(_emp(tmp_path))
    assert data.metadata.event_ids == ["0", "1"]
    first, second = data.events
    assert first["board"] == "omtf_p1"
    assert [w["data"] for w in first["links"]["1"]] == ["0x000000000000000b", "0x000000000000000c"]
    assert first["links"]["1"][0]["strobe"] is False and first["links"]["1"][1]["strobe"] is True
    assert list(second["links"]) == ["0"] and second["links"]["0"][0]["data"] == "0x000000000000000d"


def test_a_link_without_a_packet_is_not_paired_with_its_next_one(tmp_path):
    path = tmp_path / "gap.txt"
    frames = [{1: packet(0x1)}, {}, {0: packet(0xA), 1: packet(0x2)}]
    write_pattern_file(path, frames, [0, 1])

    first, second = EmpPatternDatasetLoader().load(path).events

    assert list(first["links"]) == ["1"] and first["frame"] == 0
    assert second["frame"] == 2
    assert [w["data"] for w in second["links"]["0"]] == ["0x000000000000000a"]
    assert [w["data"] for w in second["links"]["1"]] == ["0x0000000000000002"]


def test_events_of_a_named_format_are_listed_for_a_run(tmp_path):
    emp = _emp(tmp_path, "patterns.dat")
    selection = XmlRunSelection(event_id=None, event_list=None, all_events=True, all_dataset_parts=False,
                                dataset_xml=emp, dataset_parts_glob=None, probe_log=False, dataset_format="empv2")
    assert resolve_event_ids(selection) == ["0", "1"]


def _flow(tmp_path: Path, dataset: str) -> Path:
    path = tmp_path / "verify.flow.yml"
    path.write_text(textwrap.dedent(f"""\
        flow: {{name: f, kind: full_chip_rtl, backend: xsim, top_module: top, tb_module: tb_top}}
        dut: {{rtl: top.v}}
        dataset: {dataset}
        simulation: {{clk_period_ns: 4.0, reset_cycles: 4, idle_cycles_after_reset: 0, post_stimulus_drain_cycles: 4}}
        """))
    return path


@pytest.mark.parametrize("dataset, fmt", [
    ("{path: patterns.txt, format: empv2}", "empv2"),
    ("{path: patterns.txt}", None),
    ("{xml: events.xml}", "xml"),
])
def test_a_flow_names_its_dataset_in_any_format(tmp_path, dataset, fmt):
    from forge.verification.flow_loader import load_generic_flow

    cfg = load_generic_flow(_flow(tmp_path, dataset), tmp_path)
    assert cfg.dataset_format == fmt
    assert cfg.dataset_xml.name in ("patterns.txt", "events.xml")


@pytest.mark.parametrize("dataset, message", [
    ("{path: patterns.txt, format: x2o}", "dataset.format 'x2o'"),
    ("{default_event_id: 1}", "dataset.path"),
])
def test_a_flow_with_an_unusable_dataset_is_refused(tmp_path, dataset, message):
    from forge.verification.flow_loader import FlowConfigError, load_generic_flow

    with pytest.raises(FlowConfigError, match=message):
        load_generic_flow(_flow(tmp_path, dataset), tmp_path)
