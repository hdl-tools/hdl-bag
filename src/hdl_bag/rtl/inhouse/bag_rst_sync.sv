// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Reset conditioning for one clock domain. The output is always active-low.
//   AsyncAssert = 1: reset asserts immediately, deasserts after Stages clocks.
//   AsyncAssert = 0: reset asserts and deasserts after Stages clocks.

module bag_rst_sync #(
    parameter bit          ActiveLow   = 1'b1,
    parameter bit          AsyncAssert = 1'b1,
    parameter int unsigned Stages      = 2
) (
    input  logic clk_i,
    input  logic rst_i,
    output logic rst_no
);

  if (Stages < 2) begin : gen_stages_check
    $error("bag_rst_sync: Stages must be at least 2");
  end

  logic rst_n;
  // The last stage is both flopped data and the domain's asynchronous reset: that is
  // what a reset synchronizer is.
  /* verilator lint_off SYNCASYNCNET */
  logic [Stages-1:0] q;
  /* verilator lint_on SYNCASYNCNET */

  assign rst_n = ActiveLow ? rst_i : ~rst_i;

  if (AsyncAssert) begin : gen_async
    always_ff @(posedge clk_i or negedge rst_n) begin
      if (!rst_n) begin
        q <= '0;
      end else begin
        q <= {q[Stages-2:0], 1'b1};
      end
    end
  end else begin : gen_sync
    always_ff @(posedge clk_i) begin
      q <= {q[Stages-2:0], rst_n};
    end
  end

  assign rst_no = q[Stages-1];

endmodule
