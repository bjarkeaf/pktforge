// AXI-Stream ICRC (RoCEv2 Invariant CRC) appender.
//
// Consumes a RoCEv2 IPv4 frame (Eth + IPv4 + UDP + BTH + payload) on the
// slave AXI-Stream, computes the ICRC per IB Annex A17, and emits the same
// frame followed by 4 ICRC bytes in little-endian order on the master
// AXI-Stream. tlast lives on the ICRC beat.
//
// ICRC = CRC-32 (poly 0x04C11DB7, refin/refout, xorout 0xFFFFFFFF) over
// the "pseudo-packet":
//   1) 8 bytes of 0xFF (dummy LRH). Absorbed as the fixed initial CRC
//      state ICRC_INIT = 0xDEBB20E3 = crc32_update(0xFFFFFFFF, 0xFF x 8).
//   2) The frame bytes, with these positions masked to 0xFF:
//        offset 15         -> IPv4 ToS (DSCP|ECN)
//        offset 22         -> IPv4 TTL
//        offsets 24, 25    -> IPv4 header checksum
//        offsets 40, 41    -> UDP checksum
//        offset 46         -> BTH byte 4 (FECN|BECN|Resv6)
//      These offsets assume no VLAN tag; pktforge_hdr_builder does not
//      insert one.
//
// Wire byte order for the ICRC: LSB first, matching model/icrc.py and
// zlib.crc32 conventions.
//
// Scope: DATA_W=32 (4 lanes) only. tkeep assumed contiguous from lane 0.

module pktforge_icrc_appender #(
    parameter int DATA_W = 32
) (
    input  logic                clk,
    input  logic                rst,

    // Slave (from pktforge_hdr_builder)
    input  logic [DATA_W-1:0]   s_axis_tdata,
    input  logic [DATA_W/8-1:0] s_axis_tkeep,
    input  logic                s_axis_tvalid,
    output logic                s_axis_tready,
    input  logic                s_axis_tlast,

    // Master (to pktforge_fcs_appender or downstream)
    output logic [DATA_W-1:0]   m_axis_tdata,
    output logic [DATA_W/8-1:0] m_axis_tkeep,
    output logic                m_axis_tvalid,
    input  logic                m_axis_tready,
    output logic                m_axis_tlast
);

    localparam int          LANES     = DATA_W / 8;
    localparam logic [31:0] ICRC_INIT = 32'hDEBB20E3;

    // ------------------------------------------------------------------
    // Masking + CRC-32 helpers
    // ------------------------------------------------------------------
    function automatic logic is_masked_byte(input logic [11:0] offset);
        if (offset >= 12'd54) begin
            is_masked_byte = 1'b0;
        end else begin
            unique case (offset[5:0])
                6'd15, 6'd22, 6'd24, 6'd25,
                6'd40, 6'd41, 6'd46:
                    is_masked_byte = 1'b1;
                default:
                    is_masked_byte = 1'b0;
            endcase
        end
    endfunction

    function automatic logic [DATA_W-1:0] mask_data_word(
        input logic [DATA_W-1:0]  data,
        input logic [LANES-1:0]   keep,
        input logic [11:0]        base_offset
    );
        logic [DATA_W-1:0] d;
        d = data;
        for (int lane = 0; lane < LANES; lane++) begin
            logic [11:0] byte_off;
            byte_off = base_offset + 12'(lane);
            if (keep[lane] && is_masked_byte(byte_off)) begin
                d[lane*8 +: 8] = 8'hFF;
            end
        end
        return d;
    endfunction

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
    logic [11:0] byte_cnt_q;
    logic [2:0]  append_bytes_q;
    logic [31:0] append_data_q;

    logic [2:0]         k_valid;
    logic [DATA_W-1:0]  masked_data;
    logic [31:0]        crc_next;
    logic [31:0]        icrc_final;

    assign k_valid     = {2'b0, s_axis_tkeep[0]} + {2'b0, s_axis_tkeep[1]}
                       + {2'b0, s_axis_tkeep[2]} + {2'b0, s_axis_tkeep[3]};
    assign masked_data = mask_data_word(s_axis_tdata, s_axis_tkeep, byte_cnt_q);
    assign crc_next    = crc32_update_word(crc_q, masked_data, s_axis_tkeep);
    assign icrc_final  = crc_next ^ 32'hFFFFFFFF;

    // ------------------------------------------------------------------
    // Handshake and output construction (mirror of FCS appender)
    // ------------------------------------------------------------------
    assign s_axis_tready = (state_q == S_PASS) && m_axis_tready;
    assign m_axis_tvalid = (state_q == S_PASS) ? s_axis_tvalid : 1'b1;

    always_comb begin
        m_axis_tdata = '0;
        m_axis_tkeep = '0;
        m_axis_tlast = 1'b0;

        if (state_q == S_PASS) begin
            // Forward the ORIGINAL bytes (only CRC uses masked versions).
            m_axis_tdata = s_axis_tdata;
            m_axis_tkeep = s_axis_tkeep;
            if (s_axis_tvalid && s_axis_tlast) begin
                case (k_valid)
                    3'd1: begin
                        m_axis_tdata[15:8]  = icrc_final[7:0];
                        m_axis_tdata[23:16] = icrc_final[15:8];
                        m_axis_tdata[31:24] = icrc_final[23:16];
                        m_axis_tkeep        = 4'b1111;
                    end
                    3'd2: begin
                        m_axis_tdata[23:16] = icrc_final[7:0];
                        m_axis_tdata[31:24] = icrc_final[15:8];
                        m_axis_tkeep        = 4'b1111;
                    end
                    3'd3: begin
                        m_axis_tdata[31:24] = icrc_final[7:0];
                        m_axis_tkeep        = 4'b1111;
                    end
                    default: ; // k_valid == 4: no merge; full ICRC beat coming
                endcase
            end
        end else begin
            m_axis_tdata = append_data_q;
            m_axis_tkeep = (append_bytes_q == 3'd4) ? 4'b1111 :
                           (append_bytes_q == 3'd3) ? 4'b0111 :
                           (append_bytes_q == 3'd2) ? 4'b0011 :
                                                       4'b0001;
            m_axis_tlast = 1'b1;
        end
    end

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            state_q        <= S_PASS;
            crc_q          <= ICRC_INIT;
            byte_cnt_q     <= 12'h0;
            append_bytes_q <= 3'd0;
            append_data_q  <= 32'h0;
        end else begin
            case (state_q)
                S_PASS: begin
                    if (s_axis_tvalid && m_axis_tready) begin
                        crc_q      <= crc_next;
                        byte_cnt_q <= byte_cnt_q + {9'h0, k_valid};
                        if (s_axis_tlast) begin
                            state_q <= S_APPEND;
                            case (k_valid)
                                3'd1: begin
                                    append_bytes_q <= 3'd1;
                                    append_data_q  <= {24'h0, icrc_final[31:24]};
                                end
                                3'd2: begin
                                    append_bytes_q <= 3'd2;
                                    append_data_q  <= {16'h0, icrc_final[31:16]};
                                end
                                3'd3: begin
                                    append_bytes_q <= 3'd3;
                                    append_data_q  <= {8'h0, icrc_final[31:8]};
                                end
                                default: begin
                                    append_bytes_q <= 3'd4;
                                    append_data_q  <= icrc_final;
                                end
                            endcase
                        end
                    end
                end

                S_APPEND: begin
                    if (m_axis_tready) begin
                        state_q    <= S_PASS;
                        crc_q      <= ICRC_INIT;
                        byte_cnt_q <= 12'h0;
                    end
                end

                default: state_q <= S_PASS;
            endcase
        end
    end

endmodule
