// pktforge top-level integration.
//
// Wraps the AXI-Lite regfile, the RoCEv2 header/payload builder, and the
// ICRC + FCS trailer appenders. Provides a single AXI-Lite subordinate for
// configuration/control and a single AXI-Stream master for on-wire frames.
//
// Datapath:
//   pktforge_hdr_builder → pktforge_icrc_appender → pktforge_fcs_appender
//                                                              ↓
//                                                           m_axis_*
//
// Both trailer appenders are unconditionally in the datapath in this
// revision (matches the regfile's OUTPUT_OPTS reset value 0x3 = both on).
// Runtime bypass driven by OUTPUT_OPTS bits is a follow-up.
//
// Packet controller: on CTRL.START (one-cycle pulse from the regfile), the
// controller emits `packet_count` packets and then asserts STATUS.DONE. If
// `packet_count == 0` the controller emits indefinitely until CTRL.STOP.
// PACKETS_SENT counts every packet accepted by hdr_builder and is exposed
// via the regfile.

module pktforge_top (
    input  logic         clk,
    input  logic         rst,

    // AXI-Lite subordinate (from host)
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

    // AXI-Stream master (final on-wire bytes)
    output logic [31:0]  m_axis_tdata,
    output logic [3:0]   m_axis_tkeep,
    output logic         m_axis_tvalid,
    input  logic         m_axis_tready,
    output logic         m_axis_tlast
);

    // ------------------------------------------------------------------
    // Regfile ↔ datapath signals
    // ------------------------------------------------------------------
    logic         ctrl_start;
    logic         ctrl_stop;
    /* verilator lint_off UNUSEDSIGNAL */
    logic         ctrl_one_shot;
    logic [31:0]  frame_type;
    logic [31:0]  eth_vlan;
    logic [31:0]  rate_mode;
    logic [31:0]  rate_line_percent;
    logic [31:0]  rate_ifg_bytes;
    logic [31:0]  seed;
    logic [31:0]  output_opts;    // only [1:0] consumed in this revision
    /* verilator lint_on UNUSEDSIGNAL */

    logic [47:0]  eth_src_mac, eth_dst_mac;
    logic [31:0]  ip_src, ip_dst, ip_misc;
    logic [31:0]  transport_ports;
    logic [31:0]  roce_op_pkey, roce_dest_qp, roce_psn_ack;
    logic [31:0]  sweep_sip_min, sweep_sip_max, sweep_sip_step;
    logic [31:0]  sweep_dip_min, sweep_dip_max, sweep_dip_step;
    logic [31:0]  sweep_sport_min, sweep_sport_max, sweep_sport_step;
    logic [31:0]  sweep_dport_min, sweep_dport_max, sweep_dport_step;
    logic [31:0]  sweep_size_min, sweep_size_max, sweep_size_step;
    logic [31:0]  packet_count;

    // Status back to regfile
    logic         status_running_c;
    logic         status_done_q;
    logic         status_error_c;
    logic [31:0]  packets_sent_q;

    assign status_error_c = 1'b0;

    pktforge_regfile_axil u_regfile (
        .clk                   (clk),
        .rst                   (rst),
        .s_axil_awaddr         (s_axil_awaddr),
        .s_axil_awvalid        (s_axil_awvalid),
        .s_axil_awready        (s_axil_awready),
        .s_axil_wdata          (s_axil_wdata),
        .s_axil_wstrb          (s_axil_wstrb),
        .s_axil_wvalid         (s_axil_wvalid),
        .s_axil_wready         (s_axil_wready),
        .s_axil_bresp          (s_axil_bresp),
        .s_axil_bvalid         (s_axil_bvalid),
        .s_axil_bready         (s_axil_bready),
        .s_axil_araddr         (s_axil_araddr),
        .s_axil_arvalid        (s_axil_arvalid),
        .s_axil_arready        (s_axil_arready),
        .s_axil_rdata          (s_axil_rdata),
        .s_axil_rresp          (s_axil_rresp),
        .s_axil_rvalid         (s_axil_rvalid),
        .s_axil_rready         (s_axil_rready),

        .ctrl_start_o          (ctrl_start),
        .ctrl_stop_o           (ctrl_stop),
        .ctrl_one_shot_o       (ctrl_one_shot),

        .frame_type_o          (frame_type),
        .eth_src_mac_o         (eth_src_mac),
        .eth_dst_mac_o         (eth_dst_mac),
        .eth_vlan_o            (eth_vlan),
        .ip_src_o              (ip_src),
        .ip_dst_o              (ip_dst),
        .ip_misc_o             (ip_misc),
        .transport_ports_o     (transport_ports),
        .roce_op_pkey_o        (roce_op_pkey),
        .roce_dest_qp_o        (roce_dest_qp),
        .roce_psn_ack_o        (roce_psn_ack),

        .sweep_sip_min_o       (sweep_sip_min),
        .sweep_sip_max_o       (sweep_sip_max),
        .sweep_sip_step_o      (sweep_sip_step),
        .sweep_dip_min_o       (sweep_dip_min),
        .sweep_dip_max_o       (sweep_dip_max),
        .sweep_dip_step_o      (sweep_dip_step),
        .sweep_sport_min_o     (sweep_sport_min),
        .sweep_sport_max_o     (sweep_sport_max),
        .sweep_sport_step_o    (sweep_sport_step),
        .sweep_dport_min_o     (sweep_dport_min),
        .sweep_dport_max_o     (sweep_dport_max),
        .sweep_dport_step_o    (sweep_dport_step),
        .sweep_size_min_o      (sweep_size_min),
        .sweep_size_max_o      (sweep_size_max),
        .sweep_size_step_o     (sweep_size_step),

        .rate_mode_o           (rate_mode),
        .rate_line_percent_o   (rate_line_percent),
        .rate_ifg_bytes_o      (rate_ifg_bytes),
        .output_opts_o         (output_opts),
        .packet_count_o        (packet_count),
        .seed_o                (seed),

        .status_running_i      (status_running_c),
        .status_done_i         (status_done_q),
        .status_error_i        (status_error_c),
        .packets_sent_i        (packets_sent_q)
    );

    // ------------------------------------------------------------------
    // Packet controller
    // ------------------------------------------------------------------
    // While `running_q` is high, pulse pkt_valid to hdr_builder whenever it
    // is ready. Each accepted trigger increments packets_sent_q. Stop when
    // packets_sent_q reaches packet_count_latched (unless packet_count = 0,
    // meaning "run forever" until CTRL.STOP).
    logic         running_q;
    logic [31:0]  packet_count_latched_q;
    logic         hdr_pkt_valid_c;
    logic         hdr_pkt_ready;

    assign status_running_c = running_q;

    always_ff @(posedge clk) begin
        if (rst) begin
            running_q              <= 1'b0;
            status_done_q          <= 1'b0;
            packet_count_latched_q <= 32'h0;
            packets_sent_q         <= 32'h0;
        end else begin
            if (ctrl_start) begin
                running_q              <= 1'b1;
                status_done_q          <= 1'b0;
                packets_sent_q         <= 32'h0;
                packet_count_latched_q <= packet_count;
            end else if (ctrl_stop) begin
                running_q     <= 1'b0;
                status_done_q <= 1'b1;
            end else if (running_q && hdr_pkt_valid_c && hdr_pkt_ready) begin
                // Packet handed off to hdr_builder this cycle.
                packets_sent_q <= packets_sent_q + 32'd1;
                if ((packet_count_latched_q != 32'h0) &&
                    (packets_sent_q + 32'd1 >= packet_count_latched_q)) begin
                    running_q     <= 1'b0;
                    status_done_q <= 1'b1;
                end
            end
        end
    end

    assign hdr_pkt_valid_c = running_q && hdr_pkt_ready;

    // ------------------------------------------------------------------
    // Datapath
    // ------------------------------------------------------------------
    logic [31:0] hdr_axis_tdata;
    logic [3:0]  hdr_axis_tkeep;
    logic        hdr_axis_tvalid;
    logic        hdr_axis_tready;
    logic        hdr_axis_tlast;

    pktforge_hdr_builder u_hdr_builder (
        .clk                (clk),
        .rst                (rst),
        .start_i            (ctrl_start),
        .pkt_valid_i        (hdr_pkt_valid_c),
        .pkt_ready_o        (hdr_pkt_ready),

        .eth_src_mac_i      (eth_src_mac),
        .eth_dst_mac_i      (eth_dst_mac),
        .ip_src_i           (ip_src),
        .ip_dst_i           (ip_dst),
        .ip_misc_i          (ip_misc),
        .transport_ports_i  (transport_ports),
        .roce_op_pkey_i     (roce_op_pkey),
        .roce_dest_qp_i     (roce_dest_qp),
        .roce_psn_ack_i     (roce_psn_ack),
        .sweep_sip_min_i    (sweep_sip_min),
        .sweep_sip_max_i    (sweep_sip_max),
        .sweep_sip_step_i   (sweep_sip_step),
        .sweep_dip_min_i    (sweep_dip_min),
        .sweep_dip_max_i    (sweep_dip_max),
        .sweep_dip_step_i   (sweep_dip_step),
        .sweep_sport_min_i  (sweep_sport_min),
        .sweep_sport_max_i  (sweep_sport_max),
        .sweep_sport_step_i (sweep_sport_step),
        .sweep_dport_min_i  (sweep_dport_min),
        .sweep_dport_max_i  (sweep_dport_max),
        .sweep_dport_step_i (sweep_dport_step),
        .sweep_size_min_i   (sweep_size_min),
        .sweep_size_max_i   (sweep_size_max),
        .sweep_size_step_i  (sweep_size_step),

        .append_icrc_i      (output_opts[1]),
        .append_fcs_i       (output_opts[0]),

        .m_axis_tdata       (hdr_axis_tdata),
        .m_axis_tkeep       (hdr_axis_tkeep),
        .m_axis_tvalid      (hdr_axis_tvalid),
        .m_axis_tready      (hdr_axis_tready),
        .m_axis_tlast       (hdr_axis_tlast)
    );

    logic [31:0] icrc_axis_tdata;
    logic [3:0]  icrc_axis_tkeep;
    logic        icrc_axis_tvalid;
    logic        icrc_axis_tready;
    logic        icrc_axis_tlast;

    pktforge_icrc_appender u_icrc (
        .clk           (clk),
        .rst           (rst),
        .s_axis_tdata  (hdr_axis_tdata),
        .s_axis_tkeep  (hdr_axis_tkeep),
        .s_axis_tvalid (hdr_axis_tvalid),
        .s_axis_tready (hdr_axis_tready),
        .s_axis_tlast  (hdr_axis_tlast),
        .m_axis_tdata  (icrc_axis_tdata),
        .m_axis_tkeep  (icrc_axis_tkeep),
        .m_axis_tvalid (icrc_axis_tvalid),
        .m_axis_tready (icrc_axis_tready),
        .m_axis_tlast  (icrc_axis_tlast)
    );

    pktforge_fcs_appender u_fcs (
        .clk           (clk),
        .rst           (rst),
        .s_axis_tdata  (icrc_axis_tdata),
        .s_axis_tkeep  (icrc_axis_tkeep),
        .s_axis_tvalid (icrc_axis_tvalid),
        .s_axis_tready (icrc_axis_tready),
        .s_axis_tlast  (icrc_axis_tlast),
        .m_axis_tdata  (m_axis_tdata),
        .m_axis_tkeep  (m_axis_tkeep),
        .m_axis_tvalid (m_axis_tvalid),
        .m_axis_tready (m_axis_tready),
        .m_axis_tlast  (m_axis_tlast)
    );

endmodule
