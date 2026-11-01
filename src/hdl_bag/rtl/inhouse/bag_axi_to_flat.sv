// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi_to_flat: one AXI4 slave port of a BAG fabric, PULP structs -> flat signals.
// The slave sees the low AddrWidth bits of the struct address: the fabric's route bits on
// top (see bag_axi_guard) are dropped. ATOP is never set (the guards tie it to 0).

module bag_axi_to_flat #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned DataWidth    = 32,
    parameter int unsigned IdWidth      = 1,
    parameter int unsigned UserWidth    = 1,
    parameter int unsigned ReqAddrWidth = AddrWidth,  // struct address (with route bits)
    parameter type         axi_req_t    = logic,
    parameter type         axi_resp_t   = logic
) (
    input  axi_req_t  req_i,
    output axi_resp_t resp_o,

    output logic [    IdWidth-1:0] awid_o,
    output logic [  AddrWidth-1:0] awaddr_o,
    output logic [            7:0] awlen_o,
    output logic [            2:0] awsize_o,
    output logic [            1:0] awburst_o,
    output logic                   awlock_o,
    output logic [            3:0] awcache_o,
    output logic [            2:0] awprot_o,
    output logic [            3:0] awqos_o,
    output logic [            3:0] awregion_o,
    output logic [  UserWidth-1:0] awuser_o,
    output logic                   awvalid_o,
    input  logic                   awready_i,
    output logic [  DataWidth-1:0] wdata_o,
    output logic [DataWidth/8-1:0] wstrb_o,
    output logic                   wlast_o,
    output logic [  UserWidth-1:0] wuser_o,
    output logic                   wvalid_o,
    input  logic                   wready_i,
    input  logic [    IdWidth-1:0] bid_i,
    input  logic [            1:0] bresp_i,
    input  logic [  UserWidth-1:0] buser_i,
    input  logic                   bvalid_i,
    output logic                   bready_o,
    output logic [    IdWidth-1:0] arid_o,
    output logic [  AddrWidth-1:0] araddr_o,
    output logic [            7:0] arlen_o,
    output logic [            2:0] arsize_o,
    output logic [            1:0] arburst_o,
    output logic                   arlock_o,
    output logic [            3:0] arcache_o,
    output logic [            2:0] arprot_o,
    output logic [            3:0] arqos_o,
    output logic [            3:0] arregion_o,
    output logic [  UserWidth-1:0] aruser_o,
    output logic                   arvalid_o,
    input  logic                   arready_i,
    input  logic [    IdWidth-1:0] rid_i,
    input  logic [  DataWidth-1:0] rdata_i,
    input  logic [            1:0] rresp_i,
    input  logic                   rlast_i,
    input  logic [  UserWidth-1:0] ruser_i,
    input  logic                   rvalid_i,
    output logic                   rready_o
);

  logic [ReqAddrWidth-1:0] aw_addr, ar_addr;
  assign aw_addr = req_i.aw.addr;
  assign ar_addr = req_i.ar.addr;

  assign awid_o     = req_i.aw.id;
  assign awaddr_o   = aw_addr[AddrWidth-1:0];
  assign awlen_o    = req_i.aw.len;
  assign awsize_o   = req_i.aw.size;
  assign awburst_o  = req_i.aw.burst;
  assign awlock_o   = req_i.aw.lock;
  assign awcache_o  = req_i.aw.cache;
  assign awprot_o   = req_i.aw.prot;
  assign awqos_o    = req_i.aw.qos;
  assign awregion_o = req_i.aw.region;
  assign awuser_o   = req_i.aw.user;
  assign awvalid_o  = req_i.aw_valid;
  assign wdata_o    = req_i.w.data;
  assign wstrb_o    = req_i.w.strb;
  assign wlast_o    = req_i.w.last;
  assign wuser_o    = req_i.w.user;
  assign wvalid_o   = req_i.w_valid;
  assign bready_o   = req_i.b_ready;
  assign arid_o     = req_i.ar.id;
  assign araddr_o   = ar_addr[AddrWidth-1:0];
  assign arlen_o    = req_i.ar.len;
  assign arsize_o   = req_i.ar.size;
  assign arburst_o  = req_i.ar.burst;
  assign arlock_o   = req_i.ar.lock;
  assign arcache_o  = req_i.ar.cache;
  assign arprot_o   = req_i.ar.prot;
  assign arqos_o    = req_i.ar.qos;
  assign arregion_o = req_i.ar.region;
  assign aruser_o   = req_i.ar.user;
  assign arvalid_o  = req_i.ar_valid;
  assign rready_o   = req_i.r_ready;

  always_comb begin
    resp_o          = '0;
    resp_o.aw_ready = awready_i;
    resp_o.w_ready  = wready_i;
    resp_o.b.id     = bid_i;
    resp_o.b.resp   = bresp_i;
    resp_o.b.user   = buser_i;
    resp_o.b_valid  = bvalid_i;
    resp_o.ar_ready = arready_i;
    resp_o.r.id     = rid_i;
    resp_o.r.data   = rdata_i;
    resp_o.r.resp   = rresp_i;
    resp_o.r.last   = rlast_i;
    resp_o.r.user   = ruser_i;
    resp_o.r_valid  = rvalid_i;
  end

  // Route bits and ATOP are internal to the fabric.
  if (ReqAddrWidth > AddrWidth) begin : gen_route_bits
    logic unused_route;
    assign unused_route = ^{aw_addr[ReqAddrWidth-1:AddrWidth], ar_addr[ReqAddrWidth-1:AddrWidth]};
  end
  logic unused_atop;
  assign unused_atop = ^req_i.aw.atop;

endmodule : bag_axi_to_flat
