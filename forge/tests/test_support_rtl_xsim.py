"""Behavioural xsim tests for the framework support RTL (forge/rtl/support/).

Every plugin's generated top level instantiates these modules, so their
cycle-level contract is tested here, once, against the files the framework
actually ships — not against a plugin's copy:

- RegisterStage / signal_delay / slr_crossing_delay: output equals input
  exactly STAGES/DEPTH cycles later (the latency the static latency checker
  assumes for register_stages/delay_cycles/boundary connections).
- cdc_sync2ff: 2 destination-clock cycles; cdc_reset_sync: asserts
  asynchronously, deasserts 2 destination-clock cycles after release.
- cdc_pulse_sync: every source pulse arrives as exactly one destination
  pulse, for a faster and a slower destination clock.
- cdc_mailbox: the destination only ever sees coherent source words, and
  converges to the held value.
- cdc_async_fifo: every written word is read back once, in order, with
  backpressure (writer faster than reader) and without.

Skipped where xvlog/xelab/xsim aren't on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge.generation.support_rtl import SUPPORT_RTL_BY_TRANSFORMATION, SUPPORT_RTL_DIR

_XSIM_TOOLS = all(shutil.which(t) for t in ("xvlog", "xelab", "xsim"))

_TB_COMMON = """
`timescale 1ns / 1ps
"""

TESTBENCHES = {
    "delay_lines": _TB_COMMON + r"""
module tb;
    reg clk = 0; always #5 clk = ~clk;
    reg rst = 1;
    reg [7:0] din = 0;
    wire [7:0] rs3, sd1, sd4, sc1, sc2, sc3;

    RegisterStage      #(.DATAWIDTH(8), .STAGES(3)) u_rs3 (.clk(clk), .data_in(din), .data_out(rs3));
    signal_delay       #(.WIDTH(8), .DEPTH(1)) u_sd1 (.clk(clk), .rst(rst), .din(din), .dout(sd1));
    signal_delay       #(.WIDTH(8), .DEPTH(4)) u_sd4 (.clk(clk), .rst(rst), .din(din), .dout(sd4));
    slr_crossing_delay #(.WIDTH(8), .DEPTH(1)) u_sc1 (.clk(clk), .rst(rst), .din(din), .dout(sc1));
    slr_crossing_delay #(.WIDTH(8), .DEPTH(2)) u_sc2 (.clk(clk), .rst(rst), .din(din), .dout(sc2));
    slr_crossing_delay #(.WIDTH(8), .DEPTH(3)) u_sc3 (.clk(clk), .rst(rst), .din(din), .dout(sc3));

    reg [7:0] hist [0:63];
    integer cyc, errors = 0;

    task chk(input [7:0] got, input integer n, input [8*8:1] name);
        if (cyc >= n && got !== hist[cyc - n]) begin
            $display("FAIL %0s cyc=%0d got=%0d exp=%0d", name, cyc, got, hist[cyc - n]);
            errors = errors + 1;
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        #1 rst = 0;
        for (cyc = 0; cyc < 40; cyc = cyc + 1) begin
            din = (cyc * 37 + 11) & 8'hFF;
            @(negedge clk);
            hist[cyc] = din;
            chk(rs3, 3, "rs3"); chk(sd1, 1, "sd1"); chk(sd4, 4, "sd4");
            chk(sc1, 1, "sc1"); chk(sc2, 2, "sc2"); chk(sc3, 3, "sc3");
            @(posedge clk); #1;
        end
        if (errors == 0) $display("PASS");
        $finish;
    end
endmodule
""",
    "sync2ff_reset_sync": _TB_COMMON + r"""
module tb;
    reg clk = 0; always #5 clk = ~clk;
    reg rst = 1;
    reg [3:0] din = 0;
    wire [3:0] dout;
    reg async_rst = 0;
    wire sync_rst;
    integer errors = 0;

    cdc_sync2ff #(.WIDTH(4)) u_sync (.dst_clk(clk), .dst_rst(rst), .din(din), .dout(dout));
    cdc_reset_sync u_rsync (.dst_clk(clk), .async_rst_in(async_rst), .sync_rst_out(sync_rst));

    initial begin
        repeat (3) @(posedge clk);
        #1 rst = 0;
        repeat (3) @(posedge clk);
        #2.3 din = 4'hA;                       // asynchronous change
        @(posedge clk); #1;
        if (dout !== 4'h0) begin $display("FAIL sync2ff early: %h", dout); errors = errors + 1; end
        @(posedge clk); #1;
        if (dout !== 4'hA) begin $display("FAIL sync2ff latency: %h", dout); errors = errors + 1; end

        #1.7 async_rst = 1;                     // asserts without a clock edge
        #0.1;
        if (sync_rst !== 1'b1) begin $display("FAIL reset_sync assert"); errors = errors + 1; end
        repeat (2) @(posedge clk);
        #2.9 async_rst = 0;
        @(posedge clk); #1;
        if (sync_rst !== 1'b1) begin $display("FAIL reset_sync released early"); errors = errors + 1; end
        @(posedge clk); #1;
        if (sync_rst !== 1'b0) begin $display("FAIL reset_sync not released"); errors = errors + 1; end
        if (errors == 0) $display("PASS");
        $finish;
    end
endmodule
""",
    "pulse_sync": _TB_COMMON + r"""
module tb;
    reg src_clk = 0;  always #5    src_clk  = ~src_clk;    // 100 MHz
    reg fast_clk = 0; always #3.5  fast_clk = ~fast_clk;   // faster destination
    reg slow_clk = 0; always #11.5 slow_clk = ~slow_clk;   // slower destination
    reg rst = 1;
    reg pulse = 0;
    wire out_fast, out_slow;
    integer n_fast = 0, n_slow = 0, k, errors = 0;
    localparam N = 20;

    cdc_pulse_sync u_fast (.src_clk(src_clk), .src_rst(rst), .pulse_in(pulse),
                           .dst_clk(fast_clk), .dst_rst(rst), .pulse_out(out_fast));
    cdc_pulse_sync u_slow (.src_clk(src_clk), .src_rst(rst), .pulse_in(pulse),
                           .dst_clk(slow_clk), .dst_rst(rst), .pulse_out(out_slow));

    always @(posedge fast_clk) if (out_fast) n_fast = n_fast + 1;
    always @(posedge slow_clk) if (out_slow) n_slow = n_slow + 1;

    initial begin
        #100.3 rst = 0;
        for (k = 0; k < N; k = k + 1) begin
            @(posedge src_clk); #1 pulse = 1;
            @(posedge src_clk); #1 pulse = 0;
            repeat (15) @(posedge src_clk);    // honours min_spacing_cycles
        end
        #500;
        if (n_fast != N) begin $display("FAIL fast dst saw %0d pulses", n_fast); errors = errors + 1; end
        if (n_slow != N) begin $display("FAIL slow dst saw %0d pulses", n_slow); errors = errors + 1; end
        if (errors == 0) $display("PASS");
        $finish;
    end
endmodule
""",
    "mailbox": _TB_COMMON + r"""
module tb;
    reg src_clk = 0; always #5   src_clk = ~src_clk;
    reg dst_clk = 0; always #6.5 dst_clk = ~dst_clk;
    reg rst = 1;
    reg [7:0] din = 0;
    wire [7:0] dout;
    wire dout_valid;
    reg [7:0] vals [0:4];
    integer idx = -1, j, ok, errors = 0;

    cdc_mailbox #(.WIDTH(8)) u_mb (.src_clk(src_clk), .src_rst(rst), .din(din),
                                  .dst_clk(dst_clk), .dst_rst(rst), .dout(dout), .dout_valid(dout_valid));

    // Coherency: every word the destination ever shows was really driven.
    always @(posedge dst_clk) if (!rst) begin
        ok = (dout == 8'h00);
        for (j = 0; j <= idx; j = j + 1) if (dout == vals[j]) ok = 1;
        if (!ok) begin $display("FAIL incoherent dout %h", dout); errors = errors + 1; end
    end

    initial begin
        vals[0] = 8'hA5; vals[1] = 8'h3C; vals[2] = 8'hF0; vals[3] = 8'h0F; vals[4] = 8'h99;
        #100.3 rst = 0;
        for (idx = 0; idx < 5; idx = idx + 1) begin
            @(posedge src_clk); #1 din = vals[idx];
            repeat (60) @(posedge src_clk);
            if (dout !== vals[idx]) begin
                $display("FAIL dout %h != %h", dout, vals[idx]); errors = errors + 1;
            end
        end
        idx = 4;
        if (errors == 0) $display("PASS");
        $finish;
    end
endmodule
""",
    "async_fifo": _TB_COMMON + r"""
// One writer/reader pair per clock ratio. The writer only advances when a
// write really happened (wr_en && !full), so no word may be lost or
// duplicated even when the FIFO applies backpressure.
module fifo_check #(parameter real WR_HALF = 5.0, parameter real RD_HALF = 5.0,
                    parameter integer N = 60) (output reg done, output integer errors);
    reg wr_clk = 0; always #(WR_HALF) wr_clk = ~wr_clk;
    reg rd_clk = 0; always #(RD_HALF) rd_clk = ~rd_clk;
    reg rst = 1;
    integer next_wr = 0, next_rd = 0;
    reg rd_valid_d = 0;
    wire [7:0] dout;
    wire full, empty, ovf, unf;
    wire [3:0] occ, hw;
    wire wr_en = !rst && (next_wr < N);

    cdc_async_fifo #(.WIDTH(8), .DEPTH(8)) u_fifo (
        .wr_clk(wr_clk), .wr_rst(rst), .din(next_wr[7:0]), .wr_en(wr_en),
        .full(full), .overflow_attempt(ovf), .occupancy(occ), .high_water(hw),
        .rd_clk(rd_clk), .rd_rst(rst), .dout(dout), .empty(empty), .underflow_attempt(unf));

    always @(posedge wr_clk) if (wr_en && !full) next_wr <= next_wr + 1;

    always @(posedge rd_clk) begin
        if (rd_valid_d) begin
            if (dout !== next_rd[7:0]) begin
                $display("FAIL fifo(%0.1f/%0.1f) word %0d got %0d", WR_HALF, RD_HALF, next_rd, dout);
                errors = errors + 1;
            end
            next_rd = next_rd + 1;
        end
        rd_valid_d <= !rst && !empty;
    end

    initial begin
        done = 0; errors = 0;
        #100.3 rst = 0;
        #(N * 20 * (WR_HALF + RD_HALF) + 500);
        if (next_rd != N) begin
            $display("FAIL fifo(%0.1f/%0.1f) read %0d of %0d", WR_HALF, RD_HALF, next_rd, N);
            errors = errors + 1;
        end
        if (hw > 8) begin $display("FAIL high_water %0d > DEPTH", hw); errors = errors + 1; end
        done = 1;
    end
endmodule

module tb;
    wire d0, d1;
    integer e0, e1;
    fifo_check #(.WR_HALF(2.0), .RD_HALF(4.5)) u_backpressure (.done(d0), .errors(e0));
    fifo_check #(.WR_HALF(5.0), .RD_HALF(2.0)) u_fast_reader  (.done(d1), .errors(e1));
    initial begin
        wait (d0 && d1);
        if (e0 == 0 && e1 == 0) $display("PASS");
        $finish;
    end
endmodule
""",
}


def test_every_transformation_kind_has_packaged_rtl() -> None:
    for kind, filename in SUPPORT_RTL_BY_TRANSFORMATION.items():
        assert (SUPPORT_RTL_DIR / filename).is_file(), f"{kind}: {filename} not packaged"


@pytest.mark.skipif(not _XSIM_TOOLS, reason="xvlog/xelab/xsim not on PATH")
@pytest.mark.parametrize("name", sorted(TESTBENCHES))
def test_support_rtl_behaviour(name: str, tmp_path: Path) -> None:
    tb = tmp_path / f"tb_{name}.sv"
    tb.write_text(TESTBENCHES[name])
    rtl = sorted(str(p) for p in SUPPORT_RTL_DIR.glob("*.v"))

    def run(cmd):
        proc = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, f"{cmd[0]} failed:\n{proc.stdout}\n{proc.stderr}"
        return proc.stdout

    run(["xvlog", *rtl])
    run(["xvlog", "--sv", str(tb)])
    run(["xelab", "tb", "-s", "snap", "-timescale", "1ns/1ps"])
    out = run(["xsim", "snap", "-R"])
    assert "FAIL" not in out, out
    assert "PASS" in out, out
