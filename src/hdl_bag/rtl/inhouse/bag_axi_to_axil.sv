// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi_to_axil: protocol adapter AXI4 -> AXI4-Lite around PULP axi_to_axi_lite
// (bursts split into single transfers; IDs reflected).

`include "axi/typedef.svh"

module bag_axi_to_axil #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned SlvDataWidth = 32,
    parameter int unsigned MstDataWidth = SlvDataWidth,
    parameter int unsigned SlvIdWidth   = 1,
    parameter int unsigned UserWidth    = 0,
    parameter int unsigned MaxTrans     = 4,
    localparam int unsigned UW = (UserWidth > 0) ? UserWidth : 1
) (
    input  logic clk_i,
    input  logic rst_ni,

    // slave port (towards the master)
    input  logic [SlvIdWidth-1:0] s_awid_i,
    input  logic [AddrWidth-1:0] s_awaddr_i,
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
    input  logic [SlvDataWidth-1:0] s_wdata_i,
    input  logic [SlvDataWidth/8-1:0] s_wstrb_i,
    input  logic s_wlast_i,
    input  logic [UW-1:0] s_wuser_i,
    input  logic s_wvalid_i,
    output logic s_wready_o,
    output logic [SlvIdWidth-1:0] s_bid_o,
    output logic [2-1:0] s_bresp_o,
    output logic [UW-1:0] s_buser_o,
    output logic s_bvalid_o,
    input  logic s_bready_i,
    input  logic [SlvIdWidth-1:0] s_arid_i,
    input  logic [AddrWidth-1:0] s_araddr_i,
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
    output logic [SlvIdWidth-1:0] s_rid_o,
    output logic [SlvDataWidth-1:0] s_rdata_o,
    output logic [2-1:0] s_rresp_o,
    output logic s_rlast_o,
    output logic [UW-1:0] s_ruser_o,
    output logic s_rvalid_o,
    input  logic s_rready_i,
    // master port (towards the slave)
    output logic [AddrWidth-1:0] m_awaddr_o,
    output logic [3-1:0] m_awprot_o,
    output logic m_awvalid_o,
    input  logic m_awready_i,
    output logic [MstDataWidth-1:0] m_wdata_o,
    output logic [MstDataWidth/8-1:0] m_wstrb_o,
    output logic m_wvalid_o,
    input  logic m_wready_i,
    input  logic [2-1:0] m_bresp_i,
    input  logic m_bvalid_i,
    output logic m_bready_o,
    output logic [AddrWidth-1:0] m_araddr_o,
    output logic [3-1:0] m_arprot_o,
    output logic m_arvalid_o,
    input  logic m_arready_i,
    input  logic [MstDataWidth-1:0] m_rdata_i,
    input  logic [2-1:0] m_rresp_i,
    input  logic m_rvalid_i,
    output logic m_rready_o
);

  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [UW-1:0] user_t;
  typedef logic [SlvIdWidth-1:0] slv_id_t;
  typedef logic [SlvDataWidth-1:0] slv_data_t;
  typedef logic [SlvDataWidth/8-1:0] slv_strb_t;
  typedef logic [MstDataWidth-1:0] mst_data_t;
  typedef logic [MstDataWidth/8-1:0] mst_strb_t;
  `AXI_TYPEDEF_ALL(slv, addr_t, slv_id_t, slv_data_t, slv_strb_t, user_t)
  `AXI_LITE_TYPEDEF_ALL(mst, addr_t, mst_data_t, mst_strb_t)

  if (SlvDataWidth != MstDataWidth) begin : gen_bad_data_width
    $fatal(1, "bag_axi_to_axil needs the same data width on both sides");
  end

  slv_req_t  slv_req;
  slv_resp_t slv_resp;
  mst_req_t  mst_req;
  mst_resp_t mst_resp;

  bag_axi_from_flat #(
      .AddrWidth   (AddrWidth),
      .DataWidth   (SlvDataWidth),
      .IdWidth     (SlvIdWidth),
      .UserWidth   (UW),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (slv_req_t),
      .axi_resp_t  (slv_resp_t)
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
      .req_o (slv_req),
      .resp_i(slv_resp)
  );

  axi_to_axi_lite #(
      .AxiAddrWidth   (AddrWidth),
      .AxiDataWidth   (SlvDataWidth),
      .AxiIdWidth     (SlvIdWidth),
      .AxiUserWidth   (UW),
      .AxiMaxWriteTxns(MaxTrans),
      .AxiMaxReadTxns (MaxTrans),
      .FullBW         (1'b0),
      .FallThrough    (1'b0),
      .full_req_t     (slv_req_t),
      .full_resp_t    (slv_resp_t),
      .lite_req_t     (mst_req_t),
      .lite_resp_t    (mst_resp_t)
  ) i_pc (
      .clk_i,
      .rst_ni,
      .test_i    (1'b0),
      .slv_req_i (slv_req),
      .slv_resp_o(slv_resp),
      .mst_req_o (mst_req),
      .mst_resp_i(mst_resp)
  );

  bag_axil_to_flat #(
      .AddrWidth   (AddrWidth),
      .DataWidth   (MstDataWidth),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (mst_req_t),
      .axi_resp_t  (mst_resp_t)
  ) i_to_flat (
      .req_i (mst_req),
      .resp_o(mst_resp),
      .awaddr_o(m_awaddr_o),
      .awprot_o(m_awprot_o),
      .awvalid_o(m_awvalid_o),
      .awready_i(m_awready_i),
      .wdata_o(m_wdata_o),
      .wstrb_o(m_wstrb_o),
      .wvalid_o(m_wvalid_o),
      .wready_i(m_wready_i),
      .bresp_i(m_bresp_i),
      .bvalid_i(m_bvalid_i),
      .bready_o(m_bready_o),
      .araddr_o(m_araddr_o),
      .arprot_o(m_arprot_o),
      .arvalid_o(m_arvalid_o),
      .arready_i(m_arready_i),
      .rdata_i(m_rdata_i),
      .rresp_i(m_rresp_i),
      .rvalid_i(m_rvalid_i),
      .rready_o(m_rready_o)
  );

endmodule : bag_axi_to_axil
