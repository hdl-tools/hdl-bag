// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_prio_arb: drop-in for common_cells rr_arb_tree as the PULP AXI muxes use it
// (AxiVldRdy, LockIn), with an optional fixed priority.
//
// FixedPrio = 0: exactly rr_arb_tree with LockIn = 1 (round robin).
// FixedPrio = 1: the lowest requesting index wins (rr_arb_tree with ExtPrio and rr_i = '0
//   selects the left input whenever it requests). rr_arb_tree forbids LockIn with ExtPrio,
//   so the lock is kept here: once a request is presented but not granted (req_o & !gnt_i),
//   only that input may request until it is granted, so the payload stays stable as AXI
//   requires and as the muxes' W-routing FIFOs assume.
//
// Ports and the parameters the muxes set keep rr_arb_tree's names.

module bag_prio_arb #(
    parameter int unsigned NumIn     = 64,
    parameter int unsigned DataWidth = 32,
    parameter type         DataType  = logic [DataWidth-1:0],
    parameter bit          AxiVldRdy = 1'b1,
    parameter bit          FixedPrio = 1'b0,
    parameter int unsigned IdxWidth  = (NumIn > 32'd1) ? unsigned'($clog2(NumIn)) : 32'd1,
    parameter type         idx_t     = logic [IdxWidth-1:0]
) (
    input  logic                clk_i,
    input  logic                rst_ni,
    input  logic                flush_i,
    input  idx_t                rr_i,
    input  logic    [NumIn-1:0] req_i,
    output logic    [NumIn-1:0] gnt_o,
    input  DataType [NumIn-1:0] data_i,
    output logic                req_o,
    input  logic                gnt_i,
    output DataType             data_o,
    output idx_t                idx_o
);

  if (!FixedPrio) begin : gen_rr
    rr_arb_tree #(
        .NumIn    (NumIn),
        .DataType (DataType),
        .AxiVldRdy(AxiVldRdy),
        .LockIn   (1'b1)
    ) i_arb (
        .clk_i,
        .rst_ni,
        .flush_i,
        .rr_i,
        .req_i,
        .gnt_o,
        .data_i,
        .gnt_i,
        .req_o,
        .data_o,
        .idx_o
    );
  end else begin : gen_fixed
    logic             lock_q;
    idx_t             lock_idx_q;
    logic [NumIn-1:0] req_masked;

    // While locked, only the presented input requests (AXI keeps its valid high).
    always_comb begin
      req_masked = req_i;
      if (lock_q) begin
        req_masked = '0;
        req_masked[lock_idx_q] = req_i[lock_idx_q];
      end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        lock_q     <= 1'b0;
        lock_idx_q <= '0;
      end else if (flush_i) begin
        lock_q <= 1'b0;
      end else begin
        lock_q <= req_o && !gnt_i;
        if (req_o && !gnt_i && !lock_q) lock_idx_q <= idx_o;
      end
    end

    rr_arb_tree #(
        .NumIn    (NumIn),
        .DataType (DataType),
        .ExtPrio  (1'b1),
        .AxiVldRdy(AxiVldRdy),
        .LockIn   (1'b0)
    ) i_arb (
        .clk_i,
        .rst_ni,
        .flush_i,
        .rr_i   ('0),
        .req_i  (req_masked),
        .gnt_o,
        .data_i,
        .gnt_i,
        .req_o,
        .data_o,
        .idx_o
    );

    logic unused_rr;
    assign unused_rr = ^rr_i;
  end

endmodule : bag_prio_arb
