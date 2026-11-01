// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi_from_flat: one AXI4 master port of a BAG fabric, flat signals -> PULP structs.
// The struct address may be wider than the port (the fabric's route bits sit on top, see
// bag_axi_guard); it is zero-extended here. AXI4 atomics (ATOP) are not carried: atop = 0.

module bag_axi_from_flat #(
    parameter int unsigned AddrWidth = 32,
    parameter int unsigned DataWidth = 32,
    parameter int unsigned IdWidth   = 1,
    parameter int unsigned UserWidth = 1,
    parameter int unsigned ReqAddrWidth = AddrWidth,  // struct address (with route bits)
    parameter type         axi_req_t  = logic,
    parameter type         axi_resp_t = logic
) (
    input  logic [  IdWidth-1:0] awid_i,
    input  logic [AddrWidth-1:0] awaddr_i,
    input  logic [          7:0] awlen_i,
    input  logic [          2:0] awsize_i,
    input  logic [          1:0] awburst_i,
    input  logic                 awlock_i,
    input  logic [          3:0] awcache_i,
    input  logic [          2:0] awprot_i,
    input  logic [          3:0] awqos_i,
    input  logic [          3:0] awregion_i,
    input  logic [UserWidth-1:0] awuser_i,
    input  logic                 awvalid_i,
    output logic                 awready_o,
    input  logic [DataWidth-1:0] wdata_i,
    input  logic [DataWidth/8-1:0] wstrb_i,
    input  logic                 wlast_i,
    input  logic [UserWidth-1:0] wuser_i,
    input  logic                 wvalid_i,
    output logic                 wready_o,
    output logic [  IdWidth-1:0] bid_o,
    output logic [          1:0] bresp_o,
    output logic [UserWidth-1:0] buser_o,
    output logic                 bvalid_o,
    input  logic                 bready_i,
    input  logic [  IdWidth-1:0] arid_i,
    input  logic [AddrWidth-1:0] araddr_i,
    input  logic [          7:0] arlen_i,
    input  logic [          2:0] arsize_i,
    input  logic [          1:0] arburst_i,
    input  logic                 arlock_i,
    input  logic [          3:0] arcache_i,
    input  logic [          2:0] arprot_i,
    input  logic [          3:0] arqos_i,
    input  logic [          3:0] arregion_i,
    input  logic [UserWidth-1:0] aruser_i,
    input  logic                 arvalid_i,
    output logic                 arready_o,
    output logic [  IdWidth-1:0] rid_o,
    output logic [DataWidth-1:0] rdata_o,
    output logic [          1:0] rresp_o,
    output logic                 rlast_o,
    output logic [UserWidth-1:0] ruser_o,
    output logic                 rvalid_o,
    input  logic                 rready_i,

    output axi_req_t  req_o,
    input  axi_resp_t resp_i
);

  always_comb begin
    req_o           = '0;
    req_o.aw.id     = awid_i;
    req_o.aw.addr   = ReqAddrWidth'(awaddr_i);  // zero-extended into the route bits
    req_o.aw.len    = awlen_i;
    req_o.aw.size   = awsize_i;
    req_o.aw.burst  = awburst_i;
    req_o.aw.lock   = awlock_i;
    req_o.aw.cache  = awcache_i;
    req_o.aw.prot   = awprot_i;
    req_o.aw.qos    = awqos_i;
    req_o.aw.region = awregion_i;
    req_o.aw.user   = awuser_i;
    req_o.aw_valid  = awvalid_i;
    req_o.w.data    = wdata_i;
    req_o.w.strb    = wstrb_i;
    req_o.w.last    = wlast_i;
    req_o.w.user    = wuser_i;
    req_o.w_valid   = wvalid_i;
    req_o.b_ready   = bready_i;
    req_o.ar.id     = arid_i;
    req_o.ar.addr   = ReqAddrWidth'(araddr_i);
    req_o.ar.len    = arlen_i;
    req_o.ar.size   = arsize_i;
    req_o.ar.burst  = arburst_i;
    req_o.ar.lock   = arlock_i;
    req_o.ar.cache  = arcache_i;
    req_o.ar.prot   = arprot_i;
    req_o.ar.qos    = arqos_i;
    req_o.ar.region = arregion_i;
    req_o.ar.user   = aruser_i;
    req_o.ar_valid  = arvalid_i;
    req_o.r_ready   = rready_i;
  end

  assign awready_o = resp_i.aw_ready;
  assign wready_o  = resp_i.w_ready;
  assign bid_o     = resp_i.b.id;
  assign bresp_o   = resp_i.b.resp;
  assign buser_o   = resp_i.b.user;
  assign bvalid_o  = resp_i.b_valid;
  assign arready_o = resp_i.ar_ready;
  assign rid_o     = resp_i.r.id;
  assign rdata_o   = resp_i.r.data;
  assign rresp_o   = resp_i.r.resp;
  assign rlast_o   = resp_i.r.last;
  assign ruser_o   = resp_i.r.user;
  assign rvalid_o  = resp_i.r_valid;

endmodule : bag_axi_from_flat
