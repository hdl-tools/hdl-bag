// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Core of bag_axi2ahb: AXI4 slave to AHB-Lite master at one data width and one clock
// (bag_axi2ahb adds width conversion, a FIFO and a CDC in front).
//
// * IDs serialized: one AXI transaction at a time (AW and AR alternate when both wait),
//   so responses return in order. A read's beats go to a 4-entry R buffer, so the next
//   transaction can start while they drain; a write ends with its B response.
//   A read stays open until all its beats are issued, which needs R buffer space: the
//   master must not make RREADY wait for a later write's progress (AW, W or B).
// * Each AXI beat becomes AHB transfers on a pipelined AHB master:
//   - a read beat, or a write beat whose strobes cover all its bytes, is one transfer of
//     AxSIZE at the beat's aligned address;
//   - AHB-Lite has no write strobes, so a write beat with some strobes clear becomes one
//     byte write per set strobe, and a beat with none is dropped.
// * Bursts: an INCR burst (AxLEN > 0) is an AHB undefined-length INCR (HBURST = INCR):
//   whole beats continue it as SEQ; it restarts with NONSEQ at a 1 KB boundary, after a
//   byte write and after an IDLE (a write beat not yet received, or a full R buffer).
//   WRAP and FIXED bursts become single transfers (HBURST = SINGLE).
// * HPROT = {cacheable/modifiable = AxCACHE[1], bufferable = AxCACHE[0],
//   privileged = AxPROT[0], data = !AxPROT[2]}; HNONSEC = AxPROT[1]; HMASTLOCK = 0.
//   Exclusive accesses (AxLOCK) get OKAY, i.e. the exclusive fails.
// * HRESP ERROR becomes SLVERR: on that R beat, or on B (any beat of the write). The
//   burst continues after an error. USER signals are not carried (zero).
// * Address: AddrOffset is subtracted (pass-through N); the AHB address is the low
//   HAddrWidth bits.
// * Limits, checked at elaboration: AddrOffset aligned to the data width; HAddrWidth >= 10.
//   AxSIZE must not exceed the data width (bag_axi2ahb converts width first).

module bag_axi2ahb_core #(
    parameter int unsigned          AddrWidth  = 32,
    parameter int unsigned          HAddrWidth = 32,
    parameter int unsigned          DataWidth  = 32,
    parameter int unsigned          IdWidth    = 1,
    parameter logic [AddrWidth-1:0] AddrOffset = '0,
    parameter type                  axi_req_t  = logic,
    parameter type                  axi_resp_t = logic
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  axi_req_t              slv_req_i,
    output axi_resp_t             slv_resp_o,

    output logic [HAddrWidth-1:0] haddr_o,
    output logic [           2:0] hburst_o,
    output logic                  hmastlock_o,
    output logic [           3:0] hprot_o,
    output logic                  hnonsec_o,
    output logic [           2:0] hsize_o,
    output logic [           1:0] htrans_o,
    output logic [ DataWidth-1:0] hwdata_o,
    output logic                  hwrite_o,
    input  logic [ DataWidth-1:0] hrdata_i,
    input  logic                  hready_i,
    input  logic                  hresp_i
);

  localparam int unsigned DBytes = DataWidth / 8;
  localparam int unsigned Lane   = DBytes > 1 ? unsigned'($clog2(DBytes)) : 1;
  localparam int unsigned RDepth = 4;

  if (AddrOffset % AddrWidth'(DBytes) != '0) begin : gen_offset_check
    $error("bag_axi2ahb_core: AddrOffset must be aligned to the data width");
  end
  if (HAddrWidth < 10) begin : gen_haddr_check
    $error("bag_axi2ahb_core: HAddrWidth must be at least 10 (1 KB bursts)");
  end

  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [IdWidth-1:0] id_t;
  typedef logic [DataWidth-1:0] data_t;
  typedef logic [DBytes-1:0] strb_t;

  localparam logic [1:0] BurstIncr = 2'b01, BurstWrap = 2'b10;
  localparam logic [1:0] TransIdle = 2'b00, TransNonseq = 2'b10, TransSeq = 2'b11;
  localparam logic [1:0] RespOkay = 2'b00, RespSlvErr = 2'b10;

  // ------------------------------------------------------------ transaction
  logic        t_valid_q;  // a transaction is open (a write stays open until B)
  logic        t_write_q;
  id_t         t_id_q;
  addr_t       t_addr_q;  // address of the current beat (untranslated)
  logic [ 7:0] t_left_q;  // beats after the current one
  logic [ 2:0] t_size_q;
  logic [ 1:0] t_burst_q;
  addr_t       t_wrap_q;  // WRAP: wrap boundary - 1
  logic        t_incr_q;  // an AHB INCR burst (INCR with AxLEN > 0)
  logic        t_first_q;  // no transfer issued yet
  logic        t_done_q;  // every beat handed to the AHB pipeline
  logic        t_err_q;  // a write transfer got an error
  logic [ 3:0] t_hprot_q;
  logic        t_nonsec_q;
  logic        last_read_q;  // the last transaction was a read (AW/AR alternate)

  // Current write beat.
  logic        wb_valid_q;
  data_t       wb_data_q;
  logic        wb_full_q;  // its strobes cover the whole beat: one transfer
  strb_t       wb_todo_q;  // byte writes still to issue
  logic        wb_last_q;

  // ------------------------------------------------------------ AHB pipeline
  logic                  ap_valid_q, ap_seq_q, ap_write_q, ap_incr_q, ap_rlast_q;
  logic [HAddrWidth-1:0] ap_haddr_q;
  logic [           2:0] ap_size_q;
  logic [           3:0] ap_hprot_q;
  logic                  ap_nonsec_q;
  data_t                 ap_wdata_q;
  id_t                   ap_id_q;
  logic                  dp_valid_q, dp_write_q, dp_rlast_q;
  data_t                 dp_wdata_q;
  id_t                   dp_id_q;
  // The previous address phase, for SEQ continuation.
  logic                  prev_valid_q, prev_full_q;
  logic [HAddrWidth-1:0] prev_haddr_q;

  // ------------------------------------------------------------ R buffer
  typedef struct packed {
    id_t        id;
    data_t      data;
    logic [1:0] resp;
    logic       last;
  } r_entry_t;
  r_entry_t       r_mem_q [RDepth];
  logic     [1:0] r_wr_q, r_rd_q;
  logic     [2:0] r_cnt_q;
  logic           r_push, r_pop;
  r_entry_t       r_in;

  // ------------------------------------------------------------ next AHB transfer
  logic [7:0] bytes;  // 2^AxSIZE
  addr_t      al_addr, word_addr, next_addr, sub_addr;
  strb_t      win;  // byte lanes of the write beat taken next
  addr_t      w_addr;  // its address (the next beat's while the current one leaves)
  logic [Lane-1:0] w_lane;  // its first byte lane
  logic       wb_release;  // the write beat buffer empties this cycle
  logic [1:0] inflight_rd;
  logic       rd_avail, wr_avail, wr_skip, sub_avail, sub_full, beat_end, seq;
  logic [Lane-1:0] low;  // lowest byte still to write
  logic [HAddrWidth-1:0] sub_haddr;

  assign bytes     = 8'd1 << t_size_q;
  assign al_addr   = t_addr_q & ~(addr_t'(bytes) - addr_t'(1));
  assign word_addr = t_addr_q & ~addr_t'(DBytes - 1);

  assign w_addr = wb_valid_q ? next_addr : t_addr_q;
  assign w_lane = Lane'(w_addr & ~(addr_t'(bytes) - addr_t'(1)));

  always_comb begin
    for (int unsigned b = 0; b < DBytes; b++) begin
      win[b] = b >= 32'(w_lane) && b < 32'(w_lane) + 32'(bytes);
    end
    if (DBytes == 1) win = '1;
    low = '0;
    for (int b = int'(DBytes) - 1; b >= 0; b--) begin
      if (wb_todo_q[b]) low = Lane'(unsigned'(b));
    end
  end

  always_comb begin
    case (t_burst_q)
      BurstIncr: next_addr = al_addr + addr_t'(bytes);
      BurstWrap: next_addr = (t_addr_q & ~t_wrap_q) | ((t_addr_q + addr_t'(bytes)) & t_wrap_q);
      default:   next_addr = t_addr_q;  // FIXED
    endcase
  end

  assign inflight_rd = 2'(ap_valid_q && !ap_write_q) + 2'(dp_valid_q && !dp_write_q);
  assign rd_avail = t_valid_q && !t_write_q && !t_done_q &&
                    32'(r_cnt_q) + 32'(inflight_rd) < RDepth;
  assign wr_avail  = t_valid_q && t_write_q && wb_valid_q && (wb_full_q || wb_todo_q != '0);
  assign wr_skip   = t_valid_q && t_write_q && wb_valid_q && !wb_full_q && wb_todo_q == '0;
  assign sub_avail = rd_avail || wr_avail;
  assign sub_full  = !t_write_q || wb_full_q;
  assign sub_addr  = sub_full ? al_addr : word_addr + addr_t'(low);
  assign sub_haddr = HAddrWidth'(sub_addr - AddrOffset);
  assign beat_end  = sub_full || (wb_todo_q & ~(strb_t'(1) << low)) == '0;
  assign seq = !t_first_q && prev_valid_q && prev_full_q && sub_full && t_incr_q &&
               sub_haddr == prev_haddr_q + HAddrWidth'(bytes) && sub_haddr[9:0] != '0;

  // ------------------------------------------------------------ AXI handshakes
  logic pick_w, aw_hs, ar_hs, w_hs, b_valid_q, b_hs, b_issue;

  assign pick_w = slv_req_i.aw_valid && (!slv_req_i.ar_valid || last_read_q);
  assign aw_hs  = !t_valid_q && pick_w;
  assign ar_hs  = !t_valid_q && slv_req_i.ar_valid && !pick_w;
  // A write beat leaves the buffer when its last transfer issues, or (no strobes) at once;
  // the next beat can enter in the same cycle, so whole beats continue as SEQ.
  assign wb_release = t_write_q && ((hready_i && wr_avail && beat_end) || wr_skip);
  assign w_hs   = t_valid_q && t_write_q && !t_done_q && slv_req_i.w_valid &&
                  (!wb_valid_q || (wb_release && !wb_last_q));
  assign b_hs   = b_valid_q && slv_req_i.b_ready;
  assign b_issue = t_valid_q && t_write_q && t_done_q && !b_valid_q &&
                   !(ap_valid_q && ap_write_q) && !(dp_valid_q && dp_write_q);

  // ------------------------------------------------------------ sequential
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      t_valid_q    <= 1'b0;
      t_write_q    <= 1'b0;
      t_id_q       <= '0;
      t_addr_q     <= '0;
      t_left_q     <= '0;
      t_size_q     <= '0;
      t_burst_q    <= '0;
      t_wrap_q     <= '0;
      t_incr_q     <= 1'b0;
      t_first_q    <= 1'b0;
      t_done_q     <= 1'b0;
      t_err_q      <= 1'b0;
      t_hprot_q    <= '0;
      t_nonsec_q   <= 1'b0;
      last_read_q  <= 1'b0;
      wb_valid_q   <= 1'b0;
      wb_data_q    <= '0;
      wb_full_q    <= 1'b0;
      wb_todo_q    <= '0;
      wb_last_q    <= 1'b0;
      ap_valid_q   <= 1'b0;
      ap_seq_q     <= 1'b0;
      ap_write_q   <= 1'b0;
      ap_incr_q    <= 1'b0;
      ap_rlast_q   <= 1'b0;
      ap_haddr_q   <= '0;
      ap_size_q    <= '0;
      ap_hprot_q   <= '0;
      ap_nonsec_q  <= 1'b0;
      ap_wdata_q   <= '0;
      ap_id_q      <= '0;
      dp_valid_q   <= 1'b0;
      dp_write_q   <= 1'b0;
      dp_rlast_q   <= 1'b0;
      dp_wdata_q   <= '0;
      dp_id_q      <= '0;
      prev_valid_q <= 1'b0;
      prev_full_q  <= 1'b0;
      prev_haddr_q <= '0;
      b_valid_q    <= 1'b0;
    end else begin
      // Open a transaction.
      if (aw_hs || ar_hs) begin
        t_valid_q   <= 1'b1;
        t_write_q   <= aw_hs;
        last_read_q <= ar_hs;
        t_first_q   <= 1'b1;
        t_done_q    <= 1'b0;
        t_err_q     <= 1'b0;
        if (aw_hs) begin
          t_id_q     <= slv_req_i.aw.id;
          t_addr_q   <= slv_req_i.aw.addr;
          t_left_q   <= slv_req_i.aw.len;
          t_size_q   <= slv_req_i.aw.size;
          t_burst_q  <= slv_req_i.aw.burst;
          t_wrap_q   <= addr_t'(((32'(slv_req_i.aw.len) + 32'd1) << slv_req_i.aw.size) - 32'd1);
          t_incr_q   <= slv_req_i.aw.burst == BurstIncr && slv_req_i.aw.len != '0;
          t_hprot_q  <= {slv_req_i.aw.cache[1:0], slv_req_i.aw.prot[0], !slv_req_i.aw.prot[2]};
          t_nonsec_q <= slv_req_i.aw.prot[1];
        end else begin
          t_id_q     <= slv_req_i.ar.id;
          t_addr_q   <= slv_req_i.ar.addr;
          t_left_q   <= slv_req_i.ar.len;
          t_size_q   <= slv_req_i.ar.size;
          t_burst_q  <= slv_req_i.ar.burst;
          t_wrap_q   <= addr_t'(((32'(slv_req_i.ar.len) + 32'd1) << slv_req_i.ar.size) - 32'd1);
          t_incr_q   <= slv_req_i.ar.burst == BurstIncr && slv_req_i.ar.len != '0;
          t_hprot_q  <= {slv_req_i.ar.cache[1:0], slv_req_i.ar.prot[0], !slv_req_i.ar.prot[2]};
          t_nonsec_q <= slv_req_i.ar.prot[1];
        end
      end

      // A write beat without strobes issues nothing.
      if (wr_skip) begin
        wb_valid_q <= 1'b0;
        if (wb_last_q) t_done_q <= 1'b1;
        else begin
          t_addr_q <= next_addr;
          t_left_q <= t_left_q - 8'd1;
        end
      end

      // The AHB pipeline advances on HREADY.
      if (hready_i) begin
        if (dp_valid_q && dp_write_q) t_err_q <= t_err_q | hresp_i;
        dp_valid_q <= ap_valid_q;
        dp_write_q <= ap_write_q;
        dp_rlast_q <= ap_rlast_q;
        dp_wdata_q <= ap_wdata_q;
        dp_id_q    <= ap_id_q;
        ap_valid_q <= sub_avail;
        prev_valid_q <= sub_avail;
        if (sub_avail) begin
          ap_seq_q     <= seq;
          ap_write_q   <= t_write_q;
          ap_incr_q    <= t_incr_q;
          ap_rlast_q   <= t_left_q == '0;
          ap_haddr_q   <= sub_haddr;
          ap_size_q    <= sub_full ? t_size_q : 3'd0;
          ap_hprot_q   <= t_hprot_q;
          ap_nonsec_q  <= t_nonsec_q;
          ap_wdata_q   <= wb_data_q;
          ap_id_q      <= t_id_q;
          prev_full_q  <= sub_full;
          prev_haddr_q <= sub_haddr;
          t_first_q    <= 1'b0;
          if (!beat_end) begin
            wb_todo_q <= wb_todo_q & ~(strb_t'(1) << low);
          end else begin
            if (t_write_q) wb_valid_q <= 1'b0;
            if (t_left_q == '0) begin
              t_done_q <= 1'b1;
              if (!t_write_q) t_valid_q <= 1'b0;  // a read closes once fully issued
            end else begin
              t_addr_q <= next_addr;
              t_left_q <= t_left_q - 8'd1;
            end
          end
        end
      end

      // Take a write beat (after the release above, so it refills the buffer).
      if (w_hs) begin
        wb_valid_q <= 1'b1;
        wb_data_q  <= slv_req_i.w.data;
        wb_full_q  <= (slv_req_i.w.strb & win) == win;
        wb_todo_q  <= slv_req_i.w.strb & win;
        wb_last_q  <= (wb_valid_q ? t_left_q - 8'd1 : t_left_q) == '0;
      end

      // B response; the write closes when it is taken.
      if (b_issue) b_valid_q <= 1'b1;
      if (b_hs) begin
        b_valid_q <= 1'b0;
        t_valid_q <= 1'b0;
      end
    end
  end

  // R buffer: a read data phase completes into it.
  assign r_push = hready_i && dp_valid_q && !dp_write_q;
  assign r_pop  = r_cnt_q != '0 && slv_req_i.r_ready;
  assign r_in   = '{id: dp_id_q, data: hrdata_i, resp: hresp_i ? RespSlvErr : RespOkay,
                    last: dp_rlast_q};

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      r_mem_q <= '{default: '0};
      r_wr_q  <= '0;
      r_rd_q  <= '0;
      r_cnt_q <= '0;
    end else begin
      if (r_push) begin
        r_mem_q[r_wr_q] <= r_in;
        r_wr_q          <= r_wr_q + 2'd1;
      end
      if (r_pop) r_rd_q <= r_rd_q + 2'd1;
      r_cnt_q <= r_cnt_q + 3'(r_push) - 3'(r_pop);
    end
  end

  always_comb begin
    slv_resp_o          = '0;
    slv_resp_o.aw_ready = aw_hs;
    slv_resp_o.ar_ready = ar_hs;
    slv_resp_o.w_ready  = w_hs;
    slv_resp_o.b_valid  = b_valid_q;
    slv_resp_o.b.id     = t_id_q;
    slv_resp_o.b.resp   = t_err_q ? RespSlvErr : RespOkay;
    slv_resp_o.r_valid  = r_cnt_q != '0;
    slv_resp_o.r.id     = r_mem_q[r_rd_q].id;
    slv_resp_o.r.data   = r_mem_q[r_rd_q].data;
    slv_resp_o.r.resp   = r_mem_q[r_rd_q].resp;
    slv_resp_o.r.last   = r_mem_q[r_rd_q].last;
  end

  assign htrans_o    = ap_valid_q ? (ap_seq_q ? TransSeq : TransNonseq) : TransIdle;
  assign haddr_o     = ap_haddr_q;
  assign hburst_o    = ap_incr_q ? 3'b001 : 3'b000;  // INCR : SINGLE
  assign hmastlock_o = 1'b0;
  assign hprot_o     = ap_hprot_q;
  assign hnonsec_o   = ap_nonsec_q;
  assign hsize_o     = ap_size_q;
  assign hwrite_o    = ap_write_q;
  assign hwdata_o    = dp_wdata_q;

endmodule
