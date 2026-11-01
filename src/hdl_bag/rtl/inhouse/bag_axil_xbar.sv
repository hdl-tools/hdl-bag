// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_xbar: AXI4-Lite fabric with flat, packed-array ports, built around the PULP crossbar (axi_lite_xbar).
//
// Each BAG master port goes through bag_axil_from_flat and a guard
// (bag_axil_guard: aliases, address rules, Access_Rules, RO/WO,
// secure-only, misses). Each BAG slave port is driven through
// bag_axil_to_flat, which drops the route bits.
// Crossbar: every master's guard decides the slave port and writes it into the route
// bits above the address; the PULP crossbar's address map is the trivial map "route bits
// = slave port", so the guards are the only decoder. PULP naming: the crossbar's *slave*
// ports are where BAG *masters* attach (Cfg.NoSlvPorts = NoMasters) and its *master*
// ports drive BAG slaves (Cfg.NoMstPorts = NoSlaves).
//
// Ports s_* / m_* are packed [port][bits]; the generated wrapper fits narrower ports.
// USER is UserWidth bits (1 bit, unused, when UserWidth = 0). ATOPs are not supported.

`include "axi/typedef.svh"

module bag_axil_xbar #(
    parameter int unsigned AddrWidth   = 32,
    parameter int unsigned DataWidth   = 32,
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

  // PULP naming: crossbar slave ports = BAG masters, master ports = BAG slaves.
  localparam axi_pkg::xbar_cfg_t Cfg = '{
      NoSlvPorts:         NoMasters,
      NoMstPorts:         NoSlaves,
      MaxMstTrans:        MaxMstTrans,
      MaxSlvTrans:        MaxSlvTrans,
      FallThrough:        1'b0,
      LatencyMode:        LatencyMode,
      PipelineStages:     32'd0,
      AxiIdWidthSlvPorts: 32'd0,
      AxiIdUsedSlvPorts:  32'd0,
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

  axi_lite_xbar #(
      .Cfg       (Cfg),
      .aw_chan_t (axi_aw_chan_t),
      .w_chan_t  (axi_w_chan_t),
      .b_chan_t  (axi_b_chan_t),
      .ar_chan_t (axi_ar_chan_t),
      .r_chan_t  (axi_r_chan_t),
      .axi_req_t (axi_req_t),
      .axi_resp_t(axi_resp_t),
      .rule_t    (rule_t)
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

endmodule : bag_axil_xbar
