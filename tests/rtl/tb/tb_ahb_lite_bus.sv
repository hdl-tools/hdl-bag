// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Smoke-test top for bag_ahb_lite_bus: 3 masters, 3 slaves.
//   0x2000_0000 +4K  s0  RW
//   0x2000_1000 +4K  s1  RO
//   0x2000_2000 +4K  s2  RW, secure-only
//   0x2000_3000 +4K  s1  RW
//   m1 has no access to s2; m2 has read-only access to s0.
//   m0 alias 0x0000_0000 +4K -> 0x2000_0000 while alias_en_i[0].
//   Fixed priority (when selected): m1 > m2 > m0.

module tb_ahb_lite_bus #(
    parameter bit FixedPriority = 1'b0
) (
    input logic       clk_i,
    input logic       rst_ni,
    input logic [0:0] alias_en_i,

    input  logic [2:0][31:0] s_haddr_i,
    input  logic [2:0][ 2:0] s_hburst_i,
    input  logic [2:0]       s_hmastlock_i,
    input  logic [2:0][ 3:0] s_hprot_i,
    input  logic [2:0]       s_hnonsec_i,
    input  logic [2:0][ 2:0] s_hsize_i,
    input  logic [2:0][ 1:0] s_htrans_i,
    input  logic [2:0][31:0] s_hwdata_i,
    input  logic [2:0]       s_hwrite_i,
    output logic [2:0][31:0] s_hrdata_o,
    output logic [2:0]       s_hready_o,
    output logic [2:0]       s_hresp_o,

    output logic [2:0]       m_hsel_o,
    output logic [2:0][31:0] m_haddr_o,
    output logic [2:0][ 2:0] m_hburst_o,
    output logic [2:0][ 3:0] m_hprot_o,
    output logic [2:0]       m_hnonsec_o,
    output logic [2:0][ 2:0] m_hsize_o,
    output logic [2:0][ 1:0] m_htrans_o,
    output logic [2:0][31:0] m_hwdata_o,
    output logic [2:0]       m_hwrite_o,
    output logic [2:0]       m_hready_o,
    input  logic [2:0][31:0] m_hrdata_i,
    input  logic [2:0]       m_hreadyout_i,
    input  logic [2:0]       m_hresp_i
);

  localparam logic [2:0][31:0] Priority = '{32'd1, 32'd0, 32'd2};
  localparam logic [3:0][31:0] RuleBase = '{
      32'h2000_3000, 32'h2000_2000, 32'h2000_1000, 32'h2000_0000
  };
  localparam logic [3:0][31:0] RuleMask = '{default: 32'h0000_0FFF};
  localparam logic [3:0][31:0] RuleSlave = '{32'd1, 32'd2, 32'd1, 32'd0};
  localparam logic [2:0][2:0] Connect = '{3'b111, 3'b011, 3'b111};
  localparam logic [2:0][2:0] MstNoWrite = '{3'b001, 3'b000, 3'b000};
  localparam logic [0:0][31:0] AliasMaster = '{32'd0};
  localparam logic [0:0][31:0] AliasBase = '{32'h0000_0000};
  localparam logic [0:0][31:0] AliasMask = '{32'h0000_0FFF};
  localparam logic [0:0][31:0] AliasTo = '{32'h2000_0000};

  bag_ahb_lite_bus #(
      .AddrWidth    (32),
      .DataWidth    (32),
      .NoMasters    (3),
      .NoSlaves     (3),
      .NoRules      (4),
      .NoAliases    (1),
      .FixedPriority(FixedPriority),
      .Priority     (Priority),
      .RuleBase     (RuleBase),
      .RuleMask     (RuleMask),
      .RuleSlave    (RuleSlave),
      .RuleNoRead   (4'b0000),
      .RuleNoWrite  (4'b0010),
      .RuleSecure   (4'b0100),
      .Connect      (Connect),
      .MstNoRead    ('0),
      .MstNoWrite   (MstNoWrite),
      .AliasMaster  (AliasMaster),
      .AliasBase    (AliasBase),
      .AliasMask    (AliasMask),
      .AliasTo      (AliasTo)
  ) u_dut (
      .clk_i        (clk_i),
      .rst_ni       (rst_ni),
      .alias_en_i   (alias_en_i),
      .s_haddr_i    (s_haddr_i),
      .s_hburst_i   (s_hburst_i),
      .s_hmastlock_i(s_hmastlock_i),
      .s_hprot_i    (s_hprot_i),
      .s_hnonsec_i  (s_hnonsec_i),
      .s_hsize_i    (s_hsize_i),
      .s_htrans_i   (s_htrans_i),
      .s_hwdata_i   (s_hwdata_i),
      .s_hwrite_i   (s_hwrite_i),
      .s_hrdata_o   (s_hrdata_o),
      .s_hready_o   (s_hready_o),
      .s_hresp_o    (s_hresp_o),
      .m_hsel_o     (m_hsel_o),
      .m_haddr_o    (m_haddr_o),
      .m_hburst_o   (m_hburst_o),
      .m_hprot_o    (m_hprot_o),
      .m_hnonsec_o  (m_hnonsec_o),
      .m_hsize_o    (m_hsize_o),
      .m_htrans_o   (m_htrans_o),
      .m_hwdata_o   (m_hwdata_o),
      .m_hwrite_o   (m_hwrite_o),
      .m_hready_o   (m_hready_o),
      .m_hrdata_i   (m_hrdata_i),
      .m_hreadyout_i(m_hreadyout_i),
      .m_hresp_i    (m_hresp_i)
  );

endmodule
