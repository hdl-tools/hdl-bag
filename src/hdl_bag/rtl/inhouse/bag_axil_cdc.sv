// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_cdc: AXI4-Lite clock-domain crossing adapter around PULP axi_cdc (generic
// channel types). s_* is in the s_clk_i domain, m_* in the m_clk_i domain.

`include "axi/typedef.svh"

module bag_axil_cdc #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned SlvDataWidth = 32,
    parameter int unsigned MstDataWidth = SlvDataWidth,
    parameter int unsigned LogDepth     = 2,
    parameter int unsigned SyncStages   = 2
) (
    input  logic s_clk_i,
    input  logic s_rst_ni,
    input  logic m_clk_i,
    input  logic m_rst_ni,

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
  typedef logic [SlvDataWidth-1:0] slv_data_t;
  typedef logic [SlvDataWidth/8-1:0] slv_strb_t;
  typedef logic [MstDataWidth-1:0] mst_data_t;
  typedef logic [MstDataWidth/8-1:0] mst_strb_t;
  `AXI_LITE_TYPEDEF_ALL(slv, addr_t, slv_data_t, slv_strb_t)
  `AXI_LITE_TYPEDEF_ALL(mst, addr_t, mst_data_t, mst_strb_t)

  if (SlvDataWidth != MstDataWidth) begin : gen_bad_data_width
    $fatal(1, "bag_axil_cdc needs the same data width on both sides");
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

  axi_cdc #(
      .aw_chan_t (slv_aw_chan_t),
      .w_chan_t  (slv_w_chan_t),
      .b_chan_t  (slv_b_chan_t),
      .ar_chan_t (slv_ar_chan_t),
      .r_chan_t  (slv_r_chan_t),
      .axi_req_t (slv_req_t),
      .axi_resp_t(slv_resp_t),
      .LogDepth  (LogDepth),
      .SyncStages(SyncStages)
  ) i_cdc (
      .src_clk_i (s_clk_i),
      .src_rst_ni(s_rst_ni),
      .src_req_i (slv_req),
      .src_resp_o(slv_resp),
      .dst_clk_i (m_clk_i),
      .dst_rst_ni(m_rst_ni),
      .dst_req_o (mst_req),
      .dst_resp_i(mst_resp)
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

endmodule : bag_axil_cdc
