// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axi_dw: AXI4 data-width converter (up or down) around PULP axi_dw_converter.

`include "axi/typedef.svh"

module bag_axi_dw #(
    parameter int unsigned AddrWidth    = 32,
    parameter int unsigned SlvDataWidth = 32,
    parameter int unsigned MstDataWidth = SlvDataWidth,
    parameter int unsigned SlvIdWidth   = 1,
    parameter int unsigned MstIdWidth   = SlvIdWidth,
    parameter int unsigned UserWidth    = 0,
    parameter int unsigned MaxReads     = 4,
    localparam int unsigned UW = (UserWidth > 0) ? UserWidth : 1
) (
    input  logic clk_i,
    input  logic rst_ni,

    // slave port (towards the master)
    input  logic [SlvIdWidth-1:0] s_awid_i,
    input  logic [AddrWidth-1:0] s_awaddr_i,
    input  logic [8-1:0] s_awlen_i,
    input  logic [3-1:0] s_awsize_i,
    input  logic [2-1:0] s_awburst_i,
    input  logic s_awlock_i,
    input  logic [4-1:0] s_awcache_i,
    input  logic [3-1:0] s_awprot_i,
    input  logic [4-1:0] s_awqos_i,
    input  logic [4-1:0] s_awregion_i,
    input  logic [UW-1:0] s_awuser_i,
    input  logic s_awvalid_i,
    output logic s_awready_o,
    input  logic [SlvDataWidth-1:0] s_wdata_i,
    input  logic [SlvDataWidth/8-1:0] s_wstrb_i,
    input  logic s_wlast_i,
    input  logic [UW-1:0] s_wuser_i,
    input  logic s_wvalid_i,
    output logic s_wready_o,
    output logic [SlvIdWidth-1:0] s_bid_o,
    output logic [2-1:0] s_bresp_o,
    output logic [UW-1:0] s_buser_o,
    output logic s_bvalid_o,
    input  logic s_bready_i,
    input  logic [SlvIdWidth-1:0] s_arid_i,
    input  logic [AddrWidth-1:0] s_araddr_i,
    input  logic [8-1:0] s_arlen_i,
    input  logic [3-1:0] s_arsize_i,
    input  logic [2-1:0] s_arburst_i,
    input  logic s_arlock_i,
    input  logic [4-1:0] s_arcache_i,
    input  logic [3-1:0] s_arprot_i,
    input  logic [4-1:0] s_arqos_i,
    input  logic [4-1:0] s_arregion_i,
    input  logic [UW-1:0] s_aruser_i,
    input  logic s_arvalid_i,
    output logic s_arready_o,
    output logic [SlvIdWidth-1:0] s_rid_o,
    output logic [SlvDataWidth-1:0] s_rdata_o,
    output logic [2-1:0] s_rresp_o,
    output logic s_rlast_o,
    output logic [UW-1:0] s_ruser_o,
    output logic s_rvalid_o,
    input  logic s_rready_i,
    // master port (towards the slave)
    output logic [MstIdWidth-1:0] m_awid_o,
    output logic [AddrWidth-1:0] m_awaddr_o,
    output logic [8-1:0] m_awlen_o,
    output logic [3-1:0] m_awsize_o,
    output logic [2-1:0] m_awburst_o,
    output logic m_awlock_o,
    output logic [4-1:0] m_awcache_o,
    output logic [3-1:0] m_awprot_o,
    output logic [4-1:0] m_awqos_o,
    output logic [4-1:0] m_awregion_o,
    output logic [UW-1:0] m_awuser_o,
    output logic m_awvalid_o,
    input  logic m_awready_i,
    output logic [MstDataWidth-1:0] m_wdata_o,
    output logic [MstDataWidth/8-1:0] m_wstrb_o,
    output logic m_wlast_o,
    output logic [UW-1:0] m_wuser_o,
    output logic m_wvalid_o,
    input  logic m_wready_i,
    input  logic [MstIdWidth-1:0] m_bid_i,
    input  logic [2-1:0] m_bresp_i,
    input  logic [UW-1:0] m_buser_i,
    input  logic m_bvalid_i,
    output logic m_bready_o,
    output logic [MstIdWidth-1:0] m_arid_o,
    output logic [AddrWidth-1:0] m_araddr_o,
    output logic [8-1:0] m_arlen_o,
    output logic [3-1:0] m_arsize_o,
    output logic [2-1:0] m_arburst_o,
    output logic m_arlock_o,
    output logic [4-1:0] m_arcache_o,
    output logic [3-1:0] m_arprot_o,
    output logic [4-1:0] m_arqos_o,
    output logic [4-1:0] m_arregion_o,
    output logic [UW-1:0] m_aruser_o,
    output logic m_arvalid_o,
    input  logic m_arready_i,
    input  logic [MstIdWidth-1:0] m_rid_i,
    input  logic [MstDataWidth-1:0] m_rdata_i,
    input  logic [2-1:0] m_rresp_i,
    input  logic m_rlast_i,
    input  logic [UW-1:0] m_ruser_i,
    input  logic m_rvalid_i,
    output logic m_rready_o
);

  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [UW-1:0] user_t;
  typedef logic [SlvIdWidth-1:0] slv_id_t;
  typedef logic [MstIdWidth-1:0] mst_id_t;
  typedef logic [SlvDataWidth-1:0] slv_data_t;
  typedef logic [SlvDataWidth/8-1:0] slv_strb_t;
  typedef logic [MstDataWidth-1:0] mst_data_t;
  typedef logic [MstDataWidth/8-1:0] mst_strb_t;
  `AXI_TYPEDEF_ALL(slv, addr_t, slv_id_t, slv_data_t, slv_strb_t, user_t)
  `AXI_TYPEDEF_ALL(mst, addr_t, mst_id_t, mst_data_t, mst_strb_t, user_t)

  if (SlvIdWidth != MstIdWidth) begin : gen_bad_id_width
    $fatal(1, "bag_axi_dw needs the same ID width on both sides");
  end

  slv_req_t  slv_req_in, slv_req;
  slv_resp_t slv_resp_out, slv_resp;

  // Exclusive accesses through the PULP converter, which ignores AxLOCK. An in-house
  // replacement for it must keep exclusive accesses legal itself, and should then carry an
  // over-long one (below) through as exclusive rather than make it fail.
  //
  // Upsizing: axi_dw_upsizer widens every modifiable INCR burst, so an exclusive access could
  // leave it misaligned to its new total (AXI A7.2.4: 2 beats of 4 bytes at 0x...08 became
  // 1 beat of 64). Exclusive accesses enter it non-modifiable: it passes them on beat for beat.
  //
  // Downsizing: axi_dw_downsizer splits each wide beat, so an exclusive access could leave it
  // with more than the 16 beats A7.2.4 allows (16 beats of 8 bytes became 32 of 4). Such an
  // access goes on as a normal one, so it fails as an exclusive access may: a read's OKAY
  // says so, and a write's beats go with no byte strobe set, so no byte changes and its OKAY
  // says so too (owner, 2026-11-12). A write still reaches the slave: one acting on any write,
  // whatever its strobes, sees it.
  localparam int unsigned MstMaxSize = $clog2(MstDataWidth / 8);
  localparam bit Downsize = SlvDataWidth > MstDataWidth;

  function automatic logic too_long(input logic [7:0] len, input logic [2:0] size);
    logic [31:0] beats;
    beats = 32'(len) + 32'd1;
    if (int'(size) > int'(MstMaxSize)) beats = beats << (int'(size) - int'(MstMaxSize));
    return Downsize && beats > 32'd16;
  endfunction

  // Downsizing, whether each write burst the converter has taken, oldest first, has its
  // strobes cleared. A burst's W beats wait for its AW (the downsizer's do anyway), so they
  // follow these in order. Upsizing, nothing waits.
  localparam int unsigned Open = 4;
  localparam int unsigned OpenW = $clog2(Open + 1);
  logic [Open-1:0] zap_q, zap_d;
  logic [OpenW-1:0] open_q, open_d;
  logic aw_room, w_known, aw_hs, w_end;

  mst_req_t  mst_req;
  mst_resp_t mst_resp;

  // D88 (owner, 2026-11-14): downsized write responses. axi_dw_downsizer answers a write with
  // a response it merges for the write its W side is on (pulp-platform/axi issue 289): while
  // a finished write's B waits for BREADY, the W side moving on changes it (SLVERR became
  // OKAY), and with the W side idle the merge restarts each cycle, so on a write split into
  // several narrow bursts (over 256 narrow beats) an error on an earlier one could be lost.
  // Here each write the converter takes gets an entry, in AW order, that merges the narrow
  // responses the converter absorbs (by ID: the oldest open entry, since one ID's narrow
  // responses come in order); the B it forwards answers with its entry merged with its own
  // narrow response, which waits with it, so the answer holds. A burst the downsizer cannot
  // split (WRAP, or a multi-beat FIXED one: bursts.py, D34) it answers itself from an
  // internal error slave on the same B path, unseen here; its entry says so and answers
  // SLVERR, as the error slave does. An in-house downsizer should merge per write itself.
  typedef struct packed {
    logic           valid;
    logic           own;   // the downsizer refuses it and answers it itself
    slv_id_t        id;
    axi_pkg::resp_t resp;
  } b_open_t;
  b_open_t [Open-1:0] bq_q, bq_d;
  logic b_absorb, b_done;
  int unsigned b_at;

  // The oldest open write of ID, or Open if none.
  function automatic int unsigned oldest(input b_open_t [Open-1:0] q, input slv_id_t id);
    oldest = Open;
    for (int unsigned i = Open; i > 0; i--) begin
      if (q[i-1].valid && q[i-1].id == id) oldest = i - 1;
    end
  endfunction

  assign b_absorb = Downsize && mst_resp.b_valid && mst_req.b_ready && !slv_resp.b_valid;
  assign b_done = Downsize && slv_resp.b_valid && slv_req_in.b_ready;
  assign b_at = oldest(bq_q, Downsize && slv_resp.b_valid ? slv_resp.b.id : mst_resp.b.id);

  assign aw_room = !Downsize || (open_q != OpenW'(Open) && !bq_q[Open-1].valid);
  assign w_known = !Downsize || open_q != '0;
  assign aw_hs = Downsize && slv_req.aw_valid && slv_resp.aw_ready;
  assign w_end = Downsize && slv_req.w_valid && slv_resp.w_ready && slv_req.w.last;

  always_comb begin
    slv_req = slv_req_in;
    if (slv_req_in.aw.lock) begin
      slv_req.aw.cache[1] = 1'b0;
      if (too_long(slv_req_in.aw.len, slv_req_in.aw.size)) slv_req.aw.lock = 1'b0;
    end
    if (slv_req_in.ar.lock) begin
      slv_req.ar.cache[1] = 1'b0;
      if (too_long(slv_req_in.ar.len, slv_req_in.ar.size)) slv_req.ar.lock = 1'b0;
    end
    slv_req.aw_valid = slv_req_in.aw_valid && aw_room;
    slv_req.w_valid = slv_req_in.w_valid && w_known;
    if (zap_q[0]) slv_req.w.strb = '0;
    slv_resp_out = slv_resp;
    slv_resp_out.aw_ready = slv_resp.aw_ready && aw_room;
    slv_resp_out.w_ready = slv_resp.w_ready && w_known;
    if (Downsize && slv_resp.b_valid && b_at < Open) begin
      slv_resp_out.b.resp = bq_q[b_at].own ? bq_q[b_at].resp :
          axi_pkg::resp_precedence(bq_q[b_at].resp, mst_resp.b.resp);
    end
  end

  // Whether the downsizer refuses AW (axi_dw_downsizer: WRAP always; FIXED of more than one
  // beat; the same rule as bursts.refusing_converter).
  function automatic logic refused(input axi_pkg::burst_t burst, input axi_pkg::len_t len);
    return burst == axi_pkg::BURST_WRAP || (burst == axi_pkg::BURST_FIXED && len != '0);
  endfunction

  always_comb begin
    bq_d = bq_q;
    if (b_absorb && b_at < Open && !bq_q[b_at].own)
      bq_d[b_at].resp = axi_pkg::resp_precedence(bq_q[b_at].resp, mst_resp.b.resp);
    if (b_done && b_at < Open) begin
      for (int unsigned i = 0; i < Open - 1; i++) begin
        if (i >= b_at) bq_d[i] = bq_q[i+1];
      end
      bq_d[Open-1] = '0;
    end
    if (aw_hs) begin
      for (int unsigned i = Open; i > 0; i--) begin
        if (!bq_d[i-1].valid && (i == 1 || bq_d[i-2].valid)) begin
          bq_d[i-1] = '{
              valid: 1'b1,
              own: refused(slv_req.aw.burst, slv_req.aw.len),
              id: slv_req.aw.id,
              resp: refused(slv_req.aw.burst, slv_req.aw.len) ? axi_pkg::RESP_SLVERR :
                  axi_pkg::RESP_EXOKAY
          };
        end
      end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) bq_q <= '0;
    else bq_q <= bq_d;
  end

  always_comb begin
    zap_d = zap_q;
    open_d = open_q;
    if (w_end) begin
      zap_d = zap_d >> 1;
      open_d = open_d - 1'b1;
    end
    if (aw_hs) begin
      zap_d[open_d[$clog2(Open)-1:0]] =
          slv_req_in.aw.lock && too_long(slv_req_in.aw.len, slv_req_in.aw.size);
      open_d = open_d + 1'b1;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      zap_q <= '0;
      open_q <= '0;
    end else begin
      zap_q <= zap_d;
      open_q <= open_d;
    end
  end

  bag_axi_from_flat #(
      .AddrWidth   (AddrWidth),
      .DataWidth   (SlvDataWidth),
      .IdWidth     (SlvIdWidth),
      .UserWidth   (UW),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (slv_req_t),
      .axi_resp_t  (slv_resp_t)
  ) i_from_flat (
      .awid_i(s_awid_i),
      .awaddr_i(s_awaddr_i),
      .awlen_i(s_awlen_i),
      .awsize_i(s_awsize_i),
      .awburst_i(s_awburst_i),
      .awlock_i(s_awlock_i),
      .awcache_i(s_awcache_i),
      .awprot_i(s_awprot_i),
      .awqos_i(s_awqos_i),
      .awregion_i(s_awregion_i),
      .awuser_i(s_awuser_i),
      .awvalid_i(s_awvalid_i),
      .awready_o(s_awready_o),
      .wdata_i(s_wdata_i),
      .wstrb_i(s_wstrb_i),
      .wlast_i(s_wlast_i),
      .wuser_i(s_wuser_i),
      .wvalid_i(s_wvalid_i),
      .wready_o(s_wready_o),
      .bid_o(s_bid_o),
      .bresp_o(s_bresp_o),
      .buser_o(s_buser_o),
      .bvalid_o(s_bvalid_o),
      .bready_i(s_bready_i),
      .arid_i(s_arid_i),
      .araddr_i(s_araddr_i),
      .arlen_i(s_arlen_i),
      .arsize_i(s_arsize_i),
      .arburst_i(s_arburst_i),
      .arlock_i(s_arlock_i),
      .arcache_i(s_arcache_i),
      .arprot_i(s_arprot_i),
      .arqos_i(s_arqos_i),
      .arregion_i(s_arregion_i),
      .aruser_i(s_aruser_i),
      .arvalid_i(s_arvalid_i),
      .arready_o(s_arready_o),
      .rid_o(s_rid_o),
      .rdata_o(s_rdata_o),
      .rresp_o(s_rresp_o),
      .rlast_o(s_rlast_o),
      .ruser_o(s_ruser_o),
      .rvalid_o(s_rvalid_o),
      .rready_i(s_rready_i),
      .req_o (slv_req_in),
      .resp_i(slv_resp_out)
  );

  axi_dw_converter #(
      .AxiMaxReads        (MaxReads),
      .AxiSlvPortDataWidth(SlvDataWidth),
      .AxiMstPortDataWidth(MstDataWidth),
      .AxiAddrWidth       (AddrWidth),
      .AxiIdWidth         (SlvIdWidth),
      .aw_chan_t          (slv_aw_chan_t),
      .mst_w_chan_t       (mst_w_chan_t),
      .slv_w_chan_t       (slv_w_chan_t),
      .b_chan_t           (slv_b_chan_t),
      .ar_chan_t          (slv_ar_chan_t),
      .mst_r_chan_t       (mst_r_chan_t),
      .slv_r_chan_t       (slv_r_chan_t),
      .axi_mst_req_t      (mst_req_t),
      .axi_mst_resp_t     (mst_resp_t),
      .axi_slv_req_t      (slv_req_t),
      .axi_slv_resp_t     (slv_resp_t)
  ) i_dw (
      .clk_i,
      .rst_ni,
      .slv_req_i (slv_req),
      .slv_resp_o(slv_resp),
      .mst_req_o (mst_req),
      .mst_resp_i(mst_resp)
  );

  bag_axi_to_flat #(
      .AddrWidth   (AddrWidth),
      .DataWidth   (MstDataWidth),
      .IdWidth     (MstIdWidth),
      .UserWidth   (UW),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (mst_req_t),
      .axi_resp_t  (mst_resp_t)
  ) i_to_flat (
      .req_i (mst_req),
      .resp_o(mst_resp),
      .awid_o(m_awid_o),
      .awaddr_o(m_awaddr_o),
      .awlen_o(m_awlen_o),
      .awsize_o(m_awsize_o),
      .awburst_o(m_awburst_o),
      .awlock_o(m_awlock_o),
      .awcache_o(m_awcache_o),
      .awprot_o(m_awprot_o),
      .awqos_o(m_awqos_o),
      .awregion_o(m_awregion_o),
      .awuser_o(m_awuser_o),
      .awvalid_o(m_awvalid_o),
      .awready_i(m_awready_i),
      .wdata_o(m_wdata_o),
      .wstrb_o(m_wstrb_o),
      .wlast_o(m_wlast_o),
      .wuser_o(m_wuser_o),
      .wvalid_o(m_wvalid_o),
      .wready_i(m_wready_i),
      .bid_i(m_bid_i),
      .bresp_i(m_bresp_i),
      .buser_i(m_buser_i),
      .bvalid_i(m_bvalid_i),
      .bready_o(m_bready_o),
      .arid_o(m_arid_o),
      .araddr_o(m_araddr_o),
      .arlen_o(m_arlen_o),
      .arsize_o(m_arsize_o),
      .arburst_o(m_arburst_o),
      .arlock_o(m_arlock_o),
      .arcache_o(m_arcache_o),
      .arprot_o(m_arprot_o),
      .arqos_o(m_arqos_o),
      .arregion_o(m_arregion_o),
      .aruser_o(m_aruser_o),
      .arvalid_o(m_arvalid_o),
      .arready_i(m_arready_i),
      .rid_i(m_rid_i),
      .rdata_i(m_rdata_i),
      .rresp_i(m_rresp_i),
      .rlast_i(m_rlast_i),
      .ruser_i(m_ruser_i),
      .rvalid_i(m_rvalid_i),
      .rready_o(m_rready_o)
  );

endmodule : bag_axi_dw
