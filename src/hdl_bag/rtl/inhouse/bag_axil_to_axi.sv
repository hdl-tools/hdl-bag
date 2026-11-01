// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_to_axi: protocol adapter AXI4-Lite -> AXI4 around PULP axi_lite_to_axi
// (single beats, ID 0).

`include "axi/typedef.svh"

module bag_axil_to_axi #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned SlvDataWidth = 32,
    parameter int unsigned MstDataWidth = SlvDataWidth,
    parameter int unsigned MstIdWidth   = 1,
    parameter int unsigned UserWidth    = 0,
    localparam int unsigned UW = (UserWidth > 0) ? UserWidth : 1
) (
    // slave port (towards the master)
    input  logic [AddrWidth-1:0] s_awaddr_i,
    input  logic [3-1:0] s_awprot_i,
    input  logic s_awvalid_i,
    output logic s_awready_o,
    input  logic [SlvDataWidth-1:0] s_wdata_i,
    input  logic [SlvDataWidth/8-1:0] s_wstrb_i,
    input  logic s_wvalid_i,
    output logic s_wready_o,
    output logic [2-1:0] s_bresp_o,
    output logic s_bvalid_o,
    input  logic s_bready_i,
    input  logic [AddrWidth-1:0] s_araddr_i,
    input  logic [3-1:0] s_arprot_i,
    input  logic s_arvalid_i,
    output logic s_arready_o,
    output logic [SlvDataWidth-1:0] s_rdata_o,
    output logic [2-1:0] s_rresp_o,
    output logic s_rvalid_o,
    input  logic s_rready_i,
    // master port (towards the slave)
    output logic [MstIdWidth-1:0] m_awid_o,
    output logic [AddrWidth-1:0] m_awaddr_o,
    output logic [8-1:0] m_awlen_o,
    output logic [3-1:0] m_awsize_o,
    output logic [2-1:0] m_awburst_o,
    output logic m_awlock_o,
    output logic [4-1:0] m_awcache_o,
    output logic [3-1:0] m_awprot_o,
    output logic [4-1:0] m_awqos_o,
    output logic [4-1:0] m_awregion_o,
    output logic [UW-1:0] m_awuser_o,
    output logic m_awvalid_o,
    input  logic m_awready_i,
    output logic [MstDataWidth-1:0] m_wdata_o,
    output logic [MstDataWidth/8-1:0] m_wstrb_o,
    output logic m_wlast_o,
    output logic [UW-1:0] m_wuser_o,
    output logic m_wvalid_o,
    input  logic m_wready_i,
    input  logic [MstIdWidth-1:0] m_bid_i,
    input  logic [2-1:0] m_bresp_i,
    input  logic [UW-1:0] m_buser_i,
    input  logic m_bvalid_i,
    output logic m_bready_o,
    output logic [MstIdWidth-1:0] m_arid_o,
    output logic [AddrWidth-1:0] m_araddr_o,
    output logic [8-1:0] m_arlen_o,
    output logic [3-1:0] m_arsize_o,
    output logic [2-1:0] m_arburst_o,
    output logic m_arlock_o,
    output logic [4-1:0] m_arcache_o,
    output logic [3-1:0] m_arprot_o,
    output logic [4-1:0] m_arqos_o,
    output logic [4-1:0] m_arregion_o,
    output logic [UW-1:0] m_aruser_o,
    output logic m_arvalid_o,
    input  logic m_arready_i,
    input  logic [MstIdWidth-1:0] m_rid_i,
    input  logic [MstDataWidth-1:0] m_rdata_i,
    input  logic [2-1:0] m_rresp_i,
    input  logic m_rlast_i,
    input  logic [UW-1:0] m_ruser_i,
    input  logic m_rvalid_i,
    output logic m_rready_o
);

  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [UW-1:0] user_t;
  typedef logic [MstIdWidth-1:0] mst_id_t;
  typedef logic [SlvDataWidth-1:0] slv_data_t;
  typedef logic [SlvDataWidth/8-1:0] slv_strb_t;
  typedef logic [MstDataWidth-1:0] mst_data_t;
  typedef logic [MstDataWidth/8-1:0] mst_strb_t;
  `AXI_LITE_TYPEDEF_ALL(slv, addr_t, slv_data_t, slv_strb_t)
  `AXI_TYPEDEF_ALL(mst, addr_t, mst_id_t, mst_data_t, mst_strb_t, user_t)

  if (SlvDataWidth != MstDataWidth) begin : gen_bad_data_width
    $fatal(1, "bag_axil_to_axi needs the same data width on both sides");
  end

  slv_req_t  slv_req;
  slv_resp_t slv_resp;
  mst_req_t  mst_req;
  mst_resp_t mst_resp;

  bag_axil_from_flat #(
      .AddrWidth   (AddrWidth),
      .DataWidth   (SlvDataWidth),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (slv_req_t),
      .axi_resp_t  (slv_resp_t)
  ) i_from_flat (
      .awaddr_i(s_awaddr_i),
      .awprot_i(s_awprot_i),
      .awvalid_i(s_awvalid_i),
      .awready_o(s_awready_o),
      .wdata_i(s_wdata_i),
      .wstrb_i(s_wstrb_i),
      .wvalid_i(s_wvalid_i),
      .wready_o(s_wready_o),
      .bresp_o(s_bresp_o),
      .bvalid_o(s_bvalid_o),
      .bready_i(s_bready_i),
      .araddr_i(s_araddr_i),
      .arprot_i(s_arprot_i),
      .arvalid_i(s_arvalid_i),
      .arready_o(s_arready_o),
      .rdata_o(s_rdata_o),
      .rresp_o(s_rresp_o),
      .rvalid_o(s_rvalid_o),
      .rready_i(s_rready_i),
      .req_o (slv_req),
      .resp_i(slv_resp)
  );

  // AXI4-Lite has no AxCACHE: the AXI4 side carries Normal Non-cacheable Bufferable,
  // the generator's tie-default (protocols.TIE_DEFAULTS).
  axi_lite_to_axi #(
      .AxiDataWidth(SlvDataWidth),
      .req_lite_t  (slv_req_t),
      .resp_lite_t (slv_resp_t),
      .axi_req_t   (mst_req_t),
      .axi_resp_t  (mst_resp_t)
  ) i_pc (
      .slv_req_lite_i (slv_req),
      .slv_resp_lite_o(slv_resp),
      .slv_aw_cache_i (4'b0011),
      .slv_ar_cache_i (4'b0011),
      .mst_req_o      (mst_req),
      .mst_resp_i     (mst_resp)
  );

  bag_axi_to_flat #(
      .AddrWidth   (AddrWidth),
      .DataWidth   (MstDataWidth),
      .IdWidth     (MstIdWidth),
      .UserWidth   (UW),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (mst_req_t),
      .axi_resp_t  (mst_resp_t)
  ) i_to_flat (
      .req_i (mst_req),
      .resp_o(mst_resp),
      .awid_o(m_awid_o),
      .awaddr_o(m_awaddr_o),
      .awlen_o(m_awlen_o),
      .awsize_o(m_awsize_o),
      .awburst_o(m_awburst_o),
      .awlock_o(m_awlock_o),
      .awcache_o(m_awcache_o),
      .awprot_o(m_awprot_o),
      .awqos_o(m_awqos_o),
      .awregion_o(m_awregion_o),
      .awuser_o(m_awuser_o),
      .awvalid_o(m_awvalid_o),
      .awready_i(m_awready_i),
      .wdata_o(m_wdata_o),
      .wstrb_o(m_wstrb_o),
      .wlast_o(m_wlast_o),
      .wuser_o(m_wuser_o),
      .wvalid_o(m_wvalid_o),
      .wready_i(m_wready_i),
      .bid_i(m_bid_i),
      .bresp_i(m_bresp_i),
      .buser_i(m_buser_i),
      .bvalid_i(m_bvalid_i),
      .bready_o(m_bready_o),
      .arid_o(m_arid_o),
      .araddr_o(m_araddr_o),
      .arlen_o(m_arlen_o),
      .arsize_o(m_arsize_o),
      .arburst_o(m_arburst_o),
      .arlock_o(m_arlock_o),
      .arcache_o(m_arcache_o),
      .arprot_o(m_arprot_o),
      .arqos_o(m_arqos_o),
      .arregion_o(m_arregion_o),
      .aruser_o(m_aruser_o),
      .arvalid_o(m_arvalid_o),
      .arready_i(m_arready_i),
      .rid_i(m_rid_i),
      .rdata_i(m_rdata_i),
      .rresp_i(m_rresp_i),
      .rlast_i(m_rlast_i),
      .ruser_i(m_ruser_i),
      .rvalid_i(m_rvalid_i),
      .rready_o(m_rready_o)
  );

endmodule : bag_axil_to_axi
