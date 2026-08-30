// Per-dimension sweep counter.
//
// Maintains a single sweep counter that advances by `step_i` each time
// `advance_i` pulses, wrapping back to `min_i` when the next value would
// exceed `max_i`. `start_i` (one-cycle pulse) reloads the counter to
// `min_i`, matching packet_index=0 in the golden model.
//
// When `step_i` is zero the sweep is disabled: `value_o` follows `base_i`
// unchanged. This mirrors `model/golden.py:_sweep_value` where a `None`
// sweep dim returns the base config value.
//
// One instance per swept field (5 total: src IP, dst IP, src port, dst
// port, frame size). Each instance is dimensioned via the WIDTH parameter.

module pktforge_sweep #(
    parameter int WIDTH = 32
) (
    input  logic             clk,
    input  logic             rst,

    input  logic             start_i,    // 1-cycle pulse: reload counter to min_i
    input  logic             advance_i,  // 1-cycle pulse: step to next value

    input  logic [WIDTH-1:0] base_i,     // used when step_i == 0
    input  logic [WIDTH-1:0] min_i,
    input  logic [WIDTH-1:0] max_i,
    input  logic [WIDTH-1:0] step_i,

    output logic [WIDTH-1:0] value_o
);

    logic [WIDTH-1:0] counter_q;
    logic [WIDTH-1:0] next_c;

    always_comb begin
        next_c = counter_q + step_i;
        if (next_c > max_i) begin
            next_c = min_i;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            counter_q <= min_i;
        end else if (start_i) begin
            counter_q <= min_i;
        end else if (advance_i && (step_i != '0)) begin
            counter_q <= next_c;
        end
    end

    assign value_o = (step_i == '0) ? base_i : counter_q;

endmodule
