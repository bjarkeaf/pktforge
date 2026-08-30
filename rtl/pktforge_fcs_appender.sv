// AXI-Stream FCS appender.
//
// Consumes an L2 frame on the slave AXI-Stream, computes the Ethernet FCS
// (CRC-32, poly 0x04C11DB7, init 0xFFFFFFFF, refin, refout, xorout
// 0xFFFFFFFF; matches zlib.crc32 and `model/fcs.py:compute_fcs`), and emits
// the frame followed by 4 FCS bytes in little-endian order on the master
// AXI-Stream. tlast lives on the FCS beat.
//
// If the last input beat has 1-3 valid lanes (partial tkeep), the first
// 1-3 FCS bytes are merged into that beat's empty lanes; any remaining FCS
// bytes ship in one additional APPEND beat. If the last input beat is full
// (tkeep=0xF), the appender emits one extra beat with all 4 FCS bytes.
//
// Scope: DATA_W=32 (4 lanes) only. tkeep is assumed to be contiguous from
// lane 0 (standard AXI-Stream convention, matches pktforge_hdr_builder).

module pktforge_fcs_appender #(
    parameter int DATA_W = 32
) (
    input  logic                clk,
    input  logic                rst,

    // Slave (upstream, e.g. from pktforge_hdr_builder)
    input  logic [DATA_W-1:0]   s_axis_tdata,
    input  logic [DATA_W/8-1:0] s_axis_tkeep,
    input  logic                s_axis_tvalid,
    output logic                s_axis_tready,
    input  logic                s_axis_tlast,

    // Master (downstream, with FCS appended when enable_i is high)
    output logic [DATA_W-1:0]   m_axis_tdata,
    output logic [DATA_W/8-1:0] m_axis_tkeep,
    output logic                m_axis_tvalid,
    input  logic                m_axis_tready,
    output logic                m_axis_tlast,

    // Runtime enable. When 0, the appender is a pure pass-through: no CRC
    // computation, no APPEND beat, s_axis is forwarded to m_axis unchanged
    // (tlast included). Toggle only between frames.
    input  logic                enable_i
);

    localparam int LANES = DATA_W / 8;

    // ------------------------------------------------------------------
    // CRC-32 helpers (reflected, matches zlib.crc32 / IEEE 802.3 FCS)
    // ------------------------------------------------------------------
    function automatic logic [31:0] crc32_update_byte(
        input logic [31:0] crc, input logic [7:0] b);
        logic [31:0] c;
        c = crc ^ {24'h0, b};
        for (int i = 0; i < 8; i++) begin
            c = c[0] ? ((c >> 1) ^ 32'hEDB88320) : (c >> 1);
        end
        return c;
    endfunction

    function automatic logic [31:0] crc32_update_word(
        input logic [31:0]        crc,
        input logic [DATA_W-1:0]  data,
        input logic [LANES-1:0]   keep);
        logic [31:0] c;
        c = crc;
        if (keep[0]) c = crc32_update_byte(c, data[7:0]);
        if (keep[1]) c = crc32_update_byte(c, data[15:8]);
        if (keep[2]) c = crc32_update_byte(c, data[23:16]);
        if (keep[3]) c = crc32_update_byte(c, data[31:24]);
        return c;
    endfunction

    // ------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------
    typedef enum logic [0:0] {S_PASS = 1'b0, S_APPEND = 1'b1} state_t;
    state_t state_q;

    logic [31:0] crc_q;
    logic [2:0]  append_bytes_q;    // 0..4 remaining FCS bytes for APPEND beat
    logic [31:0] append_data_q;     // packed lane 0 = LSB

    // ------------------------------------------------------------------
    // Combinational computation of the final FCS on the current cycle,
    // used both for the S_PASS merge into the last input beat and for
    // seeding the S_APPEND register on the transition.
    // ------------------------------------------------------------------
    logic [2:0]  k_valid;
    logic [31:0] crc_next;
    logic [31:0] fcs_final;

    assign k_valid   = {2'b0, s_axis_tkeep[0]} + {2'b0, s_axis_tkeep[1]}
                     + {2'b0, s_axis_tkeep[2]} + {2'b0, s_axis_tkeep[3]};
    assign crc_next  = crc32_update_word(crc_q, s_axis_tdata, s_axis_tkeep);
    assign fcs_final = crc_next ^ 32'hFFFFFFFF;

    // ------------------------------------------------------------------
    // Handshake and output construction
    // ------------------------------------------------------------------
    assign s_axis_tready = !enable_i ? m_axis_tready
                                     : ((state_q == S_PASS) && m_axis_tready);
    assign m_axis_tvalid = !enable_i ? s_axis_tvalid
                                     : ((state_q == S_PASS) ? s_axis_tvalid : 1'b1);

    always_comb begin
        m_axis_tdata = '0;
        m_axis_tkeep = '0;
        m_axis_tlast = 1'b0;

        if (!enable_i) begin
            m_axis_tdata = s_axis_tdata;
            m_axis_tkeep = s_axis_tkeep;
            m_axis_tlast = s_axis_tlast;
        end else if (state_q == S_PASS) begin
            m_axis_tdata = s_axis_tdata;
            m_axis_tkeep = s_axis_tkeep;
            // Never assert tlast here; the FCS beat carries it.
            if (s_axis_tvalid && s_axis_tlast) begin
                case (k_valid)
                    3'd1: begin
                        m_axis_tdata[15:8]  = fcs_final[7:0];
                        m_axis_tdata[23:16] = fcs_final[15:8];
                        m_axis_tdata[31:24] = fcs_final[23:16];
                        m_axis_tkeep        = 4'b1111;
                    end
                    3'd2: begin
                        m_axis_tdata[23:16] = fcs_final[7:0];
                        m_axis_tdata[31:24] = fcs_final[15:8];
                        m_axis_tkeep        = 4'b1111;
                    end
                    3'd3: begin
                        m_axis_tdata[31:24] = fcs_final[7:0];
                        m_axis_tkeep        = 4'b1111;
                    end
                    default: ; // k_valid == 4: no merge; all FCS in APPEND
                endcase
            end
        end else begin
            // S_APPEND: emit the remaining FCS bytes, tlast asserted.
            m_axis_tdata = append_data_q;
            m_axis_tkeep = (append_bytes_q == 3'd4) ? 4'b1111 :
                           (append_bytes_q == 3'd3) ? 4'b0111 :
                           (append_bytes_q == 3'd2) ? 4'b0011 :
                                                       4'b0001; // == 1
            m_axis_tlast = 1'b1;
        end
    end

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            state_q         <= S_PASS;
            crc_q           <= 32'hFFFFFFFF;
            append_bytes_q  <= 3'd0;
            append_data_q   <= 32'h0;
        end else if (!enable_i) begin
            state_q <= S_PASS;
            crc_q   <= 32'hFFFFFFFF;
        end else begin
            case (state_q)
                S_PASS: begin
                    if (s_axis_tvalid && m_axis_tready) begin
                        crc_q <= crc_next;
                        if (s_axis_tlast) begin
                            state_q <= S_APPEND;
                            case (k_valid)
                                3'd1: begin
                                    append_bytes_q <= 3'd1;
                                    append_data_q  <= {24'h0, fcs_final[31:24]};
                                end
                                3'd2: begin
                                    append_bytes_q <= 3'd2;
                                    append_data_q  <= {16'h0, fcs_final[31:16]};
                                end
                                3'd3: begin
                                    append_bytes_q <= 3'd3;
                                    append_data_q  <= {8'h0, fcs_final[31:8]};
                                end
                                default: begin // k_valid == 4
                                    append_bytes_q <= 3'd4;
                                    append_data_q  <= fcs_final;
                                end
                            endcase
                        end
                    end
                end

                S_APPEND: begin
                    if (m_axis_tready) begin
                        state_q <= S_PASS;
                        crc_q   <= 32'hFFFFFFFF;
                    end
                end

                default: state_q <= S_PASS;
            endcase
        end
    end

endmodule
