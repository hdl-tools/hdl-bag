// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// AXI4 port-protocol properties (Phase 5 PR 2b, D60): BAG's own, since the public ZipCPU
// full-AXI4 files are deliberately non-functional. References are to AMBA AXI IHI0022.
//
// One instance watches one AXI4 port of the top. DesignIsSlave says which side the design
// is on: each rule is asserted on what the design drives and assumed on what the
// environment drives (requests come from the master, responses from the slave).
//
// Every rule is sampled at a rising edge of clk_i, on the values before the edge, as AXI
// is synchronous to ACLK. A cycle in reset (rst_ni low at that edge or at the edge before)
// checks nothing: a reset abandons every transfer.
//
// Transaction tracking covers every ID (D64) and stays small: write data is matched to
// write addresses in order through a bounded FIFO (W may come before its AW, A3.3.1);
// completed writes owed a response, and outstanding reads with their lengths and beats so
// far, are kept in two tables of the port's own IDs. A response must match the oldest open
// entry of its ID, so the environment slave can answer only what it was asked, and the
// design only what it was asked, on every ID in every trace. Each table holds
// MaxOutstanding entries over all IDs, and the environment keeps within that: an
// environment master opens no more (tracker_has_room_* assumed), and an environment slave
// holds AWREADY, WREADY or ARREADY low while the table they would fill is full, as a slave
// of that acceptance capability may. On the design's side tracker_has_room_* is still
// asserted, so a gap in that cap fails rather than passing silently. A crossbar may
// legally keep more open on one port than the tables hold (axi_xbar_pow2: 5 reads to one
// slave); those states go unexplored. 4 by default (D66): 8 nearly tripled the protocol
// bmc's run time on axi_xbar_rules (profiled at depth 12), while 4 costs almost nothing.
// Per-class formal (Phase 5 PR 4) revisits the bound.
//
// The checks:
//   handshake  VALID and its payload hold from VALID until READY, on every channel (A3.2.1)
//   burst      AxBURST not reserved; AxSIZE no wider than the bus; WRAP length 2, 4, 8 or
//              16 from an address aligned to the size; FIXED at most 16 beats; INCR never
//              crosses 4 KB (A3.4.1); exclusive: power-of-two total of at most 128 bytes,
//              at most 16 beats, aligned to the total (A7.2.4)
//   wlast      WLAST on the AWLEN-th beat of each write, in AW order (A3.4.1, A3.3.1)
//   responses  a write response only for a write whose address and last data beat have
//              both been accepted; read data only for an outstanding read of that ID, RLAST
//              on its ARLEN-th beat, reads of one ID in order (A5.3); EXOKAY only for an
//              exclusive access (A7.2)

`default_nettype none

module bag_faxi4 #(
    parameter bit          DesignIsSlave  = 1'b1,
    parameter int unsigned AddrWidth      = 32,
    parameter int unsigned DataWidth      = 32,
    parameter int unsigned IdWidth        = 1,
    parameter int unsigned MaxOutstanding = 4
) (
    input wire                     clk_i,
    input wire                     rst_ni,
    // write address
    input wire                     awvalid,
    input wire                     awready,
    input wire [IdWidth-1:0]       awid,
    input wire [AddrWidth-1:0]     awaddr,
    input wire [7:0]               awlen,
    input wire [2:0]               awsize,
    input wire [1:0]               awburst,
    input wire                     awlock,
    input wire [3:0]               awcache,
    input wire [2:0]               awprot,
    input wire [3:0]               awqos,
    input wire [3:0]               awregion,
    // write data
    input wire                     wvalid,
    input wire                     wready,
    input wire [DataWidth-1:0]     wdata,
    input wire [DataWidth/8-1:0]   wstrb,
    input wire                     wlast,
    // write response
    input wire                     bvalid,
    input wire                     bready,
    input wire [IdWidth-1:0]       bid,
    input wire [1:0]               bresp,
    // read address
    input wire                     arvalid,
    input wire                     arready,
    input wire [IdWidth-1:0]       arid,
    input wire [AddrWidth-1:0]     araddr,
    input wire [7:0]               arlen,
    input wire [2:0]               arsize,
    input wire [1:0]               arburst,
    input wire                     arlock,
    input wire [3:0]               arcache,
    input wire [2:0]               arprot,
    input wire [3:0]               arqos,
    input wire [3:0]               arregion,
    // read data
    input wire                     rvalid,
    input wire                     rready,
    input wire [IdWidth-1:0]       rid,
    input wire [DataWidth-1:0]     rdata,
    input wire [1:0]               rresp,
    input wire                     rlast
);

  localparam int unsigned MaxSize = $clog2(DataWidth / 8);
  localparam int unsigned Cw = $clog2(MaxOutstanding + 2);
  localparam logic [Cw-1:0] CwOne = 1;
  localparam int unsigned Iw = $clog2(MaxOutstanding);  // a table index

  if (MaxOutstanding < 2 || MaxOutstanding > 8 || (MaxOutstanding & (MaxOutstanding - 1)) != 0)
  begin : gen_check
    $error("bag_faxi4: MaxOutstanding must be a power of two from 2 to 8");
  end
  // The 4 KB rule reads the low 12 address bits; AxSIZE codes widths from 8 to 1024 bits.
  if (AddrWidth < 12) begin : gen_check_addr
    $error("bag_faxi4: AddrWidth must be at least 12");
  end
  if (DataWidth < 8 || DataWidth > 1024 || (DataWidth & (DataWidth - 1)) != 0)
  begin : gen_check_data
    $error("bag_faxi4: DataWidth must be a power of two from 8 to 1024");
  end
  if (IdWidth < 1) begin : gen_check_id
    $error("bag_faxi4: IdWidth must be at least 1");
  end

  // ---------------------------------------------------------------- sampling
  // `live`: this edge and the one before were out of reset, so the previous edge's values
  // (the _q registers) belong to the same run of the bus.
  logic was_out = 1'b0;
  always_ff @(posedge clk_i) was_out <= rst_ni;
  wire live = rst_ni && was_out;

  // The previous edge's handshake state and payloads.
  logic aw_wait_q = 1'b0, w_wait_q = 1'b0, ar_wait_q = 1'b0, b_wait_q = 1'b0, r_wait_q = 1'b0;
  logic [IdWidth+AddrWidth+8+3+2+1+4+3+4+4-1:0] aw_q, ar_q;
  logic [DataWidth+DataWidth/8:0] w_q;
  logic [IdWidth+1:0] b_q;
  logic [IdWidth+DataWidth+2:0] r_q;
  wire [$bits(aw_q)-1:0] aw_now = {awid, awaddr, awlen, awsize, awburst, awlock, awcache,
                                   awprot, awqos, awregion};
  wire [$bits(ar_q)-1:0] ar_now = {arid, araddr, arlen, arsize, arburst, arlock, arcache,
                                   arprot, arqos, arregion};
  wire [$bits(w_q)-1:0] w_now = {wdata, wstrb, wlast};
  wire [$bits(b_q)-1:0] b_now = {bid, bresp};
  wire [$bits(r_q)-1:0] r_now = {rid, rdata, rresp, rlast};
  always_ff @(posedge clk_i) begin
    aw_wait_q <= rst_ni && awvalid && !awready;
    w_wait_q  <= rst_ni && wvalid && !wready;
    ar_wait_q <= rst_ni && arvalid && !arready;
    b_wait_q  <= rst_ni && bvalid && !bready;
    r_wait_q  <= rst_ni && rvalid && !rready;
    aw_q <= aw_now;
    ar_q <= ar_now;
    w_q  <= w_now;
    b_q  <= b_now;
    r_q  <= r_now;
  end

  // Requests are the master's to keep (assumed if the design is the slave, asserted if it
  // is the master); responses are the slave's. Each check is a boolean, true when the rule
  // holds at this edge.
  logic ok_aw_hold, ok_w_hold, ok_ar_hold, ok_b_hold, ok_r_hold;
  assign ok_aw_hold = !(live && aw_wait_q) || (awvalid && aw_now == aw_q);
  assign ok_w_hold  = !(live && w_wait_q) || (wvalid && w_now == w_q);
  assign ok_ar_hold = !(live && ar_wait_q) || (arvalid && ar_now == ar_q);
  assign ok_b_hold  = !(live && b_wait_q) || (bvalid && b_now == b_q);
  assign ok_r_hold  = !(live && r_wait_q) || (rvalid && r_now == r_q);

  // ---------------------------------------------------------------- burst legality
  function automatic logic burst_ok(input logic [AddrWidth-1:0] addr, input logic [7:0] len,
                                    input logic [2:0] size, input logic [1:0] burst,
                                    input logic lock);
    // Unsigned 32-bit arithmetic throughout: 256 beats of 128 bytes is 32 KB.
    logic [31:0] bytes, total, offset, start;
    logic        ok;
    ok = burst != 2'b11 && {29'd0, size} <= MaxSize;
    bytes = 32'd1 << size;
    total = ({24'd0, len} + 32'd1) << size;
    offset = {20'd0, addr[11:0]};
    start = offset & ~(bytes - 32'd1);
    if (burst == 2'b10) begin  // WRAP
      ok &= (len == 8'd1 || len == 8'd3 || len == 8'd7 || len == 8'd15);
      ok &= (offset & (bytes - 32'd1)) == 32'd0;
    end else if (burst == 2'b00) begin  // FIXED
      ok &= len <= 8'd15;
    end else begin  // INCR
      ok &= start + total <= 32'd4096;
    end
    if (lock) begin  // exclusive
      ok &= len <= 8'd15 && total <= 32'd128 && (total & (total - 32'd1)) == 32'd0;
      ok &= (offset & (total - 32'd1)) == 32'd0;
    end
    return ok;
  endfunction

  wire ok_aw_burst = !(live && awvalid) || burst_ok(awaddr, awlen, awsize, awburst, awlock);
  wire ok_ar_burst = !(live && arvalid) || burst_ok(araddr, arlen, arsize, arburst, arlock);

  // ---------------------------------------------------------------- write tracking
  // Write data follows write addresses in order, but may come first (A3.3.1). One FIFO holds
  // whichever side is ahead: accepted addresses waiting for their data ({id, len}, aw_ahead
  // high) or finished data bursts waiting for their address ({-, beats-1}, aw_ahead low).
  // A matched pair retires at once, so only one side is ever ahead. A match completes a
  // write; a write response is due only for a completed write.
  wire aw_hs = awvalid && awready, w_hs = wvalid && wready;

  localparam int unsigned Ew = 1 + IdWidth + 8;  // {AWLOCK, AWID, AWLEN}
  logic [Ew-1:0] q [MaxOutstanding];
  logic [Cw-1:0] n = '0;
  logic aw_ahead = 1'b0;
  logic [7:0] w_beats = '0;  // beats of the write-data burst in progress, before this one

  // The address this beat's burst belongs to, if known: the oldest waiting address, or one
  // accepted at this very edge when nothing is waiting.
  wire        len_known = (aw_ahead && n != '0) || (aw_hs && n == '0);
  wire [7:0]  burst_len = (aw_ahead && n != '0) ? q[0][7:0] : awlen;
  wire ok_wlast = !(live && w_hs && len_known)
                  || (wlast == (w_beats == burst_len) && w_beats <= burst_len);
  // An address arriving after its data: the oldest finished burst had awlen + 1 beats.
  wire ok_w_early = !(live && aw_hs && !aw_ahead && n != '0) || awlen == q[0][7:0];
  // An address arriving with nothing waiting while a data burst is under way is that burst's:
  // the beats it has taken must fit (the next beat, checked by ok_wlast, is beat w_beats).
  wire ok_w_partial = !(live && aw_hs && n == '0 && w_beats != '0) || awlen >= w_beats;

  // This edge's events, and the write they complete (if any).
  wire w_done = w_hs && wlast;
  wire pop_aw = w_done && aw_ahead && n != '0;  // data finishes the oldest waiting address
  wire pop_w = aw_hs && !aw_ahead && n != '0;  // an address finishes the oldest data burst
  wire pair = aw_hs && w_done && n == '0;  // both arrive together, nothing waiting
  wire [IdWidth-1:0] done_id = pop_aw ? q[0][Ew-2:8] : awid;
  wire done_lock = pop_aw ? q[0][Ew-1] : awlock;
  wire completes = pop_aw || pop_w || pair;
  // What gets stored: the address (if it does not complete a write) or the data burst.
  wire push_aw = aw_hs && !pop_w && !pair;
  wire push_w = w_done && !pop_aw && !pair;
  // A store into a full FIFO: the tracker's capacity (see the header).
  wire overflow = (push_aw || push_w) && !(pop_aw || pop_w) && n == Cw'(MaxOutstanding);

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      n <= '0;
      aw_ahead <= 1'b0;
      w_beats <= '0;
    end else begin
      if (w_hs) w_beats <= wlast ? 8'd0 : w_beats + 8'd1;
      if (pop_aw || pop_w) begin
        // Shift out the oldest entry, appending what this edge stores, if anything.
        for (int unsigned i = 0; i + 1 < MaxOutstanding; i++) begin
          q[i] <= q[i+1];
        end
        // The index is cast after the subtraction: n can equal MaxOutstanding.
        if (push_aw && n > CwOne) q[Iw'(n - CwOne)] <= {awlock, awid, awlen};
        else if (push_aw) q[0] <= {awlock, awid, awlen};
        if (push_w && n > CwOne) q[Iw'(n - CwOne)] <= {1'b0, IdWidth'(0), w_beats};
        else if (push_w) q[0] <= {1'b0, IdWidth'(0), w_beats};
        n <= n - 1'b1 + Cw'(push_aw || push_w);
        if (n == CwOne && (push_aw || push_w)) aw_ahead <= push_aw;
      end else if ((push_aw || push_w) && !overflow) begin
        q[Iw'(n)] <= push_aw ? {awlock, awid, awlen} : {1'b0, IdWidth'(0), w_beats};
        n <= n + 1'b1;
        aw_ahead <= push_aw;
      end
    end
  end

  // ---------------------------------------------------------------- response tables
  // Oldest entry first. A response belongs to the oldest open entry of its ID: one ID's
  // responses come in order, different IDs' may interleave (A5.3).
  wire b_hs = bvalid && bready, ar_hs = arvalid && arready, r_hs = rvalid && rready;

  // Completed writes owed a response: their IDs, and whether each was exclusive.
  logic [IdWidth-1:0] bq [MaxOutstanding];
  logic bq_lock [MaxOutstanding];
  logic [Cw-1:0] bn = '0;
  logic b_found;
  logic [Iw-1:0] b_at;
  always_comb begin
    b_found = 1'b0;
    b_at = '0;
    for (int i = int'(MaxOutstanding) - 1; i >= 0; i--) begin
      if (Cw'(i) < bn && bq[i] == bid) begin
        b_found = 1'b1;
        b_at = Iw'(i);
      end
    end
  end
  wire b_pop = b_hs && b_found;
  wire b_overflow = completes && !b_pop && bn == Cw'(MaxOutstanding);

  // Outstanding reads: ID, ARLEN, beats so far, and whether each is exclusive.
  logic [IdWidth-1:0] rq_id [MaxOutstanding];
  logic rq_lock [MaxOutstanding];
  logic [7:0] rq_len [MaxOutstanding];
  logic [7:0] rq_beats [MaxOutstanding];
  logic [Cw-1:0] rn = '0;
  logic r_found;
  logic [Iw-1:0] r_at;
  always_comb begin
    r_found = 1'b0;
    r_at = '0;
    for (int i = int'(MaxOutstanding) - 1; i >= 0; i--) begin
      if (Cw'(i) < rn && rq_id[i] == rid) begin
        r_found = 1'b1;
        r_at = Iw'(i);
      end
    end
  end
  wire r_pop = r_hs && rlast && r_found;
  wire r_overflow = ar_hs && !r_pop && rn == Cw'(MaxOutstanding);

  wire ok_b_open = !(live && bvalid) || b_found;
  wire ok_r_open = !(live && rvalid) || r_found;
  wire ok_rlast = !(live && r_hs && r_found)
                  || (rlast == (rq_beats[r_at] == rq_len[r_at]) && rq_beats[r_at] <= rq_len[r_at]);
  // EXOKAY answers only an exclusive access (A7.2); an exclusive access may also get OKAY.
  localparam logic [1:0] ExOkay = 2'b01;
  wire ok_b_exokay = !(live && bvalid && b_found && bresp == ExOkay) || bq_lock[b_at];
  wire ok_r_exokay = !(live && rvalid && r_found && rresp == ExOkay) || rq_lock[r_at];

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      bn <= '0;
      rn <= '0;
    end else begin
      // Write responses: drop the answered entry, append a completed write.
      if (b_pop) begin
        for (int unsigned i = 0; i + 1 < MaxOutstanding; i++) begin
          if (Iw'(i) >= b_at) begin
            bq[i] <= bq[i+1];
            bq_lock[i] <= bq_lock[i+1];
          end
        end
        if (completes) begin
          bq[Iw'(bn - CwOne)] <= done_id;
          bq_lock[Iw'(bn - CwOne)] <= done_lock;
        end
        bn <= bn - CwOne + Cw'(completes);
      end else if (completes && !b_overflow) begin
        bq[Iw'(bn)] <= done_id;
        bq_lock[Iw'(bn)] <= done_lock;
        bn <= bn + CwOne;
      end
      // Reads: count a beat, drop a finished read, append an accepted one.
      if (r_hs && r_found && !rlast) rq_beats[r_at] <= rq_beats[r_at] + 8'd1;
      if (r_pop) begin
        for (int unsigned i = 0; i + 1 < MaxOutstanding; i++) begin
          if (Iw'(i) >= r_at) begin
            rq_id[i] <= rq_id[i+1];
            rq_lock[i] <= rq_lock[i+1];
            rq_len[i] <= rq_len[i+1];
            rq_beats[i] <= rq_beats[i+1];
          end
        end
        if (ar_hs) begin
          rq_id[Iw'(rn - CwOne)] <= arid;
          rq_lock[Iw'(rn - CwOne)] <= arlock;
          rq_len[Iw'(rn - CwOne)] <= arlen;
          rq_beats[Iw'(rn - CwOne)] <= 8'd0;
        end
        rn <= rn - CwOne + Cw'(ar_hs);
      end else if (ar_hs && !r_overflow) begin
        rq_id[Iw'(rn)] <= arid;
        rq_lock[Iw'(rn)] <= arlock;
        rq_len[Iw'(rn)] <= arlen;
        rq_beats[Iw'(rn)] <= 8'd0;
        rn <= rn + CwOne;
      end
    end
  end

  // The tracker's bound, as a rule on whoever opens transactions.
  wire ok_w_room = !(live && (overflow || b_overflow));
  wire ok_r_room = !(live && r_overflow);
  // An environment slave accepts nothing a full table would have to store: no address or
  // data while completed writes fill the response table; no address while addresses wait in
  // a full FIFO, no data while data bursts do; no read while the read table is full. With
  // these, ok_w_room and ok_r_room hold whatever the design opens.
  localparam logic [Cw-1:0] Full = Cw'(MaxOutstanding);
  wire ok_slave_cap = !(bn == Full && (awready || wready))
                      && !(n == Full && aw_ahead && awready)
                      && !(n == Full && !aw_ahead && wready)
                      && !(rn == Full && arready);

  // ---------------------------------------------------------------- assert or assume
  // The design's rules are asserted at each edge. The environment's are assumed at every
  // solver step: the values an edge samples are then legal too, and so is what the design
  // passes on combinationally to a port whose checker asserts at every step (ZipCPU's
  // AXI4-Lite files). Assumed only at edges, the last step of a trace was left free.
  if (DesignIsSlave) begin : gen_design_is_slave
    // The environment is the master: its requests are assumed, the design's responses
    // asserted.
    always_comb begin
      if (rst_ni) begin
        assume (ok_aw_hold);
        assume (ok_w_hold);
        assume (ok_ar_hold);
        assume (ok_aw_burst);
        assume (ok_ar_burst);
        assume (ok_wlast);
        assume (ok_w_early);
        assume (ok_w_partial);
        assume (ok_w_room);
        assume (ok_r_room);
      end
    end
    always_ff @(posedge clk_i) begin
      if (rst_ni) begin
        b_hold: assert (ok_b_hold);
        r_hold: assert (ok_r_hold);
        b_only_for_a_complete_write: assert (ok_b_open);
        r_only_for_an_open_read: assert (ok_r_open);
        rlast_on_the_last_beat: assert (ok_rlast);
        b_exokay_only_if_exclusive: assert (ok_b_exokay);
        r_exokay_only_if_exclusive: assert (ok_r_exokay);
      end
    end
  end else begin : gen_design_is_master
    // The environment is the slave: its responses are assumed, the design's requests
    // asserted.
    always_ff @(posedge clk_i) begin
      if (rst_ni) begin
        aw_hold: assert (ok_aw_hold);
        w_hold: assert (ok_w_hold);
        ar_hold: assert (ok_ar_hold);
        aw_burst_legal: assert (ok_aw_burst);
        ar_burst_legal: assert (ok_ar_burst);
        wlast_on_the_last_beat: assert (ok_wlast);
        wlast_matches_its_address: assert (ok_w_early);
        address_takes_its_early_data: assert (ok_w_partial);
        tracker_has_room_for_writes: assert (ok_w_room);
        tracker_has_room_for_reads: assert (ok_r_room);
      end
    end
    always_comb begin
      if (rst_ni) begin
        assume (ok_slave_cap);
        assume (ok_b_hold);
        assume (ok_r_hold);
        assume (ok_b_open);
        assume (ok_r_open);
        assume (ok_rlast);
        assume (ok_b_exokay);
        assume (ok_r_exokay);
      end
    end
  end

endmodule

`default_nettype wire
