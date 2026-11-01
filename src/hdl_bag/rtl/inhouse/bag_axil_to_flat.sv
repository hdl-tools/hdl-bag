// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_to_flat: one AXI4-Lite slave port of a BAG fabric, PULP structs -> flat
// signals. The slave sees the low AddrWidth bits of the struct address: the fabric's
// route bits on top (see bag_axil_guard) are dropped.

module bag_axil_to_flat #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned DataWidth    = 32,
    parameter int unsigned ReqAddrWidth = AddrWidth,  // struct address (with route bits)
    parameter type         axi_req_t    = logic,
    parameter type         axi_resp_t   = logic
) (
    input  axi_req_t  req_i,
    output axi_resp_t resp_o,

    output logic [  AddrWidth-1:0] awaddr_o,
    output logic [            2:0] awprot_o,
    output logic                   awvalid_o,
    input  logic                   awready_i,
    output logic [  DataWidth-1:0] wdata_o,
    output logic [DataWidth/8-1:0] wstrb_o,
    output logic                   wvalid_o,
    input  logic                   wready_i,
    input  logic [            1:0] bresp_i,
    input  logic                   bvalid_i,
    output logic                   bready_o,
    output logic [  AddrWidth-1:0] araddr_o,
    output logic [            2:0] arprot_o,
    output logic                   arvalid_o,
    input  logic                   arready_i,
    input  logic [  DataWidth-1:0] rdata_i,
    input  logic [            1:0] rresp_i,
    input  logic                   rvalid_i,
    output logic                   rready_o
);

  logic [ReqAddrWidth-1:0] aw_addr, ar_addr;
  assign aw_addr = req_i.aw.addr;
  assign ar_addr = req_i.ar.addr;

  assign awaddr_o  = aw_addr[AddrWidth-1:0];
  assign awprot_o  = req_i.aw.prot;
  assign awvalid_o = req_i.aw_valid;
  assign wdata_o   = req_i.w.data;
  assign wstrb_o   = req_i.w.strb;
  assign wvalid_o  = req_i.w_valid;
  assign bready_o  = req_i.b_ready;
  assign araddr_o  = ar_addr[AddrWidth-1:0];
  assign arprot_o  = req_i.ar.prot;
  assign arvalid_o = req_i.ar_valid;
  assign rready_o  = req_i.r_ready;

  always_comb begin
    resp_o          = '0;
    resp_o.aw_ready = awready_i;
    resp_o.w_ready  = wready_i;
    resp_o.b.resp   = bresp_i;
    resp_o.b_valid  = bvalid_i;
    resp_o.ar_ready = arready_i;
    resp_o.r.data   = rdata_i;
    resp_o.r.resp   = rresp_i;
    resp_o.r_valid  = rvalid_i;
  end

  if (ReqAddrWidth > AddrWidth) begin : gen_route_bits
    logic unused_route;
    assign unused_route = ^{aw_addr[ReqAddrWidth-1:AddrWidth], ar_addr[ReqAddrWidth-1:AddrWidth]};
  end

endmodule : bag_axil_to_flat
