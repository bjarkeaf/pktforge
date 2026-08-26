// AXI-Lite subordinate register file for pktforge.
//
// Register map defined in docs/regmap.md. 32-bit data, 8-bit address
// (256 bytes / 64 registers). Byte strobes honored. Unaligned or
// reserved-address accesses return SLVERR.
//
// The regfile exposes a flat `cfg_o` bus of decoded configuration
// signals for downstream modules (header builder, sweep engine, rate
// limiter). Live counters (PACKETS_SENT) are driven by `stat_i` inputs
// from the datapath. Control triggers (START/STOP/ONE_SHOT) come out
// on `ctrl_o` as one-cycle pulses.

module pktforge_regfile_axil #(
    parameter logic [31:0] ID_MAGIC   = 32'h504B_5446,  // 'PKTF'
    parameter logic [31:0] VERSION    = 32'h0001_0000   // 1.0
) (
    input  logic         clk,
    input  logic         rst,

    // AXI-Lite subordinate. AWADDR/ARADDR are 8 bits because the map is
    // 256 bytes. Extend if the map grows.
    input  logic [7:0]   s_axil_awaddr,
    input  logic         s_axil_awvalid,
    output logic         s_axil_awready,

    input  logic [31:0]  s_axil_wdata,
    input  logic [3:0]   s_axil_wstrb,
    input  logic         s_axil_wvalid,
    output logic         s_axil_wready,

    output logic [1:0]   s_axil_bresp,
    output logic         s_axil_bvalid,
    input  logic         s_axil_bready,

    input  logic [7:0]   s_axil_araddr,
    input  logic         s_axil_arvalid,
    output logic         s_axil_arready,

    output logic [31:0]  s_axil_rdata,
    output logic [1:0]   s_axil_rresp,
    output logic         s_axil_rvalid,
    input  logic         s_axil_rready,

    // Decoded configuration. Grouped for readability. All fields are in
    // host order; the header builder byte-swaps to network order.
    output logic         ctrl_start_o,
    output logic         ctrl_stop_o,
    output logic         ctrl_one_shot_o,

    output logic [31:0]  frame_type_o,
    output logic [47:0]  eth_src_mac_o,
    output logic [47:0]  eth_dst_mac_o,
    output logic [31:0]  eth_vlan_o,
    output logic [31:0]  ip_src_o,
    output logic [31:0]  ip_dst_o,
    output logic [31:0]  ip_misc_o,
    output logic [31:0]  transport_ports_o,
    output logic [31:0]  roce_op_pkey_o,
    output logic [31:0]  roce_dest_qp_o,
    output logic [31:0]  roce_psn_ack_o,

    output logic [31:0]  sweep_sip_min_o,
    output logic [31:0]  sweep_sip_max_o,
    output logic [31:0]  sweep_sip_step_o,
    output logic [31:0]  sweep_dip_min_o,
    output logic [31:0]  sweep_dip_max_o,
    output logic [31:0]  sweep_dip_step_o,
    output logic [31:0]  sweep_sport_min_o,
    output logic [31:0]  sweep_sport_max_o,
    output logic [31:0]  sweep_sport_step_o,
    output logic [31:0]  sweep_dport_min_o,
    output logic [31:0]  sweep_dport_max_o,
    output logic [31:0]  sweep_dport_step_o,
    output logic [31:0]  sweep_size_min_o,
    output logic [31:0]  sweep_size_max_o,
    output logic [31:0]  sweep_size_step_o,

    output logic [31:0]  rate_mode_o,
    output logic [31:0]  rate_line_percent_o,
    output logic [31:0]  rate_ifg_bytes_o,
    output logic [31:0]  output_opts_o,
    output logic [31:0]  packet_count_o,
    output logic [31:0]  seed_o,

    // Live status inputs from the datapath.
    input  logic         status_running_i,
    input  logic         status_done_i,
    input  logic         status_error_i,
    input  logic [31:0]  packets_sent_i
);

    // ------------------------------------------------------------------
    // Register storage
    // ------------------------------------------------------------------
    logic [31:0] frame_type_q;
    logic [31:0] eth_src_lo_q, eth_src_hi_q;
    logic [31:0] eth_dst_lo_q, eth_dst_hi_q;
    logic [31:0] eth_vlan_q;
    logic [31:0] ip_src_q, ip_dst_q, ip_misc_q;
    logic [31:0] transport_ports_q;
    logic [31:0] roce_op_pkey_q, roce_dest_qp_q, roce_psn_ack_q;
    logic [31:0] sweep_sip_min_q, sweep_sip_max_q, sweep_sip_step_q;
    logic [31:0] sweep_dip_min_q, sweep_dip_max_q, sweep_dip_step_q;
    logic [31:0] sweep_sport_min_q, sweep_sport_max_q, sweep_sport_step_q;
    logic [31:0] sweep_dport_min_q, sweep_dport_max_q, sweep_dport_step_q;
    logic [31:0] sweep_size_min_q, sweep_size_max_q, sweep_size_step_q;
    logic [31:0] rate_mode_q, rate_line_percent_q, rate_ifg_bytes_q;
    logic [31:0] output_opts_q;
    logic [31:0] packet_count_q, seed_q;

    // CTRL bits are pulse outputs, latched here for one cycle then cleared.
    logic ctrl_start_q, ctrl_stop_q, ctrl_one_shot_q;

    assign ctrl_start_o     = ctrl_start_q;
    assign ctrl_stop_o      = ctrl_stop_q;
    assign ctrl_one_shot_o  = ctrl_one_shot_q;

    assign frame_type_o        = frame_type_q;
    assign eth_src_mac_o       = {eth_src_hi_q[15:0], eth_src_lo_q};
    assign eth_dst_mac_o       = {eth_dst_hi_q[15:0], eth_dst_lo_q};
    assign eth_vlan_o          = eth_vlan_q;
    assign ip_src_o            = ip_src_q;
    assign ip_dst_o            = ip_dst_q;
    assign ip_misc_o           = ip_misc_q;
    assign transport_ports_o   = transport_ports_q;
    assign roce_op_pkey_o      = roce_op_pkey_q;
    assign roce_dest_qp_o      = roce_dest_qp_q;
    assign roce_psn_ack_o      = roce_psn_ack_q;

    assign sweep_sip_min_o     = sweep_sip_min_q;
    assign sweep_sip_max_o     = sweep_sip_max_q;
    assign sweep_sip_step_o    = sweep_sip_step_q;
    assign sweep_dip_min_o     = sweep_dip_min_q;
    assign sweep_dip_max_o     = sweep_dip_max_q;
    assign sweep_dip_step_o    = sweep_dip_step_q;
    assign sweep_sport_min_o   = sweep_sport_min_q;
    assign sweep_sport_max_o   = sweep_sport_max_q;
    assign sweep_sport_step_o  = sweep_sport_step_q;
    assign sweep_dport_min_o   = sweep_dport_min_q;
    assign sweep_dport_max_o   = sweep_dport_max_q;
    assign sweep_dport_step_o  = sweep_dport_step_q;
    assign sweep_size_min_o    = sweep_size_min_q;
    assign sweep_size_max_o    = sweep_size_max_q;
    assign sweep_size_step_o   = sweep_size_step_q;

    assign rate_mode_o         = rate_mode_q;
    assign rate_line_percent_o = rate_line_percent_q;
    assign rate_ifg_bytes_o    = rate_ifg_bytes_q;
    assign output_opts_o       = output_opts_q;
    assign packet_count_o      = packet_count_q;
    assign seed_o              = seed_q;

    // ------------------------------------------------------------------
    // Address decode helpers
    // ------------------------------------------------------------------
    // Offsets are byte addresses. All registers are 32-bit aligned; the
    // low 2 bits of araddr/awaddr must be zero. The map has holes; a
    // valid address is enumerated in the case statements below.

    localparam logic [7:0] A_ID                = 8'h00;
    localparam logic [7:0] A_VERSION           = 8'h04;
    localparam logic [7:0] A_CTRL              = 8'h08;
    localparam logic [7:0] A_STATUS            = 8'h0C;
    localparam logic [7:0] A_FRAME_TYPE        = 8'h10;
    localparam logic [7:0] A_ETH_SRC_MAC_LO    = 8'h14;
    localparam logic [7:0] A_ETH_SRC_MAC_HI    = 8'h18;
    localparam logic [7:0] A_ETH_DST_MAC_LO    = 8'h1C;
    localparam logic [7:0] A_ETH_DST_MAC_HI    = 8'h20;
    localparam logic [7:0] A_ETH_VLAN          = 8'h24;
    localparam logic [7:0] A_IP_SRC            = 8'h28;
    localparam logic [7:0] A_IP_DST            = 8'h2C;
    localparam logic [7:0] A_IP_MISC           = 8'h30;
    localparam logic [7:0] A_TRANSPORT_PORTS   = 8'h34;
    localparam logic [7:0] A_ROCE_OP_PKEY      = 8'h38;
    localparam logic [7:0] A_ROCE_DEST_QP      = 8'h3C;
    localparam logic [7:0] A_ROCE_PSN_ACK      = 8'h40;
    localparam logic [7:0] A_SWEEP_SIP_MIN     = 8'h50;
    localparam logic [7:0] A_SWEEP_SIP_MAX     = 8'h54;
    localparam logic [7:0] A_SWEEP_SIP_STEP    = 8'h58;
    localparam logic [7:0] A_SWEEP_DIP_MIN     = 8'h60;
    localparam logic [7:0] A_SWEEP_DIP_MAX     = 8'h64;
    localparam logic [7:0] A_SWEEP_DIP_STEP    = 8'h68;
    localparam logic [7:0] A_SWEEP_SPORT_MIN   = 8'h70;
    localparam logic [7:0] A_SWEEP_SPORT_MAX   = 8'h74;
    localparam logic [7:0] A_SWEEP_SPORT_STEP  = 8'h78;
    localparam logic [7:0] A_SWEEP_DPORT_MIN   = 8'h80;
    localparam logic [7:0] A_SWEEP_DPORT_MAX   = 8'h84;
    localparam logic [7:0] A_SWEEP_DPORT_STEP  = 8'h88;
    localparam logic [7:0] A_SWEEP_SIZE_MIN    = 8'h90;
    localparam logic [7:0] A_SWEEP_SIZE_MAX    = 8'h94;
    localparam logic [7:0] A_SWEEP_SIZE_STEP   = 8'h98;
    localparam logic [7:0] A_RATE_MODE         = 8'hA0;
    localparam logic [7:0] A_RATE_LINE_PERCENT = 8'hA4;
    localparam logic [7:0] A_RATE_IFG_BYTES    = 8'hA8;
    localparam logic [7:0] A_OUTPUT_OPTS       = 8'hAC;
    localparam logic [7:0] A_PACKET_COUNT      = 8'hB0;
    localparam logic [7:0] A_SEED              = 8'hB4;
    localparam logic [7:0] A_PACKETS_SENT      = 8'hB8;

    // Returns 1 if the address is a valid mapped register.
    function automatic logic is_valid_addr(input logic [7:0] a);
        // Reject unaligned.
        if (a[1:0] != 2'b00) return 1'b0;
        unique case (a)
            A_ID, A_VERSION, A_CTRL, A_STATUS, A_FRAME_TYPE,
            A_ETH_SRC_MAC_LO, A_ETH_SRC_MAC_HI,
            A_ETH_DST_MAC_LO, A_ETH_DST_MAC_HI, A_ETH_VLAN,
            A_IP_SRC, A_IP_DST, A_IP_MISC, A_TRANSPORT_PORTS,
            A_ROCE_OP_PKEY, A_ROCE_DEST_QP, A_ROCE_PSN_ACK,
            A_SWEEP_SIP_MIN, A_SWEEP_SIP_MAX, A_SWEEP_SIP_STEP,
            A_SWEEP_DIP_MIN, A_SWEEP_DIP_MAX, A_SWEEP_DIP_STEP,
            A_SWEEP_SPORT_MIN, A_SWEEP_SPORT_MAX, A_SWEEP_SPORT_STEP,
            A_SWEEP_DPORT_MIN, A_SWEEP_DPORT_MAX, A_SWEEP_DPORT_STEP,
            A_SWEEP_SIZE_MIN, A_SWEEP_SIZE_MAX, A_SWEEP_SIZE_STEP,
            A_RATE_MODE, A_RATE_LINE_PERCENT, A_RATE_IFG_BYTES,
            A_OUTPUT_OPTS, A_PACKET_COUNT, A_SEED, A_PACKETS_SENT:
                return 1'b1;
            default: return 1'b0;
        endcase
    endfunction

    // Returns 1 if the address is writable (RW). RO addresses return
    // OKAY on write and drop the data.
    function automatic logic is_writable(input logic [7:0] a);
        unique case (a)
            A_ID, A_VERSION, A_STATUS, A_PACKETS_SENT: return 1'b0;
            default: return is_valid_addr(a);
        endcase
    endfunction

    // Apply write strobes to an old register value.
    function automatic logic [31:0] apply_wstrb(
        input logic [31:0] old_val,
        input logic [31:0] new_val,
        input logic [3:0]  strb
    );
        logic [31:0] r;
        for (int i = 0; i < 4; i++) begin
            r[i*8 +: 8] = strb[i] ? new_val[i*8 +: 8] : old_val[i*8 +: 8];
        end
        return r;
    endfunction

    // ------------------------------------------------------------------
    // Write channel
    // ------------------------------------------------------------------
    // Simple ready-when-both-valid handshake: we accept AW and W together
    // and hold BVALID until BREADY. One outstanding transaction at a time.

    typedef enum logic [1:0] {W_IDLE, W_RESP} w_state_t;
    w_state_t   w_state_q;
    logic       wr_ok_q;

    always_comb begin
        s_axil_awready = 1'b0;
        s_axil_wready  = 1'b0;
        if (w_state_q == W_IDLE) begin
            s_axil_awready = s_axil_awvalid && s_axil_wvalid;
            s_axil_wready  = s_axil_awvalid && s_axil_wvalid;
        end
    end

    assign s_axil_bvalid = (w_state_q == W_RESP);
    assign s_axil_bresp  = wr_ok_q ? 2'b00 : 2'b10; // OKAY / SLVERR

    always_ff @(posedge clk) begin
        // Control pulses default to 0 each cycle.
        ctrl_start_q    <= 1'b0;
        ctrl_stop_q     <= 1'b0;
        ctrl_one_shot_q <= 1'b0;

        if (rst) begin
            w_state_q            <= W_IDLE;
            wr_ok_q              <= 1'b0;

            frame_type_q         <= 32'h0000_0000;
            eth_src_lo_q         <= 32'h0000_0002;
            eth_src_hi_q         <= 32'h0000_0000;
            eth_dst_lo_q         <= 32'h0000_0002;
            eth_dst_hi_q         <= 32'h0000_0000;
            eth_vlan_q           <= 32'h0000_0000;
            ip_src_q             <= 32'h0A00_0001;
            ip_dst_q             <= 32'h0A00_0002;
            ip_misc_q            <= 32'h0000_0040;
            transport_ports_q    <= 32'h12B7_8000;
            roce_op_pkey_q       <= 32'h00FF_FF64;
            roce_dest_qp_q       <= 32'h0000_0010;
            roce_psn_ack_q       <= 32'h0000_0000;

            sweep_sip_min_q      <= 32'h0000_0000;
            sweep_sip_max_q      <= 32'h0000_0000;
            sweep_sip_step_q     <= 32'h0000_0001;
            sweep_dip_min_q      <= 32'h0000_0000;
            sweep_dip_max_q      <= 32'h0000_0000;
            sweep_dip_step_q     <= 32'h0000_0000;
            sweep_sport_min_q    <= 32'h0000_0000;
            sweep_sport_max_q    <= 32'h0000_0000;
            sweep_sport_step_q   <= 32'h0000_0000;
            sweep_dport_min_q    <= 32'h0000_0000;
            sweep_dport_max_q    <= 32'h0000_0000;
            sweep_dport_step_q   <= 32'h0000_0000;
            sweep_size_min_q     <= 32'h0000_0040;
            sweep_size_max_q     <= 32'h0000_0040;
            sweep_size_step_q    <= 32'h0000_0000;

            rate_mode_q          <= 32'h0000_0000;
            rate_line_percent_q  <= 32'h0000_0064;
            rate_ifg_bytes_q     <= 32'h0000_000C;
            output_opts_q        <= 32'h0000_0003;
            packet_count_q       <= 32'h0000_0000;
            seed_q               <= 32'h0000_0000;
        end else begin
            unique case (w_state_q)
                W_IDLE: begin
                    if (s_axil_awvalid && s_axil_wvalid) begin
                        wr_ok_q  <= is_valid_addr(s_axil_awaddr);
                        // Only apply the write if the address is writable.
                        // Writes to RO addresses complete with OKAY but
                        // change no state. Writes to unmapped addresses
                        // return SLVERR and change no state.
                        if (is_writable(s_axil_awaddr)) begin
                            unique case (s_axil_awaddr)
                                A_CTRL: begin
                                    // RW1S: only set bits corresponding to 1s
                                    // in wdata within strobed bytes. Trigger
                                    // pulses stay high for one cycle.
                                    if (s_axil_wstrb[0]) begin
                                        if (s_axil_wdata[0]) ctrl_start_q    <= 1'b1;
                                        if (s_axil_wdata[1]) ctrl_stop_q     <= 1'b1;
                                        if (s_axil_wdata[2]) ctrl_one_shot_q <= 1'b1;
                                    end
                                end
                                A_FRAME_TYPE:        frame_type_q        <= apply_wstrb(frame_type_q,        s_axil_wdata, s_axil_wstrb);
                                A_ETH_SRC_MAC_LO:    eth_src_lo_q        <= apply_wstrb(eth_src_lo_q,        s_axil_wdata, s_axil_wstrb);
                                A_ETH_SRC_MAC_HI:    eth_src_hi_q        <= apply_wstrb(eth_src_hi_q,        s_axil_wdata, s_axil_wstrb);
                                A_ETH_DST_MAC_LO:    eth_dst_lo_q        <= apply_wstrb(eth_dst_lo_q,        s_axil_wdata, s_axil_wstrb);
                                A_ETH_DST_MAC_HI:    eth_dst_hi_q        <= apply_wstrb(eth_dst_hi_q,        s_axil_wdata, s_axil_wstrb);
                                A_ETH_VLAN:          eth_vlan_q          <= apply_wstrb(eth_vlan_q,          s_axil_wdata, s_axil_wstrb);
                                A_IP_SRC:            ip_src_q            <= apply_wstrb(ip_src_q,            s_axil_wdata, s_axil_wstrb);
                                A_IP_DST:            ip_dst_q            <= apply_wstrb(ip_dst_q,            s_axil_wdata, s_axil_wstrb);
                                A_IP_MISC:           ip_misc_q           <= apply_wstrb(ip_misc_q,           s_axil_wdata, s_axil_wstrb);
                                A_TRANSPORT_PORTS:   transport_ports_q   <= apply_wstrb(transport_ports_q,   s_axil_wdata, s_axil_wstrb);
                                A_ROCE_OP_PKEY:      roce_op_pkey_q      <= apply_wstrb(roce_op_pkey_q,      s_axil_wdata, s_axil_wstrb);
                                A_ROCE_DEST_QP:      roce_dest_qp_q      <= apply_wstrb(roce_dest_qp_q,      s_axil_wdata, s_axil_wstrb);
                                A_ROCE_PSN_ACK:      roce_psn_ack_q      <= apply_wstrb(roce_psn_ack_q,      s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SIP_MIN:     sweep_sip_min_q     <= apply_wstrb(sweep_sip_min_q,     s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SIP_MAX:     sweep_sip_max_q     <= apply_wstrb(sweep_sip_max_q,     s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SIP_STEP:    sweep_sip_step_q    <= apply_wstrb(sweep_sip_step_q,    s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_DIP_MIN:     sweep_dip_min_q     <= apply_wstrb(sweep_dip_min_q,     s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_DIP_MAX:     sweep_dip_max_q     <= apply_wstrb(sweep_dip_max_q,     s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_DIP_STEP:    sweep_dip_step_q    <= apply_wstrb(sweep_dip_step_q,    s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SPORT_MIN:   sweep_sport_min_q   <= apply_wstrb(sweep_sport_min_q,   s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SPORT_MAX:   sweep_sport_max_q   <= apply_wstrb(sweep_sport_max_q,   s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SPORT_STEP:  sweep_sport_step_q  <= apply_wstrb(sweep_sport_step_q,  s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_DPORT_MIN:   sweep_dport_min_q   <= apply_wstrb(sweep_dport_min_q,   s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_DPORT_MAX:   sweep_dport_max_q   <= apply_wstrb(sweep_dport_max_q,   s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_DPORT_STEP:  sweep_dport_step_q  <= apply_wstrb(sweep_dport_step_q,  s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SIZE_MIN:    sweep_size_min_q    <= apply_wstrb(sweep_size_min_q,    s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SIZE_MAX:    sweep_size_max_q    <= apply_wstrb(sweep_size_max_q,    s_axil_wdata, s_axil_wstrb);
                                A_SWEEP_SIZE_STEP:   sweep_size_step_q   <= apply_wstrb(sweep_size_step_q,   s_axil_wdata, s_axil_wstrb);
                                A_RATE_MODE:         rate_mode_q         <= apply_wstrb(rate_mode_q,         s_axil_wdata, s_axil_wstrb);
                                A_RATE_LINE_PERCENT: rate_line_percent_q <= apply_wstrb(rate_line_percent_q, s_axil_wdata, s_axil_wstrb);
                                A_RATE_IFG_BYTES:    rate_ifg_bytes_q    <= apply_wstrb(rate_ifg_bytes_q,    s_axil_wdata, s_axil_wstrb);
                                A_OUTPUT_OPTS:       output_opts_q       <= apply_wstrb(output_opts_q,       s_axil_wdata, s_axil_wstrb);
                                A_PACKET_COUNT:      packet_count_q      <= apply_wstrb(packet_count_q,      s_axil_wdata, s_axil_wstrb);
                                A_SEED:              seed_q              <= apply_wstrb(seed_q,              s_axil_wdata, s_axil_wstrb);
                                default: ; // unreachable given is_writable check
                            endcase
                        end
                        w_state_q <= W_RESP;
                    end
                end
                W_RESP: begin
                    if (s_axil_bready) begin
                        w_state_q <= W_IDLE;
                    end
                end
                default: w_state_q <= W_IDLE;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Read channel
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {R_IDLE, R_RESP} r_state_t;
    r_state_t r_state_q;
    logic        rd_ok_q;
    logic [31:0] rdata_q;

    assign s_axil_arready = (r_state_q == R_IDLE);
    assign s_axil_rvalid  = (r_state_q == R_RESP);
    assign s_axil_rdata   = rdata_q;
    assign s_axil_rresp   = rd_ok_q ? 2'b00 : 2'b10;

    function automatic logic [31:0] read_mux(input logic [7:0] a);
        unique case (a)
            A_ID:                return ID_MAGIC;
            A_VERSION:           return VERSION;
            A_CTRL:              return 32'h0000_0000;                         // pulses read as 0
            A_STATUS:            return {29'h0, status_error_i, status_done_i, status_running_i};
            A_FRAME_TYPE:        return frame_type_q;
            A_ETH_SRC_MAC_LO:    return eth_src_lo_q;
            A_ETH_SRC_MAC_HI:    return eth_src_hi_q;
            A_ETH_DST_MAC_LO:    return eth_dst_lo_q;
            A_ETH_DST_MAC_HI:    return eth_dst_hi_q;
            A_ETH_VLAN:          return eth_vlan_q;
            A_IP_SRC:            return ip_src_q;
            A_IP_DST:            return ip_dst_q;
            A_IP_MISC:           return ip_misc_q;
            A_TRANSPORT_PORTS:   return transport_ports_q;
            A_ROCE_OP_PKEY:      return roce_op_pkey_q;
            A_ROCE_DEST_QP:      return roce_dest_qp_q;
            A_ROCE_PSN_ACK:      return roce_psn_ack_q;
            A_SWEEP_SIP_MIN:     return sweep_sip_min_q;
            A_SWEEP_SIP_MAX:     return sweep_sip_max_q;
            A_SWEEP_SIP_STEP:    return sweep_sip_step_q;
            A_SWEEP_DIP_MIN:     return sweep_dip_min_q;
            A_SWEEP_DIP_MAX:     return sweep_dip_max_q;
            A_SWEEP_DIP_STEP:    return sweep_dip_step_q;
            A_SWEEP_SPORT_MIN:   return sweep_sport_min_q;
            A_SWEEP_SPORT_MAX:   return sweep_sport_max_q;
            A_SWEEP_SPORT_STEP:  return sweep_sport_step_q;
            A_SWEEP_DPORT_MIN:   return sweep_dport_min_q;
            A_SWEEP_DPORT_MAX:   return sweep_dport_max_q;
            A_SWEEP_DPORT_STEP:  return sweep_dport_step_q;
            A_SWEEP_SIZE_MIN:    return sweep_size_min_q;
            A_SWEEP_SIZE_MAX:    return sweep_size_max_q;
            A_SWEEP_SIZE_STEP:   return sweep_size_step_q;
            A_RATE_MODE:         return rate_mode_q;
            A_RATE_LINE_PERCENT: return rate_line_percent_q;
            A_RATE_IFG_BYTES:    return rate_ifg_bytes_q;
            A_OUTPUT_OPTS:       return output_opts_q;
            A_PACKET_COUNT:      return packet_count_q;
            A_SEED:              return seed_q;
            A_PACKETS_SENT:      return packets_sent_i;
            default:             return 32'h0000_0000;
        endcase
    endfunction

    always_ff @(posedge clk) begin
        if (rst) begin
            r_state_q <= R_IDLE;
            rd_ok_q   <= 1'b0;
            rdata_q   <= 32'h0000_0000;
        end else begin
            unique case (r_state_q)
                R_IDLE: begin
                    if (s_axil_arvalid) begin
                        rd_ok_q   <= is_valid_addr(s_axil_araddr);
                        rdata_q   <= is_valid_addr(s_axil_araddr)
                                        ? read_mux(s_axil_araddr)
                                        : 32'h0000_0000;
                        r_state_q <= R_RESP;
                    end
                end
                R_RESP: begin
                    if (s_axil_rready) begin
                        r_state_q <= R_IDLE;
                    end
                end
                default: r_state_q <= R_IDLE;
            endcase
        end
    end

endmodule
