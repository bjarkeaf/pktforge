// Trigger rate limiter with two modes.
//
// RATE_MODE = 0  (IFG bytes):
//   After each on-wire tlast handshake, mask the trigger for
//   `RATE_IFG_BYTES / LANES` cycles. Frame size is irrelevant.
//
// RATE_MODE = 1  (LINE_PERCENT):
//   Snoops the outgoing AXI-Stream to count valid bytes per frame. At
//   frame end, computes the idle-cycle count needed to hit
//   `RATE_LINE_PERCENT` percent of line rate:
//       idle_cycles = frame_bytes / LANES * (100 - X) / X
//   and masks the trigger for that many cycles. X = 100 disables gating
//   for that frame; X = 0 (or > 100) is clamped to 100.
//
// Anything other than mode 0 or 1 disables gating (trigger passes through).

module pktforge_rate_limiter #(
    parameter int LANES = 4
) (
    input  logic         clk,
    input  logic         rst,

    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [31:0]  rate_mode_i,     // only [1:0] consumed
    input  logic [31:0]  line_percent_i,  // only [7:0] consumed
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic [31:0]  ifg_bytes_i,

    // Snoop the on-wire AXI-Stream master to count bytes per frame.
    input  logic                axis_tvalid_i,
    input  logic                axis_tready_i,
    input  logic [LANES-1:0]    axis_tkeep_i,
    input  logic                axis_tlast_i,

    input  logic         trigger_in_i,
    output logic         trigger_out_o
);

    // ------------------------------------------------------------------
    // Beat / byte accounting
    // ------------------------------------------------------------------
    logic       beat_c;
    logic       frame_end_c;
    logic [3:0] beat_bytes_c;      // 0..LANES

    assign beat_c       = axis_tvalid_i && axis_tready_i;
    assign frame_end_c  = beat_c && axis_tlast_i;
    assign beat_bytes_c = {3'b0, axis_tkeep_i[0]} + {3'b0, axis_tkeep_i[1]}
                        + {3'b0, axis_tkeep_i[2]} + {3'b0, axis_tkeep_i[3]};

    // Byte counter (excludes the beat currently in flight; frame_bytes_c
    // adds the current beat's bytes on the frame_end_c cycle).
    logic [15:0] byte_count_q;
    logic [31:0] frame_bytes_c;
    assign frame_bytes_c = {16'h0, byte_count_q} + {28'h0, beat_bytes_c};

    // ------------------------------------------------------------------
    // Idle-cycle computation (combinational; sampled on frame_end_c)
    // ------------------------------------------------------------------
    logic [7:0]  pct;
    logic [7:0]  pct_clamped;
    logic [31:0] idle_cycles_c;
    logic [39:0] numerator;
    logic [39:0] denominator;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [39:0] lp_quotient;    // upper bits never used at achievable percentages
    /* verilator lint_on UNUSEDSIGNAL */
    logic [31:0] lp_idle_cycles;

    assign pct         = line_percent_i[7:0];
    assign pct_clamped = (pct == 8'd0 || pct > 8'd100) ? 8'd100 : pct;
    assign numerator   = {8'h0, frame_bytes_c} * {32'h0, (8'd100 - pct_clamped)};
    assign denominator = {32'h0, pct_clamped} * 40'(LANES);
    assign lp_quotient = numerator / denominator;
    assign lp_idle_cycles = (pct_clamped == 8'd100) ? 32'h0 : lp_quotient[31:0];

    always_comb begin
        unique case (rate_mode_i[1:0])
            2'd0:    idle_cycles_c = ifg_bytes_i / 32'(LANES);
            2'd1:    idle_cycles_c = lp_idle_cycles;
            default: idle_cycles_c = 32'h0;
        endcase
    end

    // ------------------------------------------------------------------
    // Countdown
    // ------------------------------------------------------------------
    logic [31:0] countdown_q;

    always_ff @(posedge clk) begin
        if (rst) begin
            byte_count_q <= 16'h0;
            countdown_q  <= 32'h0;
        end else begin
            if (frame_end_c) begin
                byte_count_q <= 16'h0;
                countdown_q  <= idle_cycles_c;
            end else begin
                if (beat_c) begin
                    byte_count_q <= byte_count_q + {12'h0, beat_bytes_c};
                end
                if (countdown_q != 32'h0) begin
                    countdown_q <= countdown_q - 32'd1;
                end
            end
        end
    end

    assign trigger_out_o = trigger_in_i && (countdown_q == 32'h0);

endmodule
