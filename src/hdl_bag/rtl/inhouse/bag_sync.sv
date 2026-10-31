// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Multi-flop synchronizer for a single-bit level signal crossing into clk_i.

module bag_sync #(
    parameter int unsigned Stages     = 2,
    parameter bit          ResetValue = 1'b0
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic d_i,
    output logic q_o
);

  if (Stages < 2) begin : gen_stages_check
    $error("bag_sync: Stages must be at least 2");
  end

  logic [Stages-1:0] q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      q <= {Stages{ResetValue}};
    end else begin
      q <= {q[Stages-2:0], d_i};
    end
  end

  assign q_o = q[Stages-1];

endmodule
