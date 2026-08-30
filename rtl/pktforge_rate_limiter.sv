// Inter-frame-gap based rate limiter.
//
// Blocks the outgoing trigger for a configurable number of cycles after
// each frame-end pulse. The delay is `ifg_bytes_i / LANES` cycles (integer
// truncation); 0 means no gating and the trigger passes through unchanged.
//
// LINE_PERCENT mode (RATE_MODE = 0 in the regmap) requires per-frame size
// arithmetic and is not implemented here. This module is used whenever
// RATE_IFG_BYTES is the desired knob; consumers are free to force
// RATE_IFG_BYTES = 0 to disable pacing entirely.

module pktforge_rate_limiter #(
    parameter int LANES = 4
) (
    input  logic         clk,
    input  logic         rst,

    input  logic [31:0]  ifg_bytes_i,   // RATE_IFG_BYTES from the regfile
    input  logic         frame_end_i,   // 1-cycle pulse at each on-wire tlast beat

    input  logic         trigger_in_i,
    output logic         trigger_out_o
);

    // Countdown of cycles the trigger must stay masked. Loaded on each
    // frame_end_i pulse; decremented otherwise. Zero => trigger passes.
    logic [31:0] countdown_q;

    always_ff @(posedge clk) begin
        if (rst) begin
            countdown_q <= 32'h0;
        end else if (frame_end_i) begin
            // Integer truncation: 0..LANES-1 bytes of IFG round down to 0 gap.
            countdown_q <= ifg_bytes_i / LANES;
        end else if (countdown_q != 32'h0) begin
            countdown_q <= countdown_q - 32'd1;
        end
    end

    assign trigger_out_o = trigger_in_i && (countdown_q == 32'h0);

endmodule
