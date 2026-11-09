// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi_guard: the decoder of one AXI4 master port of a BAG fabric.
//
// AW and AR each go through bag_addr_decode (the same decoder as the AHB-Lite and APB
// fabrics): alias translation, the address rules, Access_Rules, RO/WO regions, secure-
// only regions (from AxPROT[1]) and the master's miss handling. The guard is the fabric's
// only decoder. It writes the chosen slave port index into the route bits above the
// address (the struct address is AddrWidth + SelWidth bits wide); the crossbar or shared
// bus behind it steers on those bits, and the slave port sees the low AddrWidth bits, the
// address after aliasing (D30).
//
// An access the decoder rejects (a miss or a denied access when the master's miss
// response is an error) goes to an internal axi_err_slv that answers ErrResp
// (DECERR or SLVERR); write data is consumed. With a default slave the decoder never
// rejects. The incoming route bits must be zero (bag_axi_from_flat zero-extends).

module bag_axi_guard #(
    parameter int unsigned AddrWidth = 32,
    parameter int unsigned NoMasters = 1,
    parameter int unsigned NoSlaves  = 1,
    parameter int unsigned NoRules   = 1,
    parameter int unsigned NoAliases = 1,
    parameter int unsigned MstIdx    = 0,  // this master's index on the fabric

    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleBase    = '0,
    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleMask    = '0,
    parameter logic [NoRules-1:0][31:0]          RuleSlave   = '0,
    parameter logic [NoRules-1:0]                RuleNoRead  = '0,
    parameter logic [NoRules-1:0]                RuleNoWrite = '0,
    parameter logic [NoRules-1:0]                RuleSecure  = '0,

    parameter logic [NoMasters-1:0][NoSlaves-1:0] Connect    = '1,
    parameter logic [NoMasters-1:0][NoSlaves-1:0] MstNoRead  = '0,
    parameter logic [NoMasters-1:0][NoSlaves-1:0] MstNoWrite = '0,

    parameter logic [NoMasters-1:0]       MissToDefault = '0,
    parameter logic [NoMasters-1:0][31:0] DefaultSlave  = '0,

    parameter logic [NoAliases-1:0][31:0]          AliasMaster = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasBase   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasMask   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasTo     = '0,

    parameter int unsigned IdWidth  = 1,
    parameter int unsigned MaxTrans = 8,
    parameter logic [1:0]  ErrResp  = 2'b11,  // axi_pkg::RESP_DECERR

    parameter type axi_req_t  = logic,
    parameter type axi_resp_t = logic,

    localparam int unsigned SelWidth = (NoSlaves > 1) ? $clog2(NoSlaves) : 1
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic [NoAliases-1:0] alias_en_i,
    input  axi_req_t             slv_req_i,
    output axi_resp_t            slv_resp_o,
    output axi_req_t             mst_req_o,
    input  axi_resp_t            mst_resp_i
);

  localparam logic [NoMasters-1:0] MstOneHot = NoMasters'(1) << MstIdx;
  // The demux counts transactions in flight per full ID (D65), one counter per ID value and
  // channel: with fewer bits, IDs sharing their low bits would share one limit and be
  // serialised against each other (PULP's default of 3 bits did that).
  localparam int unsigned LookBits = IdWidth;
  // axi_demux_simple sizes its per-ID counters as idx_width(MaxTrans) bits and stops
  // accepting once any counter is all ones, so MaxTrans = N admits only 2^clog2(N) - 1
  // (N = 2 admits 1). MaxTrans + 1 lets every ID have at least MaxTrans in flight.

  typedef logic [SelWidth+AddrWidth-1:0] req_addr_t;

  req_addr_t aw_in, ar_in;
  logic [AddrWidth-1:0] aw_addr, ar_addr;
  logic [NoSlaves-1:0] aw_sel, ar_sel;
  logic aw_err, ar_err;

  assign aw_in = slv_req_i.aw.addr;
  assign ar_in = slv_req_i.ar.addr;

  bag_addr_decode #(
      .AddrWidth    (AddrWidth),
      .NoMasters    (NoMasters),
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
      .AliasMaster  (AliasMaster),
      .AliasBase    (AliasBase),
      .AliasMask    (AliasMask),
      .AliasTo      (AliasTo)
  ) i_aw_decode (
      .mst_i     (MstOneHot),
      .addr_i    (aw_in[AddrWidth-1:0]),
      .write_i   (1'b1),
      .nonsec_i  (slv_req_i.aw.prot[1]),
      .alias_en_i,
      .addr_o    (aw_addr),
      .sel_o     (aw_sel),
      .err_o     (aw_err)
  );

  bag_addr_decode #(
      .AddrWidth    (AddrWidth),
      .NoMasters    (NoMasters),
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
      .AliasMaster  (AliasMaster),
      .AliasBase    (AliasBase),
      .AliasMask    (AliasMask),
      .AliasTo      (AliasTo)
  ) i_ar_decode (
      .mst_i     (MstOneHot),
      .addr_i    (ar_in[AddrWidth-1:0]),
      .write_i   (1'b0),
      .nonsec_i  (slv_req_i.ar.prot[1]),
      .alias_en_i,
      .addr_o    (ar_addr),
      .sel_o     (ar_sel),
      .err_o     (ar_err)
  );

  function automatic logic [SelWidth-1:0] index(input logic [NoSlaves-1:0] onehot);
    index = '0;
    for (int unsigned s = 0; s < NoSlaves; s++) begin
      if (onehot[s]) index = SelWidth'(s);
    end
  endfunction

  axi_req_t routed;
  always_comb begin
    routed         = slv_req_i;
    routed.aw.addr = {index(aw_sel), aw_addr};
    routed.ar.addr = {index(ar_sel), ar_addr};
    routed.aw.atop = '0;
  end

  axi_req_t  [1:0] reqs;
  axi_resp_t [1:0] resps;

  // Port 0: to the fabric core; port 1: the error slave.
  axi_demux_simple #(
      .AxiIdWidth (IdWidth),
      .AtopSupport(1'b0),
      .axi_req_t  (axi_req_t),
      .axi_resp_t (axi_resp_t),
      .NoMstPorts (2),
      .MaxTrans   (MaxTrans + 1),  // see the note at LookBits
      .AxiLookBits(LookBits),
      .UniqueIds  (1'b0)
  ) i_demux (
      .clk_i,
      .rst_ni,
      .test_i         (1'b0),
      .slv_req_i      (routed),
      .slv_aw_select_i(aw_err),
      .slv_ar_select_i(ar_err),
      .slv_resp_o,
      .mst_reqs_o     (reqs),
      .mst_resps_i    (resps)
  );

  assign mst_req_o = reqs[0];
  assign resps[0]  = mst_resp_i;

  axi_err_slv #(
      .AxiIdWidth(IdWidth),
      .axi_req_t (axi_req_t),
      .axi_resp_t(axi_resp_t),
      .Resp      (ErrResp),
      .RespWidth (32'd64),
      .RespData  (64'h0),
      .ATOPs     (1'b0),
      .MaxTrans  (1)
  ) i_err_slv (
      .clk_i,
      .rst_ni,
      .test_i    (1'b0),
      .slv_req_i (reqs[1]),
      .slv_resp_o(resps[1])
  );

  // The incoming route bits are zero by construction.
  logic unused_route;
  assign unused_route = ^{aw_in[SelWidth+AddrWidth-1:AddrWidth],
                          ar_in[SelWidth+AddrWidth-1:AddrWidth]};

endmodule : bag_axi_guard
