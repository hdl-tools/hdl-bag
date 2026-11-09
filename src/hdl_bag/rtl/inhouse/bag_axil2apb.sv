// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil2apb: AXI4-Lite -> APB4/APB3 bridge built from PULP blocks. Inside the bridge (D10)
// the path runs from the upstream to the downstream domain:
//   axi_lite_dw_converter (when the widths differ) ->
//   axi_fifo (FifoDepth > 0) -> axi_cdc (AsyncCdc) -> AddrOffset subtracted ->
//   axi_lite_to_apb (one APB slave, one rule covering every address).
// Width conversion and the FIFO run in the upstream clock; the CDC hands over to the
// downstream clock, where the offset is subtracted.
// PSLVERR returns as SLVERR.
// The downstream address is the low DownAddrWidth bits of the offset address.
//
// Integration notes:
// * CDC (AsyncCdc): both domains must be reset together and leave reset in their own
//   clocks (CLK-CDC-RESET checks the reset style). axi_cdc's gray-coded FIFOs need
//   max-delay constraints on their pointer and data paths (no SDC is generated in v1).
// * Without a FIFO or a CDC the path is combinational from the upstream request to the
//   downstream port, and from the downstream response back upstream.
// * One APB transfer at a time; PULP axi_lite_to_apb starts a transfer only with both
//   response registers empty, so an unaccepted B holds back reads and an unaccepted R
//   holds back writes.

`include "axi/typedef.svh"

module bag_axil2apb #(
    parameter int unsigned UpAddrWidth   = 32,
    parameter int unsigned DownAddrWidth = 32,
    parameter int unsigned UpDataWidth   = 64,
    parameter int unsigned DownDataWidth = 32,
    parameter int unsigned FifoDepth     = 0,  // Bridges > FIFO depth
    parameter bit          AsyncCdc      = 1'b0,  // fabrics in different domains (D10)
    // Subtracted from upstream addresses: the window base for pass-through N, else 0.
    parameter logic [UpAddrWidth-1:0] AddrOffset = '0,
    localparam int unsigned AddrWidth = (UpAddrWidth > DownAddrWidth) ? UpAddrWidth
                                                                    : DownAddrWidth
) (
    input  logic up_clk_i,
    input  logic up_rst_ni,
    input  logic down_clk_i,  // = up_clk_i when AsyncCdc = 0
    input  logic down_rst_ni,

    // upstream: slave port of the upstream fabric
    input  logic [UpAddrWidth-1:0] s_awaddr_i,
    input  logic [3-1:0] s_awprot_i,
    input  logic s_awvalid_i,
    output logic s_awready_o,
    input  logic [UpDataWidth-1:0] s_wdata_i,
    input  logic [UpDataWidth/8-1:0] s_wstrb_i,
    input  logic s_wvalid_i,
    output logic s_wready_o,
    output logic [2-1:0] s_bresp_o,
    output logic s_bvalid_o,
    input  logic s_bready_i,
    input  logic [UpAddrWidth-1:0] s_araddr_i,
    input  logic [3-1:0] s_arprot_i,
    input  logic s_arvalid_i,
    output logic s_arready_o,
    output logic [UpDataWidth-1:0] s_rdata_o,
    output logic [2-1:0] s_rresp_o,
    output logic s_rvalid_o,
    input  logic s_rready_i,

    // downstream: master port of the downstream fabric
    output logic [DownAddrWidth-1:0] m_paddr_o,
    output logic [3-1:0] m_pprot_o,
    output logic m_psel_o,
    output logic m_penable_o,
    output logic m_pwrite_o,
    output logic [DownDataWidth-1:0] m_pwdata_o,
    output logic [DownDataWidth/8-1:0] m_pstrb_o,
    input  logic m_pready_i,
    input  logic [DownDataWidth-1:0] m_prdata_i,
    input  logic m_pslverr_i
);

  typedef logic [AddrWidth-1:0] addr_t;
  typedef logic [UpDataWidth-1:0] up_data_t;
  typedef logic [UpDataWidth/8-1:0] up_strb_t;
  typedef logic [DownDataWidth-1:0] dn_data_t;
  typedef logic [DownDataWidth/8-1:0] dn_strb_t;
  `AXI_LITE_TYPEDEF_ALL(up, addr_t, up_data_t, up_strb_t)
  `AXI_LITE_TYPEDEF_ALL(dn, addr_t, dn_data_t, dn_strb_t)

  dn_req_t  dw_req, fifo_req, cdc_req, off_req;
  dn_resp_t dw_resp, fifo_resp, cdc_resp, off_resp;

  up_req_t  up_req;
  up_resp_t up_resp;

  bag_axil_from_flat #(
      .AddrWidth   (UpAddrWidth),
      .DataWidth   (UpDataWidth),
      .ReqAddrWidth(AddrWidth),
      .axi_req_t   (up_req_t),
      .axi_resp_t  (up_resp_t)
  ) i_from_flat (
      .awaddr_i(s_awaddr_i),
      .awprot_i(s_awprot_i),
      .awvalid_i(s_awvalid_i),
      .awready_o(s_awready_o),
      .wdata_i(s_wdata_i),
      .wstrb_i(s_wstrb_i),
      .wvalid_i(s_wvalid_i),
      .wready_o(s_wready_o),
      .bresp_o(s_bresp_o),
      .bvalid_o(s_bvalid_o),
      .bready_i(s_bready_i),
      .araddr_i(s_araddr_i),
      .arprot_i(s_arprot_i),
      .arvalid_i(s_arvalid_i),
      .arready_o(s_arready_o),
      .rdata_o(s_rdata_o),
      .rresp_o(s_rresp_o),
      .rvalid_o(s_rvalid_o),
      .rready_i(s_rready_i),
      .req_o (up_req),
      .resp_i(up_resp)
  );

  if (UpDataWidth != DownDataWidth) begin : gen_dw
    axi_lite_dw_converter #(
        .AxiAddrWidth       (AddrWidth),
        .AxiSlvPortDataWidth(UpDataWidth),
        .AxiMstPortDataWidth(DownDataWidth),
        .axi_lite_aw_t      (up_aw_chan_t),
        .axi_lite_slv_w_t   (up_w_chan_t),
        .axi_lite_mst_w_t   (dn_w_chan_t),
        .axi_lite_b_t       (up_b_chan_t),
        .axi_lite_ar_t      (up_ar_chan_t),
        .axi_lite_slv_r_t   (up_r_chan_t),
        .axi_lite_mst_r_t   (dn_r_chan_t),
        .axi_lite_slv_req_t (up_req_t),
        .axi_lite_slv_res_t (up_resp_t),
        .axi_lite_mst_req_t (dn_req_t),
        .axi_lite_mst_res_t (dn_resp_t)
    ) i_dw (
        .clk_i    (up_clk_i),
        .rst_ni   (up_rst_ni),
        .slv_req_i(up_req),
        .slv_res_o(up_resp),
        .mst_req_o(dw_req),
        .mst_res_i(dw_resp)
    );
  end else begin : gen_no_dw
    assign dw_req  = up_req;
    assign up_resp = dw_resp;
  end

  // Same width, no FIFO and no CDC: nothing runs in the upstream clock.
  if (UpDataWidth == DownDataWidth && FifoDepth == 0 && !AsyncCdc)
  begin : gen_up_clock_unused
    logic unused_up;
    assign unused_up = ^{up_clk_i, up_rst_ni};
  end

  if (FifoDepth > 0) begin : gen_fifo
    axi_fifo #(
        .Depth      (FifoDepth),
        .FallThrough(1'b0),
        .aw_chan_t  (dn_aw_chan_t),
        .w_chan_t   (dn_w_chan_t),
        .b_chan_t   (dn_b_chan_t),
        .ar_chan_t  (dn_ar_chan_t),
        .r_chan_t   (dn_r_chan_t),
        .axi_req_t  (dn_req_t),
        .axi_resp_t (dn_resp_t)
    ) i_fifo (
        .clk_i     (up_clk_i),
        .rst_ni    (up_rst_ni),
        .test_i    (1'b0),
        .slv_req_i (dw_req),
        .slv_resp_o(dw_resp),
        .mst_req_o (fifo_req),
        .mst_resp_i(fifo_resp)
    );
  end else begin : gen_no_fifo
    assign fifo_req = dw_req;
    assign dw_resp  = fifo_resp;
  end

  if (AsyncCdc) begin : gen_cdc
    axi_cdc #(
        .aw_chan_t (dn_aw_chan_t),
        .w_chan_t  (dn_w_chan_t),
        .b_chan_t  (dn_b_chan_t),
        .ar_chan_t (dn_ar_chan_t),
        .r_chan_t  (dn_r_chan_t),
        .axi_req_t (dn_req_t),
        .axi_resp_t(dn_resp_t),
        .LogDepth  (2),
        .SyncStages(2)
    ) i_cdc (
        .src_clk_i (up_clk_i),
        .src_rst_ni(up_rst_ni),
        .src_req_i (fifo_req),
        .src_resp_o(fifo_resp),
        .dst_clk_i (down_clk_i),
        .dst_rst_ni(down_rst_ni),
        .dst_req_o (cdc_req),
        .dst_resp_i(cdc_resp)
    );
  end else begin : gen_no_cdc
    assign cdc_req   = fifo_req;
    assign fifo_resp = cdc_resp;
  end

  // Pass-through N: the downstream fabric decodes relative to the window base.
  always_comb begin
    off_req         = cdc_req;
    off_req.aw.addr = cdc_req.aw.addr - AddrWidth'(AddrOffset);
    off_req.ar.addr = cdc_req.ar.addr - AddrWidth'(AddrOffset);
  end
  assign cdc_resp = off_resp;

  typedef struct packed {
    addr_t          paddr;
    axi_pkg::prot_t pprot;
    logic           psel;
    logic           penable;
    logic           pwrite;
    dn_data_t       pwdata;
    dn_strb_t       pstrb;
  } apb_req_t;
  typedef struct packed {
    logic     pready;
    dn_data_t prdata;
    logic     pslverr;
  } apb_resp_t;
  typedef struct packed {
    int unsigned idx;
    addr_t       start_addr;
    addr_t       end_addr;
  } rule_t;
  // One rule covering every address (end 0 = end of the address space).
  localparam rule_t [0:0] AddrMap = '{'{idx: 32'd0, start_addr: '0, end_addr: '0}};

  apb_req_t  apb_req;
  apb_resp_t apb_resp;

  axi_lite_to_apb #(
      .NoApbSlaves     (32'd1),
      .NoRules         (32'd1),
      .AddrWidth       (AddrWidth),
      .DataWidth       (DownDataWidth),
      .PipelineRequest (1'b0),
      // A spill register on the responses (D73): PULP answers a write with no strobe set (and
      // a decode error) at once, without an APB transfer, so with a fall-through response B
      // would rise in its own AW/W handshake cycle, which AXI forbids (A3.3.1).
      .PipelineResponse(1'b1),
      .axi_lite_req_t  (dn_req_t),
      .axi_lite_resp_t (dn_resp_t),
      .apb_req_t       (apb_req_t),
      .apb_resp_t      (apb_resp_t),
      .rule_t          (rule_t)
  ) i_to_apb (
      .clk_i          (down_clk_i),
      .rst_ni         (down_rst_ni),
      .axi_lite_req_i (off_req),
      .axi_lite_resp_o(off_resp),
      .apb_req_o      (apb_req),
      .apb_resp_i     (apb_resp),
      .addr_map_i     (AddrMap)
  );

  addr_t paddr;
  assign paddr          = apb_req.paddr;
  assign m_paddr_o      = paddr[DownAddrWidth-1:0];
  assign m_pprot_o      = apb_req.pprot;
  assign m_psel_o       = apb_req.psel;
  assign m_penable_o    = apb_req.penable;
  assign m_pwrite_o     = apb_req.pwrite;
  assign m_pwdata_o     = apb_req.pwdata;
  assign m_pstrb_o      = apb_req.pstrb;
  assign apb_resp.pready  = m_pready_i;
  assign apb_resp.prdata  = m_prdata_i;
  assign apb_resp.pslverr = m_pslverr_i;

  if (AddrWidth > DownAddrWidth) begin : gen_addr_msb
    logic unused_paddr;
    assign unused_paddr = ^paddr[AddrWidth-1:DownAddrWidth];
  end

endmodule : bag_axil2apb
