// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// AXI4 / AXI4-Lite master engine of the in-house bridges into AXI (bag_ahb2axi,
// bag_apb2axi): the destination side of the 4-phase req/ack handshake (see
// bag_hs_apb_slv).
//
// * One transfer at a time: a request of 2^size_i bytes at addr_i becomes single-beat AXI
//   transfers (AxLEN = 0, INCR, ID 0), and ack_o rises once all of them have responded.
// * Width (D10): a request no wider than the AXI bus is one narrow transfer on the byte
//   lanes of its address (AxSIZE = size_i); a wider one becomes
//   2^size_i / (AxiDataWidth/8) full-width transfers at consecutive addresses.
//   Write strobes are strb_i masked to the request's bytes. Read data returns on the
//   request's byte lanes of rdata_o.
// * AXI4-Lite: the same signals without AxLEN/AxSIZE/AxBURST/ID/... (the Lite subset is
//   used as is: a Lite slave reads the whole word, the lanes are picked here).
// * Responses: SLVERR or DECERR on any transfer sets err_o.
// * Address: AddrOffset is subtracted (pass-through N); the AXI address is the low
//   AxiAddrWidth bits.
// * Limits, checked at elaboration: AddrOffset aligned to both data widths.

module bag_hs_axi_mst #(
    parameter int unsigned            InAddrWidth  = 32,
    parameter int unsigned            InDataWidth  = 32,
    parameter int unsigned            AxiAddrWidth = 32,
    parameter int unsigned            AxiDataWidth = 32,
    parameter logic [InAddrWidth-1:0] AddrOffset   = '0,

    localparam int unsigned InStrbWidth  = InDataWidth / 8,
    localparam int unsigned AxiStrbWidth = AxiDataWidth / 8
) (
    input  logic                   clk_i,
    input  logic                   rst_ni,

    // Request (handshake destination side)
    input  logic                   req_i,
    input  logic [InAddrWidth-1:0] addr_i,
    input  logic                   write_i,
    input  logic [InDataWidth-1:0] wdata_i,
    input  logic [InStrbWidth-1:0] strb_i,
    input  logic [            2:0] size_i,
    input  logic [            2:0] prot_i,
    input  logic [            3:0] cache_i,
    output logic                   ack_o,
    output logic [InDataWidth-1:0] rdata_o,
    output logic                   err_o,

    // AXI4 master (ID width 1)
    output logic                    m_awid_o,
    output logic [AxiAddrWidth-1:0] m_awaddr_o,
    output logic [             7:0] m_awlen_o,
    output logic [             2:0] m_awsize_o,
    output logic [             1:0] m_awburst_o,
    output logic                    m_awlock_o,
    output logic [             3:0] m_awcache_o,
    output logic [             2:0] m_awprot_o,
    output logic [             3:0] m_awqos_o,
    output logic [             3:0] m_awregion_o,
    output logic                    m_awvalid_o,
    input  logic                    m_awready_i,
    output logic [AxiDataWidth-1:0] m_wdata_o,
    output logic [AxiStrbWidth-1:0] m_wstrb_o,
    output logic                    m_wlast_o,
    output logic                    m_wvalid_o,
    input  logic                    m_wready_i,
    input  logic                    m_bid_i,
    input  logic [             1:0] m_bresp_i,
    input  logic                    m_bvalid_i,
    output logic                    m_bready_o,
    output logic                    m_arid_o,
    output logic [AxiAddrWidth-1:0] m_araddr_o,
    output logic [             7:0] m_arlen_o,
    output logic [             2:0] m_arsize_o,
    output logic [             1:0] m_arburst_o,
    output logic                    m_arlock_o,
    output logic [             3:0] m_arcache_o,
    output logic [             2:0] m_arprot_o,
    output logic [             3:0] m_arqos_o,
    output logic [             3:0] m_arregion_o,
    output logic                    m_arvalid_o,
    input  logic                    m_arready_i,
    input  logic                    m_rid_i,
    input  logic [AxiDataWidth-1:0] m_rdata_i,
    input  logic [             1:0] m_rresp_i,
    input  logic                    m_rlast_i,
    input  logic                    m_rvalid_i,
    output logic                    m_rready_o
);

  localparam int unsigned InBytes  = InDataWidth / 8;
  localparam int unsigned AxiBytes = AxiDataWidth / 8;
  localparam int unsigned AxiSize  = unsigned'($clog2(AxiBytes));
  // Bytes of the widest operand; the lane shifts work at this width.
  localparam int unsigned WBytes   = InBytes > AxiBytes ? InBytes : AxiBytes;
  localparam int unsigned WBits    = 8 * WBytes;
  localparam int unsigned InLane   = InBytes > 1 ? unsigned'($clog2(InBytes)) : 1;
  localparam int unsigned AxiLane  = AxiBytes > 1 ? unsigned'($clog2(AxiBytes)) : 1;
  localparam int unsigned MaxParts = InBytes > AxiBytes ? InBytes / AxiBytes : 1;
  localparam int unsigned CntWidth = MaxParts > 1 ? unsigned'($clog2(MaxParts)) + 1 : 1;

  if (AddrOffset % InAddrWidth'(WBytes) != '0) begin : gen_offset_check
    $error("bag_hs_axi_mst: AddrOffset must be aligned to both data widths");
  end

  typedef enum logic [1:0] {
    Idle,
    Issue,
    Resp,
    Ack
  } state_e;
  state_e s_q;

  logic [InAddrWidth-1:0] base_q;  // translated address of the first transfer
  logic [   CntWidth-1:0] cnt_q;  // transfer index
  logic [   CntWidth-1:0] parts_q;  // transfers for this request
  logic [            2:0] size_q;  // AxSIZE of each transfer
  logic                   aw_done_q, w_done_q;
  logic [InDataWidth-1:0] rdata_q;
  logic                   err_q;
  // ack is its own flop: it feeds a synchronizer, so it must not be a state decode
  // (a decode can glitch on a two-bit state change).
  logic                   ack_q;

  // Current transfer: address, byte lanes on both sides, data and strobes.
  logic [ InAddrWidth-1:0] sub_addr;
  logic [      InLane-1:0] in_lane;
  logic [     AxiLane-1:0] axi_lane;
  logic [   WBits-1:0] wide_wdata, wide_rdata, byte_mask;
  logic [  WBytes-1:0] wide_strb, sub_mask;

  assign sub_addr = base_q + InAddrWidth'(32'(cnt_q) * AxiBytes);
  assign in_lane  = InBytes > 1 ? InLane'(sub_addr) : '0;
  assign axi_lane = AxiBytes > 1 ? AxiLane'(sub_addr) : '0;
  // The transfer's bytes, counted from its first byte.
  always_comb begin
    for (int unsigned b = 0; b < WBytes; b++) begin
      sub_mask[b]          = b < (32'd1 << size_q);
      byte_mask[8*b+:8]    = {8{sub_mask[b]}};
    end
  end

  always_comb begin
    // Move the transfer's bytes from the request lanes to the AXI lanes.
    wide_wdata = WBits'(wdata_i) >> {in_lane, 3'b000};
    wide_wdata = wide_wdata << {axi_lane, 3'b000};
    wide_strb  = (WBytes'(strb_i) >> in_lane) & sub_mask;
    wide_strb  = wide_strb << axi_lane;
    // And the read data back from the AXI lanes to the request lanes.
    wide_rdata = WBits'(m_rdata_i) >> {axi_lane, 3'b000};
    wide_rdata = (wide_rdata & byte_mask) << {in_lane, 3'b000};
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      s_q       <= Idle;
      base_q    <= '0;
      cnt_q     <= '0;
      parts_q   <= '0;
      size_q    <= '0;
      aw_done_q <= 1'b0;
      w_done_q  <= 1'b0;
      rdata_q   <= '0;
      err_q     <= 1'b0;
      ack_q     <= 1'b0;
    end else begin
      case (s_q)
        Idle: begin
          if (req_i) begin
            base_q    <= addr_i - AddrOffset;
            cnt_q     <= '0;
            rdata_q   <= '0;
            err_q     <= 1'b0;
            aw_done_q <= 1'b0;
            w_done_q  <= 1'b0;
            if (32'(size_i) > AxiSize) begin
              parts_q <= CntWidth'((32'd1 << size_i) / AxiBytes);
              size_q  <= 3'(AxiSize);
            end else begin
              parts_q <= CntWidth'(1);
              size_q  <= size_i;
            end
            s_q <= Issue;
          end
        end
        Issue: begin
          if (write_i) begin
            if (m_awvalid_o && m_awready_i) aw_done_q <= 1'b1;
            if (m_wvalid_o && m_wready_i) w_done_q <= 1'b1;
            if ((aw_done_q || m_awready_i) && (w_done_q || m_wready_i)) s_q <= Resp;
          end else if (m_arready_i) begin
            s_q <= Resp;
          end
        end
        Resp: begin
          if (write_i ? m_bvalid_i : m_rvalid_i) begin
            err_q     <= err_q | (write_i ? m_bresp_i[1] : m_rresp_i[1]);
            aw_done_q <= 1'b0;
            w_done_q  <= 1'b0;
            if (!write_i) rdata_q <= rdata_q | InDataWidth'(wide_rdata);
            if (cnt_q + 1'b1 == parts_q) begin
              ack_q <= 1'b1;
              s_q   <= Ack;
            end else begin
              cnt_q <= cnt_q + 1'b1;
              s_q   <= Issue;
            end
          end
        end
        Ack: begin  // hold the response until the request drops
          if (!req_i) begin
            ack_q <= 1'b0;
            s_q   <= Idle;
          end
        end
        default: s_q <= Idle;
      endcase
    end
  end

  assign ack_o   = ack_q;
  assign rdata_o = rdata_q;
  assign err_o   = err_q;

  logic [InAddrWidth-1:0] axi_addr;
  assign axi_addr = sub_addr;

  assign m_awid_o     = 1'b0;
  assign m_awaddr_o   = AxiAddrWidth'(axi_addr);
  assign m_awlen_o    = 8'd0;
  assign m_awsize_o   = size_q;
  assign m_awburst_o  = 2'b01;  // INCR
  assign m_awlock_o   = 1'b0;
  assign m_awcache_o  = cache_i;
  assign m_awprot_o   = prot_i;
  assign m_awqos_o    = 4'd0;
  assign m_awregion_o = 4'd0;
  assign m_awvalid_o  = s_q == Issue && write_i && !aw_done_q;
  assign m_wdata_o    = AxiDataWidth'(wide_wdata);
  assign m_wstrb_o    = AxiStrbWidth'(wide_strb);
  assign m_wlast_o    = 1'b1;
  assign m_wvalid_o   = s_q == Issue && write_i && !w_done_q;
  assign m_bready_o   = s_q == Resp && write_i;

  assign m_arid_o     = 1'b0;
  assign m_araddr_o   = AxiAddrWidth'(axi_addr);
  assign m_arlen_o    = 8'd0;
  assign m_arsize_o   = size_q;
  assign m_arburst_o  = 2'b01;
  assign m_arlock_o   = 1'b0;
  assign m_arcache_o  = cache_i;
  assign m_arprot_o   = prot_i;
  assign m_arqos_o    = 4'd0;
  assign m_arregion_o = 4'd0;
  assign m_arvalid_o  = s_q == Issue && !write_i;
  assign m_rready_o   = s_q == Resp && !write_i;

  // Single beats with ID 0: the returned IDs and RLAST carry no information.
  logic unused;
  assign unused = ^{m_bid_i, m_rid_i, m_rlast_i, m_bresp_i[0], m_rresp_i[0]};

endmodule
