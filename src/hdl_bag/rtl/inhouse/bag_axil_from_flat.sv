// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_from_flat: one AXI4-Lite master port of a BAG fabric, flat signals -> PULP
// structs. The struct address may be wider than the port (the fabric's route bits sit on
// top, see bag_axil_guard); it is zero-extended here.

module bag_axil_from_flat #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned DataWidth    = 32,
    parameter int unsigned ReqAddrWidth = AddrWidth,  // struct address (with route bits)
    parameter type         axi_req_t    = logic,
    parameter type         axi_resp_t   = logic
) (
    input  logic [  AddrWidth-1:0] awaddr_i,
    input  logic [            2:0] awprot_i,
    input  logic                   awvalid_i,
    output logic                   awready_o,
    input  logic [  DataWidth-1:0] wdata_i,
    input  logic [DataWidth/8-1:0] wstrb_i,
    input  logic                   wvalid_i,
    output logic                   wready_o,
    output logic [            1:0] bresp_o,
    output logic                   bvalid_o,
    input  logic                   bready_i,
    input  logic [  AddrWidth-1:0] araddr_i,
    input  logic [            2:0] arprot_i,
    input  logic                   arvalid_i,
    output logic                   arready_o,
    output logic [  DataWidth-1:0] rdata_o,
    output logic [            1:0] rresp_o,
    output logic                   rvalid_o,
    input  logic                   rready_i,

    output axi_req_t  req_o,
    input  axi_resp_t resp_i
);

  always_comb begin
    req_o          = '0;
    req_o.aw.addr  = ReqAddrWidth'(awaddr_i);  // zero-extended into the route bits
    req_o.aw.prot  = awprot_i;
    req_o.aw_valid = awvalid_i;
    req_o.w.data   = wdata_i;
    req_o.w.strb   = wstrb_i;
    req_o.w_valid  = wvalid_i;
    req_o.b_ready  = bready_i;
    req_o.ar.addr  = ReqAddrWidth'(araddr_i);
    req_o.ar.prot  = arprot_i;
    req_o.ar_valid = arvalid_i;
    req_o.r_ready  = rready_i;
  end

  assign awready_o = resp_i.aw_ready;
  assign wready_o  = resp_i.w_ready;
  assign bresp_o   = resp_i.b.resp;
  assign bvalid_o  = resp_i.b_valid;
  assign arready_o = resp_i.ar_ready;
  assign rdata_o   = resp_i.r.data;
  assign rresp_o   = resp_i.r.resp;
  assign rvalid_o  = resp_i.r_valid;

endmodule : bag_axil_from_flat
