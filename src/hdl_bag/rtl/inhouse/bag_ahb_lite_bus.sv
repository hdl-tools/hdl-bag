// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The hdl-bag Authors
//
// Single-layer AHB-Lite bus: NoMasters masters, NoSlaves slaves.
//
// Arbitration (only with more than one master):
//   * Each master has an input stage. A master whose address phase is accepted (its
//     HREADY is high) but which does not own the bus that cycle has the address phase
//     held; its HREADY then stays low until the held transfer has been issued and its
//     data phase has completed. Masters keep HWDATA stable while HREADY is low, so only
//     the address phase is registered.
//   * Round-robin, or fixed priority (Priority, 0 = highest).
//   * A burst (HBURST != SINGLE) or a locked sequence (HMASTLOCK) keeps the grant while
//     the owner continues with SEQ / BUSY or holds HMASTLOCK. HMASTLOCK is not forwarded
//     to slaves (v1).
// Decode is bag_addr_decode on the granted master's address. A miss or blocked access
// with no default slave port goes to an internal default slave that answers IDLE / BUSY
// with OKAY and NONSEQ / SEQ with the two-cycle ERROR response.

module bag_ahb_lite_bus #(
    parameter int unsigned AddrWidth = 32,
    parameter int unsigned DataWidth = 32,
    parameter int unsigned NoMasters = 1,
    parameter int unsigned NoSlaves  = 1,
    parameter int unsigned NoRules   = 1,
    parameter int unsigned NoAliases = 1,

    parameter bit                         FixedPriority = 1'b0,
    parameter logic [NoMasters-1:0][31:0] Priority      = '0,

    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleBase    = '0,
    parameter logic [NoRules-1:0][AddrWidth-1:0] RuleMask    = '0,
    parameter logic [NoRules-1:0][31:0]          RuleSlave   = '0,
    parameter logic [NoRules-1:0]                RuleNoRead  = '0,
    parameter logic [NoRules-1:0]                RuleNoWrite = '0,
    parameter logic [NoRules-1:0]                RuleSecure  = '0,

    parameter logic [NoMasters-1:0][NoSlaves-1:0] Connect    = '1,
    parameter logic [NoMasters-1:0][NoSlaves-1:0] MstNoRead  = '0,
    parameter logic [NoMasters-1:0][NoSlaves-1:0] MstNoWrite = '0,

    parameter logic [NoMasters-1:0]       MissToDefault = '0,
    parameter logic [NoMasters-1:0][31:0] DefaultSlave  = '0,

    parameter logic [NoAliases-1:0][31:0]          AliasMaster = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasBase   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasMask   = '0,
    parameter logic [NoAliases-1:0][AddrWidth-1:0] AliasTo     = '0
) (
    input logic                 clk_i,
    input logic                 rst_ni,
    input logic [NoAliases-1:0] alias_en_i,

    // Master side, one entry per master port.
    input  logic [NoMasters-1:0][AddrWidth-1:0] s_haddr_i,
    input  logic [NoMasters-1:0][          2:0] s_hburst_i,
    input  logic [NoMasters-1:0]                s_hmastlock_i,
    input  logic [NoMasters-1:0][          3:0] s_hprot_i,
    input  logic [NoMasters-1:0]                s_hnonsec_i,
    input  logic [NoMasters-1:0][          2:0] s_hsize_i,
    input  logic [NoMasters-1:0][          1:0] s_htrans_i,
    input  logic [NoMasters-1:0][DataWidth-1:0] s_hwdata_i,
    input  logic [NoMasters-1:0]                s_hwrite_i,
    output logic [NoMasters-1:0][DataWidth-1:0] s_hrdata_o,
    output logic [NoMasters-1:0]                s_hready_o,
    output logic [NoMasters-1:0]                s_hresp_o,

    // Slave side, one entry per slave port.
    output logic [NoSlaves-1:0]                m_hsel_o,
    output logic [NoSlaves-1:0][AddrWidth-1:0] m_haddr_o,
    output logic [NoSlaves-1:0][          2:0] m_hburst_o,
    output logic [NoSlaves-1:0][          3:0] m_hprot_o,
    output logic [NoSlaves-1:0]                m_hnonsec_o,
    output logic [NoSlaves-1:0][          2:0] m_hsize_o,
    output logic [NoSlaves-1:0][          1:0] m_htrans_o,
    output logic [NoSlaves-1:0][DataWidth-1:0] m_hwdata_o,
    output logic [NoSlaves-1:0]                m_hwrite_o,
    output logic [NoSlaves-1:0]                m_hready_o,
    input  logic [NoSlaves-1:0][DataWidth-1:0] m_hrdata_i,
    input  logic [NoSlaves-1:0]                m_hreadyout_i,
    input  logic [NoSlaves-1:0]                m_hresp_i
);

  localparam logic [1:0] Idle = 2'b00;
  localparam logic [1:0] Busy = 2'b01;
  localparam logic [1:0] Seq = 2'b11;

  // ------------------------------------------------------------ input stages
  logic [NoMasters-1:0]                held_valid_q;
  logic [NoMasters-1:0][AddrWidth-1:0] held_haddr_q;
  logic [NoMasters-1:0][          2:0] held_hburst_q;
  logic [NoMasters-1:0]                held_hmastlock_q;
  logic [NoMasters-1:0][          3:0] held_hprot_q;
  logic [NoMasters-1:0]                held_hnonsec_q;
  logic [NoMasters-1:0][          2:0] held_hsize_q;
  logic [NoMasters-1:0][          1:0] held_htrans_q;
  logic [NoMasters-1:0]                held_hwrite_q;

  // Data phase of the shared bus.
  logic                 data_valid_q;  // a master's NONSEQ / SEQ / BUSY is in its data phase
  logic [NoMasters-1:0] data_mst_q;  // one-hot owner of that data phase
  logic [ NoSlaves-1:0] data_sel_q;  // slave port answering the data phase
  logic                 data_dflt_q;  // internal default slave answering it
  logic                 bus_hready;
  logic                 bus_hresp;
  logic [DataWidth-1:0] bus_hrdata;
  logic [DataWidth-1:0] bus_hwdata;

  // Arbitration.
  logic [NoMasters-1:0] live_valid;  // address phase accepted from the master this cycle
  logic [NoMasters-1:0] req;
  logic [NoMasters-1:0] grant;  // one-hot owner of the current address phase
  logic                 granted;
  logic [NoMasters-1:0] lock_mst_q;  // one-hot burst / lock owner
  logic                 lock_valid_q;
  logic                 lock_cont;
  logic [NoMasters-1:0] last_q;  // one-hot last grant, for round-robin

  // Address phase on the shared bus.
  logic [AddrWidth-1:0] bus_haddr;
  logic [          2:0] bus_hburst;
  logic                 bus_hmastlock;
  logic [          3:0] bus_hprot;
  logic                 bus_hnonsec;
  logic [          2:0] bus_hsize;
  logic [          1:0] bus_htrans;
  logic                 bus_hwrite;
  logic [AddrWidth-1:0] dec_haddr;
  logic [ NoSlaves-1:0] dec_sel;
  logic                 dec_err;

  // Default slave.
  typedef enum logic [1:0] {
    DfltOk,
    DfltErr1,
    DfltErr2
  } dflt_state_e;
  dflt_state_e dflt_q, dflt_d;

  // ------------------------------------------------------------ master responses
  always_comb begin
    for (int unsigned m = 0; m < NoMasters; m++) begin
      if (data_valid_q && data_mst_q[m]) begin
        s_hready_o[m] = bus_hready;
        s_hresp_o[m]  = bus_hresp;
      end else begin
        s_hready_o[m] = !held_valid_q[m];
        s_hresp_o[m]  = 1'b0;
      end
      s_hrdata_o[m] = bus_hrdata;
      live_valid[m] = s_hready_o[m] && s_htrans_i[m][1];
      req[m]        = held_valid_q[m] || live_valid[m];
    end
  end

  // ------------------------------------------------------------ arbiter
  always_comb begin
    lock_cont = 1'b0;
    for (int unsigned m = 0; m < NoMasters; m++) begin
      if (lock_valid_q && lock_mst_q[m] &&
          (s_htrans_i[m] == Seq || s_htrans_i[m] == Busy || s_hmastlock_i[m])) begin
        lock_cont = 1'b1;
      end
    end
  end

  always_comb begin
    logic [31:0] best;
    logic        found;
    int unsigned last;
    grant = '0;
    found = 1'b0;
    best  = '1;
    last  = 0;
    if (lock_cont) begin
      grant = lock_mst_q;
    end else if (FixedPriority) begin
      for (int unsigned m = 0; m < NoMasters; m++) begin
        if (req[m] && Priority[m] < best) begin
          best  = Priority[m];
          grant = '0;
          grant[m] = 1'b1;
        end
      end
    end else begin
      for (int unsigned m = 0; m < NoMasters; m++) begin
        if (last_q[m]) last = m;
      end
      for (int unsigned k = 1; k <= NoMasters; k++) begin
        for (int unsigned m = 0; m < NoMasters; m++) begin
          if (!found && req[m] && m == (last + k) % NoMasters) begin
            grant[m] = 1'b1;
            found    = 1'b1;
          end
        end
      end
    end
  end

  assign granted = |grant;

  // Address phase of the granted master: its held transfer, or its live signals.
  always_comb begin
    bus_haddr     = '0;
    bus_hburst    = '0;
    bus_hmastlock = 1'b0;
    bus_hprot     = '0;
    bus_hnonsec   = 1'b0;
    bus_hsize     = '0;
    bus_htrans    = Idle;
    bus_hwrite    = 1'b0;
    for (int unsigned m = 0; m < NoMasters; m++) begin
      if (grant[m] && held_valid_q[m]) begin
        bus_haddr     = held_haddr_q[m];
        bus_hburst    = held_hburst_q[m];
        bus_hmastlock = held_hmastlock_q[m];
        bus_hprot     = held_hprot_q[m];
        bus_hnonsec   = held_hnonsec_q[m];
        bus_hsize     = held_hsize_q[m];
        bus_htrans    = held_htrans_q[m];
        bus_hwrite    = held_hwrite_q[m];
      end else if (grant[m]) begin
        bus_haddr     = s_haddr_i[m];
        bus_hburst    = s_hburst_i[m];
        bus_hmastlock = s_hmastlock_i[m];
        bus_hprot     = s_hprot_i[m];
        bus_hnonsec   = s_hnonsec_i[m];
        bus_hsize     = s_hsize_i[m];
        bus_htrans    = s_htrans_i[m];
        bus_hwrite    = s_hwrite_i[m];
      end
    end
  end

  // ------------------------------------------------------------ decode
  bag_addr_decode #(
      .AddrWidth    (AddrWidth),
      .NoMasters    (NoMasters),
      .NoSlaves     (NoSlaves),
      .NoRules      (NoRules),
      .NoAliases    (NoAliases),
      .RuleBase     (RuleBase),
      .RuleMask     (RuleMask),
      .RuleSlave    (RuleSlave),
      .RuleNoRead   (RuleNoRead),
      .RuleNoWrite  (RuleNoWrite),
      .RuleSecure   (RuleSecure),
      .Connect      (Connect),
      .MstNoRead    (MstNoRead),
      .MstNoWrite   (MstNoWrite),
      .MissToDefault(MissToDefault),
      .DefaultSlave (DefaultSlave),
      .AliasMaster  (AliasMaster),
      .AliasBase    (AliasBase),
      .AliasMask    (AliasMask),
      .AliasTo      (AliasTo)
  ) u_decode (
      .mst_i     (grant),
      .addr_i    (bus_haddr),
      .write_i   (bus_hwrite),
      .nonsec_i  (bus_hnonsec),
      .alias_en_i(alias_en_i),
      .addr_o    (dec_haddr),
      .sel_o     (dec_sel),
      .err_o     (dec_err)
  );

  for (genvar s = 0; s < NoSlaves; s++) begin : gen_slave
    assign m_hsel_o[s]    = dec_sel[s];
    assign m_haddr_o[s]   = dec_haddr;
    assign m_hburst_o[s]  = bus_hburst;
    assign m_hprot_o[s]   = bus_hprot;
    assign m_hnonsec_o[s] = bus_hnonsec;
    assign m_hsize_o[s]   = bus_hsize;
    assign m_htrans_o[s]  = bus_htrans;
    assign m_hwdata_o[s]  = bus_hwdata;
    assign m_hwrite_o[s]  = bus_hwrite;
    assign m_hready_o[s]  = bus_hready;
  end

  // ------------------------------------------------------------ data phase mux
  always_comb begin
    bus_hwdata = '0;
    for (int unsigned m = 0; m < NoMasters; m++) begin
      if (data_mst_q[m]) bus_hwdata = s_hwdata_i[m];
    end
    bus_hready = 1'b1;
    bus_hresp  = 1'b0;
    bus_hrdata = '0;
    if (data_dflt_q) begin
      bus_hready = dflt_q != DfltErr1;
      bus_hresp  = dflt_q != DfltOk;
    end else begin
      for (int unsigned s = 0; s < NoSlaves; s++) begin
        if (data_sel_q[s]) begin
          bus_hready = m_hreadyout_i[s];
          bus_hresp  = m_hresp_i[s];
          bus_hrdata = m_hrdata_i[s];
        end
      end
    end
  end

  // ------------------------------------------------------------ default slave
  always_comb begin
    dflt_d = dflt_q;
    case (dflt_q)
      DfltErr1: dflt_d = DfltErr2;
      DfltOk, DfltErr2: begin
        if (bus_hready) begin
          dflt_d = (dec_err && bus_htrans[1]) ? DfltErr1 : DfltOk;
        end
      end
      default: dflt_d = DfltOk;
    endcase
  end

  // ------------------------------------------------------------ registers
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      dflt_q       <= DfltOk;
      data_valid_q <= 1'b0;
      data_mst_q   <= '0;
      data_sel_q   <= '0;
      data_dflt_q  <= 1'b1;
      lock_valid_q <= 1'b0;
      lock_mst_q   <= '0;
      last_q       <= '0;
    end else begin
      dflt_q <= dflt_d;
      if (bus_hready) begin
        data_valid_q <= granted && bus_htrans != Idle;
        data_mst_q   <= grant;
        data_sel_q   <= dec_sel;
        data_dflt_q  <= dec_err;
        lock_valid_q <= granted && (bus_hmastlock || (bus_htrans != Idle && bus_hburst != '0));
        lock_mst_q   <= grant;
        if (granted) last_q <= grant;
      end
    end
  end

  for (genvar m = 0; m < NoMasters; m++) begin : gen_input_stage
    logic issued;
    assign issued = bus_hready && grant[m];

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        held_valid_q[m] <= 1'b0;
      end else if (held_valid_q[m]) begin
        if (issued) held_valid_q[m] <= 1'b0;
      end else if (live_valid[m] && !issued) begin
        held_valid_q[m] <= 1'b1;
      end
    end

    always_ff @(posedge clk_i) begin
      if (!held_valid_q[m] && live_valid[m] && !issued) begin
        held_haddr_q[m]     <= s_haddr_i[m];
        held_hburst_q[m]    <= s_hburst_i[m];
        held_hmastlock_q[m] <= s_hmastlock_i[m];
        held_hprot_q[m]     <= s_hprot_i[m];
        held_hnonsec_q[m]   <= s_hnonsec_i[m];
        held_hsize_q[m]     <= s_hsize_i[m];
        held_htrans_q[m]    <= s_htrans_i[m];
        held_hwrite_q[m]    <= s_hwrite_i[m];
      end
    end
  end

endmodule
