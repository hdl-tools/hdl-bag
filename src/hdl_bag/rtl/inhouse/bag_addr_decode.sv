// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Address decoder shared by the in-house fabrics.
//
// For the current master (one-hot mst_i) it applies, in order:
//   1. that master's aliases: an enabled alias moves its window to AliasTo;
//   2. the address rules: base/mask per region, the slave port it hits, RO/WO, secure-only;
//   3. that master's connectivity and Access_Rules (RO/WO) towards the hit slave port.
// A miss or a blocked access selects the master's default slave port, or raises err_o.
// err_o is also raised whenever nothing is selected, so a rule or default slave that
// points outside the slave ports can never give a silent OKAY or hang a bus.
//
// Regions and alias windows are powers of two (checked by `bag check`), so a window test
// is a mask compare. All tables come from the generated parameter package.

module bag_addr_decode #(
    parameter int unsigned AddrWidth = 32,
    parameter int unsigned NoMasters = 1,
    parameter int unsigned NoSlaves  = 1,
    parameter int unsigned NoRules   = 1,
    parameter int unsigned NoAliases = 1,

    // Address rules: one per Memory_Map region on this fabric.
    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleBase    = '0,
    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleMask    = '0,  // size - 1
    parameter logic [NoRules-1:0][31:0]          RuleSlave   = '0,
    parameter logic [NoRules-1:0]                RuleNoRead  = '0,  // WO region
    parameter logic [NoRules-1:0]                RuleNoWrite = '0,  // RO region
    parameter logic [NoRules-1:0]                RuleSecure  = '0,

    // Per master and slave port: connectivity and Access_Rules.
    parameter logic [NoMasters-1:0][NoSlaves-1:0] Connect    = '1,
    parameter logic [NoMasters-1:0][NoSlaves-1:0] MstNoRead  = '0,
    parameter logic [NoMasters-1:0][NoSlaves-1:0] MstNoWrite = '0,

    // Per master miss handling: error response, or route to DefaultSlave.
    parameter logic [NoMasters-1:0]       MissToDefault = '0,
    parameter logic [NoMasters-1:0][31:0] DefaultSlave  = '0,

    // Aliases, each owned by one master. An unused table has AliasMask = '0 and a
    // tied-low enable.
    parameter logic [NoAliases-1:0][31:0]          AliasMaster = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasBase   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasMask   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasTo     = '0
) (
    input  logic [NoMasters-1:0] mst_i,       // one-hot
    input  logic [AddrWidth-1:0] addr_i,
    input  logic                 write_i,
    input  logic                 nonsec_i,
    input  logic [NoAliases-1:0] alias_en_i,
    output logic [AddrWidth-1:0] addr_o,      // after aliasing
    output logic [ NoSlaves-1:0] sel_o,       // one-hot, or zero with err_o
    output logic                 err_o
);

  logic [AddrWidth-1:0] offset;
  logic [ NoSlaves-1:0] hit;
  logic                 match;
  logic                 deny;
  logic                 to_default;

  // 1. Aliases.
  always_comb begin
    addr_o = addr_i;
    offset = '0;
    for (int unsigned a = 0; a < NoAliases; a++) begin
      offset = addr_i - AliasBase[a];
      for (int unsigned m = 0; m < NoMasters; m++) begin
        if (alias_en_i[a] && mst_i[m] && AliasMaster[a] == m && (offset & ~AliasMask[a]) == '0)
        begin
          addr_o = AliasTo[a] + offset;
        end
      end
    end
  end

  // 2. Address rules.
  always_comb begin
    hit   = '0;
    match = 1'b0;
    deny  = 1'b0;
    for (int unsigned r = 0; r < NoRules; r++) begin
      if (((addr_o ^ RuleBase[r]) & ~RuleMask[r]) == '0) begin
        match = 1'b1;
        for (int unsigned s = 0; s < NoSlaves; s++) begin
          if (RuleSlave[r] == s) hit[s] = 1'b1;
        end
        if (write_i ? RuleNoWrite[r] : RuleNoRead[r]) deny = 1'b1;
        if (RuleSecure[r] && nonsec_i) deny = 1'b1;
      end
    end
    // 3. Connectivity and Access_Rules of the current master.
    to_default = 1'b0;
    for (int unsigned m = 0; m < NoMasters; m++) begin
      if (mst_i[m]) begin
        to_default = MissToDefault[m];
        for (int unsigned s = 0; s < NoSlaves; s++) begin
          if (hit[s] && !Connect[m][s]) deny = 1'b1;
          if (hit[s] && (write_i ? MstNoWrite[m][s] : MstNoRead[m][s])) deny = 1'b1;
        end
      end
    end
  end

  // Miss or blocked: default slave or error.
  always_comb begin
    sel_o = hit;
    err_o = 1'b0;
    if (!match || deny) begin
      sel_o = '0;
      err_o = !to_default;
      for (int unsigned m = 0; m < NoMasters; m++) begin
        for (int unsigned s = 0; s < NoSlaves; s++) begin
          if (to_default && mst_i[m] && DefaultSlave[m] == s) sel_o[s] = 1'b1;
        end
      end
    end
    if (sel_o == '0) err_o = 1'b1;
  end

endmodule
