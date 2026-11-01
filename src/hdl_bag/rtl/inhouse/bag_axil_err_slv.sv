// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// bag_axil_err_slv: AXI4-Lite slave that answers every access with an error response.
// The guard of an AXI4-Lite fabric steers denied and unmapped accesses here (PULP has an
// AXI4 axi_err_slv only). One write and one read are in flight at a time: AW and W are
// accepted independently (W may come first), B follows once both arrived; R follows AR.
// Read data is zero.

module bag_axil_err_slv #(
    parameter type        axi_req_t  = logic,
    parameter type        axi_resp_t = logic,
    parameter logic [1:0] Resp       = 2'b11  // axi_pkg::RESP_DECERR
) (
    input  logic      clk_i,
    input  logic      rst_ni,
    input  axi_req_t  slv_req_i,
    output axi_resp_t slv_resp_o
);

  logic aw_q, w_q, b_q, r_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      aw_q <= 1'b0;
      w_q  <= 1'b0;
      b_q  <= 1'b0;
      r_q  <= 1'b0;
    end else begin
      if (b_q) begin
        if (slv_req_i.b_ready) b_q <= 1'b0;
      end else begin
        if (slv_req_i.aw_valid && !aw_q) aw_q <= 1'b1;
        if (slv_req_i.w_valid && !w_q) w_q <= 1'b1;
        if ((aw_q || slv_req_i.aw_valid) && (w_q || slv_req_i.w_valid)) begin
          b_q  <= 1'b1;
          aw_q <= 1'b0;
          w_q  <= 1'b0;
        end
      end
      if (r_q) begin
        if (slv_req_i.r_ready) r_q <= 1'b0;
      end else if (slv_req_i.ar_valid) begin
        r_q <= 1'b1;
      end
    end
  end

  always_comb begin
    slv_resp_o          = '0;
    slv_resp_o.aw_ready = !aw_q && !b_q;
    slv_resp_o.w_ready  = !w_q && !b_q;
    slv_resp_o.b_valid  = b_q;
    slv_resp_o.b.resp   = Resp;
    slv_resp_o.ar_ready = !r_q;
    slv_resp_o.r_valid  = r_q;
    slv_resp_o.r.resp   = Resp;
  end

  // The payload (addresses, data, strobes, PROT) does not matter for an error.
  logic unused_payload;
  assign unused_payload = ^{slv_req_i.aw, slv_req_i.w, slv_req_i.ar};

endmodule : bag_axil_err_slv
