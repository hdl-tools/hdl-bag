// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// AHB-Lite port-protocol properties (Phase 5 PR 2b, D54: written in-house). References are
// to AMBA 3 AHB-Lite IHI0033A.
//
// One instance watches one AHB-Lite port of the top. DesignIsSlave says which side the design
// is on: each rule is asserted on what the design drives and assumed on what the environment
// drives (address and control come from the master side, responses from the slave side).
//
//   master port (DesignIsSlave = 1): the environment is a master; the design answers with
//     HREADY and HRESP. The caller ties hsel high and passes HREADY as hreadyout.
//   slave port (DesignIsSlave = 0): the design drives HSEL, the address phase and the bus
//     HREADY into an environment slave, which answers with HREADYOUT and HRESP.
//
// Every rule is sampled at a rising edge of clk_i, on the values before the edge. A cycle in
// reset (rst_ni low at that edge or at the edge before) checks nothing.
//
// The checks:
//   hold       a NONSEQ or SEQ address phase that waits (HREADY low) keeps its address and
//              control until HREADY is high, unless the master cancels it with IDLE after the
//              first cycle of an ERROR (3.6.1, 5.1.3; on a slave port, any IDLE while another
//              slave's data phase holds the bus); write data holds while its data phase
//              waits (3.6.1); a waiting IDLE turns only into NONSEQ (3.6.1, environment
//              masters)
//   size       HSIZE no wider than the bus, and the address aligned to it (3.4)
//   bursts     SEQ and BUSY only inside a burst; each SEQ and BUSY carries the burst's
//              address (incrementing or wrapping) and control (HNONSEC included); an
//              incrementing burst never crosses 1 KB; an environment master ends a
//              fixed-length burst only after its last beat or an ERROR (3.5; not asserted on
//              the design, 3.5.1)
//   responses  ERROR takes two cycles, HREADY low then high (5.1.3); an IDLE or BUSY transfer
//              gets a zero-wait OKAY (3.2); on a slave port, the slave's HREADYOUT reaches it
//              as HREADY during its data phase (the interconnect's routing, 4.1)

`default_nettype none

module bag_fahb #(
    parameter bit          DesignIsSlave = 1'b1,
    parameter int unsigned AddrWidth     = 32,
    parameter int unsigned DataWidth     = 32
) (
    input wire                 clk_i,
    input wire                 rst_ni,
    input wire                 hsel,
    input wire [AddrWidth-1:0] haddr,
    input wire [1:0]           htrans,
    input wire                 hwrite,
    input wire [2:0]           hsize,
    input wire [2:0]           hburst,
    input wire [3:0]           hprot,
    input wire                 hmastlock,
    input wire                 hnonsec,
    input wire [DataWidth-1:0] hwdata,
    input wire                 hready,     // the bus HREADY: address phases advance on it
    input wire                 hreadyout,  // the slave's: its data phase ends on it
    input wire                 hresp,
    input wire [DataWidth-1:0] hrdata,
    // For the caller's covers, at this edge: a write or read data phase completed, a data
    // phase waited, the first ERROR cycle, a SEQ beat accepted.
    output wire                write_done_o,
    output wire                read_done_o,
    output wire                wait_o,
    output wire                error_o,
    output wire                seq_o
);

  localparam int unsigned MaxSize = $clog2(DataWidth / 8);
  localparam logic [1:0] Idle = 2'b00, Busy = 2'b01, NonSeq = 2'b10, Seq = 2'b11;

  // HSIZE codes widths from 8 to 1024 bits.
  if (DataWidth < 8 || DataWidth > 1024 || (DataWidth & (DataWidth - 1)) != 0)
  begin : gen_check_data
    $error("bag_fahb: DataWidth must be a power of two from 8 to 1024");
  end

  // `live`: this edge and the one before were out of reset.
  logic was_out = 1'b0;
  always_ff @(posedge clk_i) was_out <= rst_ni;
  wire live = rst_ni && was_out;

  // This edge's address phase: accepted when the bus is ready and the port is selected.
  wire take = hsel && hready;
  wire xfer = htrans[1];  // NONSEQ or SEQ

  // ---------------------------------------------------------------- data phase
  // What the address phase accepted at the last ready edge; the data phase ends at the
  // slave's HREADYOUT. Out of reset there is no transfer: a master port's design answers as
  // for an IDLE (HREADY high, 3.1), a slave port's environment is not in a data phase.
  logic dp_sel = 1'b0, dp_xfer = 1'b0, dp_write = 1'b0;
  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      dp_sel <= DesignIsSlave;
      dp_xfer <= 1'b0;
      dp_write <= 1'b0;
    end else if (hready) begin
      dp_sel <= hsel;
      dp_xfer <= take && xfer;
      dp_write <= hwrite;
    end
  end

  // ---------------------------------------------------------------- previous edge
  localparam int unsigned Cw = AddrWidth + 2 + 1 + 3 + 3 + 4 + 1 + 1 + 1;
  wire [Cw-1:0] ctrl_now = {haddr, htrans, hwrite, hsize, hburst, hprot, hmastlock, hnonsec,
                            hsel};
  logic [Cw-1:0] ctrl_q;
  logic [DataWidth-1:0] hwdata_q;
  logic pend_q = 1'b0;   // a NONSEQ or SEQ address phase waited at the last edge
  logic wwait_q = 1'b0;  // a write data phase waited at the last edge
  logic err1_q = 1'b0;   // the first ERROR cycle was at the last edge
  logic idlew_q = 1'b0;  // an IDLE address phase waited at the last edge
  wire err1 = dp_xfer && hresp && !hreadyout;
  always_ff @(posedge clk_i) begin
    ctrl_q <= ctrl_now;
    hwdata_q <= hwdata;
    pend_q <= rst_ni && hsel && xfer && !hready;
    wwait_q <= rst_ni && dp_xfer && dp_write && !hreadyout;
    err1_q <= rst_ni && err1;
    idlew_q <= rst_ni && hsel && htrans == Idle && !hready;
  end

  // ---------------------------------------------------------------- master's rules
  // The cancel to IDLE after an ERROR's first cycle. On a slave port the ERROR may have come
  // from another slave or the interconnect, which this port cannot see, so a change to IDLE
  // is allowed there while another slave's data phase holds the bus; while this slave's own
  // does (dp_sel: it waits on this slave), the ERROR must be its own. Every other change of
  // a waiting address phase is not allowed.
  wire cancel = htrans == Idle && (err1_q || (!DesignIsSlave && !dp_sel));
  wire ok_hold = !(live && pend_q) || ctrl_now == ctrl_q || cancel;
  wire ok_wdata = !(live && wwait_q) || hwdata == hwdata_q;
  // While HREADY is low, a master's IDLE may only turn into NONSEQ (3.6.1). Assumed of an
  // environment master; a slave samples only when HREADY is high.
  wire ok_idle_wait = !(live && idlew_q) || htrans == Idle || htrans == NonSeq;
  wire [AddrWidth-1:0] size_mask = AddrWidth'((1 << hsize) - 1);
  wire ok_size = !(live && hsel && xfer)
                 || ({29'd0, hsize} <= MaxSize && (haddr & size_mask) == '0);

  // Burst state, from the accepted address phases: the burst's control, the next beat's
  // address, and the beats left of a fixed-length burst.
  logic b_on = 1'b0, b_err = 1'b0;
  logic [2:0] b_burst, b_size;
  logic b_write, b_nonsec;
  logic [3:0] b_prot;
  logic [AddrWidth-1:0] b_next;
  logic [4:0] b_left;

  wire        fixed = hburst != 3'd0 && hburst != 3'd1;
  wire [4:0]  beats = (hburst[2:1] == 2'd1) ? 5'd4 : (hburst[2:1] == 2'd2) ? 5'd8 : 5'd16;
  function automatic logic [AddrWidth-1:0] next_addr(input logic [AddrWidth-1:0] a,
                                                     input logic [2:0] size,
                                                     input logic [2:0] burst);
    logic [AddrWidth-1:0] step, span;
    step = AddrWidth'(1) << size;
    if (burst != 3'd0 && burst != 3'd1 && !burst[0]) begin  // WRAP4, WRAP8, WRAP16
      span = step << ((burst[2:1] == 2'd1) ? 2 : (burst[2:1] == 2'd2) ? 3 : 4);
      return (a & ~(span - 1)) | ((a + step) & (span - 1));
    end
    return a + step;
  endfunction
  wire b_fixed = b_burst != 3'd0 && b_burst != 3'd1;
  wire b_incr = !b_fixed || b_burst[0];
  // An incrementing burst may not cross 1 KB: a beat there must start a new burst.
  wire [31:0] b_next32 = 32'(b_next);
  wire b_at_1k = b_incr && b_next32[9:0] == 10'd0;

  wire in_burst = htrans == Seq || htrans == Busy;
  wire ok_burst = !(live && hsel && in_burst)
                  || (b_on && !b_at_1k && haddr == b_next && hburst == b_burst
                      && hsize == b_size && hwrite == b_write && hprot == b_prot
                      && hnonsec == b_nonsec);
  // A master's fixed-length burst with beats left, not excused by an ERROR, shows only SEQ or
  // BUSY, in waits too (3.5, 3.6.1). Only assumed of an environment master: a slave must cope
  // with a burst an interconnect ends early (3.5.1), so it is not asserted on what the design
  // sends to a slave.
  wire ok_burst_ends = !(live && hsel && b_on && b_fixed && b_left != 5'd0 && !b_err)
                       || in_burst;

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      b_on <= 1'b0;
      b_err <= 1'b0;
    end else begin
      if (err1) b_err <= 1'b1;
      if (take) begin
        if (htrans == NonSeq) begin
          b_on <= hburst != 3'd0;
          b_err <= 1'b0;
          b_burst <= hburst;
          b_size <= hsize;
          b_write <= hwrite;
          b_prot <= hprot;
          b_nonsec <= hnonsec;
          b_next <= next_addr(haddr, hsize, hburst);
          b_left <= fixed ? beats - 5'd1 : 5'd0;
        end else if (htrans == Seq) begin
          b_next <= next_addr(haddr, hsize, b_burst);
          if (b_fixed) begin
            b_left <= b_left - 5'd1;
            if (b_left == 5'd1) b_on <= 1'b0;
          end
        end else if (htrans == Idle) begin
          b_on <= 1'b0;
        end
      end else if (!hsel && hready) begin
        b_on <= 1'b0;  // the master moved elsewhere: no burst continues here
      end
    end
  end

  // ---------------------------------------------------------------- slave's rules
  wire ok_err_second = !(live && err1_q) || (hresp && hreadyout);
  wire ok_err_first = !(live && dp_xfer && hresp && hreadyout) || err1_q;
  wire ok_idle_okay = !(live && dp_sel && !dp_xfer) || (hreadyout && !hresp);

  // ---------------------------------------------------------------- assert or assume
  // The design's rules are asserted at each edge, the environment's assumed at every solver
  // step (as in bag_faxi4): what the design passes on combinationally is then legal in a
  // trace's last step too.
  if (DesignIsSlave) begin : gen_design_is_slave
    always_comb begin
      if (rst_ni) begin
        assume (ok_hold);
        assume (ok_wdata);
        assume (ok_size);
        assume (ok_burst);
        assume (ok_burst_ends);
        assume (ok_idle_wait);
      end
    end
    always_ff @(posedge clk_i) begin
      if (rst_ni) begin
        error_second_cycle: assert (ok_err_second);
        error_takes_two_cycles: assert (ok_err_first);
        idle_gets_zero_wait_okay: assert (ok_idle_okay);
      end
    end
  end else begin : gen_design_is_master
    // The interconnect routes this slave's HREADYOUT to it as HREADY in its data phase.
    wire ok_routed = !(live && dp_sel) || hready == hreadyout;
    always_ff @(posedge clk_i) begin
      if (rst_ni) begin
        address_holds_while_waiting: assert (ok_hold);
        write_data_holds_while_waiting: assert (ok_wdata);
        size_fits_and_aligned: assert (ok_size);
        seq_follows_its_burst: assert (ok_burst);
        hreadyout_reaches_hready: assert (ok_routed);
      end
    end
    always_comb begin
      if (rst_ni) begin
        assume (ok_err_second);
        assume (ok_err_first);
        assume (ok_idle_okay);
      end
    end
  end

  assign write_done_o = live && dp_xfer && dp_write && hreadyout;
  assign read_done_o = live && dp_xfer && !dp_write && hreadyout;
  assign wait_o = live && dp_xfer && !hreadyout;
  assign error_o = live && err1;
  assign seq_o = live && take && htrans == Seq;

  // Unused here, kept for a complete port.
  logic unused;
  assign unused = ^{hrdata};

endmodule

`default_nettype wire
