// Vendor IP wrapper. Three functional clock domains — FORGE manages one per
// module, so this must be reported as outside its envelope during adoption
// rather than half-integrated.
module axi_bridge (
    input  wire        s_axi_aclk,
    input  wire        m_axi_aclk,
    input  wire        ref_clk,
    input  wire        s_axi_aresetn,
    input  wire [31:0] s_axi_wdata,
    output wire [31:0] m_axi_wdata,
    output wire        m_axi_wvalid
);
    // Encrypted in the real vendor deliverable; a stub here.
    assign m_axi_wdata  = s_axi_wdata;
    assign m_axi_wvalid = 1'b0;
endmodule
