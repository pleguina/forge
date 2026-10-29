"""A build is reused only when its compile list, sources, headers, commands and simulator are the same."""
from __future__ import annotations

from pathlib import Path

from forge.verification import compile_key
from forge.verification.compile_key import build_key, reusable


def _project(tmp_path: Path):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "defs.vh").write_text("`define W 8\n")
    (inc / "stimulus.mem").write_text("00\n01\n")
    rtl = tmp_path / "dut.v"
    rtl.write_text("module dut; endmodule\n")
    lst = tmp_path / "sources.f"
    lst.write_text(f'-I"{inc}"\n"{rtl}"\n')
    return lst, rtl, inc


def _key(lst, flags=()):
    return build_key(lst, [["verilator", "--binary", *flags, "-f", str(lst)]], ["no-such-simulator"])


def test_the_same_inputs_give_the_same_key(tmp_path):
    lst, _, _ = _project(tmp_path)
    assert _key(lst) == _key(lst)


def test_a_source_a_header_or_a_flag_changes_the_key(tmp_path):
    lst, rtl, inc = _project(tmp_path)
    base = _key(lst)
    rtl.write_text("module dut; wire x; endmodule\n")
    assert _key(lst) != base
    base = _key(lst)
    (inc / "defs.vh").write_text("`define W 16\n")
    assert _key(lst) != base
    base = _key(lst)
    assert _key(lst, ["-DPROBE_LOG=1"]) != base


def test_run_time_data_does_not_change_the_key(tmp_path):
    lst, _, inc = _project(tmp_path)
    base = _key(lst)
    (inc / "stimulus.mem").write_text("ff\n")
    assert _key(lst) == base


def test_another_simulator_version_changes_the_key(tmp_path, monkeypatch):
    lst, _, _ = _project(tmp_path)
    monkeypatch.setattr(compile_key, "tool_version", lambda exe: "v1")
    base = _key(lst)
    monkeypatch.setattr(compile_key, "tool_version", lambda exe: "v2")
    assert _key(lst) != base


def test_a_sentinel_from_other_inputs_or_an_old_one_is_not_reusable(tmp_path):
    sentinel = tmp_path / ".build_complete"
    assert not reusable(sentinel, "k")
    sentinel.write_text("1")            # written before builds were keyed
    assert not reusable(sentinel, "k")
    sentinel.write_text("k\n")
    assert reusable(sentinel, "k") and not reusable(sentinel, "other")
