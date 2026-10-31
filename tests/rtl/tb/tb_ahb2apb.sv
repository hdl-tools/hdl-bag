// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Smoke-test top for bag_ahb2apb: the bridge is the only AHB slave, so HSEL is tied
// high and HREADY is its own HREADYOUT. Ports use the fabric naming of the BFMs
// (s_* = AHB master side, m_* = APB slave side). With AsyncCdc = 0 both sides run
// from hclk_i.

module tb_ahb2apb #(
    parameter int unsigned HDataWidth = 32,
    parameter int unsigned PDataWidth = 32,
    parameter logic [31:0] AddrOffset = '0,
    parameter bit          AsyncCdc   = 1'b0
) (
    input logic hclk_i,
    input logic hrst_ni,
    input logic pclk_i,
    input logic prst_ni,

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

    output logic [            31:0] m_paddr_o,
    output logic [             2:0] m_pprot_o,
    output logic                    m_psel_o,
    output logic                    m_penable_o,
    output logic                    m_pwrite_o,
    output logic [  PDataWidth-1:0] m_pwdata_o,
    output logic [PDataWidth/8-1:0] m_pstrb_o,
    input  logic                    m_pready_i,
    input  logic [  PDataWidth-1:0] m_prdata_i,
    input  logic                    m_pslverr_i
);

  // Without CDC the bridge runs both sides from hclk_i; pclk_i is then unused.
  logic pclk;
  assign pclk = AsyncCdc ? pclk_i : hclk_i;

  // AHB-Lite slaves do not see HMASTLOCK (v1).
  logic unused_hmastlock;
  assign unused_hmastlock = s_hmastlock_i;

  bag_ahb2apb #(
      .HAddrWidth(32),
      .HDataWidth(HDataWidth),
      .PAddrWidth(32),
      .PDataWidth(PDataWidth),
      .AddrOffset(AddrOffset),
      .AsyncCdc  (AsyncCdc)
  ) u_dut (
      .hclk_i     (hclk_i),
      .hrst_ni    (hrst_ni),
      .hsel_i     (1'b1),
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
      .hreadyout_o(s_hready_o),
      .hresp_o    (s_hresp_o),
      .pclk_i     (pclk),
      .prst_ni    (prst_ni),
      .paddr_o    (m_paddr_o),
      .pprot_o    (m_pprot_o),
      .psel_o     (m_psel_o),
      .penable_o  (m_penable_o),
      .pwrite_o   (m_pwrite_o),
      .pwdata_o   (m_pwdata_o),
      .pstrb_o    (m_pstrb_o),
      .pready_i   (m_pready_i),
      .prdata_i   (m_prdata_i),
      .pslverr_i  (m_pslverr_i)
  );

endmodule
