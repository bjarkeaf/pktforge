// Trivial AXI-Stream passthrough used solely to validate the cocotb test
// harness end-to-end before any real pktforge RTL exists. Delete when the
// first real module (regfile_axil, axis_master, etc.) lands.

module pktforge_passthrough #(
    parameter int DATA_W = 32
) (
    input  logic                  clk,
    input  logic                  rst,

    input  logic [DATA_W-1:0]     s_axis_tdata,
    input  logic [DATA_W/8-1:0]   s_axis_tkeep,
    input  logic                  s_axis_tvalid,
    output logic                  s_axis_tready,
    input  logic                  s_axis_tlast,

    output logic [DATA_W-1:0]     m_axis_tdata,
    output logic [DATA_W/8-1:0]   m_axis_tkeep,
    output logic                  m_axis_tvalid,
    input  logic                  m_axis_tready,
    output logic                  m_axis_tlast
);

    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tkeep  = s_axis_tkeep;
    assign m_axis_tvalid = s_axis_tvalid;
    assign m_axis_tlast  = s_axis_tlast;
    assign s_axis_tready = m_axis_tready;

    // Reset is unused (combinational passthrough) but declared so the
    // interface matches downstream modules and Verilator does not flag it.
    // verilator lint_off UNUSEDSIGNAL
    wire _unused_ok = &{1'b0, clk, rst};
    // verilator lint_on UNUSEDSIGNAL

endmodule
