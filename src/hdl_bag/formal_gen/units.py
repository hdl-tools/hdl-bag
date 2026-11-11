# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Route properties (Phase 5 PR 3): each fabric and bridge on its own, against the IR.

Each unit (an in-house fabric that decodes, or a bridge) has a route job of its own, whose
top is the unit's harness, with free inputs. A unit harness holds the unit exactly as the
generated top instantiates it (the same instance template, rendered with every bus as a
harness port), an environment and the properties:

- the environment: each domain starts in reset and is released for good; masters and
  slaves drive nothing in reset and at the edge after it (AXI A3.1.2); on each bus, what
  the environment drives follows the bus's protocol, assumed by the same checkers the
  protocol job uses (bag_faxi4, bag_fahb, ZipCPU's AXI4-Lite and APB files), which assert
  what the unit drives;
- a fabric's slave ports (D69 part 2, D71): every request that appears there is one a
  master made and the reference (formal_gen.decode) sends to that port, exactly: a
  scoreboard per master holds its accepted requests as the reference has them (aliased
  address, write, non-secure bit, slave port), and each slave-port request must match its
  master's oldest entry for that port (AXI4: the ID prefix names the master; ID-less
  fabrics with several masters: the one entry that matches, D78). A miss or a denial
  reaches no slave port, and a master gets a response only for a request a slave took or
  the reference answers itself (_delivered);
- fixed-priority arbitration (D70): when two masters of a shared bus or AHB-Lite bus
  start a request at the same edge, both bound for a slave, the one with the lower
  Topology priority value is accepted first;
- a bridge's window and offset (D69 part 3): with single-beat accesses one at a time
  upstream, every downstream request lies in the beat of the upstream address translated
  by the fabrics' windows (route_model.window), and none appears without one.
"""

from dataclasses import dataclass

from hdl_bag.formal_gen.decode import decoded_fabrics, masters_of, port_accesses, reference
from hdl_bag.gen.netlist import Bus, Instance, Netlist
from hdl_bag.gen.render import GenerateError, lit
from hdl_bag.gen.wrapper import InstanceContext, Scope, render_instance_ctx, reset_net
from hdl_bag.ir.model import Fabric, Spec
from hdl_bag.naming import flat_signal_name
from hdl_bag.protocols import PROTO_TOKEN, TIE_WRITE_STROBE, Protocol, towards_slave
from hdl_bag.tests_gen.route_model import window

# Bounded depth of a route job whose unit spans clock domains, in solver steps (free clocks:
# about two per edge), plus ROUTE_DEPTH_PER_HALVING for each halving of a bridge's data width
# after the first (each narrow transfer is another APB access). Measured: mixed_cdc_chain's
# br_per (a round trip through axi_cdc, no conversion) reached its last cover at step 21;
# bridges_pulp's br_per (64 to 32 bits) passed with every cover at 24 and timed out at 28;
# mixed_narrow_apb's br_c8 (64 to 8 bits) reached its last cover at step 33.
ROUTE_DEPTH = 24
ROUTE_DEPTH_PER_HALVING = 6
# A unit in one clock domain runs on one clock, for ROUTE_CYCLES edges plus
# ROUTE_CYCLES_PER_HALVING per halving of a bridge's data width (an AXI4-Lite to 8- or 16-bit
# APB bridge's read response came at edge 8). A crossbar gets no more: at 12 edges
# mixed_cdc_chain's reached only edge 10 in 20 minutes.
ROUTE_CYCLES = 8
ROUTE_CYCLES_PER_HALVING = 2
# Scoreboard entries per master and request channel (D71): an AXI4 master keeps at most 4
# transactions open (bag_faxi4's tables, D66), and the environment keeps the others within
# the same bound; an AHB-Lite master has one address phase beyond its data phase; the APB
# mux passes its one master's setup phase on in the same cycle.
SCOREBOARD = {Protocol.AXI4: 4, Protocol.AXI4_LITE: 4, Protocol.AHB_LITE: 2, Protocol.APB3: 1,
              Protocol.APB4: 1}  # fmt: skip
BRIDGES = ("bag_ahb2apb", "bag_ahb2axi", "bag_apb2ahb", "bag_apb2axi", "bag_axi2ahb",
           "bag_axi2apb", "bag_axi2axil")  # fmt: skip


@dataclass(frozen=True)
class Attach:
    """One bus of a unit, and whether the unit is the slave on it."""

    bus: Bus
    unit_is_slave: bool


@dataclass(frozen=True)
class ZipChecker:
    """A ZipCPU checker instance in a unit harness (its module is a parameterised copy)."""

    vendor: str
    module: str
    params: tuple[tuple[str, int], ...]


@dataclass
class Unit:
    """One unit harness: its module text, its ports (name, width, is input) and checkers."""

    inst: Instance
    module: str
    text: str
    ports: list[tuple[str, int, bool]]
    zipcpu: list[ZipChecker]
    own: set[str]
    clocks: tuple[str, ...]  # one per clock domain the unit touches


def route_depth(unit: Unit) -> tuple[bool, int]:
    """UNIT's job: whether it needs free clocks (it spans domains), and its depth."""
    up, down = unit.inst.up, unit.inst.down
    ratio = up.data_width // down.data_width if up is not None and down is not None else 1
    halvings = max(ratio, 1).bit_length() - 1  # data widths are powers of two
    if len(unit.clocks) == 1:
        return False, ROUTE_CYCLES + ROUTE_CYCLES_PER_HALVING * halvings
    return True, ROUTE_DEPTH + ROUTE_DEPTH_PER_HALVING * max(halvings - 1, 0)


def id_prefixes(fab: Fabric) -> list[int]:
    """The ID prefix each master of AXI4 fabric FAB (by Port index) carries at its slave
    ports: its index, except on a fixed-priority shared bus, whose mux takes the masters in
    priority order (bag_axi_shared_bus's rank: how many masters have a lower Priority value)
    and so prefixes that rank. Taken from the IR's priorities, not the package's."""
    order = masters_of(fab)
    if fab.topology != "shared-bus" or fab.arbitration != "fixed-priority":
        return list(range(len(order)))
    prio = {p.port: p.priority or 0 for p in fab.master_ports}
    return [sum(prio[k] < prio[m] for k in order) for m in order]


def units(netlist: Netlist) -> list[Instance]:
    """The instances the route job checks: in-house fabrics that decode, and bridges."""
    bridges = [i for i in netlist.instances if i.kind == "bridge" and i.template in BRIDGES]
    return [*decoded_fabrics(netlist), *bridges]


def _attachments(inst: Instance) -> list[Attach]:
    if inst.kind == "fabric":
        return [*(Attach(b, True) for b in inst.masters), *(Attach(b, False) for b in inst.slaves)]
    assert inst.up is not None and inst.down is not None
    return [Attach(inst.up, True), Attach(inst.down, False)]


def _name(spec: Spec, bus: Bus, sig: str) -> str:
    prefix = {p.port: p.prefix for p in spec.wrapper.ports}.get(bus.name)
    return flat_signal_name(spec.wrapper.signal_pattern, bus.name, PROTO_TOKEN[bus.protocol],
                            sig, prefix)  # fmt: skip


def _width(bus: Bus, sig: str) -> int:
    return Scope.width(bus, sig)


class _Harness:
    """Builds one unit harness's body."""

    def __init__(self, spec: Spec, inst: Instance, ctx: InstanceContext) -> None:
        self.spec, self.inst, self.ctx = spec, inst, ctx
        self.body: list[str] = []
        self.zipcpu: list[ZipChecker] = []
        self.own: set[str] = set()

    def v(self, bus: Bus, sig: str) -> str:
        """What BUS carries on SIG in the harness: its port, a tie, or the spec's default."""
        w = _width(bus, sig)
        tie = bus.ties.get(sig)
        if sig in bus.signals and tie is None:
            return _name(self.spec, bus, sig)
        if tie == TIE_WRITE_STROBE or (tie is None and sig == "pstrb"):
            return f"({self.v(bus, 'pwrite')} ? {w}'h{(1 << w) - 1:X} : {w}'h0)"
        return lit(w, tie or 0)

    # ------------------------------------------------------------------ environment
    def domains(self) -> dict[str, str]:
        """Each domain of the unit -> its clock."""
        out = {}
        for a in _attachments(self.inst):
            out[a.bus.domain] = next(c.name for c in self.spec.clocks if c.domain == a.bus.domain)
        if self.inst.fabric is not None:
            d = self.inst.fabric.domain
            out[d] = next(c.name for c in self.spec.clocks if c.domain == d)
        return out

    def reset_env(self) -> None:
        self.body.append("  // Each domain starts in reset and, once released, stays out of reset.")
        for d, clk in sorted(self.domains().items()):
            rst = reset_net(d)
            self.body += [
                f"  logic bag_first_{d} = 1'b1, bag_out_{d} = 1'b0;",
                f"  always_ff @(posedge {clk}) begin bag_first_{d} <= 1'b0; "
                f"bag_out_{d} <= {rst}; end",
                f"  always_comb if (bag_first_{d}) assume (!{rst});",
                f"  always_comb if (bag_out_{d}) assume ({rst});",
                f"  wire bag_live_{d} = {rst} && bag_out_{d};  // out of reset since the last edge",
                f"  wire bag_in_reset_{d} = !{rst};",
            ]

    def pins_static(self, pins: list[str]) -> None:
        """Alias-enable pins change only in reset (Phase 4: they are quasi-static), as in the
        reset and protocol jobs: here, never once the fabric's domain is out of reset."""
        if not pins or self.inst.fabric is None:
            return
        d = self.inst.fabric.domain
        clk = self.domains()[d]
        for pin in pins:
            self.body += [
                f"  logic bag_pin_was_{pin};",
                f"  always_ff @(posedge {clk}) bag_pin_was_{pin} <= {pin};",
                f"  always_comb if (bag_out_{d}) assume ({pin} == bag_pin_was_{pin});",
            ]

    def quiet(self, a: Attach) -> None:
        """What the environment drives is idle in reset and at the edge after it."""
        bus, d = a.bus, a.bus.domain
        idle: list[tuple[str, int]] = []
        p = bus.protocol
        if p in (Protocol.AXI4, Protocol.AXI4_LITE):
            idle = [(s, 0) for s in (("awvalid", "wvalid", "arvalid") if a.unit_is_slave
                                     else ("bvalid", "rvalid"))]  # fmt: skip
        elif p is Protocol.AHB_LITE:
            idle = [("htrans", 0)] if a.unit_is_slave else [("hreadyout", 1), ("hresp", 0)]
        else:
            idle = [("psel", 0)] if a.unit_is_slave else []
        for sig, val in idle:
            if sig in bus.signals and sig not in bus.ties:
                self.body.append(f"  always_comb if (!bag_live_{d}) assume ({self.v(bus, sig)} == "
                                 f"{lit(_width(bus, sig), val)});")  # fmt: skip

    def checker(self, a: Attach) -> None:
        """The protocol checker on A: it assumes what the environment drives."""
        bus, d = a.bus, a.bus.domain
        clk = self.domains()[d]
        p = bus.protocol
        if p is Protocol.AXI4:
            self.own.add("bag_faxi4.sv")
            from hdl_bag.formal_gen import _FAXI4 as sigs

            idw = max(bus.id_width, 1)
            conns = [("clk_i", clk), ("rst_ni", f"!bag_in_reset_{d}")]
            # An ID-less bus gives the checker a one-bit ID of 0 (only the IDs: *valid ends
            # in "id" too).
            ids = ("awid", "bid", "arid", "rid")
            conns += [(s, "1'b0" if s in ids and not bus.id_width else self.v(bus, s))
                      for s in sigs]  # fmt: skip
            conns += [(f"{e}_o", "") for e in ("multi_write", "w_first", "same_id", "full")]
            head = (f"  bag_faxi4 #(.DesignIsSlave({int(a.unit_is_slave)}), "
                    f".AddrWidth({bus.addr_width}), .DataWidth({bus.data_width}), "
                    f".IdWidth({idw})) u_chk_{bus.name} (")  # fmt: skip
        elif p is Protocol.AHB_LITE:
            self.own.add("bag_fahb.sv")
            from hdl_bag.formal_gen import _FAHB

            hsel = self.v(bus, "hsel") if "hsel" in bus.signals else "1'b1"
            out = self.v(bus, "hreadyout") if "hreadyout" in bus.signals else self.v(bus, "hready")
            conns = [("clk_i", clk), ("rst_ni", f"!bag_in_reset_{d}"), ("hsel", hsel)]
            conns += [(s, self.v(bus, s)) for s in _FAHB]
            conns += [("hreadyout", out), ("hresp", self.v(bus, "hresp")),
                      ("hrdata", self.v(bus, "hrdata"))]  # fmt: skip
            conns += [(f"{e}_o", "") for e in ("write_done", "read_done", "wait", "error", "seq")]
            head = (f"  bag_fahb #(.DesignIsSlave({int(a.unit_is_slave)}), "
                    f".AddrWidth({bus.addr_width}), .DataWidth({bus.data_width})) "
                    f"u_chk_{bus.name} (")  # fmt: skip
            if a.unit_is_slave and "hreadyout" in bus.signals:
                # The unit is the only slave on this bus: the bus's HREADY is its HREADYOUT.
                self.body.append(f"  always_comb assume ({self.v(bus, 'hready')} == {out});")
        else:
            from hdl_bag.formal_gen import _APB, _AXIL, _CHECKER, zipcpu_params

            family = "axil" if p is Protocol.AXI4_LITE else "apb"
            vendor = _CHECKER[(family, not a.unit_is_slave)]
            module = f"bag_{self.spec.project.name}_{vendor}_{self.inst.name}_{bus.name}"
            self.zipcpu.append(
                ZipChecker(vendor, module, zipcpu_params(bus, family, self.slave_can(a)))
            )
            if family == "axil":
                conns = [("i_clk", clk), ("i_axi_reset_n", f"!bag_in_reset_{d}")]
                conns += [(f"i_axi_{s}", self.v(bus, s)) for s in _AXIL]
                conns += [(f"f_axi_{c}", "") for c in ("rd_outstanding", "wr_outstanding",
                                                       "awr_outstanding")]  # fmt: skip
            else:
                conns = [("PCLK", clk), ("PRESETn", f"!bag_in_reset_{d}")]
                psel, pen = self.v(bus, "psel"), self.v(bus, "penable")
                for port, s in _APB.items():
                    val = self.v(bus, s)
                    if port == "PREADY":
                        val = f"({val} && {psel})"
                    elif port == "PSLVERR" and vendor == "fapb_master":
                        val = f"({val} && {psel} && {pen} && {self.v(bus, 'pready')})"
                    conns.append((port, val))
            head = f"  {module} u_chk_{bus.name} ("
        lines = [head, *(f"      .{c}({x})," for c, x in conns)]
        lines[-1] = lines[-1].rstrip(",")
        self.body += [*lines, "  );"]

    def slave_can(self, a: Attach) -> frozenset[str] | None:
        """On a fabric's slave port, what the fabric reference sends there (as the protocol
        job's checkers have it); None elsewhere (the environment may send anything)."""
        fab = self.inst.fabric
        if fab is None or a.unit_is_slave:
            return None
        return port_accesses(self.spec, fab)[self.inst.slaves.index(a.bus)]

    # ------------------------------------------------------------------ requests
    def requests(self, bus: Bus) -> list[tuple[str, str, str, str, str | None]]:
        """The request events on BUS: (channel, valid, address, write, non-secure bit or
        None when the bus does not carry it)."""
        v = self.v

        def prot(sig: str) -> str | None:
            return f"{v(bus, sig)}[1]" if sig in bus.signals and sig not in bus.ties else None

        p = bus.protocol
        if p in (Protocol.AXI4, Protocol.AXI4_LITE):
            return [
                ("aw", v(bus, "awvalid"), v(bus, "awaddr"), "1'b1", prot("awprot")),
                ("ar", v(bus, "arvalid"), v(bus, "araddr"), "1'b0", prot("arprot")),
            ]
        if p is Protocol.AHB_LITE:
            sel = f"{v(bus, 'hsel')} && " if "hsel" in bus.signals else ""
            ns = (
                v(bus, "hnonsec")
                if "hnonsec" in bus.signals and "hnonsec" not in bus.ties
                else None
            )
            return [("a", f"({sel}{v(bus, 'htrans')}[1] && {v(bus, 'hready')})",
                     v(bus, "haddr"), v(bus, "hwrite"), ns)]  # fmt: skip
        return [("p", f"({v(bus, 'psel')} && !{v(bus, 'penable')})", v(bus, "paddr"),
                 v(bus, "pwrite"), prot("pprot"))]  # fmt: skip

    def fabric_side(self, bus: Bus, k: int, sig: str) -> str:
        """SIG of fabric slave port K as the fabric drives it, before it is fitted to the
        slave's own width: a slave sees only its low address bits (D29), and the decode needs
        the whole address."""
        port = _name(self.spec, bus, sig)
        net = self.ctx.used.get((bus.name, sig), "")
        if not net or net == port:  # the fabric drives the port itself, at full width
            return self.v(bus, sig)
        if not net.isidentifier():
            raise GenerateError(f"route harness: fabric {self.inst.name} drives {port} as "
                                f"{net!r}, not a net it can index (template changed?)")  # fmt: skip
        return f"{net}[{k}]"

    # ------------------------------------------------------------------ properties
    def master_nonsec(self, bus: Bus, ch: str) -> str:
        """The non-secure bit the fabric sees with a request of master BUS on channel CH: the
        port's, its tie, or the spec's default (0) when the bus does not carry it."""
        sig = {"aw": "awprot", "ar": "arprot", "a": "hnonsec", "p": "pprot"}[ch]
        tie = bus.ties.get(sig)
        if sig in bus.signals and tie is None:
            return self.v(bus, sig) if ch == "a" else f"{self.v(bus, sig)}[1]"
        bit = 0 if tie is None else 1 if tie == -1 else tie & 1 if ch == "a" else (tie >> 1) & 1
        return f"1'b{bit}"

    def master_request(self, bus: Bus, ch: str) -> tuple[str, str, str, str, str]:
        """Master BUS's request on CH: (presented, passable, accepted, address, write).
        Presented is the environment's alone (VALID, HTRANS, the setup phase), so an
        assumption on it never constrains the fabric. Passable is what a fabric may already
        pass on to a slave: an AXI request presented and not yet accepted, but an AHB-Lite
        address phase only once accepted (HREADY high) and an APB setup phase at once."""
        v = self.v
        if ch in ("aw", "ar"):
            valid = v(bus, f"{ch}valid")
            return (valid, valid, f"({valid} && {v(bus, ch + 'ready')})", v(bus, f"{ch}addr"),
                    "1'b1" if ch == "aw" else "1'b0")  # fmt: skip
        if ch == "a":
            sel = f"{v(bus, 'hsel')} && " if "hsel" in bus.signals else ""
            shown = f"({sel}{v(bus, 'htrans')}[1])"
            acc = f"({sel}{v(bus, 'htrans')}[1] && {v(bus, 'hready')})"
            return shown, acc, acc, v(bus, "haddr"), v(bus, "hwrite")
        setup = f"({v(bus, 'psel')} && !{v(bus, 'penable')})"
        return setup, setup, setup, v(bus, "paddr"), v(bus, "pwrite")

    def fabric_edge(self) -> None:
        """D69 part 2 and D71: every request at a slave port is one a master sent there.

        Each master's accepted requests wait in a scoreboard (one per master and request
        channel, in order, at most SCOREBOARD entries) as the reference has them: the address
        after the master's aliases, read or write, the non-secure bit, and the slave port the
        reference sends it to. A miss or a denial is never entered, so it reaches no slave.
        A request at slave port k must equal its master's oldest entry for k (address, write
        and non-secure bit exactly; order holds per master and slave, not across slaves),
        or, with none, the request the master presents now (an AXI fabric may pass a request
        on before its own handshake; an AHB-Lite address phase counts once accepted). The
        slave's handshake takes the entry. The master is the
        ID's top bits on an AXI4 fabric with several masters, the one master on APB or with
        one master; on an ID-less fabric with several masters, the master whose entry
        matches: the environment never has two masters' requests to one slave with the same
        address, write and non-secure bit open at once (D78), so at most one does.

        The environment keeps each master within its scoreboard (an assumption on what it
        presents). On AHB-Lite that bound is the fabric's to keep instead (_buffered): a
        master presents its next address phase whatever the fabric has taken, and only the
        fabric's HREADY lets it in, so an assumption there would prune the fabric."""
        fab = self.inst.fabric
        assert fab is not None
        name, d = fab.name, fab.domain
        self.body += reference(self.spec, fab, name)
        nm, ns = len(fab.master_ports), len(fab.slave_ports)
        # The netlist's slots are by Port index, as the reference numbers masters and slaves.
        assert len(self.inst.masters) == nm and len(self.inst.slaves) == ns
        can = port_accesses(self.spec, fab)
        clk = self.domains()[d]
        aw = fab.addr_width
        tw = max(ns.bit_length(), 1)
        q = SCOREBOARD[Protocol(fab.protocol)]
        cw = (q + 1).bit_length()  # counts 0 to q, with a bit to spare
        chans = {"aw": ("aw", "ar"), "a": ("a",), "p": ("p",)}
        proto = {Protocol.AXI4: "aw", Protocol.AXI4_LITE: "aw", Protocol.AHB_LITE: "a"}
        channels = chans[proto.get(Protocol(fab.protocol), "p")]
        ids = fab.id_width
        by_id = Protocol(fab.protocol) is Protocol.AXI4 and nm > 1 and ids is not None
        # The scoreboards' state and what each master presents now.
        for ch in channels:
            for m, bus in enumerate(self.inst.masters):
                sb = f"sb_{ch}{m}"
                shown, req, acc, addr, write = self.master_request(bus, ch)
                self.body += [
                    f"  // {sb}: {masters_of(fab)[m]}'s "
                    f"{ch.upper()} requests, accepted and not yet at a slave (D71)",
                    f"  logic [{q - 1}:0][{aw - 1}:0] {sb}_addr = '0;",
                    f"  logic [{q - 1}:0] {sb}_wr = '0, {sb}_ns = '0;",
                    f"  logic [{q - 1}:0][{tw - 1}:0] {sb}_tgt = '0;",
                    f"  logic [{cw - 1}:0] {sb}_n = '0;",
                    f"  logic {sb}_early = 1'b0;  // taken by a slave before its own handshake",
                    f"  wire {sb}_acc = {acc};",
                    f"  wire [{aw - 1}:0] {sb}_a = {name}_aliased({m}, {aw}'({addr}));",
                    f"  wire {sb}_w = {write};",
                    f"  wire {sb}_s = {self.master_nonsec(bus, ch)};",
                    f"  wire [{tw - 1}:0] {sb}_t = {tw}'({name}_target({m}, {sb}_a, {sb}_w, "
                    f"{sb}_s));",
                    f"  wire {sb}_p = {req} && !{sb}_early && {sb}_t != {tw}'({ns});  "
                    "// presented now, bound for a slave",
                    f"  wire {sb}_ask = {shown} && {sb}_t != {tw}'({ns});  "
                    "// the master asks for a slave (the environment's alone)",
                ]
                if ch != "a":
                    self.body.append(f"  always_comb if ({sb}_n == {cw}'({q})) assume "
                                     f"(!({shown}));  // the environment keeps within the "
                                     "scoreboard")  # fmt: skip
                    continue
                label = f"{name}_{bus.name}_a_buffered"
                self.body += [
                    f"  always @(posedge {clk}) if (bag_live_{d} && {acc}) {label}: "
                    f"assert ({sb}_n != {cw}'({q}));  // the fabric takes no more",
                    f"  always @(posedge {clk}) cover_{label}: cover (bag_live_{d} && {acc});",
                ]
        # Each slave port: the master entry its request must match, and the match it takes.
        takes: dict[tuple[str, int], list[tuple[int, str, str]]] = {}
        for k, bus in enumerate(self.inst.slaves):
            for ch, valid, _, write, nsb in self.requests(bus):
                addr = self.fabric_side(bus, k, {"aw": "awaddr", "ar": "araddr", "a": "haddr",
                                                 "p": "paddr"}[ch])  # fmt: skip
                hs = f"{valid} && {self.v(bus, ch + 'ready')}" if ch in ("aw", "ar") else valid
                which: dict[int, str] = {m: "1'b1" for m in range(nm)}
                if by_id and ids is not None:
                    idv = self.fabric_side(bus, k, f"{ch}id")
                    top = f"int'({idv}[{ids.slave_side - 1}:{ids.master_side}])"
                    which = {m: f"({top} == {r})" for m, r in enumerate(id_prefixes(fab))}
                slave = _Slave(f"{aw}'({addr})", write if ch in ("a", "p") else None, nsb)
                matches = []
                for m in range(nm):
                    sb = f"sb_{ch}{m}"
                    hd = f"{sb}_hd{k}"

                    head = _same(f"{sb}_addr[{hd}]", f"{sb}_wr[{hd}]", f"{sb}_ns[{hd}]", slave)
                    now = _same(f"{sb}_a", f"{sb}_w", f"{sb}_s", slave)
                    self.body += [
                        f"  logic {hd}_has;  // {sb}'s oldest entry for slave port {k}",
                        f"  logic [{cw - 1}:0] {hd};",
                        "  always_comb begin",
                        f"    {hd}_has = 1'b0;",
                        f"    {hd} = '0;",
                        f"    for (int j = {q - 1}; j >= 0; j--)",
                        f"      if ({cw}'(j) < {sb}_n && {sb}_tgt[j] == {tw}'({k})) begin "
                        f"{hd}_has = 1'b1; {hd} = {cw}'(j); end",
                        "  end",
                        f"  wire {sb}_m{k} = {which[m]} && ({hd}_has ? "
                        f"({head}) : ({sb}_p && {sb}_t == {tw}'({k}) && {now}));",
                        f"  wire {sb}_take{k} = ({hs}) && {sb}_m{k};",
                    ]
                    matches.append(f"{sb}_m{k}")
                    takes.setdefault((ch, m), []).append((k, f"{sb}_take{k}", f"{hd}_has"))
                label = f"{name}_{bus.name}_{ch}_routed"
                # Sampled at the edge, as the slave samples it: between edges a free clock lets
                # an input the checkers constrain only at edges take any value.
                bclk = self.domains()[bus.domain]
                self.body.append(f"  always @(posedge {bclk}) if (bag_live_{d} && {valid}) "
                                 f"{label}: assert ({' || '.join(matches)});")  # fmt: skip
                # A channel the reference never uses: the check says nothing arrives there,
                # and no cover can be reached.
                kind = {"aw": "write", "ar": "read"}.get(ch)
                if kind is not None and kind not in can[k]:
                    self.body.append(f"  // {label}: no cover, the IR sends {bus.name} no {kind}")
                    continue
                self.body.append(f"  always @(posedge {bclk}) cover_{label}: "
                                 f"cover (bag_live_{d} && {valid});")  # fmt: skip
        # Each scoreboard: the entries slaves took leave, then the request accepted now joins
        # (unless a slave took it already, or it is a miss or a denial).
        for (ch, m), tk in takes.items():
            sb = f"sb_{ch}{m}"
            gone = " || ".join(f"({t} && {h} && sb_{ch}{m}_hd{k} == {cw}'(j))" for k, t, h in tk)
            took = " || ".join(f"({t} && !{h})" for k, t, h in tk)
            self.body += [
                f"  always_ff @(posedge {clk}) begin",
                f"    if (!bag_live_{d}) begin {sb}_n <= '0; {sb}_early <= 1'b0; end",
                f"    else begin : {sb}_next",
                f"      logic [{q - 1}:0][{aw - 1}:0] a;",
                f"      logic [{q - 1}:0] w, s;",
                f"      logic [{q - 1}:0][{tw - 1}:0] t;",
                f"      logic [{cw - 1}:0] c;",
                "      logic took;",
                "      a = '0; w = '0; s = '0; t = '0; c = '0;",
                f"      took = {took};",
                f"      for (int j = 0; j < {q}; j++)",
                f"        if ({cw}'(j) < {sb}_n && !({gone})) begin",
                f"          a[c] = {sb}_addr[j]; w[c] = {sb}_wr[j]; s[c] = {sb}_ns[j]; "
                f"t[c] = {sb}_tgt[j]; c = c + 1'b1;",
                "        end",
                f"      if ({sb}_acc && !{sb}_early && !took && {sb}_t != {tw}'({ns})) begin",
                f"        a[c] = {sb}_a; w[c] = {sb}_w; s[c] = {sb}_s; t[c] = {sb}_t; "
                "c = c + 1'b1;",
                "      end",
                f"      {sb}_addr <= a; {sb}_wr <= w; {sb}_ns <= s; {sb}_tgt <= t; {sb}_n <= c;",
                f"      {sb}_early <= {sb}_early ? !{sb}_acc : (took && !{sb}_acc);",
                "    end",
                "  end",
            ]
        self.delivered(takes, tw, ns)
        if by_id or nm == 1:
            return
        # D78: no two masters' requests to one slave with the same address, write and
        # non-secure bit are open at once, so a slave-port request matches one master.
        for ch in channels:
            for m2 in range(nm):
                for m in range(nm):
                    if m == m2:
                        continue
                    a, b = f"sb_{ch}{m}", f"sb_{ch}{m2}"
                    eq = (f"{a}_tgt[j] == {b}_t && {a}_addr[j] == {b}_a && {a}_wr[j] == {b}_w && "
                          f"{a}_ns[j] == {b}_s")  # fmt: skip
                    now = (f"{a}_ask && {a}_t == {b}_t && {a}_a == {b}_a && {a}_w == {b}_w && "
                           f"{a}_s == {b}_s")  # fmt: skip
                    clash = f"sb_{ch}_clash_{m}_{m2}"
                    first = now if m < m2 else "1'b0"  # each pair presented together once
                    self.body += [
                        f"  logic {clash};",
                        "  always_comb begin",
                        f"    {clash} = {first};",
                        f"    for (int j = 0; j < {q}; j++)",
                        f"      if ({cw}'(j) < {a}_n && {eq}) {clash} = 1'b1;",
                        "  end",
                        f"  always_comb if ({b}_ask) assume (!{clash});",
                    ]
            # The assumption leaves contention: two masters ask for one slave at once.
            a, b = f"sb_{ch}0", f"sb_{ch}1"
            both = f"{a}_ask && {b}_ask && {a}_t == {b}_t"
            self.body.append(f"  always @(posedge {clk}) cover_{name}_{ch}_contended: cover "
                             f"(bag_live_{d} && {both});")  # fmt: skip

    def master_response(self, bus: Bus, ch: str, pre: str) -> tuple[list[str], str]:
        """(declarations, event): a response to master BUS on CH's requests completes (AXI:
        the B handshake, or the R handshake of a read's last beat; AHB-Lite: a data phase
        ends; APB: the access phase ends). PRE names the harness's own nets."""
        v = self.v
        if ch == "aw":
            return [], f"({v(bus, 'bvalid')} && {v(bus, 'bready')})"
        if ch == "ar":
            last = f" && {v(bus, 'rlast')}" if "rlast" in bus.signals else ""
            return [], f"({v(bus, 'rvalid')} && {v(bus, 'rready')}{last})"
        if ch == "a":
            fab = self.inst.fabric
            assert fab is not None
            clk, d, hready = self.domains()[fab.domain], fab.domain, v(bus, "hready")
            return [
                f"  logic {pre}_dp = 1'b0;  // a data phase is open",
                f"  always_ff @(posedge {clk}) {pre}_dp <= bag_live_{d} && ({pre}_acc || "
                f"({pre}_dp && !{hready}));",
            ], f"({pre}_dp && {hready})"
        return [], f"({v(bus, 'psel')} && {v(bus, 'penable')} && {v(bus, 'pready')})"

    def delivered(self, takes: dict[tuple[str, int], list[tuple[int, str, str]]], tw: int,
                  ns: int) -> None:  # fmt: skip
        """D71: a master gets a response only for a request that reached a slave or that the
        reference answers itself (a miss or a denial). Each master and channel counts its
        requests taken by a slave and its misses, less its responses; a response with none
        counted means the fabric answered a request it should have passed on, which no
        slave-port check can see. No response comes in the cycle its own request is taken
        (a data phase follows its address phase, an AXI response its handshake), so only
        what was counted at earlier edges pays for it. A request dropped with no response
        at all is a liveness bug, outside a bounded safety check."""
        fab = self.inst.fabric
        assert fab is not None
        d = fab.domain
        clk = self.domains()[d]
        for (ch, m), tk in takes.items():
            bus = self.inst.masters[m]
            sb = f"sb_{ch}{m}"
            decls, resp = self.master_response(bus, ch, sb)
            gained = " + ".join([*(f"8'({t})" for _, t, _ in tk),
                                 f"8'({sb}_acc && {sb}_t == {tw}'({ns}))"])  # fmt: skip
            label = f"{fab.name}_{bus.name}_{ch}_delivered"
            self.body += [
                *decls,
                f"  logic [7:0] {sb}_due = '0;  // requests taken or missed, less responses",
                f"  wire [7:0] {sb}_gain = {gained};",
                f"  always_ff @(posedge {clk}) if (!bag_live_{d}) {sb}_due <= '0; "
                f"else {sb}_due <= {sb}_due + {sb}_gain - 8'({resp});",
                f"  always @(posedge {clk}) if (bag_live_{d} && {resp}) "
                f"{label}: assert ({sb}_due != '0);",
                f"  always @(posedge {clk}) cover_{label}: cover (bag_live_{d} && {resp});",
            ]

    def arbitration(self) -> None:
        """D70: of two masters that start at the same edge, the one with the lower priority
        value goes first. On a shared bus that is the first address handshake (the mux
        arbitrates there); the AHB-Lite bus takes an ungranted master's address phase into a
        buffer (HREADY stays high for it), so there the order shows as which data phase
        completes first."""
        fab = self.inst.fabric
        assert fab is not None
        if fab.arbitration != "fixed-priority":
            return
        if fab.topology not in ("shared-bus", "single-layer"):
            # FAB-ARBITRATION allows fixed priorities only on the shared and AHB-Lite buses.
            raise GenerateError(f"route harness: no D70 property for fixed-priority "
                                f"{fab.topology} fabric {fab.name}")  # fmt: skip
        name, d = fab.name, fab.domain
        clk = self.domains()[d]
        prio = {p.port: p.priority or 0 for p in fab.master_ports}
        order = masters_of(fab)
        ns = len(fab.slave_ports)
        axi = fab.protocol in (Protocol.AXI4, Protocol.AXI4_LITE)
        chans = ("aw", "ar") if axi else ("a",)
        for ch in chans:
            for m, bus in enumerate(self.inst.masters):
                _, valid, addr, write, nsb = next(e for e in self.requests(bus) if e[0] == ch)
                n = nsb or "1'b0"
                pre = f"bag_{ch}{m}"
                if not axi:
                    hready = self.v(bus, "hready")
                    self.body += [
                        f"  wire {pre}_acc = {self.v(bus, 'htrans')}[1] && {hready};",
                        f"  wire {pre}_nonseq = {self.v(bus, 'htrans')} == 2'b10;",
                        f"  logic {pre}_dp = 1'b0;  // a data phase is open",
                        f"  always_ff @(posedge {clk}) {pre}_dp <= bag_live_{d} && "
                        f"({pre}_acc || ({pre}_dp && !{hready}));",
                        f"  wire {pre}_start = {pre}_acc && {pre}_nonseq && !{pre}_dp;  "
                        "// a new transfer, nothing outstanding",
                        f"  wire {pre}_won = {pre}_dp && {hready};  // its data phase completes",
                    ]
                    continue
                self.body += [
                    f"  wire {pre}_req = {valid};",
                    f"  wire {pre}_acc = {valid} && {self.v(bus, ch + 'ready')};",
                    f"  wire {pre}_won = {pre}_acc;",
                    f"  logic {pre}_pend = 1'b0;  // requested and not accepted at the last edge",
                    f"  always_ff @(posedge {clk}) {pre}_pend <= bag_live_{d} && {pre}_req && "
                    f"!{pre}_acc;",
                    f"  wire {pre}_start = {pre}_req && !{pre}_pend;",
                ]
                if axi:
                    # Only a request bound for a slave reaches the arbiter (a miss or denial is
                    # answered by the master's guard), and only one with nothing outstanding on
                    # its channel, which the guard never holds back.
                    resp = "b" if ch == "aw" else "r"
                    ends = f"{self.v(bus, resp + 'valid')} && {self.v(bus, resp + 'ready')}"
                    if resp == "r" and "rlast" in bus.signals:
                        ends += f" && {self.v(bus, 'rlast')}"  # a read ends at its last beat
                    self.body += [
                        f"  logic [7:0] {pre}_out = '0;",
                        f"  always_ff @(posedge {clk}) if (!bag_live_{d}) {pre}_out <= '0; else "
                        f"{pre}_out <= {pre}_out + 8'({pre}_acc) - 8'({ends});",
                        f"  wire {pre}_cont = {pre}_out == '0 && {name}_target({m}, "
                        f"{name}_aliased({m}, {addr}), {write}, {n}) != {ns};",
                    ]
            if not axi:
                # The AHB-Lite bus keeps its grant through a wait state, a burst (SEQ, BUSY)
                # and a locked sequence, so priority decides only on an idle bus: at the last
                # edge every master was IDLE with no data phase open, and none locks now.
                idle = " && ".join(f"{self.v(b, 'htrans')} == 2'b00 && !bag_{ch}{k}_dp"
                                   for k, b in enumerate(self.inst.masters))  # fmt: skip
                lock = " || ".join(self.v(b, "hmastlock") for b in self.inst.masters
                                   if "hmastlock" in b.signals) or "1'b0"  # fmt: skip
                self.body += [
                    "  logic bag_bus_idle = 1'b0;",
                    f"  always_ff @(posedge {clk}) bag_bus_idle <= bag_live_{d} && {idle};",
                ]
                for k in range(len(self.inst.masters)):
                    self.body.append(f"  wire bag_{ch}{k}_cont = bag_bus_idle && !({lock});")
            for i, mi in enumerate(order):
                for j, mj in enumerate(order):
                    if i == j or not prio[mi] < prio[mj]:
                        continue
                    a, b = f"bag_{ch}{i}", f"bag_{ch}{j}"
                    race = f"bag_race_{ch}_{i}_{j}"
                    both = f"({a}_start && {b}_start && {a}_cont && {b}_cont)"
                    self.body += [
                        f"  logic {race} = 1'b0;  // {mi} and {mj} started together, {mi} "
                        "not yet in",
                        f"  always_ff @(posedge {clk}) {race} <= bag_live_{d} && "
                        f"({race} || {both}) && !{a}_won;",
                        f"  always @(posedge {clk}) if (bag_live_{d} && ({race} || {both})) "
                        f"{name}_{ch}_{mi}_before_{mj}: assert (!({b}_won && !{a}_won));",
                        f"  always @(posedge {clk}) cover_{name}_{ch}_{mi}_before_{mj}: "
                        # A race won at once (the bus grants in the cycle both start) is
                        # never registered: it counts here too.
                        f"cover (bag_live_{d} && ({race} || {both}) && {a}_won);",
                    ]

    def bridge_window(self) -> None:
        """D69 part 3: downstream requests lie in the translated upstream beat."""
        br = self.inst.bridge
        up, dn = self.inst.up, self.inst.down
        assert br is not None and up is not None and dn is not None
        fabrics = {f.name: f for f in self.spec.fabrics}
        w_up, w_dn = (
            window(self.spec, fabrics[br.upstream]),
            window(self.spec, fabrics[br.downstream]),
        )
        du, dd = up.domain, dn.domain
        cu, cd = self.domains()[du], self.domains()[dd]
        v = self.v
        beat = max(up.data_width, dn.data_width) // 8
        p = up.protocol
        # Upstream: single-beat accesses, one at a time.
        if p in (Protocol.AXI4, Protocol.AXI4_LITE):
            acc = [(f"{v(up, 'awvalid')} && {v(up, 'awready')}", v(up, "awaddr")),
                   (f"{v(up, 'arvalid')} && {v(up, 'arready')}", v(up, "araddr"))]  # fmt: skip
            done = (f"({v(up, 'bvalid')} && {v(up, 'bready')}) || "
                    f"({v(up, 'rvalid')} && {v(up, 'rready')})")  # fmt: skip
            start = f"({v(up, 'awvalid')} || {v(up, 'arvalid')})"
            if p is Protocol.AXI4:
                self.body += [f"  always_comb assume ({v(up, 'awlen')} == 8'd0);",
                              f"  always_comb assume ({v(up, 'arlen')} == 8'd0);"]  # fmt: skip
            self.body.append(f"  always_comb assume (!({v(up, 'awvalid')} && {v(up, 'arvalid')}));")
        elif p is Protocol.AHB_LITE:
            hsel = v(up, "hsel") if "hsel" in up.signals else "1'b1"
            phase = f"{hsel} && {v(up, 'htrans')}[1] && {v(up, 'hready')}"
            acc = [(phase, v(up, "haddr"))]
            done = f"bag_dp_{br.name} && {v(up, 'hready')}"
            start = f"{v(up, 'htrans')}[1]"
            self.body += [
                f"  always_comb assume ({v(up, 'htrans')} != 2'b01 && {v(up, 'htrans')} != 2'b11);",
                f"  always_comb assume ({v(up, 'hburst')} == 3'd0);",
                f"  logic bag_dp_{br.name} = 1'b0;  // a data phase is open",
                f"  always_ff @(posedge {cu}) bag_dp_{br.name} <= bag_live_{du} && "
                f"(({phase}) || (bag_dp_{br.name} && !{v(up, 'hready')}));",
            ]
        else:
            acc = [(f"{v(up, 'psel')} && !{v(up, 'penable')}", v(up, "paddr"))]
            done = f"{v(up, 'psel')} && {v(up, 'penable')} && {v(up, 'pready')}"
            start = v(up, "psel")
        uw = up.addr_width
        self.body += [
            "  logic bag_busy = 1'b0;  // an upstream access is open",
            f"  logic [{uw - 1}:0] bag_up_addr = '0;",
            f"  always_ff @(posedge {cu}) begin",
            f"    if (!bag_live_{du}) bag_busy <= 1'b0;",
            *(f"    else if ({c}) begin bag_busy <= 1'b1; bag_up_addr <= {a}; end" for c, a in acc),
            f"    else if ({done}) bag_busy <= 1'b0;",
            "  end",
        ]
        # A new upstream request only once the last has completed (AXI: VALID rises only
        # when idle; AHB: no address phase while a data phase is open; APB: one at a time).
        if p in (Protocol.AXI4, Protocol.AXI4_LITE):
            self.body.append("  logic bag_up_pend = 1'b0;")
            self.body.append(f"  always_ff @(posedge {cu}) bag_up_pend <= bag_live_{du} && "
                             f"{start} && "
                             f"!(({acc[0][0]}) || ({acc[1][0]}));")  # fmt: skip
            self.body.append(f"  always_comb if (bag_busy && !bag_up_pend) assume (!{start});")
        elif p is Protocol.AHB_LITE:
            self.body.append(f"  always_comb if (bag_busy) assume (!{v(up, 'htrans')}[1]);")
        # The upstream fabric sends the bridge only addresses in its regions there (formal
        # corpus: a 24-bit upstream and the route model's 32-bit translation differ outside
        # them), unless the bridge is a default slave, which takes misses anywhere.
        upfab = fabrics[br.upstream]
        if not any(d.default_slave == br.name for d in upfab.decode):
            spans = [(r.base - w_up, r.base + r.size - 1 - w_up) for r in self.spec.memory_map
                     if r.fabric == br.upstream and r.slave == br.name]  # fmt: skip
            assert spans and all(0 <= lo <= hi < 1 << uw for lo, hi in spans), (br.name, spans)
            if p in (Protocol.AXI4, Protocol.AXI4_LITE):
                shown = [(v(up, "awvalid"), v(up, "awaddr")), (v(up, "arvalid"), v(up, "araddr"))]
            elif p is Protocol.AHB_LITE:
                hsel = v(up, "hsel") if "hsel" in up.signals else "1'b1"
                shown = [(f"{hsel} && {v(up, 'htrans')}[1]", v(up, "haddr"))]
            else:
                shown = [(v(up, "psel"), v(up, "paddr"))]
            for c, a in shown:
                inside = " || ".join(f"({a} >= {lit(uw, lo)} && {a} <= {lit(uw, hi)})"
                                     for lo, hi in spans)  # fmt: skip
                self.body.append(f"  always_comb if ({c}) assume ({inside});")
        dw = dn.addr_width
        open_, win = "bag_busy", "bag_up_addr"
        if du == dd:
            # In one domain a bridge may forward the request it is presented before its own
            # handshake (a pass-through, or a fall-through request path): that request
            # counts too, at its own address (held while it waits). Across domains nothing
            # passes the CDC before the upstream handshake.
            if p in (Protocol.AXI4, Protocol.AXI4_LITE):
                req = [(v(up, "awvalid"), v(up, "awaddr")), (v(up, "arvalid"), v(up, "araddr"))]
            elif p is Protocol.AHB_LITE:
                sel = v(up, "hsel") if "hsel" in up.signals else "1'b1"
                req = [(f"{sel} && {v(up, 'htrans')}[1]", v(up, "haddr"))]
            else:
                req = [(v(up, "psel"), v(up, "paddr"))]
            now = req[-1][1]
            for c, a in reversed(req[:-1]):
                now = f"(({c}) ? {a} : {now})"
            self.body += [
                f"  wire bag_up_req = {' || '.join(f'({c})' for c, _ in req)};",
                f"  wire [{uw - 1}:0] bag_win_addr = bag_up_req ? {now} : bag_up_addr;",
            ]
            open_, win = "(bag_busy || bag_up_req)", "bag_win_addr"
        exp = f"{dw}'(65'({win}) + 65'({lit(64, w_up)}) - 65'({lit(64, w_dn)}))"
        mask = f"~{dw}'({beat - 1})"
        for ch, valid, addr, _, _ in self.requests(dn):
            label = f"{br.name}_{ch}_in_window"
            self.body += [
                f"  always @(posedge {cd}) if (bag_live_{du} && bag_live_{dd} && {valid}) "
                f"{label}: assert ({open_} && (({addr} ^ {exp}) & {mask}) == '0);",
                f"  always @(posedge {cd}) cover_{label}: "
                f"cover (bag_live_{du} && bag_live_{dd} && {valid});",
            ]


@dataclass(frozen=True)
class _Slave:
    """A request at a fabric slave port, as a scoreboard entry is compared with it: the
    address, and the write and non-secure bits where the channel and bus carry them."""

    addr: str
    write: str | None
    nonsec: str | None


def _same(addr: str, write: str, nonsec: str, slave: _Slave) -> str:
    """An entry (ADDR, WRITE, NONSEC) equals the request at SLAVE."""
    terms = [f"{addr} == {slave.addr}"]
    if slave.write is not None:
        terms.append(f"{write} == {slave.write}")
    if slave.nonsec is not None:
        terms.append(f"{nonsec} == {slave.nonsec}")
    return " && ".join(terms)


def _decls(ports: list[tuple[str, int, bool]]) -> str:
    """Module port declarations for (name, width, is input) PORTS."""
    return ",\n".join(
        f"    {'input' if i else 'output'} logic {f'[{w - 1}:0] ' if w > 1 else ''}{n}"
        for n, w, i in ports
    )


def render_unit(spec: Spec, inst: Instance) -> Unit:
    """INST alone in a harness module, with its environment and properties."""
    atts = _attachments(inst)
    scope = Scope(spec, "flat", {a.bus.name: a.bus for a in atts})
    text, ctx = render_instance_ctx(scope, inst)
    h = _Harness(spec, inst, ctx)
    ports: list[tuple[str, int, bool]] = []
    for d, clk in sorted(h.domains().items()):
        ports += [(clk, 1, True), (reset_net(d), 1, True)]
    ports += [(pin, 1, True) for pin in sorted(scope.inputs_used)]
    for a in atts:
        for sig in a.bus.signals:
            if sig in a.bus.ties:
                continue
            into_unit = towards_slave(sig, a.bus.is_slave) == a.unit_is_slave
            ports.append((_name(spec, a.bus, sig), _width(a.bus, sig), into_unit))
    ports = list({n: (n, w, i) for n, w, i in reversed(ports)}.values())[::-1]
    h.reset_env()
    h.pins_static(sorted(scope.inputs_used))
    for a in atts:
        h.quiet(a)
        h.checker(a)
    if inst.kind == "fabric":
        h.fabric_edge()
        h.arbitration()
    else:
        h.bridge_window()
    module = f"bag_{spec.project.name}_unit_{inst.name}"
    decl = _decls(ports)
    lines = [f"module {module} (", decl, ");", *scope.decls, *scope.assigns, text, *h.body,
             "endmodule", ""]  # fmt: skip
    clocks = tuple(sorted(set(h.domains().values())))
    return Unit(inst, module, "\n".join(lines), ports, h.zipcpu, h.own, clocks)


def render_route(spec: Spec, netlist: Netlist, hdr: str) -> list[tuple[str, Unit]]:
    """SPEC's route harnesses: per unit, its file's text (the unit's harness module, the
    job's top) and the unit. Each unit has a job of its own: the units share nothing, and a
    joint job's every solver step holds them all (bridges_pulp's eight units reached step 9
    of 16 in 20 minutes)."""
    out = []
    for inst in units(netlist):
        u = render_unit(spec, inst)
        text = "\n".join([hdr.rstrip("\n"), "",
                          f"// Route properties of {spec.project.name}'s {inst.kind} {inst.name} "
                          "on its own (see hdl_bag.formal_gen.units).", u.text])  # fmt: skip
        out.append((text, u))
    return out


def route_needs(netlist: Netlist) -> tuple[set[str], set[str]]:
    """The checker files the route jobs read: BAG's own (formal/) and the ZipCPU vendors."""
    from hdl_bag.formal_gen import _CHECKER

    own: set[str] = set()
    vendors: set[str] = set()
    for inst in units(netlist):
        for a in _attachments(inst):
            p = a.bus.protocol
            if p is Protocol.AXI4:
                own.add("bag_faxi4.sv")
            elif p is Protocol.AHB_LITE:
                own.add("bag_fahb.sv")
            else:
                family = "axil" if p is Protocol.AXI4_LITE else "apb"
                vendors.add(_CHECKER[(family, not a.unit_is_slave)])
    return own, vendors
