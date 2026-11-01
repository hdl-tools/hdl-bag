// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_shared_bus: AXI4-Lite fabric with flat, packed-array ports, built around a shared bus.
//
// Each BAG master port goes through bag_axil_from_flat and a guard
// (bag_axil_guard: aliases, address rules, Access_Rules, RO/WO,
// secure-only, misses). Each BAG slave port is driven through
// bag_axil_to_flat, which drops the route bits.
// Shared bus: guards -> bag_axil_mux (N:1, round robin or fixed priority: the
// lowest Priority value wins) -> mid-point (axi_fifo of FifoDepth, then axi_cut or
// axi_multicut of CutStages) -> axi_lite_demux (1:M), which steers on the route bits the
// guards wrote above the address.
//
// Ports s_* / m_* are packed [port][bits]; the generated wrapper fits narrower ports.
// USER is UserWidth bits (1 bit, unused, when UserWidth = 0). ATOPs are not supported.

`include "axi/typedef.svh"

module bag_axil_shared_bus #(
    parameter int unsigned AddrWidth   = 32,
    parameter int unsigned DataWidth   = 32,
    parameter int unsigned NoMasters   = 1,
    parameter int unsigned NoSlaves    = 1,
    parameter int unsigned NoRules     = 1,
    parameter int unsigned NoAliases   = 1,
    parameter int unsigned MaxMstTrans = 4,
    parameter int unsigned MaxSlvTrans = 4,
    parameter bit          FixedPrio   = 1'b0,
    parameter logic [NoMasters-1:0][31:0] Priority = '0,  // 0 = highest
    parameter int unsigned FifoDepth   = 0,
    parameter int unsigned CutStages   = 0,
    // Decode tables (bag_addr_decode), set from the generated package.
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
    parameter logic [NoMasters-1:0][1:0]  MissResp      = '1,  // per master: DECERR/SLVERR
    parameter logic [NoAliases-1:0][31:0]          AliasMaster = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasBase   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasMask   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasTo     = '0
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic [NoAliases-1:0] alias_en_i,

    // BAG master ports (index = Port index on the fabric)
    input  logic [NoMasters-1:0][AddrWidth-1:0] s_awaddr_i,
    input  logic [NoMasters-1:0][3-1:0] s_awprot_i,
    input  logic [NoMasters-1:0] s_awvalid_i,
    output logic [NoMasters-1:0] s_awready_o,
    input  logic [NoMasters-1:0][DataWidth-1:0] s_wdata_i,
    input  logic [NoMasters-1:0][DataWidth/8-1:0] s_wstrb_i,
    input  logic [NoMasters-1:0] s_wvalid_i,
    output logic [NoMasters-1:0] s_wready_o,
    output logic [NoMasters-1:0][2-1:0] s_bresp_o,
    output logic [NoMasters-1:0] s_bvalid_o,
    input  logic [NoMasters-1:0] s_bready_i,
    input  logic [NoMasters-1:0][AddrWidth-1:0] s_araddr_i,
    input  logic [NoMasters-1:0][3-1:0] s_arprot_i,
    input  logic [NoMasters-1:0] s_arvalid_i,
    output logic [NoMasters-1:0] s_arready_o,
    output logic [NoMasters-1:0][DataWidth-1:0] s_rdata_o,
    output logic [NoMasters-1:0][2-1:0] s_rresp_o,
    output logic [NoMasters-1:0] s_rvalid_o,
    input  logic [NoMasters-1:0] s_rready_i,
    // BAG slave ports
    output logic [NoSlaves-1:0][AddrWidth-1:0] m_awaddr_o,
    output logic [NoSlaves-1:0][3-1:0] m_awprot_o,
    output logic [NoSlaves-1:0] m_awvalid_o,
    input  logic [NoSlaves-1:0] m_awready_i,
    output logic [NoSlaves-1:0][DataWidth-1:0] m_wdata_o,
    output logic [NoSlaves-1:0][DataWidth/8-1:0] m_wstrb_o,
    output logic [NoSlaves-1:0] m_wvalid_o,
    input  logic [NoSlaves-1:0] m_wready_i,
    input  logic [NoSlaves-1:0][2-1:0] m_bresp_i,
    input  logic [NoSlaves-1:0] m_bvalid_i,
    output logic [NoSlaves-1:0] m_bready_o,
    output logic [NoSlaves-1:0][AddrWidth-1:0] m_araddr_o,
    output logic [NoSlaves-1:0][3-1:0] m_arprot_o,
    output logic [NoSlaves-1:0] m_arvalid_o,
    input  logic [NoSlaves-1:0] m_arready_i,
    input  logic [NoSlaves-1:0][DataWidth-1:0] m_rdata_i,
    input  logic [NoSlaves-1:0][2-1:0] m_rresp_i,
    input  logic [NoSlaves-1:0] m_rvalid_i,
    output logic [NoSlaves-1:0] m_rready_o
);

  localparam int unsigned SelWidth = (NoSlaves > 1) ? $clog2(NoSlaves) : 1;
  localparam int unsigned ReqAddrWidth = SelWidth + AddrWidth;

  // Fixed priority needs distinct Priority values (bag check: TOPO-PRIORITY-REPEATED).
  function automatic bit priorities_distinct();
    priorities_distinct = 1'b1;
    for (int unsigned a = 0; a < NoMasters; a++) begin
      for (int unsigned b = a + 1; b < NoMasters; b++) begin
        if (Priority[a] == Priority[b]) priorities_distinct = 1'b0;
      end
    end
  endfunction
  if (FixedPrio && !priorities_distinct()) begin : gen_bad_priority
    $fatal(1, "fixed-priority masters need distinct Priority values");
  end

  typedef logic [ReqAddrWidth-1:0] addr_t;
  typedef logic [DataWidth-1:0] data_t;
  typedef logic [DataWidth/8-1:0] strb_t;
  `AXI_LITE_TYPEDEF_ALL(axi, addr_t, data_t, strb_t)

  axi_req_t  [NoMasters-1:0] in_req, guard_req;
  axi_resp_t [NoMasters-1:0] in_resp, guard_resp;
  axi_req_t  [NoSlaves-1:0] out_req;
  axi_resp_t [NoSlaves-1:0] out_resp;

  for (genvar m = 0; m < NoMasters; m++) begin : gen_masters
    bag_axil_from_flat #(
        .AddrWidth   (AddrWidth),
        .DataWidth   (DataWidth),
        .ReqAddrWidth(ReqAddrWidth),
        .axi_req_t   (axi_req_t),
        .axi_resp_t  (axi_resp_t)
    ) i_from_flat (
        .awaddr_i(s_awaddr_i[m]),
        .awprot_i(s_awprot_i[m]),
        .awvalid_i(s_awvalid_i[m]),
        .awready_o(s_awready_o[m]),
        .wdata_i(s_wdata_i[m]),
        .wstrb_i(s_wstrb_i[m]),
        .wvalid_i(s_wvalid_i[m]),
        .wready_o(s_wready_o[m]),
        .bresp_o(s_bresp_o[m]),
        .bvalid_o(s_bvalid_o[m]),
        .bready_i(s_bready_i[m]),
        .araddr_i(s_araddr_i[m]),
        .arprot_i(s_arprot_i[m]),
        .arvalid_i(s_arvalid_i[m]),
        .arready_o(s_arready_o[m]),
        .rdata_o(s_rdata_o[m]),
        .rresp_o(s_rresp_o[m]),
        .rvalid_o(s_rvalid_o[m]),
        .rready_i(s_rready_i[m]),
        .req_o (in_req[m]),
        .resp_i(in_resp[m])
    );

    bag_axil_guard #(
        .AddrWidth (AddrWidth),
        .NoMasters (NoMasters),
        .NoSlaves  (NoSlaves),
        .NoRules   (NoRules),
        .NoAliases (NoAliases),
        .MstIdx    (m),
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
        .AliasTo      (AliasTo),
        .MaxTrans     (MaxMstTrans),
        .ErrResp      (MissResp[m]),
        .aw_chan_t    (axi_aw_chan_t),
        .w_chan_t     (axi_w_chan_t),
        .b_chan_t     (axi_b_chan_t),
        .ar_chan_t    (axi_ar_chan_t),
        .r_chan_t     (axi_r_chan_t),
        .axi_req_t    (axi_req_t),
        .axi_resp_t   (axi_resp_t)
    ) i_guard (
        .clk_i,
        .rst_ni,
        .alias_en_i,
        .slv_req_i (in_req[m]),
        .slv_resp_o(in_resp[m]),
        .mst_req_o (guard_req[m]),
        .mst_resp_i(guard_resp[m])
    );
  end

  // Mux inputs in priority order when FixedPrio (the mux grants the lowest index).
  function automatic int unsigned rank(input int unsigned m);
    rank = 0;
    if (FixedPrio) begin
      for (int unsigned k = 0; k < NoMasters; k++) begin
        if (Priority[k] < Priority[m]) rank++;
      end
    end else begin
      rank = m;
    end
  endfunction

  axi_req_t  [NoMasters-1:0] mux_req;
  axi_resp_t [NoMasters-1:0] mux_resp;
  for (genvar m = 0; m < NoMasters; m++) begin : gen_order
    assign mux_req[rank(m)] = guard_req[m];
    assign guard_resp[m]    = mux_resp[rank(m)];
  end

  axi_req_t  mid_req [3];
  axi_resp_t mid_resp[3];

  bag_axil_mux #(
      .FixedPrio  (FixedPrio),
      .aw_chan_t  (axi_aw_chan_t),
      .w_chan_t   (axi_w_chan_t),
      .b_chan_t   (axi_b_chan_t),
      .ar_chan_t  (axi_ar_chan_t),
      .r_chan_t   (axi_r_chan_t),
      .axi_req_t  (axi_req_t),
      .axi_resp_t (axi_resp_t),
      .NoSlvPorts (NoMasters),
      .MaxTrans   (MaxMstTrans),
      .FallThrough(1'b0),
      .SpillAw    (1'b0),  // latency only from the Mid-point slice
      .SpillAr    (1'b0)
  ) i_mux (
      .clk_i,
      .rst_ni,
      .test_i     (1'b0),
      .slv_reqs_i (mux_req),
      .slv_resps_o(mux_resp),
      .mst_req_o  (mid_req[0]),
      .mst_resp_i (mid_resp[0])
  );

  // Mid-point: FIFO, then register slice(s).
  if (FifoDepth > 0) begin : gen_fifo
    axi_fifo #(
        .Depth      (FifoDepth),
        .FallThrough(1'b0),
        .aw_chan_t (axi_aw_chan_t),
        .w_chan_t  (axi_w_chan_t),
        .b_chan_t  (axi_b_chan_t),
        .ar_chan_t (axi_ar_chan_t),
        .r_chan_t  (axi_r_chan_t),
        .axi_req_t (axi_req_t),
        .axi_resp_t(axi_resp_t)
    ) i_fifo (
        .clk_i,
        .rst_ni,
        .test_i    (1'b0),
        .slv_req_i (mid_req[0]),
        .slv_resp_o(mid_resp[0]),
        .mst_req_o (mid_req[1]),
        .mst_resp_i(mid_resp[1])
    );
  end else begin : gen_no_fifo
    assign mid_req[1]  = mid_req[0];
    assign mid_resp[0] = mid_resp[1];
  end

  if (CutStages == 1) begin : gen_cut
    axi_cut #(
        .Bypass    (1'b0),
        .aw_chan_t (axi_aw_chan_t),
        .w_chan_t  (axi_w_chan_t),
        .b_chan_t  (axi_b_chan_t),
        .ar_chan_t (axi_ar_chan_t),
        .r_chan_t  (axi_r_chan_t),
        .axi_req_t (axi_req_t),
        .axi_resp_t(axi_resp_t)
    ) i_cut (
        .clk_i,
        .rst_ni,
        .slv_req_i (mid_req[1]),
        .slv_resp_o(mid_resp[1]),
        .mst_req_o (mid_req[2]),
        .mst_resp_i(mid_resp[2])
    );
  end else if (CutStages > 1) begin : gen_multicut
    axi_multicut #(
        .NoCuts    (CutStages),
        .aw_chan_t (axi_aw_chan_t),
        .w_chan_t  (axi_w_chan_t),
        .b_chan_t  (axi_b_chan_t),
        .ar_chan_t (axi_ar_chan_t),
        .r_chan_t  (axi_r_chan_t),
        .axi_req_t (axi_req_t),
        .axi_resp_t(axi_resp_t)
    ) i_cut (
        .clk_i,
        .rst_ni,
        .slv_req_i (mid_req[1]),
        .slv_resp_o(mid_resp[1]),
        .mst_req_o (mid_req[2]),
        .mst_resp_i(mid_resp[2])
    );
  end else begin : gen_no_cut
    assign mid_req[2]  = mid_req[1];
    assign mid_resp[1] = mid_resp[2];
  end

  // Steer on the route bits the guards wrote above the address.
  logic [SelWidth-1:0] aw_route, ar_route;
  assign aw_route = mid_req[2].aw.addr[ReqAddrWidth-1:AddrWidth];
  assign ar_route = mid_req[2].ar.addr[ReqAddrWidth-1:AddrWidth];

  if (NoSlaves == 1) begin : gen_no_demux
    assign out_req[0]  = mid_req[2];
    assign mid_resp[2] = out_resp[0];
    logic unused_route;
    assign unused_route = ^{aw_route, ar_route};  // one slave: always 0
  end else begin : gen_demux
    axi_lite_demux #(
        .aw_chan_t  (axi_aw_chan_t),
        .w_chan_t   (axi_w_chan_t),
        .b_chan_t   (axi_b_chan_t),
        .ar_chan_t  (axi_ar_chan_t),
        .r_chan_t   (axi_r_chan_t),
        .axi_req_t  (axi_req_t),
        .axi_resp_t (axi_resp_t),
        .NoMstPorts (NoSlaves),
        .MaxTrans   (MaxSlvTrans),
        .FallThrough(1'b0),
        .SpillAw    (1'b0),
        .SpillAr    (1'b0)
    ) i_demux (
        .clk_i,
        .rst_ni,
        .test_i         (1'b0),
        .slv_req_i      (mid_req[2]),
        .slv_aw_select_i(aw_route),
        .slv_ar_select_i(ar_route),
        .slv_resp_o     (mid_resp[2]),
        .mst_reqs_o     (out_req),
        .mst_resps_i    (out_resp)
    );
  end

  for (genvar s = 0; s < NoSlaves; s++) begin : gen_slaves
    bag_axil_to_flat #(
        .AddrWidth   (AddrWidth),
        .DataWidth   (DataWidth),
        .ReqAddrWidth(ReqAddrWidth),
        .axi_req_t   (axi_req_t),
        .axi_resp_t  (axi_resp_t)
    ) i_to_flat (
        .req_i (out_req[s]),
        .resp_o(out_resp[s]),
        .awaddr_o(m_awaddr_o[s]),
        .awprot_o(m_awprot_o[s]),
        .awvalid_o(m_awvalid_o[s]),
        .awready_i(m_awready_i[s]),
        .wdata_o(m_wdata_o[s]),
        .wstrb_o(m_wstrb_o[s]),
        .wvalid_o(m_wvalid_o[s]),
        .wready_i(m_wready_i[s]),
        .bresp_i(m_bresp_i[s]),
        .bvalid_i(m_bvalid_i[s]),
        .bready_o(m_bready_o[s]),
        .araddr_o(m_araddr_o[s]),
        .arprot_o(m_arprot_o[s]),
        .arvalid_o(m_arvalid_o[s]),
        .arready_i(m_arready_i[s]),
        .rdata_i(m_rdata_i[s]),
        .rresp_i(m_rresp_i[s]),
        .rvalid_i(m_rvalid_i[s]),
        .rready_o(m_rready_o[s])
    );
  end

endmodule : bag_axil_shared_bus
