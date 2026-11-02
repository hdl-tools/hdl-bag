// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi_xbar: AXI4 fabric with flat, packed-array ports, built around the PULP crossbar (axi_xbar).
//
// Each BAG master port goes through bag_axi_from_flat and a guard
// (bag_axi_guard: aliases, address rules, Access_Rules, RO/WO,
// secure-only, misses). Each BAG slave port is driven through
// bag_axi_to_flat, which drops the route bits.
// Crossbar: every master's guard decides the slave port and writes it into the route
// bits above the address; the PULP crossbar's address map is the trivial map "route bits
// = slave port", so the guards are the only decoder. PULP naming: the crossbar's *slave*
// ports are where BAG *masters* attach (Cfg.NoSlvPorts = NoMasters) and its *master*
// ports drive BAG slaves (Cfg.NoMstPorts = NoSlaves).
//
// Ports s_* / m_* are packed [port][bits]; the generated wrapper fits narrower ports.
// USER is UserWidth bits (1 bit, unused, when UserWidth = 0). ATOPs are not supported.

`include "axi/typedef.svh"

module bag_axi_xbar #(
    parameter int unsigned AddrWidth   = 32,
    parameter int unsigned DataWidth   = 32,
    parameter int unsigned IdWidthMst  = 1,  // BAG master side
    parameter int unsigned IdWidthSlv  = 1,  // BAG slave side: + clog2(NoMasters)
    parameter int unsigned UserWidth   = 0,
    parameter int unsigned NoMasters   = 1,
    parameter int unsigned NoSlaves    = 1,
    parameter int unsigned NoRules     = 1,
    parameter int unsigned NoAliases   = 1,
    parameter int unsigned MaxMstTrans = 4,
    parameter int unsigned MaxSlvTrans = 4,
    parameter logic [9:0]  LatencyMode = '0,  // axi_pkg::xbar_latency_e
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
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasTo     = '0,
    localparam int unsigned UW = (UserWidth > 0) ? UserWidth : 1
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic [NoAliases-1:0] alias_en_i,

    // BAG master ports (index = Port index on the fabric)
    input  logic [NoMasters-1:0][IdWidthMst-1:0] s_awid_i,
    input  logic [NoMasters-1:0][AddrWidth-1:0] s_awaddr_i,
    input  logic [NoMasters-1:0][8-1:0] s_awlen_i,
    input  logic [NoMasters-1:0][3-1:0] s_awsize_i,
    input  logic [NoMasters-1:0][2-1:0] s_awburst_i,
    input  logic [NoMasters-1:0] s_awlock_i,
    input  logic [NoMasters-1:0][4-1:0] s_awcache_i,
    input  logic [NoMasters-1:0][3-1:0] s_awprot_i,
    input  logic [NoMasters-1:0][4-1:0] s_awqos_i,
    input  logic [NoMasters-1:0][4-1:0] s_awregion_i,
    input  logic [NoMasters-1:0][UW-1:0] s_awuser_i,
    input  logic [NoMasters-1:0] s_awvalid_i,
    output logic [NoMasters-1:0] s_awready_o,
    input  logic [NoMasters-1:0][DataWidth-1:0] s_wdata_i,
    input  logic [NoMasters-1:0][DataWidth/8-1:0] s_wstrb_i,
    input  logic [NoMasters-1:0] s_wlast_i,
    input  logic [NoMasters-1:0][UW-1:0] s_wuser_i,
    input  logic [NoMasters-1:0] s_wvalid_i,
    output logic [NoMasters-1:0] s_wready_o,
    output logic [NoMasters-1:0][IdWidthMst-1:0] s_bid_o,
    output logic [NoMasters-1:0][2-1:0] s_bresp_o,
    output logic [NoMasters-1:0][UW-1:0] s_buser_o,
    output logic [NoMasters-1:0] s_bvalid_o,
    input  logic [NoMasters-1:0] s_bready_i,
    input  logic [NoMasters-1:0][IdWidthMst-1:0] s_arid_i,
    input  logic [NoMasters-1:0][AddrWidth-1:0] s_araddr_i,
    input  logic [NoMasters-1:0][8-1:0] s_arlen_i,
    input  logic [NoMasters-1:0][3-1:0] s_arsize_i,
    input  logic [NoMasters-1:0][2-1:0] s_arburst_i,
    input  logic [NoMasters-1:0] s_arlock_i,
    input  logic [NoMasters-1:0][4-1:0] s_arcache_i,
    input  logic [NoMasters-1:0][3-1:0] s_arprot_i,
    input  logic [NoMasters-1:0][4-1:0] s_arqos_i,
    input  logic [NoMasters-1:0][4-1:0] s_arregion_i,
    input  logic [NoMasters-1:0][UW-1:0] s_aruser_i,
    input  logic [NoMasters-1:0] s_arvalid_i,
    output logic [NoMasters-1:0] s_arready_o,
    output logic [NoMasters-1:0][IdWidthMst-1:0] s_rid_o,
    output logic [NoMasters-1:0][DataWidth-1:0] s_rdata_o,
    output logic [NoMasters-1:0][2-1:0] s_rresp_o,
    output logic [NoMasters-1:0] s_rlast_o,
    output logic [NoMasters-1:0][UW-1:0] s_ruser_o,
    output logic [NoMasters-1:0] s_rvalid_o,
    input  logic [NoMasters-1:0] s_rready_i,
    // BAG slave ports
    output logic [NoSlaves-1:0][IdWidthSlv-1:0] m_awid_o,
    output logic [NoSlaves-1:0][AddrWidth-1:0] m_awaddr_o,
    output logic [NoSlaves-1:0][8-1:0] m_awlen_o,
    output logic [NoSlaves-1:0][3-1:0] m_awsize_o,
    output logic [NoSlaves-1:0][2-1:0] m_awburst_o,
    output logic [NoSlaves-1:0] m_awlock_o,
    output logic [NoSlaves-1:0][4-1:0] m_awcache_o,
    output logic [NoSlaves-1:0][3-1:0] m_awprot_o,
    output logic [NoSlaves-1:0][4-1:0] m_awqos_o,
    output logic [NoSlaves-1:0][4-1:0] m_awregion_o,
    output logic [NoSlaves-1:0][UW-1:0] m_awuser_o,
    output logic [NoSlaves-1:0] m_awvalid_o,
    input  logic [NoSlaves-1:0] m_awready_i,
    output logic [NoSlaves-1:0][DataWidth-1:0] m_wdata_o,
    output logic [NoSlaves-1:0][DataWidth/8-1:0] m_wstrb_o,
    output logic [NoSlaves-1:0] m_wlast_o,
    output logic [NoSlaves-1:0][UW-1:0] m_wuser_o,
    output logic [NoSlaves-1:0] m_wvalid_o,
    input  logic [NoSlaves-1:0] m_wready_i,
    input  logic [NoSlaves-1:0][IdWidthSlv-1:0] m_bid_i,
    input  logic [NoSlaves-1:0][2-1:0] m_bresp_i,
    input  logic [NoSlaves-1:0][UW-1:0] m_buser_i,
    input  logic [NoSlaves-1:0] m_bvalid_i,
    output logic [NoSlaves-1:0] m_bready_o,
    output logic [NoSlaves-1:0][IdWidthSlv-1:0] m_arid_o,
    output logic [NoSlaves-1:0][AddrWidth-1:0] m_araddr_o,
    output logic [NoSlaves-1:0][8-1:0] m_arlen_o,
    output logic [NoSlaves-1:0][3-1:0] m_arsize_o,
    output logic [NoSlaves-1:0][2-1:0] m_arburst_o,
    output logic [NoSlaves-1:0] m_arlock_o,
    output logic [NoSlaves-1:0][4-1:0] m_arcache_o,
    output logic [NoSlaves-1:0][3-1:0] m_arprot_o,
    output logic [NoSlaves-1:0][4-1:0] m_arqos_o,
    output logic [NoSlaves-1:0][4-1:0] m_arregion_o,
    output logic [NoSlaves-1:0][UW-1:0] m_aruser_o,
    output logic [NoSlaves-1:0] m_arvalid_o,
    input  logic [NoSlaves-1:0] m_arready_i,
    input  logic [NoSlaves-1:0][IdWidthSlv-1:0] m_rid_i,
    input  logic [NoSlaves-1:0][DataWidth-1:0] m_rdata_i,
    input  logic [NoSlaves-1:0][2-1:0] m_rresp_i,
    input  logic [NoSlaves-1:0] m_rlast_i,
    input  logic [NoSlaves-1:0][UW-1:0] m_ruser_i,
    input  logic [NoSlaves-1:0] m_rvalid_i,
    output logic [NoSlaves-1:0] m_rready_o
);

  localparam int unsigned SelWidth = (NoSlaves > 1) ? $clog2(NoSlaves) : 1;
  localparam int unsigned ReqAddrWidth = SelWidth + AddrWidth;

  // The slave side carries the master index above the master-side ID.
  if (IdWidthSlv != IdWidthMst + ((NoMasters > 1) ? unsigned'($clog2(NoMasters)) : 0))
  begin : gen_bad_id_width
    $fatal(1, "IdWidthSlv must be IdWidthMst + clog2(NoMasters)");
  end

  typedef logic [ReqAddrWidth-1:0] addr_t;
  typedef logic [DataWidth-1:0] data_t;
  typedef logic [DataWidth/8-1:0] strb_t;
  typedef logic [IdWidthMst-1:0] mside_id_t;
  typedef logic [IdWidthSlv-1:0] sside_id_t;
  typedef logic [UW-1:0] user_t;
  // mside_*: requests from BAG masters; sside_*: requests to BAG slaves.
  `AXI_TYPEDEF_ALL(mside, addr_t, mside_id_t, data_t, strb_t, user_t)
  `AXI_TYPEDEF_ALL(sside, addr_t, sside_id_t, data_t, strb_t, user_t)

  mside_req_t  [NoMasters-1:0] in_req, guard_req;
  mside_resp_t [NoMasters-1:0] in_resp, guard_resp;
  sside_req_t  [NoSlaves-1:0] out_req;
  sside_resp_t [NoSlaves-1:0] out_resp;

  for (genvar m = 0; m < NoMasters; m++) begin : gen_masters
    bag_axi_from_flat #(
        .AddrWidth   (AddrWidth),
        .DataWidth   (DataWidth),
        .IdWidth     (IdWidthMst),
        .UserWidth   (UW),
        .ReqAddrWidth(ReqAddrWidth),
        .axi_req_t   (mside_req_t),
        .axi_resp_t  (mside_resp_t)
    ) i_from_flat (
        .awid_i(s_awid_i[m]),
        .awaddr_i(s_awaddr_i[m]),
        .awlen_i(s_awlen_i[m]),
        .awsize_i(s_awsize_i[m]),
        .awburst_i(s_awburst_i[m]),
        .awlock_i(s_awlock_i[m]),
        .awcache_i(s_awcache_i[m]),
        .awprot_i(s_awprot_i[m]),
        .awqos_i(s_awqos_i[m]),
        .awregion_i(s_awregion_i[m]),
        .awuser_i(s_awuser_i[m]),
        .awvalid_i(s_awvalid_i[m]),
        .awready_o(s_awready_o[m]),
        .wdata_i(s_wdata_i[m]),
        .wstrb_i(s_wstrb_i[m]),
        .wlast_i(s_wlast_i[m]),
        .wuser_i(s_wuser_i[m]),
        .wvalid_i(s_wvalid_i[m]),
        .wready_o(s_wready_o[m]),
        .bid_o(s_bid_o[m]),
        .bresp_o(s_bresp_o[m]),
        .buser_o(s_buser_o[m]),
        .bvalid_o(s_bvalid_o[m]),
        .bready_i(s_bready_i[m]),
        .arid_i(s_arid_i[m]),
        .araddr_i(s_araddr_i[m]),
        .arlen_i(s_arlen_i[m]),
        .arsize_i(s_arsize_i[m]),
        .arburst_i(s_arburst_i[m]),
        .arlock_i(s_arlock_i[m]),
        .arcache_i(s_arcache_i[m]),
        .arprot_i(s_arprot_i[m]),
        .arqos_i(s_arqos_i[m]),
        .arregion_i(s_arregion_i[m]),
        .aruser_i(s_aruser_i[m]),
        .arvalid_i(s_arvalid_i[m]),
        .arready_o(s_arready_o[m]),
        .rid_o(s_rid_o[m]),
        .rdata_o(s_rdata_o[m]),
        .rresp_o(s_rresp_o[m]),
        .rlast_o(s_rlast_o[m]),
        .ruser_o(s_ruser_o[m]),
        .rvalid_o(s_rvalid_o[m]),
        .rready_i(s_rready_i[m]),
        .req_o (in_req[m]),
        .resp_i(in_resp[m])
    );

    bag_axi_guard #(
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
        .IdWidth      (IdWidthMst),
        .MaxTrans     (MaxMstTrans),
        .ErrResp      (MissResp[m]),
        .axi_req_t    (mside_req_t),
        .axi_resp_t   (mside_resp_t)
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

  // PULP naming: crossbar slave ports = BAG masters, master ports = BAG slaves.
  localparam axi_pkg::xbar_cfg_t Cfg = '{
      NoSlvPorts:         NoMasters,
      NoMstPorts:         NoSlaves,
      // + 1: axi_demux's ID counters saturate at 2^clog2(MaxTrans) - 1 (see bag_axi_guard), so
      // Max mst trans alone would admit fewer per ID than configured.
      MaxMstTrans:        MaxMstTrans + 1,
      MaxSlvTrans:        MaxSlvTrans,
      FallThrough:        1'b0,
      LatencyMode:        LatencyMode,
      PipelineStages:     32'd0,
      AxiIdWidthSlvPorts: IdWidthMst,
      AxiIdUsedSlvPorts:  IdWidthMst,
      UniqueIds:          1'b0,
      AxiAddrWidth:       ReqAddrWidth,
      AxiDataWidth:       DataWidth,
      NoAddrRules:        NoSlaves
  };

  typedef struct packed {
    int unsigned idx;
    addr_t       start_addr;
    addr_t       end_addr;
  } rule_t;

  // Route bits = slave port. The last slave's end wraps to 0: end of address space.
  function automatic rule_t [NoSlaves-1:0] route_map();
    for (int unsigned s = 0; s < NoSlaves; s++) begin
      route_map[s].idx        = s;
      route_map[s].start_addr = addr_t'(s) << AddrWidth;
      route_map[s].end_addr   = (addr_t'(s) + addr_t'(1)) << AddrWidth;
    end
  endfunction
  localparam rule_t [NoSlaves-1:0] AddrMap = route_map();

  // Pairs the guards never route (Access_Rules none) need no crossbar path.
  function automatic logic [NoMasters-1:0][NoSlaves-1:0] connectivity();
    connectivity = Connect;
    for (int unsigned m = 0; m < NoMasters; m++) begin
      if (MissToDefault[m]) connectivity[m][DefaultSlave[m]] = 1'b1;
    end
  endfunction

  axi_xbar #(
      .Cfg          (Cfg),
      .ATOPs        (1'b0),
      .Connectivity (connectivity()),
      .slv_aw_chan_t(mside_aw_chan_t),
      .mst_aw_chan_t(sside_aw_chan_t),
      .w_chan_t     (mside_w_chan_t),
      .slv_b_chan_t (mside_b_chan_t),
      .mst_b_chan_t (sside_b_chan_t),
      .slv_ar_chan_t(mside_ar_chan_t),
      .mst_ar_chan_t(sside_ar_chan_t),
      .slv_r_chan_t (mside_r_chan_t),
      .mst_r_chan_t (sside_r_chan_t),
      .slv_req_t    (mside_req_t),
      .slv_resp_t   (mside_resp_t),
      .mst_req_t    (sside_req_t),
      .mst_resp_t   (sside_resp_t),
      .rule_t       (rule_t)
  ) i_xbar (
      .clk_i,
      .rst_ni,
      .test_i               (1'b0),
      .slv_ports_req_i      (guard_req),
      .slv_ports_resp_o     (guard_resp),
      .mst_ports_req_o      (out_req),
      .mst_ports_resp_i     (out_resp),
      .addr_map_i           (AddrMap),
      .en_default_mst_port_i('0),  // the guards resolve misses
      .default_mst_port_i   ('0)
  );

  for (genvar s = 0; s < NoSlaves; s++) begin : gen_slaves
    bag_axi_to_flat #(
        .AddrWidth   (AddrWidth),
        .DataWidth   (DataWidth),
        .IdWidth     (IdWidthSlv),
        .UserWidth   (UW),
        .ReqAddrWidth(ReqAddrWidth),
        .axi_req_t   (sside_req_t),
        .axi_resp_t  (sside_resp_t)
    ) i_to_flat (
        .req_i (out_req[s]),
        .resp_o(out_resp[s]),
        .awid_o(m_awid_o[s]),
        .awaddr_o(m_awaddr_o[s]),
        .awlen_o(m_awlen_o[s]),
        .awsize_o(m_awsize_o[s]),
        .awburst_o(m_awburst_o[s]),
        .awlock_o(m_awlock_o[s]),
        .awcache_o(m_awcache_o[s]),
        .awprot_o(m_awprot_o[s]),
        .awqos_o(m_awqos_o[s]),
        .awregion_o(m_awregion_o[s]),
        .awuser_o(m_awuser_o[s]),
        .awvalid_o(m_awvalid_o[s]),
        .awready_i(m_awready_i[s]),
        .wdata_o(m_wdata_o[s]),
        .wstrb_o(m_wstrb_o[s]),
        .wlast_o(m_wlast_o[s]),
        .wuser_o(m_wuser_o[s]),
        .wvalid_o(m_wvalid_o[s]),
        .wready_i(m_wready_i[s]),
        .bid_i(m_bid_i[s]),
        .bresp_i(m_bresp_i[s]),
        .buser_i(m_buser_i[s]),
        .bvalid_i(m_bvalid_i[s]),
        .bready_o(m_bready_o[s]),
        .arid_o(m_arid_o[s]),
        .araddr_o(m_araddr_o[s]),
        .arlen_o(m_arlen_o[s]),
        .arsize_o(m_arsize_o[s]),
        .arburst_o(m_arburst_o[s]),
        .arlock_o(m_arlock_o[s]),
        .arcache_o(m_arcache_o[s]),
        .arprot_o(m_arprot_o[s]),
        .arqos_o(m_arqos_o[s]),
        .arregion_o(m_arregion_o[s]),
        .aruser_o(m_aruser_o[s]),
        .arvalid_o(m_arvalid_o[s]),
        .arready_i(m_arready_i[s]),
        .rid_i(m_rid_i[s]),
        .rdata_i(m_rdata_i[s]),
        .rresp_i(m_rresp_i[s]),
        .rlast_i(m_rlast_i[s]),
        .ruser_i(m_ruser_i[s]),
        .rvalid_i(m_rvalid_i[s]),
        .rready_o(m_rready_o[s])
    );
  end

endmodule : bag_axi_xbar
