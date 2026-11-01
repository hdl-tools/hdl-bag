// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Smoke-test top for bag_axi2ahb (s_* = AXI master side, m_* = AHB slave side): the bridge
// is the only AHB master and the AHB slave model the only slave, so HSEL is high and HREADY
// is the slave's HREADYOUT. Lite = 1 ties the AXI4-only inputs as the instance template
// does for an AXI4-Lite upstream. With AsyncCdc = 0 both sides run from aclk_i.

module tb_axi2ahb #(
    parameter int unsigned ADataWidth = 32,
    parameter int unsigned HDataWidth = 32,
    parameter int unsigned IdWidth    = 4,
    parameter int unsigned FifoDepth  = 0,
    parameter bit          Lite       = 1'b0,
    parameter logic [31:0] AddrOffset = '0,
    parameter bit          AsyncCdc   = 1'b0
) (
    input logic aclk_i,
    input logic arst_ni,
    input logic hclk_i,
    input logic hrst_ni,

    input  logic [IdWidth-1:0]    s_awid,
    input  logic [31:0]           s_awaddr,
    input  logic [7:0]            s_awlen,
    input  logic [2:0]            s_awsize,
    input  logic [1:0]            s_awburst,
    input  logic                  s_awlock,
    input  logic [3:0]            s_awcache,
    input  logic [2:0]            s_awprot,
    input  logic [3:0]            s_awqos,
    input  logic [3:0]            s_awregion,
    input  logic                  s_awvalid,
    output logic                  s_awready,
    input  logic [ADataWidth-1:0] s_wdata,
    input  logic [ADataWidth/8-1:0] s_wstrb,
    input  logic                  s_wlast,
    input  logic                  s_wvalid,
    output logic                  s_wready,
    output logic [IdWidth-1:0]    s_bid,
    output logic [1:0]            s_bresp,
    output logic                  s_bvalid,
    input  logic                  s_bready,
    input  logic [IdWidth-1:0]    s_arid,
    input  logic [31:0]           s_araddr,
    input  logic [7:0]            s_arlen,
    input  logic [2:0]            s_arsize,
    input  logic [1:0]            s_arburst,
    input  logic                  s_arlock,
    input  logic [3:0]            s_arcache,
    input  logic [2:0]            s_arprot,
    input  logic [3:0]            s_arqos,
    input  logic [3:0]            s_arregion,
    input  logic                  s_arvalid,
    output logic                  s_arready,
    output logic [IdWidth-1:0]    s_rid,
    output logic [ADataWidth-1:0] s_rdata,
    output logic [1:0]            s_rresp,
    output logic                  s_rlast,
    output logic                  s_rvalid,
    input  logic                  s_rready,

    output logic [          31:0] m_haddr_o,
    output logic [           2:0] m_hburst_o,
    output logic [           3:0] m_hprot_o,
    output logic                  m_hnonsec_o,
    output logic [           2:0] m_hsize_o,
    output logic [           1:0] m_htrans_o,
    output logic [HDataWidth-1:0] m_hwdata_o,
    output logic                  m_hwrite_o,
    output logic                  m_hsel_o,
    output logic                  m_hready_o,
    input  logic [HDataWidth-1:0] m_hrdata_i,
    input  logic                  m_hreadyout_i,
    input  logic                  m_hresp_i
);

  localparam logic [2:0] FullSize = 3'(unsigned'($clog2(ADataWidth / 8)));

  logic hclk, hmastlock, unused_buser, unused_ruser;
  assign hclk       = AsyncCdc ? hclk_i : aclk_i;
  assign m_hsel_o   = 1'b1;
  assign m_hready_o = m_hreadyout_i;

  bag_axi2ahb #(
      .UpAddrWidth  (32),
      .DownAddrWidth(32),
      .UpDataWidth  (ADataWidth),
      .DownDataWidth(HDataWidth),
      .IdWidth      (IdWidth),
      .UserWidth    (1),
      .MaxTrans     (4),
      .FifoDepth    (FifoDepth),
      .AsyncCdc     (AsyncCdc),
      .AddrOffset   (AddrOffset)
  ) u_dut (
      .up_clk_i   (aclk_i),
      .up_rst_ni  (arst_ni),
      .down_clk_i (hclk),
      .down_rst_ni(hrst_ni),
      .s_awid_i(Lite ? '0 : s_awid),
      .s_awaddr_i(s_awaddr),
      .s_awlen_i(Lite ? 8'd0 : s_awlen),
      .s_awsize_i(Lite ? FullSize : s_awsize),
      .s_awburst_i(Lite ? 2'b01 : s_awburst),
      .s_awlock_i(s_awlock),
      .s_awcache_i(Lite ? 4'b0011 : s_awcache),
      .s_awprot_i(s_awprot),
      .s_awqos_i(s_awqos),
      .s_awregion_i(s_awregion),
      .s_awvalid_i(s_awvalid),
      .s_awready_o(s_awready),
      .s_wdata_i(s_wdata),
      .s_wstrb_i(s_wstrb),
      .s_wlast_i(Lite ? 1'b1 : s_wlast),
      .s_wvalid_i(s_wvalid),
      .s_wready_o(s_wready),
      .s_bid_o(s_bid),
      .s_bresp_o(s_bresp),
      .s_bvalid_o(s_bvalid),
      .s_bready_i(s_bready),
      .s_arid_i(Lite ? '0 : s_arid),
      .s_araddr_i(s_araddr),
      .s_arlen_i(Lite ? 8'd0 : s_arlen),
      .s_arsize_i(Lite ? FullSize : s_arsize),
      .s_arburst_i(Lite ? 2'b01 : s_arburst),
      .s_arlock_i(s_arlock),
      .s_arcache_i(Lite ? 4'b0011 : s_arcache),
      .s_arprot_i(s_arprot),
      .s_arqos_i(s_arqos),
      .s_arregion_i(s_arregion),
      .s_arvalid_i(s_arvalid),
      .s_arready_o(s_arready),
      .s_rid_o(s_rid),
      .s_rdata_o(s_rdata),
      .s_rresp_o(s_rresp),
      .s_rlast_o(s_rlast),
      .s_rvalid_o(s_rvalid),
      .s_rready_i(s_rready),
      .s_awuser_i(1'b0),
      .s_aruser_i(1'b0),
      .s_wuser_i(1'b0),
      .s_buser_o(unused_buser),
      .s_ruser_o(unused_ruser),
      .m_haddr_o(m_haddr_o),
      .m_hburst_o(m_hburst_o),
      .m_hmastlock_o(hmastlock),
      .m_hprot_o(m_hprot_o),
      .m_hnonsec_o(m_hnonsec_o),
      .m_hsize_o(m_hsize_o),
      .m_htrans_o(m_htrans_o),
      .m_hwdata_o(m_hwdata_o),
      .m_hwrite_o(m_hwrite_o),
      .m_hrdata_i(m_hrdata_i),
      .m_hready_i(m_hreadyout_i),
      .m_hresp_i(m_hresp_i)
  );

  // AHB-Lite slaves do not see HMASTLOCK (v1); the bridge never locks.
  logic unused;
  assign unused = ^{hmastlock, unused_buser, unused_ruser};

endmodule
