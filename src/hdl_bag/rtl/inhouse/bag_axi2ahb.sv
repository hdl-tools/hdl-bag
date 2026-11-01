// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi2ahb: AXI4 -> AHB-Lite bridge (an AXI4-Lite upstream connects through the AXI4
// ports: the instance ties AxLEN = 0, AxSIZE = the full width, INCR and WLAST = 1).
// Inside the bridge (D10) the path runs from the upstream to the downstream domain:
//   axi_dw_converter (when the widths differ) -> axi_fifo (FifoDepth > 0) ->
//   axi_cdc (AsyncCdc) -> bag_axi2ahb_core (IDs serialized, bursts mapped,
//   AddrOffset subtracted).
// Width conversion and the FIFO run in the upstream clock; the CDC hands over to the
// downstream clock. See bag_axi2ahb_core for the AXI to AHB mapping.
//
// Integration notes:
// * CDC (AsyncCdc): both domains must be reset together and leave reset in their own
//   clocks (CLK-CDC-RESET checks the reset style). axi_cdc's gray-coded FIFOs need
//   max-delay constraints on their pointer and data paths (no SDC is generated in v1).
// * The core registers its AHB outputs and its AXI responses, but AWREADY/ARREADY/WREADY
//   are combinational from the valids when there is no FIFO or CDC in front.
// * PULP axi_dw_converter limits apply when the widths differ: WRAP bursts and
//   non-modifiable bursts that need splitting are answered with SLVERR.

`include "axi/typedef.svh"

module bag_axi2ahb #(
    parameter int unsigned UpAddrWidth   = 32,
    parameter int unsigned DownAddrWidth = 32,
    parameter int unsigned UpDataWidth   = 64,
    parameter int unsigned DownDataWidth = 32,  // AHB data width
    parameter int unsigned IdWidth       = 1,
    parameter int unsigned UserWidth     = 0,
    parameter int unsigned MaxTrans      = 4,
    parameter int unsigned FifoDepth     = 0,  // Bridges > FIFO depth
    parameter bit          AsyncCdc      = 1'b0,  // fabrics in different domains (D10)
    // Subtracted from upstream addresses: the window base for pass-through N, else 0.
    parameter logic [UpAddrWidth-1:0] AddrOffset = '0,
    localparam int unsigned AddrWidth = (UpAddrWidth > DownAddrWidth) ? UpAddrWidth
                                                                    : DownAddrWidth,
    localparam int unsigned UW = (UserWidth > 0) ? UserWidth : 1
) (
    input  logic up_clk_i,
    input  logic up_rst_ni,
    input  logic down_clk_i,  // = up_clk_i when AsyncCdc = 0
    input  logic down_rst_ni,

    // upstream: slave port of the upstream fabric
    input  logic [IdWidth-1:0] s_awid_i,
    input  logic [UpAddrWidth-1:0] s_awaddr_i,
    input  logic [8-1:0] s_awlen_i,
    input  logic [3-1:0] s_awsize_i,
    input  logic [2-1:0] s_awburst_i,
    input  logic s_awlock_i,
    input  logic [4-1:0] s_awcache_i,
    input  logic [3-1:0] s_awprot_i,
    input  logic [4-1:0] s_awqos_i,
    input  logic [4-1:0] s_awregion_i,
    input  logic [UW-1:0] s_awuser_i,
    input  logic s_awvalid_i,
    output logic s_awready_o,
    input  logic [UpDataWidth-1:0] s_wdata_i,
    input  logic [UpDataWidth/8-1:0] s_wstrb_i,
    input  logic s_wlast_i,
    input  logic [UW-1:0] s_wuser_i,
    input  logic s_wvalid_i,
    output logic s_wready_o,
    output logic [IdWidth-1:0] s_bid_o,
    output logic [2-1:0] s_bresp_o,
    output logic [UW-1:0] s_buser_o,
    output logic s_bvalid_o,
    input  logic s_bready_i,
    input  logic [IdWidth-1:0] s_arid_i,
    input  logic [UpAddrWidth-1:0] s_araddr_i,
    input  logic [8-1:0] s_arlen_i,
    input  logic [3-1:0] s_arsize_i,
    input  logic [2-1:0] s_arburst_i,
    input  logic s_arlock_i,
    input  logic [4-1:0] s_arcache_i,
    input  logic [3-1:0] s_arprot_i,
    input  logic [4-1:0] s_arqos_i,
    input  logic [4-1:0] s_arregion_i,
    input  logic [UW-1:0] s_aruser_i,
    input  logic s_arvalid_i,
    output logic s_arready_o,
    output logic [IdWidth-1:0] s_rid_o,
    output logic [UpDataWidth-1:0] s_rdata_o,
    output logic [2-1:0] s_rresp_o,
    output logic s_rlast_o,
    output logic [UW-1:0] s_ruser_o,
    output logic s_rvalid_o,
    input  logic s_rready_i,

    // downstream: master port of the downstream AHB-Lite fabric
    output logic [DownAddrWidth-1:0] m_haddr_o,
    output logic [3-1:0] m_hburst_o,
    output logic m_hmastlock_o,
    output logic [4-1:0] m_hprot_o,
    output logic m_hnonsec_o,
    output logic [3-1:0] m_hsize_o,
    output logic [2-1:0] m_htrans_o,
    output logic [DownDataWidth-1:0] m_hwdata_o,
    output logic m_hwrite_o,
    input  logic [DownDataWidth-1:0] m_hrdata_i,
    input  logic m_hready_i,
    input  logic m_hresp_i
);

  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [UpDataWidth-1:0] up_data_t;
  typedef logic [UpDataWidth/8-1:0] up_strb_t;
  typedef logic [DownDataWidth-1:0] dn_data_t;
  typedef logic [DownDataWidth/8-1:0] dn_strb_t;
  typedef logic [IdWidth-1:0] id_t;
  typedef logic [UW-1:0] user_t;
  // AXI4 at the upstream and at the downstream (AHB) data width.
  `AXI_TYPEDEF_ALL(full, addr_t, id_t, up_data_t, up_strb_t, user_t)
  `AXI_TYPEDEF_ALL(dn, addr_t, id_t, dn_data_t, dn_strb_t, user_t)

  full_req_t  full_req;
  full_resp_t full_resp;
  dn_req_t    dw_req, fifo_req, cdc_req;
  dn_resp_t   dw_resp, fifo_resp, cdc_resp;

  bag_axi_from_flat #(
      .AddrWidth   (UpAddrWidth),
      .DataWidth   (UpDataWidth),
      .IdWidth     (IdWidth),
      .UserWidth   (UW),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (full_req_t),
      .axi_resp_t  (full_resp_t)
  ) i_from_flat (
      .awid_i(s_awid_i),
      .awaddr_i(s_awaddr_i),
      .awlen_i(s_awlen_i),
      .awsize_i(s_awsize_i),
      .awburst_i(s_awburst_i),
      .awlock_i(s_awlock_i),
      .awcache_i(s_awcache_i),
      .awprot_i(s_awprot_i),
      .awqos_i(s_awqos_i),
      .awregion_i(s_awregion_i),
      .awuser_i(s_awuser_i),
      .awvalid_i(s_awvalid_i),
      .awready_o(s_awready_o),
      .wdata_i(s_wdata_i),
      .wstrb_i(s_wstrb_i),
      .wlast_i(s_wlast_i),
      .wuser_i(s_wuser_i),
      .wvalid_i(s_wvalid_i),
      .wready_o(s_wready_o),
      .bid_o(s_bid_o),
      .bresp_o(s_bresp_o),
      .buser_o(s_buser_o),
      .bvalid_o(s_bvalid_o),
      .bready_i(s_bready_i),
      .arid_i(s_arid_i),
      .araddr_i(s_araddr_i),
      .arlen_i(s_arlen_i),
      .arsize_i(s_arsize_i),
      .arburst_i(s_arburst_i),
      .arlock_i(s_arlock_i),
      .arcache_i(s_arcache_i),
      .arprot_i(s_arprot_i),
      .arqos_i(s_arqos_i),
      .arregion_i(s_arregion_i),
      .aruser_i(s_aruser_i),
      .arvalid_i(s_arvalid_i),
      .arready_o(s_arready_o),
      .rid_o(s_rid_o),
      .rdata_o(s_rdata_o),
      .rresp_o(s_rresp_o),
      .rlast_o(s_rlast_o),
      .ruser_o(s_ruser_o),
      .rvalid_o(s_rvalid_o),
      .rready_i(s_rready_i),
      .req_o (full_req),
      .resp_i(full_resp)
  );

  if (UpDataWidth != DownDataWidth) begin : gen_dw
    axi_dw_converter #(
        .AxiMaxReads        (MaxTrans),
        .AxiSlvPortDataWidth(UpDataWidth),
        .AxiMstPortDataWidth(DownDataWidth),
        .AxiAddrWidth       (AddrWidth),
        .AxiIdWidth         (IdWidth),
        .aw_chan_t          (full_aw_chan_t),
        .mst_w_chan_t       (dn_w_chan_t),
        .slv_w_chan_t       (full_w_chan_t),
        .b_chan_t           (full_b_chan_t),
        .ar_chan_t          (full_ar_chan_t),
        .mst_r_chan_t       (dn_r_chan_t),
        .slv_r_chan_t       (full_r_chan_t),
        .axi_mst_req_t      (dn_req_t),
        .axi_mst_resp_t     (dn_resp_t),
        .axi_slv_req_t      (full_req_t),
        .axi_slv_resp_t     (full_resp_t)
    ) i_dw (
        .clk_i     (up_clk_i),
        .rst_ni    (up_rst_ni),
        .slv_req_i (full_req),
        .slv_resp_o(full_resp),
        .mst_req_o (dw_req),
        .mst_resp_i(dw_resp)
    );
  end else begin : gen_no_dw
    assign dw_req    = full_req;
    assign full_resp = dw_resp;
  end

  if (FifoDepth > 0) begin : gen_fifo
    axi_fifo #(
        .Depth      (FifoDepth),
        .FallThrough(1'b0),
        .aw_chan_t  (dn_aw_chan_t),
        .w_chan_t   (dn_w_chan_t),
        .b_chan_t   (dn_b_chan_t),
        .ar_chan_t  (dn_ar_chan_t),
        .r_chan_t   (dn_r_chan_t),
        .axi_req_t  (dn_req_t),
        .axi_resp_t (dn_resp_t)
    ) i_fifo (
        .clk_i     (up_clk_i),
        .rst_ni    (up_rst_ni),
        .test_i    (1'b0),
        .slv_req_i (dw_req),
        .slv_resp_o(dw_resp),
        .mst_req_o (fifo_req),
        .mst_resp_i(fifo_resp)
    );
  end else begin : gen_no_fifo
    assign fifo_req = dw_req;
    assign dw_resp  = fifo_resp;
  end

  if (AsyncCdc) begin : gen_cdc
    axi_cdc #(
        .aw_chan_t (dn_aw_chan_t),
        .w_chan_t  (dn_w_chan_t),
        .b_chan_t  (dn_b_chan_t),
        .ar_chan_t (dn_ar_chan_t),
        .r_chan_t  (dn_r_chan_t),
        .axi_req_t (dn_req_t),
        .axi_resp_t(dn_resp_t),
        .LogDepth  (2),
        .SyncStages(2)
    ) i_cdc (
        .src_clk_i (up_clk_i),
        .src_rst_ni(up_rst_ni),
        .src_req_i (fifo_req),
        .src_resp_o(fifo_resp),
        .dst_clk_i (down_clk_i),
        .dst_rst_ni(down_rst_ni),
        .dst_req_o (cdc_req),
        .dst_resp_i(cdc_resp)
    );
  end else begin : gen_no_cdc
    assign cdc_req   = fifo_req;
    assign fifo_resp = cdc_resp;
  end

  // Without width conversion, FIFO or CDC nothing runs in the upstream clock.
  if (UpDataWidth == DownDataWidth && FifoDepth == 0 && !AsyncCdc) begin : gen_no_up_clk
    logic unused_up;
    assign unused_up = ^{up_clk_i, up_rst_ni};
  end

  bag_axi2ahb_core #(
      .AddrWidth (AddrWidth),
      .HAddrWidth(DownAddrWidth),
      .DataWidth (DownDataWidth),
      .IdWidth   (IdWidth),
      .AddrOffset(AddrWidth'(AddrOffset)),
      .axi_req_t (dn_req_t),
      .axi_resp_t(dn_resp_t)
  ) i_core (
      .clk_i      (down_clk_i),
      .rst_ni     (down_rst_ni),
      .slv_req_i  (cdc_req),
      .slv_resp_o (cdc_resp),
      .haddr_o    (m_haddr_o),
      .hburst_o   (m_hburst_o),
      .hmastlock_o(m_hmastlock_o),
      .hprot_o    (m_hprot_o),
      .hnonsec_o  (m_hnonsec_o),
      .hsize_o    (m_hsize_o),
      .htrans_o   (m_htrans_o),
      .hwdata_o   (m_hwdata_o),
      .hwrite_o   (m_hwrite_o),
      .hrdata_i   (m_hrdata_i),
      .hready_i   (m_hready_i),
      .hresp_i    (m_hresp_i)
  );

endmodule : bag_axi2ahb
