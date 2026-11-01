// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// APB3/APB4 slave front end of the in-house APB bridges (bag_apb2ahb, bag_apb2axi).
//
// One APB transfer at a time is handed to an engine in the destination clock through a
// 4-phase req/ack handshake (the bag_ahb2apb pattern):
//   1. In the ACCESS phase the payload (addr, write, wdata, strb, prot) is latched and
//      req rises.
//   2. The engine runs the transfer, holds rdata/err stable and raises ack.
//   3. The front end completes the APB transfer (PREADY, PSLVERR) and drops req; the
//      engine then drops ack. A new transfer starts only once ack is low.
// With AsyncCdc = 1, req and ack pass through bag_sync. The payload and the response are
// held stable while req (or ack) is high, so they cross without synchronizers (CDC/STA:
// constrain those paths with a max-delay, data-path-only exception).
// Reset: prst_ni and dst_rst_ni must assert together (one reset event for both sides).

module bag_hs_apb_slv #(
    parameter int unsigned AddrWidth  = 32,
    parameter int unsigned DataWidth  = 32,
    parameter bit          AsyncCdc   = 1'b0,
    parameter int unsigned SyncStages = 2,

    localparam int unsigned StrbWidth = DataWidth / 8
) (
    // APB slave (upstream clock)
    input  logic                 pclk_i,
    input  logic                 prst_ni,
    input  logic                 psel_i,
    input  logic                 penable_i,
    input  logic                 pwrite_i,
    input  logic [AddrWidth-1:0] paddr_i,
    input  logic [          2:0] pprot_i,
    input  logic [DataWidth-1:0] pwdata_i,
    input  logic [StrbWidth-1:0] pstrb_i,
    output logic                 pready_o,
    output logic [DataWidth-1:0] prdata_o,
    output logic                 pslverr_o,

    // Engine (destination clock)
    input  logic                 dst_clk_i,
    input  logic                 dst_rst_ni,
    output logic                 dst_req_o,    // level, in the destination clock
    output logic [AddrWidth-1:0] dst_addr_o,   // payload: stable while dst_req_o is high
    output logic                 dst_write_o,
    output logic [DataWidth-1:0] dst_wdata_o,
    output logic [StrbWidth-1:0] dst_strb_o,
    output logic [          2:0] dst_prot_o,
    input  logic                 dst_ack_i,    // a destination-clock register
    input  logic [DataWidth-1:0] dst_rdata_i,  // stable while dst_ack_i is high
    input  logic                 dst_err_i
);

  logic req_q, ack_s;

  if (AsyncCdc) begin : gen_cdc
    bag_sync #(.Stages(SyncStages)) u_req_sync (
        .clk_i (dst_clk_i),
        .rst_ni(dst_rst_ni),
        .d_i   (req_q),
        .q_o   (dst_req_o)
    );
    bag_sync #(.Stages(SyncStages)) u_ack_sync (
        .clk_i (pclk_i),
        .rst_ni(prst_ni),
        .d_i   (dst_ack_i),
        .q_o   (ack_s)
    );
  end else begin : gen_no_cdc
    assign dst_req_o = req_q;
    assign ack_s     = dst_ack_i;
    // Without a CDC the engine runs from pclk_i too; the destination clock is unused here.
    logic unused_dst;
    assign unused_dst = ^{dst_clk_i, dst_rst_ni};
  end

  typedef enum logic [1:0] {
    Idle,
    Wait,
    Done
  } state_e;
  state_e s_q;

  logic [DataWidth-1:0] rdata_q;
  logic                 err_q;

  always_ff @(posedge pclk_i or negedge prst_ni) begin
    if (!prst_ni) begin
      s_q         <= Idle;
      req_q       <= 1'b0;
      dst_addr_o  <= '0;
      dst_write_o <= 1'b0;
      dst_wdata_o <= '0;
      dst_strb_o  <= '0;
      dst_prot_o  <= '0;
      rdata_q     <= '0;
      err_q       <= 1'b0;
    end else begin
      case (s_q)
        Idle: begin
          if (psel_i && penable_i && !ack_s) begin
            dst_addr_o  <= paddr_i;
            dst_write_o <= pwrite_i;
            dst_wdata_o <= pwdata_i;
            dst_strb_o  <= pwrite_i ? pstrb_i : '0;
            dst_prot_o  <= pprot_i;
            req_q       <= 1'b1;
            s_q         <= Wait;
          end
        end
        Wait: begin
          if (ack_s) begin
            req_q   <= 1'b0;
            rdata_q <= dst_rdata_i;
            err_q   <= dst_err_i;
            s_q     <= Done;
          end
        end
        Done:    s_q <= Idle;  // PREADY was high for one cycle
        default: s_q <= Idle;
      endcase
    end
  end

  assign pready_o  = s_q == Done;
  assign prdata_o  = rdata_q;
  assign pslverr_o = s_q == Done && err_q;

endmodule
