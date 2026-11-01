// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Smoke-test top for bag_apb2ahb: the bridge is the only AHB master and the AHB slave model
// the only slave, so HSEL is high and HREADY is the slave's HREADYOUT. Ports use the
// fabric naming of the BFMs (s_* = APB master side, m_* = AHB slave side). With
// AsyncCdc = 0 both sides run from pclk_i.

module tb_apb2ahb #(
    parameter int unsigned PDataWidth = 32,
    parameter int unsigned HDataWidth = 32,
    parameter logic [31:0] AddrOffset = '0,
    parameter bit          AsyncCdc   = 1'b0
) (
    input logic pclk_i,
    input logic prst_ni,
    input logic hclk_i,
    input logic hrst_ni,

    input  logic [            31:0] s_paddr_i,
    input  logic [             2:0] s_pprot_i,
    input  logic                    s_psel_i,
    input  logic                    s_penable_i,
    input  logic                    s_pwrite_i,
    input  logic [  PDataWidth-1:0] s_pwdata_i,
    input  logic [PDataWidth/8-1:0] s_pstrb_i,
    output logic                    s_pready_o,
    output logic [  PDataWidth-1:0] s_prdata_o,
    output logic                    s_pslverr_o,

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

  logic hclk, hmastlock;
  assign hclk       = AsyncCdc ? hclk_i : pclk_i;
  assign m_hsel_o   = 1'b1;
  assign m_hready_o = m_hreadyout_i;

  bag_apb2ahb #(
      .PAddrWidth(32),
      .PDataWidth(PDataWidth),
      .HAddrWidth(32),
      .HDataWidth(HDataWidth),
      .AddrOffset(AddrOffset),
      .AsyncCdc  (AsyncCdc)
  ) u_dut (
      .pclk_i     (pclk_i),
      .prst_ni    (prst_ni),
      .psel_i     (s_psel_i),
      .penable_i  (s_penable_i),
      .pwrite_i   (s_pwrite_i),
      .paddr_i    (s_paddr_i),
      .pprot_i    (s_pprot_i),
      .pwdata_i   (s_pwdata_i),
      .pstrb_i    (s_pstrb_i),
      .pready_o   (s_pready_o),
      .prdata_o   (s_prdata_o),
      .pslverr_o  (s_pslverr_o),
      .hclk_i     (hclk),
      .hrst_ni    (hrst_ni),
      .haddr_o    (m_haddr_o),
      .hburst_o   (m_hburst_o),
      .hmastlock_o(hmastlock),
      .hprot_o    (m_hprot_o),
      .hnonsec_o  (m_hnonsec_o),
      .hsize_o    (m_hsize_o),
      .htrans_o   (m_htrans_o),
      .hwdata_o   (m_hwdata_o),
      .hwrite_o   (m_hwrite_o),
      .hrdata_i   (m_hrdata_i),
      .hready_i   (m_hreadyout_i),
      .hresp_i    (m_hresp_i)
  );

  // AHB-Lite slaves do not see HMASTLOCK (v1); the bridge never locks.
  logic unused_hmastlock;
  assign unused_hmastlock = hmastlock;

endmodule
