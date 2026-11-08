// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// APB3/APB4 decoder-mux: one master, NoSlaves slaves.
//
// PSEL goes to the slave port that bag_addr_decode selects; PRDATA, PREADY and PSLVERR
// come back from it. A miss or blocked access with no default slave is answered by an
// internal default slave: PREADY high, PSLVERR high, PRDATA zero. Towards the master
// (D61), PREADY is held high while PSEL is low, and PSLVERR is driven only in the access
// phase of a completing transfer. APB is not pipelined, so the mux is purely
// combinational. APB3 instances tie pprot and pstrb.
// alias_en_i must be quasi-static (or synchronous and stable during a transfer).

module bag_apb_mux #(
    parameter int unsigned AddrWidth = 32,
    parameter int unsigned DataWidth = 32,
    parameter int unsigned NoSlaves  = 1,
    parameter int unsigned NoRules   = 1,
    parameter int unsigned NoAliases = 1,

    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleBase    = '0,
    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleMask    = '0,
    parameter logic [NoRules-1:0][31:0]          RuleSlave   = '0,
    parameter logic [NoRules-1:0]                RuleNoRead  = '0,
    parameter logic [NoRules-1:0]                RuleNoWrite = '0,
    parameter logic [NoRules-1:0]                RuleSecure  = '0,

    parameter logic [NoSlaves-1:0] Connect    = '1,
    parameter logic [NoSlaves-1:0] MstNoRead  = '0,
    parameter logic [NoSlaves-1:0] MstNoWrite = '0,

    parameter bit          MissToDefault = 1'b0,
    parameter int unsigned DefaultSlave  = 0,

    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasBase = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasMask = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasTo   = '0,

    localparam int unsigned StrbWidth = DataWidth / 8
) (
    input logic [NoAliases-1:0] alias_en_i,

    // Master side (a master attaches here).
    input  logic [AddrWidth-1:0] s_paddr_i,
    input  logic [          2:0] s_pprot_i,
    input  logic                 s_psel_i,
    input  logic                 s_penable_i,
    input  logic                 s_pwrite_i,
    input  logic [DataWidth-1:0] s_pwdata_i,
    input  logic [StrbWidth-1:0] s_pstrb_i,
    output logic                 s_pready_o,
    output logic [DataWidth-1:0] s_prdata_o,
    output logic                 s_pslverr_o,

    // Slave side, one entry per slave port.
    output logic [NoSlaves-1:0][AddrWidth-1:0] m_paddr_o,
    output logic [NoSlaves-1:0][          2:0] m_pprot_o,
    output logic [NoSlaves-1:0]                m_psel_o,
    output logic [NoSlaves-1:0]                m_penable_o,
    output logic [NoSlaves-1:0]                m_pwrite_o,
    output logic [NoSlaves-1:0][DataWidth-1:0] m_pwdata_o,
    output logic [NoSlaves-1:0][StrbWidth-1:0] m_pstrb_o,
    input  logic [NoSlaves-1:0]                m_pready_i,
    input  logic [NoSlaves-1:0][DataWidth-1:0] m_prdata_i,
    input  logic [NoSlaves-1:0]                m_pslverr_i
);

  logic [AddrWidth-1:0] addr;
  logic [ NoSlaves-1:0] sel;
  logic                 err;

  bag_addr_decode #(
      .AddrWidth    (AddrWidth),
      .NoMasters    (1),
      .NoSlaves     (NoSlaves),
      .NoRules      (NoRules),
      .NoAliases    (NoAliases),
      .RuleBase     (RuleBase),
      .RuleMask     (RuleMask),
      .RuleSlave    (RuleSlave),
      .RuleNoRead   (RuleNoRead),
      .RuleNoWrite  (RuleNoWrite),
      .RuleSecure   (RuleSecure),
      .Connect      (Connect),
      .MstNoRead    (MstNoRead),
      .MstNoWrite   (MstNoWrite),
      .MissToDefault(MissToDefault),
      .DefaultSlave (DefaultSlave),
      .AliasMaster  ('0),
      .AliasBase    (AliasBase),
      .AliasMask    (AliasMask),
      .AliasTo      (AliasTo)
  ) u_decode (
      .mst_i     (1'b1),
      .addr_i    (s_paddr_i),
      .write_i   (s_pwrite_i),
      .nonsec_i  (s_pprot_i[1]),
      .alias_en_i(alias_en_i),
      .addr_o    (addr),
      .sel_o     (sel),
      .err_o     (err)
  );

  for (genvar s = 0; s < NoSlaves; s++) begin : gen_slave
    assign m_paddr_o[s]   = addr;
    assign m_pprot_o[s]   = s_pprot_i;
    assign m_psel_o[s]    = s_psel_i & sel[s];
    assign m_penable_o[s] = s_penable_i & sel[s];
    assign m_pwrite_o[s]  = s_pwrite_i;
    assign m_pwdata_o[s]  = s_pwdata_i;
    assign m_pstrb_o[s]   = s_pstrb_i;
  end

  // The selected slave's response, or the mux's own error for a miss or a refused access.
  logic rsp_ready, rsp_err;
  always_comb begin
    rsp_ready  = err;
    s_prdata_o = '0;
    rsp_err    = err;
    for (int unsigned s = 0; s < NoSlaves; s++) begin
      if (sel[s]) begin
        rsp_ready  = m_pready_i[s];
        s_prdata_o = m_prdata_i[s];
        rsp_err    = m_pslverr_i[s];
      end
    end
  end

  // Towards the master (D61): PREADY is steady high while PSEL is low (including in reset,
  // where masters hold PSEL low), so it never follows the address decode or a slave while
  // idle; PSLVERR only in the access phase of a completing transfer, as AMBA recommends, so a
  // master never sees a stray error.
  assign s_pready_o  = ~s_psel_i | rsp_ready;
  assign s_pslverr_o = s_psel_i & s_penable_i & rsp_ready & rsp_err;

endmodule
