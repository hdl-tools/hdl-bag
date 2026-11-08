// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Registers on the solver's own step, for the formal environment (hdl_bag.formal_gen).
// Read by Yosys's own frontend (read_verilog -formal), where always @($global_clock) updates
// on every solver step whatever the design's clocks do. yosys-slang has no such construct: a
// register on a net marked (* gclk *) is clocked by an ordinary free net there. The slang-read
// property modules instantiate these (read_slang --extern-modules), one per initial value.

`default_nettype none

module bag_step_ff0 (input wire d, output reg q);
  initial q = 1'b0;
  always @($global_clock) q <= d;
endmodule

module bag_step_ff1 (input wire d, output reg q);
  initial q = 1'b1;
  always @($global_clock) q <= d;
endmodule

`default_nettype wire
