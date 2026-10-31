// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// AHB-Lite slave to APB3/APB4 master bridge.
//
// * One transfer at a time; bursts become single APB transfers (one per beat).
// * Width (D10): a beat wider than the APB bus is split into HDataWidth/PDataWidth
//   sequential APB transfers; narrower beats use PSTRB (APB4) on their byte lanes.
// * Address: AddrOffset is subtracted (pass-through N); 0 for pass-through Y.
// * PPROT = {instruction = !HPROT[0], non-secure = HNONSEC, privileged = HPROT[1]}.
// * PSLVERR on any part becomes the two-cycle HRESP ERROR.
// * Clocks (D10): the two sides talk through a 4-phase req/ack handshake. With
//   AsyncCdc = 1 req and ack pass through bag_sync; the payload is held stable while
//   req (or ack) is high, so it crosses without synchronizers (CDC/STA: constrain the
//   payload paths with a max-delay, data-path-only exception).
// * Reset: hrst_ni and prst_ni must assert together (one reset event for both sides).
//   Resetting one side alone during a transfer can desynchronize the handshake.
// * Limits, checked at elaboration: PDataWidth <= HDataWidth <= 8 * PDataWidth, and
//   AddrOffset aligned to the AHB data width.

module bag_ahb2apb #(
    parameter int unsigned           HAddrWidth = 32,
    parameter int unsigned           HDataWidth = 32,
    parameter int unsigned           PAddrWidth = 32,
    parameter int unsigned           PDataWidth = 32,
    parameter logic [HAddrWidth-1:0] AddrOffset = '0,
    parameter bit                    AsyncCdc   = 1'b0,
    parameter int unsigned           SyncStages = 2,

    localparam int unsigned PStrbWidth = PDataWidth / 8
) (
    // AHB-Lite slave
    input  logic                  hclk_i,
    input  logic                  hrst_ni,
    input  logic                  hsel_i,
    input  logic [HAddrWidth-1:0] haddr_i,
    input  logic [           2:0] hburst_i,
    input  logic [           3:0] hprot_i,
    input  logic                  hnonsec_i,
    input  logic [           2:0] hsize_i,
    input  logic [           1:0] htrans_i,
    input  logic [HDataWidth-1:0] hwdata_i,
    input  logic                  hwrite_i,
    input  logic                  hready_i,
    output logic [HDataWidth-1:0] hrdata_o,
    output logic                  hreadyout_o,
    output logic                  hresp_o,

    // APB master
    input  logic                  pclk_i,
    input  logic                  prst_ni,
    output logic [PAddrWidth-1:0] paddr_o,
    output logic [           2:0] pprot_o,
    output logic                  psel_o,
    output logic                  penable_o,
    output logic                  pwrite_o,
    output logic [PDataWidth-1:0] pwdata_o,
    output logic [PStrbWidth-1:0] pstrb_o,
    input  logic                  pready_i,
    input  logic [PDataWidth-1:0] prdata_i,
    input  logic                  pslverr_i
);

  localparam int unsigned PBytes = PDataWidth / 8;
  localparam int unsigned HBytes = HDataWidth / 8;
  localparam int unsigned PLaneBits = PBytes > 1 ? unsigned'($clog2(PBytes)) : 1;

  if (PDataWidth > HDataWidth || HDataWidth > 8 * PDataWidth) begin : gen_width_check
    $error("bag_ahb2apb: need PDataWidth <= HDataWidth <= 8 * PDataWidth");
  end
  if (AddrOffset % HAddrWidth'(HBytes) != '0) begin : gen_offset_check
    $error("bag_ahb2apb: AddrOffset must be aligned to the AHB data width");
  end

  // ------------------------------------------------------------ payload (AHB domain)
  logic [HAddrWidth-1:0] req_addr_q;  // translated, aligned to the APB data width
  logic [           3:0] req_count_q;  // APB transfers for this beat
  logic [PStrbWidth-1:0] req_strb_q;
  logic [           2:0] req_prot_q;
  logic                  req_write_q;
  logic [HDataWidth-1:0] req_wdata_q;
  logic                  req_q;
  logic                  ack_s;

  // Response (APB domain).
  logic [HDataWidth-1:0] rsp_rdata_q;
  logic                  rsp_err_q;
  logic                  ack_q;
  logic                  req_s;

  // ------------------------------------------------------------ handshake sync
  if (AsyncCdc) begin : gen_cdc
    bag_sync #(.Stages(SyncStages)) u_req_sync (
        .clk_i (pclk_i),
        .rst_ni(prst_ni),
        .d_i   (req_q),
        .q_o   (req_s)
    );
    bag_sync #(.Stages(SyncStages)) u_ack_sync (
        .clk_i (hclk_i),
        .rst_ni(hrst_ni),
        .d_i   (ack_q),
        .q_o   (ack_s)
    );
  end else begin : gen_no_cdc
    assign req_s = req_q;
    assign ack_s = ack_q;
  end

  // ------------------------------------------------------------ AHB side
  typedef enum logic [2:0] {
    AIdle,
    AReq,
    AWait,
    ADone,
    AErr1,
    AErr2
  } ahb_state_e;
  ahb_state_e a_q;

  logic                  accept;
  logic [HAddrWidth-1:0] beat_addr;
  logic [           6:0] beat_bytes;
  logic [ PLaneBits-1:0] lane_offset;
  logic [HDataWidth-1:0] rdata_q;

  assign accept = hsel_i && hready_i && htrans_i[1] &&
                  (a_q == AIdle || a_q == ADone || a_q == AErr2);
  assign beat_addr = haddr_i - AddrOffset;
  assign beat_bytes = 7'd1 << hsize_i;
  // Byte lane within the APB word; an 8-bit APB bus has a single lane.
  assign lane_offset = PBytes > 1 ? PLaneBits'(beat_addr) : '0;

  always_ff @(posedge hclk_i or negedge hrst_ni) begin
    if (!hrst_ni) begin
      a_q         <= AIdle;
      req_q       <= 1'b0;
      req_addr_q  <= '0;
      req_count_q <= '0;
      req_strb_q  <= '0;
      req_prot_q  <= '0;
      req_write_q <= 1'b0;
      req_wdata_q <= '0;
      rdata_q     <= '0;
    end else begin
      case (a_q)
        AReq: begin
          if (!ack_s) begin
            req_wdata_q <= hwdata_i;
            req_q       <= 1'b1;
            a_q         <= AWait;
          end
        end
        AWait: begin
          if (ack_s) begin
            req_q   <= 1'b0;
            rdata_q <= rsp_rdata_q;
            a_q     <= rsp_err_q ? AErr1 : ADone;
          end
        end
        AErr1: a_q <= AErr2;
        AIdle, ADone, AErr2: a_q <= AIdle;
        default: a_q <= AIdle;
      endcase
      if (accept) begin
        a_q        <= AReq;
        req_addr_q <= beat_addr & ~HAddrWidth'(PBytes - 1);
        if (32'(beat_bytes) > PBytes) begin
          req_count_q <= 4'(32'(beat_bytes) / PBytes);
          req_strb_q  <= '1;
        end else begin
          req_count_q <= 4'd1;
          req_strb_q  <= PStrbWidth'((32'd1 << beat_bytes) - 32'd1) << lane_offset;
        end
        req_prot_q  <= {!hprot_i[0], hnonsec_i, hprot_i[1]};
        req_write_q <= hwrite_i;
      end
    end
  end

  // Not needed: every beat is a separate APB transfer (HBURST, BUSY vs IDLE), and
  // HPROT[3:2] (cacheable, bufferable) have no APB equivalent.
  logic unused;
  assign unused = ^{hburst_i, hprot_i[3:2], htrans_i[0]};

  assign hreadyout_o = !(a_q == AReq || a_q == AWait || a_q == AErr1);
  assign hresp_o     = a_q == AErr1 || a_q == AErr2;
  assign hrdata_o    = rdata_q;

  // ------------------------------------------------------------ APB side
  typedef enum logic [1:0] {
    PIdle,
    PSetup,
    PAccess,
    PAck
  } apb_state_e;
  apb_state_e p_q;

  logic [           3:0] cnt_q;
  logic [HAddrWidth-1:0] sub_addr;
  int unsigned           lane;

  assign sub_addr = req_addr_q + HAddrWidth'(32'(cnt_q) * PBytes);
  assign lane     = (32'(sub_addr) % HBytes) / PBytes;

  always_ff @(posedge pclk_i or negedge prst_ni) begin
    if (!prst_ni) begin
      p_q         <= PIdle;
      cnt_q       <= '0;
      ack_q       <= 1'b0;
      rsp_err_q   <= 1'b0;
      rsp_rdata_q <= '0;
    end else begin
      case (p_q)
        PIdle: begin
          if (req_s && !ack_q) begin
            cnt_q       <= '0;
            rsp_err_q   <= 1'b0;
            rsp_rdata_q <= '0;
            p_q         <= PSetup;
          end
        end
        PSetup: p_q <= PAccess;
        PAccess: begin
          if (pready_i) begin
            rsp_err_q <= rsp_err_q | pslverr_i;
            for (int unsigned l = 0; l < HDataWidth / PDataWidth; l++) begin
              if (l == lane) rsp_rdata_q[l*PDataWidth+:PDataWidth] <= prdata_i;
            end
            if (cnt_q + 4'd1 == req_count_q) begin
              ack_q <= 1'b1;
              p_q   <= PAck;
            end else begin
              cnt_q <= cnt_q + 4'd1;
              p_q   <= PSetup;
            end
          end
        end
        PAck: begin
          if (!req_s) begin
            ack_q <= 1'b0;
            p_q   <= PIdle;
          end
        end
        default: p_q <= PIdle;
      endcase
    end
  end

  always_comb begin
    pwdata_o = '0;
    for (int unsigned l = 0; l < HDataWidth / PDataWidth; l++) begin
      if (l == lane) pwdata_o = req_wdata_q[l*PDataWidth+:PDataWidth];
    end
  end

  assign paddr_o   = PAddrWidth'(sub_addr);
  assign pprot_o   = req_prot_q;
  assign psel_o    = p_q == PSetup || p_q == PAccess;
  assign penable_o = p_q == PAccess;
  assign pwrite_o  = req_write_q;
  assign pstrb_o   = req_write_q ? req_strb_q : '0;

endmodule
