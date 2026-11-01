// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Smoke-test top for bag_ahb2axi (s_* = AHB master side, m_* = AXI slave side, ID width
// 1). HREADY is the bridge's HREADYOUT, held low while s_hready_gate_i is high (another
// slave's wait state); HSEL comes from s_hsel_i. With AsyncCdc = 0 both sides run from
// hclk_i.

module tb_ahb2axi #(
    parameter int unsigned HDataWidth = 32,
    parameter int unsigned ADataWidth = 32,
    parameter logic [31:0] AddrOffset = '0,
    parameter bit          AsyncCdc   = 1'b0
) (
    input logic hclk_i,
    input logic hrst_ni,
    input logic aclk_i,
    input logic arst_ni,

    input  logic                  s_hsel_i,
    input  logic                  s_hready_gate_i,
    input  logic [          31:0] s_haddr_i,
    input  logic [           2:0] s_hburst_i,
    input  logic                  s_hmastlock_i,
    input  logic [           3:0] s_hprot_i,
    input  logic                  s_hnonsec_i,
    input  logic [           2:0] s_hsize_i,
    input  logic [           1:0] s_htrans_i,
    input  logic [HDataWidth-1:0] s_hwdata_i,
    input  logic                  s_hwrite_i,
    output logic [HDataWidth-1:0] s_hrdata_o,
    output logic                  s_hready_o,
    output logic                  s_hresp_o,

    output logic [1-1:0]          m_awid,
    output logic [31:0]           m_awaddr,
    output logic [7:0]            m_awlen,
    output logic [2:0]            m_awsize,
    output logic [1:0]            m_awburst,
    output logic                  m_awlock,
    output logic [3:0]            m_awcache,
    output logic [2:0]            m_awprot,
    output logic [3:0]            m_awqos,
    output logic [3:0]            m_awregion,
    output logic                  m_awvalid,
    input  logic                  m_awready,
    output logic [ADataWidth-1:0] m_wdata,
    output logic [ADataWidth/8-1:0] m_wstrb,
    output logic                  m_wlast,
    output logic                  m_wvalid,
    input  logic                  m_wready,
    input  logic [1-1:0]          m_bid,
    input  logic [1:0]            m_bresp,
    input  logic                  m_bvalid,
    output logic                  m_bready,
    output logic [1-1:0]          m_arid,
    output logic [31:0]           m_araddr,
    output logic [7:0]            m_arlen,
    output logic [2:0]            m_arsize,
    output logic [1:0]            m_arburst,
    output logic                  m_arlock,
    output logic [3:0]            m_arcache,
    output logic [2:0]            m_arprot,
    output logic [3:0]            m_arqos,
    output logic [3:0]            m_arregion,
    output logic                  m_arvalid,
    input  logic                  m_arready,
    input  logic [1-1:0]          m_rid,
    input  logic [ADataWidth-1:0] m_rdata,
    input  logic [1:0]            m_rresp,
    input  logic                  m_rlast,
    input  logic                  m_rvalid,
    output logic                  m_rready
);

  logic aclk;
  assign aclk = AsyncCdc ? aclk_i : hclk_i;

  logic hreadyout;
  assign s_hready_o = hreadyout && !s_hready_gate_i;

  // AHB-Lite slaves do not see HMASTLOCK (v1).
  logic unused_hmastlock;
  assign unused_hmastlock = s_hmastlock_i;

  bag_ahb2axi #(
      .HAddrWidth(32),
      .HDataWidth(HDataWidth),
      .AAddrWidth(32),
      .ADataWidth(ADataWidth),
      .AddrOffset(AddrOffset),
      .AsyncCdc  (AsyncCdc)
  ) u_dut (
      .hclk_i     (hclk_i),
      .hrst_ni    (hrst_ni),
      .hsel_i     (s_hsel_i),
      .haddr_i    (s_haddr_i),
      .hburst_i   (s_hburst_i),
      .hprot_i    (s_hprot_i),
      .hnonsec_i  (s_hnonsec_i),
      .hsize_i    (s_hsize_i),
      .htrans_i   (s_htrans_i),
      .hwdata_i   (s_hwdata_i),
      .hwrite_i   (s_hwrite_i),
      .hready_i   (s_hready_o),
      .hrdata_o   (s_hrdata_o),
      .hreadyout_o(hreadyout),
      .hresp_o    (s_hresp_o),
      .aclk_i     (aclk),
      .arst_ni    (arst_ni),
      .m_awid_o(m_awid),
      .m_awaddr_o(m_awaddr),
      .m_awlen_o(m_awlen),
      .m_awsize_o(m_awsize),
      .m_awburst_o(m_awburst),
      .m_awlock_o(m_awlock),
      .m_awcache_o(m_awcache),
      .m_awprot_o(m_awprot),
      .m_awqos_o(m_awqos),
      .m_awregion_o(m_awregion),
      .m_awvalid_o(m_awvalid),
      .m_awready_i(m_awready),
      .m_wdata_o(m_wdata),
      .m_wstrb_o(m_wstrb),
      .m_wlast_o(m_wlast),
      .m_wvalid_o(m_wvalid),
      .m_wready_i(m_wready),
      .m_bid_i(m_bid),
      .m_bresp_i(m_bresp),
      .m_bvalid_i(m_bvalid),
      .m_bready_o(m_bready),
      .m_arid_o(m_arid),
      .m_araddr_o(m_araddr),
      .m_arlen_o(m_arlen),
      .m_arsize_o(m_arsize),
      .m_arburst_o(m_arburst),
      .m_arlock_o(m_arlock),
      .m_arcache_o(m_arcache),
      .m_arprot_o(m_arprot),
      .m_arqos_o(m_arqos),
      .m_arregion_o(m_arregion),
      .m_arvalid_o(m_arvalid),
      .m_arready_i(m_arready),
      .m_rid_i(m_rid),
      .m_rdata_i(m_rdata),
      .m_rresp_i(m_rresp),
      .m_rlast_i(m_rlast),
      .m_rvalid_i(m_rvalid),
      .m_rready_o(m_rready)
  );

endmodule
