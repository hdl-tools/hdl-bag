// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// AHB-Lite slave to AXI4/AXI4-Lite master bridge.
//
// * One outstanding transfer: each AHB beat (bursts included) becomes single-beat AXI
//   transfers with ID 0 (AXI ID width 1); HREADYOUT stays low until they respond.
// * Width (D10): a beat no wider than the AXI bus is one narrow AXI transfer on the byte
//   lanes of its address; a 64-bit beat into a 32-bit AXI bus becomes two transfers.
// * AxPROT = {instruction = !HPROT[0], non-secure = HNONSEC, privileged = HPROT[1]};
//   AxCACHE = {2'b00, modifiable = HPROT[3], bufferable = HPROT[2]}.
// * SLVERR or DECERR (on any part) becomes the two-cycle HRESP ERROR.
// * Address: AddrOffset is subtracted (pass-through N); 0 for pass-through Y.
// * Clocks (D10): the two sides talk through a 4-phase req/ack handshake; with
//   AsyncCdc = 1 req and ack pass through bag_sync. The payload is held stable while req
//   (or ack) is high, so it crosses without synchronizers (CDC/STA: constrain the payload
//   paths with a max-delay, data-path-only exception).
// * Reset: hrst_ni and arst_ni must assert together (one reset event for both sides).
// * Limits, checked at elaboration: AddrOffset aligned to both data widths.

module bag_ahb2axi #(
    parameter int unsigned           HAddrWidth = 32,
    parameter int unsigned           HDataWidth = 32,
    parameter int unsigned           AAddrWidth = 32,
    parameter int unsigned           ADataWidth = 32,
    parameter logic [HAddrWidth-1:0] AddrOffset = '0,
    parameter bit                    AsyncCdc   = 1'b0,
    parameter int unsigned           SyncStages = 2,

    localparam int unsigned AStrbWidth = ADataWidth / 8
) (
    // AHB-Lite slave
    input  logic                  hclk_i,
    input  logic                  hrst_ni,
    input  logic                  hsel_i,
    input  logic [HAddrWidth-1:0] haddr_i,
    input  logic [           2:0] hburst_i,
    input  logic [           3:0] hprot_i,
    input  logic                  hnonsec_i,
    input  logic [           2:0] hsize_i,
    input  logic [           1:0] htrans_i,
    input  logic [HDataWidth-1:0] hwdata_i,
    input  logic                  hwrite_i,
    input  logic                  hready_i,
    output logic [HDataWidth-1:0] hrdata_o,
    output logic                  hreadyout_o,
    output logic                  hresp_o,

    // AXI4 master (ID width 1); an AXI4-Lite instance uses the Lite subset
    input  logic                  aclk_i,  // = hclk_i when AsyncCdc = 0
    input  logic                  arst_ni,
    output logic                  m_awid_o,
    output logic [AAddrWidth-1:0] m_awaddr_o,
    output logic [           7:0] m_awlen_o,
    output logic [           2:0] m_awsize_o,
    output logic [           1:0] m_awburst_o,
    output logic                  m_awlock_o,
    output logic [           3:0] m_awcache_o,
    output logic [           2:0] m_awprot_o,
    output logic [           3:0] m_awqos_o,
    output logic [           3:0] m_awregion_o,
    output logic                  m_awvalid_o,
    input  logic                  m_awready_i,
    output logic [ADataWidth-1:0] m_wdata_o,
    output logic [AStrbWidth-1:0] m_wstrb_o,
    output logic                  m_wlast_o,
    output logic                  m_wvalid_o,
    input  logic                  m_wready_i,
    input  logic                  m_bid_i,
    input  logic [           1:0] m_bresp_i,
    input  logic                  m_bvalid_i,
    output logic                  m_bready_o,
    output logic                  m_arid_o,
    output logic [AAddrWidth-1:0] m_araddr_o,
    output logic [           7:0] m_arlen_o,
    output logic [           2:0] m_arsize_o,
    output logic [           1:0] m_arburst_o,
    output logic                  m_arlock_o,
    output logic [           3:0] m_arcache_o,
    output logic [           2:0] m_arprot_o,
    output logic [           3:0] m_arqos_o,
    output logic [           3:0] m_arregion_o,
    output logic                  m_arvalid_o,
    input  logic                  m_arready_i,
    input  logic                  m_rid_i,
    input  logic [ADataWidth-1:0] m_rdata_i,
    input  logic [           1:0] m_rresp_i,
    input  logic                  m_rlast_i,
    input  logic                  m_rvalid_i,
    output logic                  m_rready_o
);

  // ------------------------------------------------------------ payload (AHB domain)
  logic [HAddrWidth-1:0] req_addr_q;
  logic [           2:0] req_size_q;
  logic [           2:0] req_prot_q;
  logic [           3:0] req_cache_q;
  logic                  req_write_q;
  logic [HDataWidth-1:0] req_wdata_q;
  logic                  req_q;
  logic                  ack_s;

  // Response (AXI domain).
  logic [HDataWidth-1:0] rsp_rdata;
  logic                  rsp_err;
  logic                  ack;
  logic                  req_s;

  // ------------------------------------------------------------ handshake sync
  if (AsyncCdc) begin : gen_cdc
    bag_sync #(.Stages(SyncStages)) u_req_sync (
        .clk_i (aclk_i),
        .rst_ni(arst_ni),
        .d_i   (req_q),
        .q_o   (req_s)
    );
    bag_sync #(.Stages(SyncStages)) u_ack_sync (
        .clk_i (hclk_i),
        .rst_ni(hrst_ni),
        .d_i   (ack),
        .q_o   (ack_s)
    );
  end else begin : gen_no_cdc
    assign req_s = req_q;
    assign ack_s = ack;
  end

  // ------------------------------------------------------------ AHB side
  typedef enum logic [2:0] {
    AIdle,
    AReq,
    AWait,
    ADone,
    AErr1,
    AErr2
  } ahb_state_e;
  ahb_state_e a_q;

  logic                  accept;
  logic [HDataWidth-1:0] rdata_q;

  assign accept = hsel_i && hready_i && htrans_i[1] &&
                  (a_q == AIdle || a_q == ADone || a_q == AErr2);

  always_ff @(posedge hclk_i or negedge hrst_ni) begin
    if (!hrst_ni) begin
      a_q         <= AIdle;
      req_q       <= 1'b0;
      req_addr_q  <= '0;
      req_size_q  <= '0;
      req_prot_q  <= '0;
      req_cache_q <= '0;
      req_write_q <= 1'b0;
      req_wdata_q <= '0;
      rdata_q     <= '0;
    end else begin
      case (a_q)
        AReq: begin
          if (!ack_s) begin
            req_wdata_q <= hwdata_i;
            req_q       <= 1'b1;
            a_q         <= AWait;
          end
        end
        AWait: begin
          if (ack_s) begin
            req_q   <= 1'b0;
            rdata_q <= rsp_rdata;
            a_q     <= rsp_err ? AErr1 : ADone;
          end
        end
        AErr1: a_q <= AErr2;
        AIdle, ADone, AErr2: a_q <= AIdle;
        default: a_q <= AIdle;
      endcase
      if (accept) begin
        a_q         <= AReq;
        req_addr_q  <= haddr_i;
        req_size_q  <= hsize_i;
        req_prot_q  <= {!hprot_i[0], hnonsec_i, hprot_i[1]};
        req_cache_q <= {2'b00, hprot_i[3], hprot_i[2]};
        req_write_q <= hwrite_i;
      end
    end
  end

  // Not needed: every beat is a separate AXI transfer (HBURST, BUSY vs IDLE).
  logic unused;
  assign unused = ^{hburst_i, htrans_i[0]};

  assign hreadyout_o = !(a_q == AReq || a_q == AWait || a_q == AErr1);
  assign hresp_o     = a_q == AErr1 || a_q == AErr2;
  assign hrdata_o    = rdata_q;

  // ------------------------------------------------------------ AXI side
  bag_hs_axi_mst #(
      .InAddrWidth (HAddrWidth),
      .InDataWidth (HDataWidth),
      .AxiAddrWidth(AAddrWidth),
      .AxiDataWidth(ADataWidth),
      .AddrOffset  (AddrOffset)
  ) u_axi (
      .clk_i       (aclk_i),
      .rst_ni      (arst_ni),
      .req_i       (req_s),
      .addr_i      (req_addr_q),
      .write_i     (req_write_q),
      .wdata_i     (req_wdata_q),
      .strb_i      ('1),
      .size_i      (req_size_q),
      .prot_i      (req_prot_q),
      .cache_i     (req_cache_q),
      .ack_o       (ack),
      .rdata_o     (rsp_rdata),
      .err_o       (rsp_err),
      .m_awid_o    (m_awid_o),
      .m_awaddr_o  (m_awaddr_o),
      .m_awlen_o   (m_awlen_o),
      .m_awsize_o  (m_awsize_o),
      .m_awburst_o (m_awburst_o),
      .m_awlock_o  (m_awlock_o),
      .m_awcache_o (m_awcache_o),
      .m_awprot_o  (m_awprot_o),
      .m_awqos_o   (m_awqos_o),
      .m_awregion_o(m_awregion_o),
      .m_awvalid_o (m_awvalid_o),
      .m_awready_i (m_awready_i),
      .m_wdata_o   (m_wdata_o),
      .m_wstrb_o   (m_wstrb_o),
      .m_wlast_o   (m_wlast_o),
      .m_wvalid_o  (m_wvalid_o),
      .m_wready_i  (m_wready_i),
      .m_bid_i     (m_bid_i),
      .m_bresp_i   (m_bresp_i),
      .m_bvalid_i  (m_bvalid_i),
      .m_bready_o  (m_bready_o),
      .m_arid_o    (m_arid_o),
      .m_araddr_o  (m_araddr_o),
      .m_arlen_o   (m_arlen_o),
      .m_arsize_o  (m_arsize_o),
      .m_arburst_o (m_arburst_o),
      .m_arlock_o  (m_arlock_o),
      .m_arcache_o (m_arcache_o),
      .m_arprot_o  (m_arprot_o),
      .m_arqos_o   (m_arqos_o),
      .m_arregion_o(m_arregion_o),
      .m_arvalid_o (m_arvalid_o),
      .m_arready_i (m_arready_i),
      .m_rid_i     (m_rid_i),
      .m_rdata_i   (m_rdata_i),
      .m_rresp_i   (m_rresp_i),
      .m_rlast_i   (m_rlast_i),
      .m_rvalid_i  (m_rvalid_i),
      .m_rready_o  (m_rready_o)
  );

endmodule
