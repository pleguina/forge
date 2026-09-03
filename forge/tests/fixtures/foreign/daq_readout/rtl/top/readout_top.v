// The existing structural top. FORGE generates a replacement for this, so
// it must not appear as a managed module — but its instantiations are what
// prove how many of each module the design contains.
module readout_top (
    input  wire        sys_clk,
    input  wire        sys_rst,
    input  wire        link_clk,
    input  wire        link_rst,
    input  wire [23:0] ch0_raw, ch1_raw, ch2_raw, ch3_raw,
    input  wire        ch0_raw_valid, ch1_raw_valid, ch2_raw_valid, ch3_raw_valid,
    output wire [7:0]  link_data,
    output wire        link_data_valid,
    output wire [31:0] monitor_count
);
    wire [31:0] hit0, hit1, hit2, hit3;
    wire        hv0, hv1, hv2, hv3;
    wire [31:0] event_word;
    wire        event_valid;
    wire [3:0]  channel_mask;
    wire [15:0] trigger_word;
    wire        trigger_valid;

    channel_decoder u_dec0 (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .raw_word(ch0_raw), .raw_valid(ch0_raw_valid), .hit(hit0), .hit_valid(hv0));
    channel_decoder u_dec1 (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .raw_word(ch1_raw), .raw_valid(ch1_raw_valid), .hit(hit1), .hit_valid(hv1));
    channel_decoder u_dec2 (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .raw_word(ch2_raw), .raw_valid(ch2_raw_valid), .hit(hit2), .hit_valid(hv2));
    channel_decoder u_dec3 (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .raw_word(ch3_raw), .raw_valid(ch3_raw_valid), .hit(hit3), .hit_valid(hv3));

    hit_aggregator u_agg (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .ch0_hit(hit0), .ch0_hit_valid(hv0), .ch1_hit(hit1), .ch1_hit_valid(hv1),
        .ch2_hit(hit2), .ch2_hit_valid(hv2), .ch3_hit(hit3), .ch3_hit_valid(hv3),
        .event_word(event_word), .event_valid(event_valid), .channel_mask(channel_mask));

    trigger_path u_trig (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .event_word(event_word), .event_valid(event_valid),
        .trigger_word(trigger_word), .trigger_valid(trigger_valid));

    monitor_path u_mon (.sys_clk(sys_clk), .sys_rst(sys_rst),
        .event_word(event_word), .event_valid(event_valid),
        .monitor_count(monitor_count));

    readout_link u_link (.link_clk(link_clk), .link_rst(link_rst),
        .trigger_word(trigger_word), .trigger_valid(trigger_valid),
        .link_data(link_data), .link_data_valid(link_data_valid));
endmodule
