"""Unit tests for forge.verify.readmemh_stimulus and the readmemh-mode SV
emission helpers in stimulus_helpers.py.

Real end-to-end proof (single real compile across 2 real events, real
distinct pass results, real xvlog/xelab-run-once observation) lives in
tests/test_test_cli_group.py — this file covers the framework-generic
mechanics in isolation: memory-file writing, plusarg-read emission, and
the runtime-check SV template shape.
"""

from __future__ import annotations

from pathlib import Path

from forge.verify.dataset_adapter import CanonicalDataset
from forge.verify.dataset_format import (
    DATASET_SCHEMA,
    DatasetMetadata,
    EnvironmentMetadata,
    SemanticMetadata,
)
from forge.verify.readmemh_stimulus import write_event_memory_file
from forge.verify.stimulus_helpers import (
    emit_event_index_read,
    emit_runtime_output_check,
    write_readmemh_stimulus_svh,
)


def _canonical(events) -> CanonicalDataset:
    return CanonicalDataset(
        events=events,
        metadata=DatasetMetadata(
            schema=DATASET_SCHEMA,
            event_ids=[str(i) for i in range(len(events))],
            semantic=SemanticMetadata(source_content_hash="h"),
            environment=EnvironmentMetadata(source_path="d.xml", generated_at="now"),
        ),
    )


# ── write_event_memory_file ─────────────────────────────────────────────

def test_write_event_memory_file_one_hex_line_per_event(tmp_path: Path):
    canonical = _canonical([{"v": 1}, {"v": 2}, {"v": 3}])
    out_path = tmp_path / "events.mem"
    index_to_id = write_event_memory_file(canonical, lambda ev: f"{ev['v']:02x}", out_path)

    assert out_path.read_text().splitlines() == ["01", "02", "03"]
    assert index_to_id == {0: "0", 1: "1", 2: "2"}


def test_write_event_memory_file_index_to_id_uses_real_event_ids():
    """event_index is purely positional; event_id is the real (possibly
    non-numeric) identifier at that position — proven with non-contiguous,
    non-numeric real ids."""
    canonical = CanonicalDataset(
        events=[{"v": 1}, {"v": 2}],
        metadata=DatasetMetadata(
            schema=DATASET_SCHEMA,
            event_ids=["run-355100-event-1842", "run-355100-event-1900"],
            semantic=SemanticMetadata(source_content_hash="h"),
            environment=EnvironmentMetadata(source_path="d.xml", generated_at="now"),
        ),
    )

    def _tmp_writer(tmp_path: Path):
        return write_event_memory_file(canonical, lambda ev: f"{ev['v']:x}", tmp_path / "e.mem")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        index_to_id = _tmp_writer(Path(td))
    assert index_to_id == {0: "run-355100-event-1842", 1: "run-355100-event-1900"}


def test_write_event_memory_file_creates_parent_dirs(tmp_path: Path):
    canonical = _canonical([{"v": 1}])
    out_path = tmp_path / "nested" / "dir" / "e.mem"
    write_event_memory_file(canonical, lambda ev: "1", out_path)
    assert out_path.exists()


def test_write_event_memory_file_empty_dataset_writes_empty_file(tmp_path: Path):
    canonical = _canonical([])
    out_path = tmp_path / "e.mem"
    index_to_id = write_event_memory_file(canonical, lambda ev: "0", out_path)
    assert index_to_id == {}
    assert out_path.read_text() == "\n"


# ── emit_event_index_read ───────────────────────────────────────────────

def test_emit_event_index_read_uses_plusarg_not_event_id():
    line = emit_event_index_read("idx")
    assert '"EVENT_INDEX=%d"' in line
    assert "EVENT_ID" not in line
    assert "idx = 0" in line


def test_emit_event_index_read_default_var_name():
    line = emit_event_index_read()
    assert "event_index" in line


# ── emit_runtime_output_check ───────────────────────────────────────────

def test_emit_runtime_output_check_uses_runtime_expected_not_literal():
    lines = emit_runtime_output_check("pt_out", "exp_out", width=8, label="chk")
    joined = "\n".join(lines)
    assert "check_id=chk|" in joined  # no event_id prefix — not known at generation time
    assert "expected=0x%h" in joined
    assert "observed=0x%h" in joined
    assert "exp_out, pt_out" in joined  # both runtime args to $display
    assert "if (pt_out !== exp_out) begin" in joined
    assert '$fatal(1, "Check failed: chk");' in joined


def test_emit_runtime_output_check_label_falls_back_to_signal():
    lines = emit_runtime_output_check("pt_out", "exp_out", width=1)
    assert "check_id=pt_out|" in lines[0]


# ── write_readmemh_stimulus_svh ─────────────────────────────────────────

def test_write_readmemh_stimulus_svh_preamble_before_task(tmp_path: Path):
    out_path = tmp_path / "stimulus_current.svh"
    write_readmemh_stimulus_svh(
        preamble_lines=["reg [7:0] mem [0:1];", 'initial $readmemh("x.mem", mem);'],
        task_body_lines=["    y <= mem[0];"],
        out_path=out_path,
        header_comment="test flow",
    )
    text = out_path.read_text()
    preamble_pos = text.index("initial $readmemh")
    # rindex, not index: the header's own prose also mentions the
    # signature once (inside `//`) — the real declaration is the last
    # occurrence.
    task_pos = text.rindex("task automatic run_stimulus();")
    assert preamble_pos < task_pos
    assert "endtask : run_stimulus" in text


def test_write_readmemh_stimulus_svh_satisfies_real_stimulus_contract(tmp_path: Path):
    """The exact real contract check used by the backends — a module-scope
    preamble ahead of the task must not trip any of the 9 regex checks."""
    from forge.verify.stimulus_contract import validate_stimulus

    out_path = tmp_path / "stimulus_current.svh"
    write_readmemh_stimulus_svh(
        preamble_lines=["reg [7:0] mem [0:1];", 'initial $readmemh("x.mem", mem);'],
        task_body_lines=[
            "    integer idx;",
            '    if (!$value$plusargs("EVENT_INDEX=%d", idx)) idx = 0;',
            "    data_out <= mem[idx];",
            "    @(posedge ap_clk);",
            '    if (data_out !== mem[idx]) $fatal(1, "Check failed: x");',
        ],
        out_path=out_path,
    )
    result = validate_stimulus(out_path)
    assert result.ok, result.errors
    assert result.errors == []


def test_write_readmemh_stimulus_svh_exactly_one_real_task_signature(tmp_path: Path):
    """Exactly one real (non-comment) task declaration — the header's own
    prose mentions the signature too (inside `//`), so this counts only
    non-comment lines, matching stimulus_contract.py's own check #7."""
    out_path = tmp_path / "stimulus_current.svh"
    write_readmemh_stimulus_svh(
        preamble_lines=["reg [7:0] mem [0:1];"],
        task_body_lines=["    y <= mem[0];"],
        out_path=out_path,
    )
    non_comment_lines = [
        line for line in out_path.read_text().splitlines()
        if not line.lstrip().startswith("//")
    ]
    real_task_lines = [l for l in non_comment_lines if "task automatic run_stimulus();" in l]
    assert len(real_task_lines) == 1
