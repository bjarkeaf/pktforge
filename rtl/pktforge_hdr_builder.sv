// RoCEv2 header + payload generator.
//
// Consumes decoded configuration from the regfile output bus and, on each
// pkt_valid_i pulse, emits exactly one on-wire frame on the m_axis_* master
// interface. Byte-exact against model/golden.py for the RoCEv2 path with
// append_icrc=0 and append_fcs=0.
//
// Scope (Phase 2):
//   - RoCEv2 only (frame_type is ignored here; wired at the top level)
//   - No VLAN
//   - No FCS, no ICRC (separate downstream modules in later phases)
//   - Frame size taken from sweep_size_min_i as a fixed value; the sweep
//     engine lands in Phase 3
//   - PSN counter is internal: loaded from roce_psn_ack_i[23:0] on start_i,
//     advances by 1 per emitted packet, wraps at 2^24
//
// The IPv4 header carries id=1 (matches scapy's default when the field is
// unset). UDP checksum is zero on the wire; the golden model was updated to
// match spec-compliant RoCEv2 emitters (IB Annex A17.4.5.3).

module pktforge_hdr_builder #(
    parameter int DATA_W = 32
) (
    input  logic clk,
    input  logic rst,

    // Control
    input  logic start_i,       // one-cycle pulse: load PSN from roce_psn_ack_i
    input  logic pkt_valid_i,   // one-cycle pulse: emit one packet
    output logic pkt_ready_o,   // high when the builder can accept a trigger

    // Config bus (stable during a packet emission). Upper reserved bits of
    // several regs are intentionally unread here; see docs/regmap.md.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [47:0] eth_src_mac_i,
    input  logic [47:0] eth_dst_mac_i,
    input  logic [31:0] ip_src_i,           // byte 0 on wire in [31:24]
    input  logic [31:0] ip_dst_i,           // byte 0 on wire in [31:24]
    input  logic [31:0] ip_misc_i,          // TTL[7:0], DSCP[13:8], ECN[15:14]
    input  logic [31:0] transport_ports_i,  // sport[15:0], dport[31:16]
    input  logic [31:0] roce_op_pkey_i,     // opcode[7:0], pkey[23:8]
    input  logic [31:0] roce_dest_qp_i,     // dest_qp[23:0]
    input  logic [31:0] roce_psn_ack_i,     // psn_start[23:0], ack_req[24]
    input  logic [31:0] sweep_size_min_i,   // frame size in bytes (fixed for now)
    /* verilator lint_on UNUSEDSIGNAL */

    // AXI-Stream master
    output logic [DATA_W-1:0]      m_axis_tdata,
    output logic [DATA_W/8-1:0]    m_axis_tkeep,
    output logic                   m_axis_tvalid,
    input  logic                   m_axis_tready,
    output logic                   m_axis_tlast
);

    localparam int LANES   = DATA_W / 8;
    localparam int HDR_LEN = 54;   // Eth(14) + IPv4(20) + UDP(8) + BTH(12)

    // ------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------
    typedef enum logic [0:0] {S_IDLE = 1'b0, S_EMIT = 1'b1} state_t;
    state_t state_q;

    logic [23:0] psn_q;       // running PSN (advances per packet)
    logic [23:0] pkt_psn_q;   // PSN of the packet currently in EMIT

    logic [11:0] size_q;      // total frame size (bytes) for current packet
    logic [11:0] byte_cnt_q;  // byte offset of lane 0 for the current beat

    logic [7:0]  hdr_bytes_q [0:HDR_LEN-1];

    assign pkt_ready_o = (state_q == S_IDLE);

    // ------------------------------------------------------------------
    // Trigger-time fields (combinational)
    // ------------------------------------------------------------------
    logic [11:0] trig_size;
    logic [15:0] ip_total_len;
    logic [15:0] udp_len;
    logic [7:0]  ip_tos;
    logic [7:0]  bth_ack_req_byte;

    assign trig_size        = sweep_size_min_i[11:0];
    assign ip_total_len     = {4'h0, trig_size} - 16'd14;
    assign udp_len          = {4'h0, trig_size} - 16'd34;
    assign ip_tos           = {ip_misc_i[13:8], ip_misc_i[15:14]};
    assign bth_ack_req_byte = {roce_psn_ack_i[24], 7'h00};

    // ------------------------------------------------------------------
    // IPv4 header checksum (RFC 1071 one's complement over 10 16-bit words)
    //   w0 = {0x45, ToS}          w5 (chksum) = 0 (omitted)
    //   w1 = total_length         w6 = ip_src[31:16]
    //   w2 = 0x0001 (id)          w7 = ip_src[15:0]
    //   w3 = 0x4000 (DF)          w8 = ip_dst[31:16]
    //   w4 = {TTL, 0x11}          w9 = ip_dst[15:0]
    // ------------------------------------------------------------------
    logic [19:0] ip_sum;
    logic [16:0] ip_sum_f1;
    logic [15:0] ip_sum_f2;
    logic [15:0] ip_checksum;

    assign ip_sum      = { 4'h0, 8'h45,          ip_tos          }
                       + { 4'h0, ip_total_len                     }
                       + 20'h0_0001
                       + 20'h0_4000
                       + { 4'h0, ip_misc_i[7:0], 8'h11            }
                       + { 4'h0, ip_src_i[31:16]                  }
                       + { 4'h0, ip_src_i[15:0]                   }
                       + { 4'h0, ip_dst_i[31:16]                  }
                       + { 4'h0, ip_dst_i[15:0]                   };
    assign ip_sum_f1   = {1'b0, ip_sum[15:0]} + {13'h0, ip_sum[19:16]};
    assign ip_sum_f2   = ip_sum_f1[15:0] + {15'h0, ip_sum_f1[16]};
    assign ip_checksum = ~ip_sum_f2;

    // ------------------------------------------------------------------
    // Header staging (combinational): assembled on the trigger cycle,
    // latched into hdr_bytes_q so the emit path is decoupled from any
    // config wobble after the trigger.
    // ------------------------------------------------------------------
    logic [7:0] hdr_next [0:HDR_LEN-1];
    always_comb begin
        // Ethernet (dst MAC, src MAC, EtherType)
        hdr_next[0]  = eth_dst_mac_i[47:40];
        hdr_next[1]  = eth_dst_mac_i[39:32];
        hdr_next[2]  = eth_dst_mac_i[31:24];
        hdr_next[3]  = eth_dst_mac_i[23:16];
        hdr_next[4]  = eth_dst_mac_i[15:8];
        hdr_next[5]  = eth_dst_mac_i[7:0];
        hdr_next[6]  = eth_src_mac_i[47:40];
        hdr_next[7]  = eth_src_mac_i[39:32];
        hdr_next[8]  = eth_src_mac_i[31:24];
        hdr_next[9]  = eth_src_mac_i[23:16];
        hdr_next[10] = eth_src_mac_i[15:8];
        hdr_next[11] = eth_src_mac_i[7:0];
        hdr_next[12] = 8'h08;                    // EtherType 0x0800 = IPv4
        hdr_next[13] = 8'h00;

        // IPv4
        hdr_next[14] = 8'h45;                    // Version=4, IHL=5
        hdr_next[15] = ip_tos;
        hdr_next[16] = ip_total_len[15:8];
        hdr_next[17] = ip_total_len[7:0];
        hdr_next[18] = 8'h00;                    // Identification (scapy default 1)
        hdr_next[19] = 8'h01;
        hdr_next[20] = 8'h40;                    // Flags=DF, FragOffset=0
        hdr_next[21] = 8'h00;
        hdr_next[22] = ip_misc_i[7:0];           // TTL
        hdr_next[23] = 8'h11;                    // Protocol = UDP
        hdr_next[24] = ip_checksum[15:8];
        hdr_next[25] = ip_checksum[7:0];
        hdr_next[26] = ip_src_i[31:24];
        hdr_next[27] = ip_src_i[23:16];
        hdr_next[28] = ip_src_i[15:8];
        hdr_next[29] = ip_src_i[7:0];
        hdr_next[30] = ip_dst_i[31:24];
        hdr_next[31] = ip_dst_i[23:16];
        hdr_next[32] = ip_dst_i[15:8];
        hdr_next[33] = ip_dst_i[7:0];

        // UDP
        hdr_next[34] = transport_ports_i[15:8];  // sport hi
        hdr_next[35] = transport_ports_i[7:0];   // sport lo
        hdr_next[36] = transport_ports_i[31:24]; // dport hi
        hdr_next[37] = transport_ports_i[23:16]; // dport lo
        hdr_next[38] = udp_len[15:8];
        hdr_next[39] = udp_len[7:0];
        hdr_next[40] = 8'h00;                    // UDP checksum = 0 (RoCEv2)
        hdr_next[41] = 8'h00;

        // BTH
        hdr_next[42] = roce_op_pkey_i[7:0];      // Opcode
        hdr_next[43] = 8'h00;                    // SE|MigReq|PadCnt|TVer
        hdr_next[44] = roce_op_pkey_i[23:16];    // P_Key hi
        hdr_next[45] = roce_op_pkey_i[15:8];     // P_Key lo
        hdr_next[46] = 8'h00;                    // FECN|BECN|Resv6
        hdr_next[47] = roce_dest_qp_i[23:16];    // DestQP hi
        hdr_next[48] = roce_dest_qp_i[15:8];
        hdr_next[49] = roce_dest_qp_i[7:0];
        hdr_next[50] = bth_ack_req_byte;
        hdr_next[51] = psn_q[23:16];
        hdr_next[52] = psn_q[15:8];
        hdr_next[53] = psn_q[7:0];
    end

    // ------------------------------------------------------------------
    // Payload byte generator (deterministic from the packet's latched PSN).
    // The seed matches model/golden.py:
    //   seed = psn.to_bytes(4,"big") + b"pktforge" + b"\x00"*4
    // and payload byte i = seed[i mod 16].
    // ------------------------------------------------------------------
    function automatic logic [7:0] payload_byte(input logic [3:0] seed_idx);
        unique case (seed_idx)
            4'h0: return 8'h00;
            4'h1: return pkt_psn_q[23:16];
            4'h2: return pkt_psn_q[15:8];
            4'h3: return pkt_psn_q[7:0];
            4'h4: return 8'h70;                  // 'p'
            4'h5: return 8'h6B;                  // 'k'
            4'h6: return 8'h74;                  // 't'
            4'h7: return 8'h66;                  // 'f'
            4'h8: return 8'h6F;                  // 'o'
            4'h9: return 8'h72;                  // 'r'
            4'hA: return 8'h67;                  // 'g'
            4'hB: return 8'h65;                  // 'e'
            default: return 8'h00;               // seed[12..15] = 0
        endcase
    endfunction

    // ------------------------------------------------------------------
    // AXI-Stream output (combinational)
    // ------------------------------------------------------------------
    always_comb begin
        m_axis_tdata  = '0;
        m_axis_tkeep  = '0;
        m_axis_tvalid = (state_q == S_EMIT);
        m_axis_tlast  = 1'b0;

        for (int unsigned lane = 0; lane < LANES; lane++) begin
            logic [11:0] byte_idx;
            logic [3:0]  seed_idx;
            logic [7:0]  b;
            byte_idx = byte_cnt_q + 12'(lane);
            // seed_idx = (byte_idx - HDR_LEN) mod 16. HDR_LEN=54, so
            // ((byte_idx - 54) mod 16) == ((byte_idx + 10) mod 16).
            seed_idx = byte_idx[3:0] + 4'd10;
            if (byte_idx < 12'(HDR_LEN)) begin
                b = hdr_bytes_q[byte_idx[5:0]];
            end else begin
                b = payload_byte(seed_idx);
            end
            if (byte_idx < size_q) begin
                m_axis_tdata[lane*8 +: 8] = b;
                m_axis_tkeep[lane]        = 1'b1;
            end
        end

        // Last beat is the one whose lane 0 is at (size_q - 1) or earlier
        // and whose last lane reaches or passes size_q.
        if (state_q == S_EMIT) begin
            m_axis_tlast = ({4'h0, byte_cnt_q} + 16'(LANES)) >= {4'h0, size_q};
        end
    end

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            state_q     <= S_IDLE;
            psn_q       <= 24'h0;
            pkt_psn_q   <= 24'h0;
            size_q      <= 12'h0;
            byte_cnt_q  <= 12'h0;
            for (int i = 0; i < HDR_LEN; i++) hdr_bytes_q[i] <= 8'h0;
        end else begin
            if (start_i) begin
                psn_q <= roce_psn_ack_i[23:0];
            end

            case (state_q)
                S_IDLE: begin
                    if (pkt_valid_i) begin
                        pkt_psn_q  <= psn_q;
                        size_q     <= trig_size;
                        byte_cnt_q <= 12'h0;
                        for (int i = 0; i < HDR_LEN; i++) hdr_bytes_q[i] <= hdr_next[i];
                        // start_i and pkt_valid_i in the same cycle: use the
                        // freshly loaded PSN for this packet.
                        if (start_i) begin
                            pkt_psn_q <= roce_psn_ack_i[23:0];
                            psn_q     <= roce_psn_ack_i[23:0] + 24'd1;
                            // Rebuild the last three header bytes with the
                            // just-loaded PSN so this packet ships correctly.
                            hdr_bytes_q[51] <= roce_psn_ack_i[23:16];
                            hdr_bytes_q[52] <= roce_psn_ack_i[15:8];
                            hdr_bytes_q[53] <= roce_psn_ack_i[7:0];
                        end else begin
                            psn_q <= psn_q + 24'd1;
                        end
                        state_q <= S_EMIT;
                    end
                end

                S_EMIT: begin
                    if (m_axis_tready) begin
                        if (m_axis_tlast) begin
                            state_q <= S_IDLE;
                        end else begin
                            byte_cnt_q <= byte_cnt_q + 12'(LANES);
                        end
                    end
                end

                default: state_q <= S_IDLE;
            endcase
        end
    end

endmodule
