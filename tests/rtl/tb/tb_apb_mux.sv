// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Smoke-test top for bag_apb_mux: 3 slaves, 4 regions, one alias.
//   0x1000_0000 +4K  s0  RW
//   0x1000_1000 +4K  s1  RO
//   0x1000_2000 +4K  s2  WO, secure-only
//   0x1000_3000 +4K  s1  RW (second region of s1)
//   alias 0x0000_0000 +4K -> 0x1000_0000 while alias_en_i[0]

module tb_apb_mux #(
    parameter bit          MissToDefault = 1'b0,
    parameter int unsigned DefaultSlave  = 0
) (
    input logic       clk_i,
    input logic [0:0] alias_en_i,

    input  logic [31:0] s_paddr_i,
    input  logic [ 2:0] s_pprot_i,
    input  logic        s_psel_i,
    input  logic        s_penable_i,
    input  logic        s_pwrite_i,
    input  logic [31:0] s_pwdata_i,
    input  logic [ 3:0] s_pstrb_i,
    output logic        s_pready_o,
    output logic [31:0] s_prdata_o,
    output logic        s_pslverr_o,

    output logic [2:0][31:0] m_paddr_o,
    output logic [2:0][ 2:0] m_pprot_o,
    output logic [2:0]       m_psel_o,
    output logic [2:0]       m_penable_o,
    output logic [2:0]       m_pwrite_o,
    output logic [2:0][31:0] m_pwdata_o,
    output logic [2:0][ 3:0] m_pstrb_o,
    input  logic [2:0]       m_pready_i,
    input  logic [2:0][31:0] m_prdata_i,
    input  logic [2:0]       m_pslverr_i
);

  // The cocotb clock; the mux itself is combinational.
  logic unused_clk;
  assign unused_clk = clk_i;

  localparam logic [3:0][31:0] RuleBase = '{
      32'h1000_3000, 32'h1000_2000, 32'h1000_1000, 32'h1000_0000
  };
  localparam logic [3:0][31:0] RuleMask = '{default: 32'h0000_0FFF};
  localparam logic [3:0][31:0] RuleSlave = '{32'd1, 32'd2, 32'd1, 32'd0};
  localparam logic [0:0][31:0] AliasBase = '{32'h0000_0000};
  localparam logic [0:0][31:0] AliasMask = '{32'h0000_0FFF};
  localparam logic [0:0][31:0] AliasTo = '{32'h1000_0000};

  bag_apb_mux #(
      .AddrWidth    (32),
      .DataWidth    (32),
      .NoSlaves     (3),
      .NoRules      (4),
      .NoAliases    (1),
      .RuleBase     (RuleBase),
      .RuleMask     (RuleMask),
      .RuleSlave    (RuleSlave),
      .RuleNoRead   (4'b0100),
      .RuleNoWrite  (4'b0010),
      .RuleSecure   (4'b0100),
      .MissToDefault(MissToDefault),
      .DefaultSlave (DefaultSlave),
      .AliasBase    (AliasBase),
      .AliasMask    (AliasMask),
      .AliasTo      (AliasTo)
  ) u_dut (
      .alias_en_i (alias_en_i),
      .s_paddr_i  (s_paddr_i),
      .s_pprot_i  (s_pprot_i),
      .s_psel_i   (s_psel_i),
      .s_penable_i(s_penable_i),
      .s_pwrite_i (s_pwrite_i),
      .s_pwdata_i (s_pwdata_i),
      .s_pstrb_i  (s_pstrb_i),
      .s_pready_o (s_pready_o),
      .s_prdata_o (s_prdata_o),
      .s_pslverr_o(s_pslverr_o),
      .m_paddr_o  (m_paddr_o),
      .m_pprot_o  (m_pprot_o),
      .m_psel_o   (m_psel_o),
      .m_penable_o(m_penable_o),
      .m_pwrite_o (m_pwrite_o),
      .m_pwdata_o (m_pwdata_o),
      .m_pstrb_o  (m_pstrb_o),
      .m_pready_i (m_pready_i),
      .m_prdata_i (m_prdata_i),
      .m_pslverr_i(m_pslverr_i)
  );

endmodule
