// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// APB3/APB4 slave to AHB-Lite master bridge.
//
// * One APB transfer at a time becomes single AHB transfers (NONSEQ, HBURST SINGLE, one
//   at a time with IDLE between them):
//   - a read, or a write with every PSTRB bit set, is one transfer of the APB width;
//   - AHB-Lite has no write strobes, so a write with some PSTRB bits clear becomes one
//     byte write per set bit, and a write with no PSTRB bit set does nothing.
// * Width (D10): the APB bus is never wider than the AHB bus (APB <= 32 bits, AHB >= 32),
//   so the APB data sits on the AHB byte lanes of its address.
// * HPROT = {2'b00 (non-cacheable, non-bufferable), privileged = PPROT[0],
//   data = !PPROT[2]}; HNONSEC = PPROT[1]; HMASTLOCK = 0.
// * HRESP ERROR on any part becomes PSLVERR (the remaining parts are still issued).
// * Address: AddrOffset is subtracted (pass-through N); 0 for pass-through Y.
// * Clocks (D10): the two sides talk through a 4-phase req/ack handshake (bag_hs_apb_slv);
//   with AsyncCdc = 1 req and ack pass through bag_sync. Reset: prst_ni and hrst_ni must
//   assert together (one reset event for both sides).
// * Limits, checked at elaboration: PDataWidth <= HDataWidth, and AddrOffset aligned to
//   the AHB data width.

module bag_apb2ahb #(
    parameter int unsigned           PAddrWidth = 32,
    parameter int unsigned           PDataWidth = 32,
    parameter int unsigned           HAddrWidth = 32,
    parameter int unsigned           HDataWidth = 32,
    parameter logic [PAddrWidth-1:0] AddrOffset = '0,
    parameter bit                    AsyncCdc   = 1'b0,
    parameter int unsigned           SyncStages = 2,

    localparam int unsigned PStrbWidth = PDataWidth / 8
) (
    // APB slave
    input  logic                  pclk_i,
    input  logic                  prst_ni,
    input  logic                  psel_i,
    input  logic                  penable_i,
    input  logic                  pwrite_i,
    input  logic [PAddrWidth-1:0] paddr_i,
    input  logic [           2:0] pprot_i,
    input  logic [PDataWidth-1:0] pwdata_i,
    input  logic [PStrbWidth-1:0] pstrb_i,
    output logic                  pready_o,
    output logic [PDataWidth-1:0] prdata_o,
    output logic                  pslverr_o,

    // AHB-Lite master
    input  logic                  hclk_i,  // = pclk_i when AsyncCdc = 0
    input  logic                  hrst_ni,
    output logic [HAddrWidth-1:0] haddr_o,
    output logic [           2:0] hburst_o,
    output logic                  hmastlock_o,
    output logic [           3:0] hprot_o,
    output logic                  hnonsec_o,
    output logic [           2:0] hsize_o,
    output logic [           1:0] htrans_o,
    output logic [HDataWidth-1:0] hwdata_o,
    output logic                  hwrite_o,
    input  logic [HDataWidth-1:0] hrdata_i,
    input  logic                  hready_i,
    input  logic                  hresp_i
);

  localparam int unsigned PBytes = PDataWidth / 8;
  localparam int unsigned HBytes = HDataWidth / 8;
  localparam int unsigned HLane  = HBytes > 1 ? unsigned'($clog2(HBytes)) : 1;
  localparam int unsigned PLane  = PBytes > 1 ? unsigned'($clog2(PBytes)) : 1;
  localparam logic [2:0]  PSize  = 3'(unsigned'($clog2(PBytes)));

  if (PDataWidth > HDataWidth) begin : gen_width_check
    $error("bag_apb2ahb: need PDataWidth <= HDataWidth");
  end
  if (AddrOffset % PAddrWidth'(HBytes) != '0) begin : gen_offset_check
    $error("bag_apb2ahb: AddrOffset must be aligned to the AHB data width");
  end

  logic                  req, write, err_q;
  logic                  ack_q;
  logic [PAddrWidth-1:0] addr;
  logic [PDataWidth-1:0] wdata, rdata_q;
  logic [PStrbWidth-1:0] strb;
  logic [           2:0] prot;

  bag_hs_apb_slv #(
      .AddrWidth (PAddrWidth),
      .DataWidth (PDataWidth),
      .AsyncCdc  (AsyncCdc),
      .SyncStages(SyncStages)
  ) u_apb (
      .pclk_i     (pclk_i),
      .prst_ni    (prst_ni),
      .psel_i     (psel_i),
      .penable_i  (penable_i),
      .pwrite_i   (pwrite_i),
      .paddr_i    (paddr_i),
      .pprot_i    (pprot_i),
      .pwdata_i   (pwdata_i),
      .pstrb_i    (pstrb_i),
      .pready_o   (pready_o),
      .prdata_o   (prdata_o),
      .pslverr_o  (pslverr_o),
      .dst_clk_i  (hclk_i),
      .dst_rst_ni (hrst_ni),
      .dst_req_o  (req),
      .dst_addr_o (addr),
      .dst_write_o(write),
      .dst_wdata_o(wdata),
      .dst_strb_o (strb),
      .dst_prot_o (prot),
      .dst_ack_i  (ack_q),
      .dst_rdata_i(rdata_q),
      .dst_err_i  (err_q)
  );

  // ------------------------------------------------------------ AHB side
  typedef enum logic [1:0] {
    HIdle,
    HAddr,  // address phase on the bus, waiting for HREADY
    HData,  // data phase, waiting for HREADY
    HAck
  } state_e;
  state_e s_q;

  logic [PAddrWidth-1:0] word;  // translated, aligned to the APB width
  logic [     HLane-1:0] hlane;  // AHB byte lane of the APB word
  logic [PStrbWidth-1:0] todo_q;  // byte writes still to issue
  logic                  bytes_q;  // byte-write mode
  logic [     PLane-1:0] byte_idx;  // the lowest byte still to write
  logic                  full;

  assign word  = (addr - AddrOffset) & ~PAddrWidth'(PBytes - 1);
  assign hlane = HBytes > 1 ? HLane'(word) : '0;
  assign full  = !write || strb == '1;

  always_comb begin
    byte_idx = '0;
    for (int b = int'(PBytes) - 1; b >= 0; b--) begin
      if (todo_q[b]) byte_idx = PLane'(unsigned'(b));
    end
  end

  always_ff @(posedge hclk_i or negedge hrst_ni) begin
    if (!hrst_ni) begin
      s_q     <= HIdle;
      ack_q   <= 1'b0;
      err_q   <= 1'b0;
      rdata_q <= '0;
      todo_q  <= '0;
      bytes_q <= 1'b0;
    end else begin
      case (s_q)
        HIdle: begin
          if (req && !ack_q) begin
            err_q   <= 1'b0;
            rdata_q <= '0;
            bytes_q <= !full;
            todo_q  <= strb;
            if (full || strb != '0) begin
              s_q <= HAddr;
            end else begin
              ack_q <= 1'b1;  // a write with no strobe: nothing to do
              s_q   <= HAck;
            end
          end
        end
        HAddr: if (hready_i) s_q <= HData;
        HData: begin
          if (hready_i) begin
            err_q <= err_q | hresp_i;
            if (!write) rdata_q <= PDataWidth'(hrdata_i >> {hlane, 3'b000});
            if (bytes_q && (todo_q & ~(PStrbWidth'(1'b1) << byte_idx)) != '0) begin
              todo_q <= todo_q & ~(PStrbWidth'(1'b1) << byte_idx);
              s_q    <= HAddr;
            end else begin
              ack_q <= 1'b1;
              s_q   <= HAck;
            end
          end
        end
        HAck: begin
          if (!req) begin
            ack_q <= 1'b0;
            s_q   <= HIdle;
          end
        end
        default: s_q <= HIdle;
      endcase
    end
  end

  logic [PAddrWidth-1:0] xfer_addr;
  assign xfer_addr   = bytes_q ? word + PAddrWidth'(byte_idx) : word;

  assign htrans_o    = s_q == HAddr ? 2'b10 : 2'b00;  // NONSEQ : IDLE
  assign haddr_o     = HAddrWidth'(xfer_addr);
  assign hburst_o    = 3'b000;  // SINGLE
  assign hmastlock_o = 1'b0;
  assign hsize_o     = bytes_q ? 3'd0 : PSize;
  assign hwrite_o    = write;
  assign hprot_o     = {2'b00, prot[0], !prot[2]};
  assign hnonsec_o   = prot[1];
  assign hwdata_o    = HDataWidth'(wdata) << {hlane, 3'b000};

endmodule
