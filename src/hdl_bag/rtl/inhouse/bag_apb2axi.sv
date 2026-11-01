// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// APB3/APB4 slave to AXI4/AXI4-Lite master bridge.
//
// * One APB transfer at a time becomes one single-beat AXI transfer (ID 0, AxLEN 0).
// * Width (D10): the APB bus is never wider than the AXI bus (APB <= 32 bits, AXI >= 32),
//   so a transfer is one narrow AXI transfer of the APB width on the byte lanes of its
//   address. PSTRB gives WSTRB (APB3 has no PSTRB: the instance ties it to all ones).
// * AxPROT = PPROT (the same encoding); AxCACHE = 4'b0011 (normal, non-cacheable,
//   bufferable: the tie default of an AXI master without cache information).
// * SLVERR or DECERR becomes PSLVERR.
// * Address: AddrOffset is subtracted (pass-through N); 0 for pass-through Y.
// * Clocks (D10): the two sides talk through a 4-phase req/ack handshake (bag_hs_apb_slv);
//   with AsyncCdc = 1 req and ack pass through bag_sync. Reset: prst_ni and arst_ni must
//   assert together (one reset event for both sides).
// * Limits, checked at elaboration: PDataWidth <= ADataWidth, and AddrOffset aligned to
//   the AXI data width.

module bag_apb2axi #(
    parameter int unsigned           PAddrWidth = 32,
    parameter int unsigned           PDataWidth = 32,
    parameter int unsigned           AAddrWidth = 32,
    parameter int unsigned           ADataWidth = 32,
    parameter logic [PAddrWidth-1:0] AddrOffset = '0,
    parameter bit                    AsyncCdc   = 1'b0,
    parameter int unsigned           SyncStages = 2,

    localparam int unsigned PStrbWidth = PDataWidth / 8,
    localparam int unsigned AStrbWidth = ADataWidth / 8
) (
    // APB slave
    input  logic                  pclk_i,
    input  logic                  prst_ni,
    input  logic                  psel_i,
    input  logic                  penable_i,
    input  logic                  pwrite_i,
    input  logic [PAddrWidth-1:0] paddr_i,
    input  logic [           2:0] pprot_i,
    input  logic [PDataWidth-1:0] pwdata_i,
    input  logic [PStrbWidth-1:0] pstrb_i,
    output logic                  pready_o,
    output logic [PDataWidth-1:0] prdata_o,
    output logic                  pslverr_o,

    // AXI4 master (ID width 1); an AXI4-Lite instance uses the Lite subset
    input  logic                  aclk_i,  // = pclk_i when AsyncCdc = 0
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

  if (PDataWidth > ADataWidth) begin : gen_width_check
    $error("bag_apb2axi: need PDataWidth <= ADataWidth");
  end

  localparam logic [2:0] PSize = 3'(unsigned'($clog2(PDataWidth / 8)));

  logic                  req, write, ack, err;
  logic [PAddrWidth-1:0] addr;
  logic [PDataWidth-1:0] wdata, rdata;
  logic [PStrbWidth-1:0] strb;
  logic [           2:0] prot;

  bag_hs_apb_slv #(
      .AddrWidth (PAddrWidth),
      .DataWidth (PDataWidth),
      .AsyncCdc  (AsyncCdc),
      .SyncStages(SyncStages)
  ) u_apb (
      .pclk_i     (pclk_i),
      .prst_ni    (prst_ni),
      .psel_i     (psel_i),
      .penable_i  (penable_i),
      .pwrite_i   (pwrite_i),
      .paddr_i    (paddr_i),
      .pprot_i    (pprot_i),
      .pwdata_i   (pwdata_i),
      .pstrb_i    (pstrb_i),
      .pready_o   (pready_o),
      .prdata_o   (prdata_o),
      .pslverr_o  (pslverr_o),
      .dst_clk_i  (aclk_i),
      .dst_rst_ni (arst_ni),
      .dst_req_o  (req),
      .dst_addr_o (addr),
      .dst_write_o(write),
      .dst_wdata_o(wdata),
      .dst_strb_o (strb),
      .dst_prot_o (prot),
      .dst_ack_i  (ack),
      .dst_rdata_i(rdata),
      .dst_err_i  (err)
  );

  bag_hs_axi_mst #(
      .InAddrWidth (PAddrWidth),
      .InDataWidth (PDataWidth),
      .AxiAddrWidth(AAddrWidth),
      .AxiDataWidth(ADataWidth),
      .AddrOffset  (AddrOffset)
  ) u_axi (
      .clk_i       (aclk_i),
      .rst_ni      (arst_ni),
      .req_i       (req),
      .addr_i      (addr & ~PAddrWidth'(PDataWidth / 8 - 1)),
      .write_i     (write),
      .wdata_i     (wdata),
      .strb_i      (strb),
      .size_i      (PSize),
      .prot_i      (prot),
      .cache_i     (4'b0011),
      .ack_o       (ack),
      .rdata_o     (rdata),
      .err_o       (err),
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
