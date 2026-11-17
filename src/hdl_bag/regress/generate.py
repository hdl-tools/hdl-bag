# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Random configurations for ``bag regress`` (Phase 5 PR 5).

``random_config(seed)`` draws a config at workbook level with the DSL the gate corpora use.
It draws structure, not legality: every config goes through workbook -> ``bag check`` ->
resolve, and a check error on a drawn config is a bug in this generator (counted and fixed,
plan PR 5). One ``random.Random`` per seed, seeded from a string (hashed with SHA-512, so
PYTHONHASHSEED does not matter), makes a seed's config the same on every run (D97).

The shape: a root fabric with masters; child fabrics behind bridges whose template joins
the two protocols (the registry says which); now and then a feeder fabric with its own
master that bridges into an existing fabric; slaves on every fabric. AXI ports may differ
from their fabric in protocol (AXI4 / AXI4-Lite), width and domain (BAG inserts adapters);
AHB-Lite and APB ports match their fabric. Each fabric's targets share its address block,
densely or with gaps, and a bridge's window is its slot, so the fabrics behind it decode
inside it.
"""

import random
from dataclasses import dataclass, field
from pathlib import Path

from hdl_bag.checks import CheckResult, run_checks
from hdl_bag.reader import read_workbook
from hdl_bag.regress.dsl import (
    AHB_MASTER_NA,
    AHB_SLAVE_NA,
    APB3_NA,
    APB4_NA,
    AXI,
    AXI4_NA,
    AXIL_NA,
    ERROR,
    Cfg,
    hex_address,
)
from hdl_bag.template.builder import build_workbook
from hdl_bag.templates.registry import builtin_registry

PROTOCOLS = ("AXI4", "AXI4-Lite", "AHB-Lite", "APB4", "APB3")
WIDTHS = {"AXI4": (32, 64, 128), "AXI4-Lite": (32, 64), "AHB-Lite": (32, 64),
          "APB4": (8, 16, 32), "APB3": (8, 16, 32)}  # fmt: skip
LATENCY = ("NO_LATENCY", "CUT_SLV_AX", "CUT_MST_AX", "CUT_ALL_AX", "CUT_SLV_PORTS",
           "CUT_MST_PORTS", "CUT_ALL_PORTS")  # fmt: skip
SLICES = ("none", "cut", "multicut-2", "multicut-3")
KB = 0x400
# The File header goes into every generated file as written, so it is SystemVerilog comments.
HEADER = "// Random configuration drawn by bag regress.\n"


def _na(proto: str, master: bool) -> dict[str, str]:
    """The optional signals that do not exist on PROTO, for a master or a slave port."""
    if proto == "AHB-Lite":
        return AHB_MASTER_NA if master else AHB_SLAVE_NA
    return {"AXI4": AXI4_NA, "AXI4-Lite": AXIL_NA, "APB4": APB4_NA, "APB3": APB3_NA}[proto]


def _bridges() -> dict[tuple[str, str], list[str]]:
    """(upstream, downstream protocol) -> the bridge templates that join them."""
    out: dict[tuple[str, str], list[str]] = {}
    reg = builtin_registry()
    for name in reg.names("bridge"):
        m = reg.get(name)
        assert m is not None
        for up in m.upstream:
            for down in m.downstream:
                out.setdefault((up, down), []).append(name)
    return out


@dataclass
class _Fabric:
    name: str
    proto: str
    width: int
    domain: str
    masters: list[str] = field(default_factory=list)  # masters and bridge ends, port order
    targets: list[str] = field(default_factory=list)  # slaves and bridges, port order
    external: bool = False


@dataclass
class _Draw:
    """The state of one draw: names, domains and the fabrics as they grow."""

    rng: random.Random
    cfg: Cfg
    n_domains: int
    fabrics: dict[str, _Fabric] = field(default_factory=dict)
    port_proto: dict[str, str] = field(default_factory=dict)  # master / slave -> protocol
    slaves: set[str] = field(default_factory=set)
    downstream: dict[str, str] = field(default_factory=dict)  # bridge -> its fabric
    upstream: dict[str, str] = field(default_factory=dict)  # bridge -> the fabric it serves
    used_domains: set[int] = field(default_factory=set)
    block: dict[str, tuple[int, int]] = field(default_factory=dict)  # fabric -> (base, size)
    offset: dict[str, bool] = field(default_factory=dict)  # fabric -> sees window offsets
    pass_through: dict[str, bool] = field(default_factory=dict)  # bridge -> Y
    counts: dict[str, int] = field(default_factory=dict)
    dense: bool = False  # the map: regions and windows fill their slots, side by side

    def name(self, kind: str) -> str:
        k = self.counts.get(kind, 0)
        self.counts[kind] = k + 1
        return f"{kind}{k}"

    def domain(self, d: int | None = None) -> str:
        d = self.rng.randrange(self.n_domains) if d is None else d
        self.used_domains.add(d)
        return f"D{d}"


def random_config(seed: int) -> Cfg:
    """The config SEED draws: the same on every run and machine (D97)."""
    rng = random.Random(f"bag-regress:{seed}")
    name = f"rnd{seed}"
    style = rng.choice(("flat", "flat", "sv-interface"))
    cfg = Cfg(name, phase=5, style=style)
    cfg.data.title = f"BAG random config: seed {seed}"
    cfg.data.description = f"Drawn by bag regress (bag regress --seed {seed} redraws it)."
    cfg.data.project["Spec revision"] = f"seed {seed}"
    d = _Draw(rng, cfg, n_domains=rng.choice((1, 1, 2, 3)))
    d.dense = rng.random() < 0.5
    bridges = _bridges()

    root = _fabric(d, rng.choice(PROTOCOLS), d.domain(0))
    for _ in range(rng.choice((1, 1, 2, 3)) if root.proto not in ("APB4", "APB3") else 1):
        _master(d, root)
    frontier = [root]
    for _ in range(rng.choice((0, 1, 1, 2, 3, 4))):
        # Half the time behind the newest fabric, so chains of bridges grow deep.
        parent = frontier[-1] if rng.random() < 0.5 else rng.choice(frontier)
        options = [p for p in PROTOCOLS if bridges.get((parent.proto, p))]
        if not options:
            continue
        child = _fabric(d, rng.choice(options), d.domain())
        br = _bridge(d, parent, child, rng.choice(bridges[(parent.proto, child.proto)]))
        d.offset[child.name] = d.offset.get(parent.name, False) or not d.pass_through[br]
        frontier.append(child)
    if rng.random() < 0.2:
        _feeder(d, bridges)
    for fab in list(d.fabrics.values()):
        # A direct master beside a pass-through N bridge would see other addresses
        # (DEC-MIXED-TRANSLATION).
        if (fab.proto not in ("APB4", "APB3") and not d.offset.get(fab.name, False)
                and len(fab.masters) < 3 and rng.random() < 0.25):  # fmt: skip
            _master(d, fab)
        for _ in range(rng.choice((1, 1, 2, 3)) if fab.targets else rng.choice((1, 2, 3))):
            _slave(d, fab)
    _external(d)
    for fab in d.fabrics.values():
        _emit_fabric(d, fab)
    _map(d, root, 0x4000_0000, 0x4000_0000)
    for fab in list(d.fabrics.values()):
        if fab.name not in d.block:
            _map(d, fab, 0x8000_0000, 0x4000_0000)  # a feeder's own fabric, apart
    _decode(d)
    _hierarchy(d)
    for k in sorted(d.used_domains):
        cfg.domain(f"D{k}", f"clk_{k}", f"rst_{k}_n")
    return cfg


def _fabric(d: _Draw, proto: str, domain: str) -> _Fabric:
    fab = _Fabric(d.name("f"), proto, d.rng.choice(WIDTHS[proto]), domain)
    d.fabrics[fab.name] = fab
    return fab


def _emit_fabric(d: _Draw, fab: _Fabric) -> None:
    """The Fabrics row, once the fabric's ports are known (arbitration needs the count)."""
    rng = d.rng
    many = len(fab.masters) > 1
    arb = rng.choice(("round-robin", "fixed-priority")) if many else "single-master"
    topology = None
    if fab.proto in AXI:
        topology = rng.choice(("crossbar", "crossbar", "shared-bus"))
        if topology == "crossbar" or not many:
            arb = "round-robin"  # PULP's crossbars arbitrate round robin only
    d.cfg.fabric(fab.name, fab.proto, fab.width, 32, fab.domain, arb, topology=topology,
                 latency=rng.choice(LATENCY),
                 max_trans=(rng.choice((1, 2, 4)), rng.choice((1, 2, 4))),
                 midpoint=(rng.choice((0, 0, 2)), rng.choice(SLICES)),
                 template="bag_external" if fab.external else None)  # fmt: skip
    prios = list(range(len(fab.masters)))
    rng.shuffle(prios)
    order = list(range(len(fab.masters)))
    rng.shuffle(order)  # Port indices differ from row order
    for idx, m in zip(order, fab.masters, strict=True):
        d.cfg.link(m, fab.name, idx, prios[idx] if arb == "fixed-priority" else None)
    order = list(range(len(fab.targets)))
    rng.shuffle(order)
    for idx, t in zip(order, fab.targets, strict=True):
        d.cfg.link(fab.name, t, idx)


def _port_shape(d: _Draw, fab: _Fabric) -> tuple[str, int, str]:
    """Protocol, width and domain of a port on FAB: AXI ports vary (adapters), others match."""
    rng = d.rng
    if fab.proto not in AXI or rng.random() < 0.5:
        return fab.proto, fab.width, fab.domain
    proto = rng.choice(AXI)
    return proto, rng.choice(WIDTHS[proto]), d.domain()


def _master(d: _Draw, fab: _Fabric) -> str:
    name = d.name("m")
    proto, width, domain = _port_shape(d, fab)
    d.cfg.master(
        name, proto, width, 32, domain, id_width=d.rng.randint(1, 4) if proto == "AXI4" else None
    )
    d.port_proto[name] = proto
    fab.masters.append(name)
    _option(d, name, proto, master=True)
    return name


def _slave(d: _Draw, fab: _Fabric) -> str:
    name = d.name("s")
    proto, width, domain = _port_shape(d, fab)
    idw: int | str | None = None
    if proto == "AXI4":
        idw = d.rng.choice(("auto", "auto", 2, 8))
    d.cfg.slave(name, proto, width, 32, domain, id_width=idw)
    d.port_proto[name] = proto
    d.slaves.add(name)
    fab.targets.append(name)
    _option(d, name, proto, master=False)
    return name


def _bridge(d: _Draw, up: _Fabric, down: _Fabric, template: str,
            pass_through: bool | None = None) -> str:  # fmt: skip
    name = d.name("br")
    through = d.rng.random() < 0.5 if pass_through is None else pass_through
    d.pass_through[name] = through
    # The window base is set when the map is drawn; the row is patched then.
    d.cfg.bridge(name, 0, pass_through=through, template=template,
                 fifo_depth=d.rng.choice((0, 0, 1, 2)))  # fmt: skip
    up.targets.append(name)
    down.masters.append(name)
    d.downstream[name] = down.name
    d.upstream[name] = up.name
    _option(d, name, down.proto, master=True)
    return name


def _feeder(d: _Draw, bridges: dict[tuple[str, str], list[str]]) -> None:
    """A fabric with its own master that bridges into an existing fabric (a debug port).
    Its bridge passes system addresses through, so it feeds only a fabric that decodes them;
    its window is that fabric's whole block."""
    into = d.rng.choice(list(d.fabrics.values()))
    if into.proto in ("APB4", "APB3") or d.offset.get(into.name, False):
        return  # an APB fabric takes one master
    options = [p for p in PROTOCOLS if bridges.get((p, into.proto))]
    if not options:
        return
    feeder = _fabric(d, d.rng.choice(options), d.domain())
    _master(d, feeder)
    _bridge(d, feeder, into, d.rng.choice(bridges[(feeder.proto, into.proto)]), True)


def _external(d: _Draw) -> None:
    """Now and then one fabric whose targets are all slaves is user-supplied (D8, D41)."""
    if d.rng.random() >= 0.1:
        return
    leaves = [f for f in d.fabrics.values() if all(t in d.slaves for t in f.targets)]
    fab = d.rng.choice(leaves)
    fab.external = True
    d.cfg.user_port(fab.name, "irq_o", "output", 1, "expose")
    d.cfg.user_port(fab.name, "err_o", "output", 2, "open")
    d.cfg.user_port(fab.name, "cfg_i", "input", 4, d.rng.choice(("expose", "tie-0", "0x5")))
    d.cfg.user_port(fab.name, "clk_aux_i", "input", 1, f"clock {fab.domain}")


def _option(d: _Draw, port: str, proto: str, master: bool) -> None:
    rng = d.rng
    values: dict[str, str | int] = dict(_na(proto, master))
    if proto in AXI:
        values["fifo"] = rng.choice((0, 0, 0, 2))
        values["slice"] = rng.choice(("none", "none", *SLICES[1:]))
        for sig in ("qos", "region", "cache", "lock"):
            if sig not in values and rng.random() < 0.2:
                values[sig] = rng.choice(("tie-0", "tie-default"))
    elif proto == "APB4" and master and rng.random() < 0.3:
        values["pstrb"] = "tie-default"  # every write a full word
    elif proto in ("APB4", "APB3") and not master and rng.random() < 0.3:
        values["pslverr"] = "tie-0"  # a slave that never errors
    d.cfg.option(port, **values)


def _map(d: _Draw, fab: _Fabric, base: int, size: int) -> None:
    """Share FAB's block [BASE, BASE + SIZE) among its targets: a dense map gives them
    adjacent slots that their regions fill; a sparse one spreads them with gaps and shrinks
    them. The fabric behind a bridge maps inside the bridge's window."""
    rng = d.rng
    d.block[fab.name] = (base, size)
    n = len(fab.targets)
    slots = 1 << (n - 1).bit_length()
    if not d.dense:
        slots *= rng.choice((2, 4))
    slot = size // slots
    picks = list(range(n)) if d.dense else sorted(rng.sample(range(slots), n))
    for t, k in zip(fab.targets, picks, strict=True):
        at = base + k * slot
        if t in d.slaves:
            _regions(d, t, at, slot, _carries_prot(d, fab))
        elif d.downstream[t] in d.block:  # a feeder's bridge: the whole fabric it feeds
            into_base, into_size = d.block[d.downstream[t]]
            d.cfg.region(t, "win", into_base, into_size)
            _set_window(d, t, into_base)
        else:
            win = _bridge_window(d, t, at, slot)
            _map(d, d.fabrics[d.downstream[t]], at, win)


def _bridge_window(d: _Draw, bridge: str, at: int, slot: int) -> int:
    win = slot if d.dense else min(slot, max(16 * KB, slot >> d.rng.choice((0, 2, 4))))
    d.cfg.region(bridge, "win", at, win)
    _set_window(d, bridge, at)
    return win


def _set_window(d: _Draw, bridge: str, base: int) -> None:
    row = next(r for r in d.cfg.data.bridges if r[0] == bridge)
    row[2] = hex_address(base)


def _carries_prot(d: _Draw, fab: _Fabric) -> bool:
    """Whether every master path into FAB carries PROT, so a secure-only region on it is
    legal (APB3 has no PPROT: SEC-PROT-TIED)."""
    if fab.proto == "APB3":
        return False
    for m in fab.masters:
        if m in d.upstream:
            if not _carries_prot(d, d.fabrics[d.upstream[m]]):
                return False
        elif d.port_proto[m] == "APB3":
            return False
    return True


def _regions(d: _Draw, slave: str, at: int, slot: int, may_secure: bool) -> None:
    rng = d.rng
    size = slot if d.dense else min(slot, max(4 * KB, slot >> rng.choice((2, 4, 8))))
    count = 2 if size >= 8 * KB and rng.random() < 0.3 else 1
    part = size // count
    for k in range(count):
        access = rng.choice(("RW", "RW", "RW", "RO", "WO"))
        secure = may_secure and rng.random() < 0.1
        d.cfg.region(slave, f"r{k}", at + k * part, part, access=access, secure=secure)


def _decode(d: _Draw) -> None:
    """An Address_Decode row per master port, Access_Rules now and then, and at most one
    alias per master (D72), always on or on a pin, onto a slave the master may reach."""
    rng = d.rng
    base = {r[0]: int(str(r[2]).replace("_", ""), 16) for r in d.cfg.data.memory_map}
    pins: list[str] = []
    for fab in d.fabrics.values():
        local = [t for t in fab.targets if t in d.slaves]
        for m in fab.masters:
            default = None
            miss = ERROR[fab.proto]
            if fab.proto in AXI:
                miss = rng.choice(("DECERR", "SLVERR", "default-slave" if local else "DECERR"))
                if miss == "default-slave":
                    default = rng.choice(local)
            alias, onto = None, None
            if m not in d.downstream and local and rng.random() < 0.25:
                onto = rng.choice(local)
                pin = rng.choice(("always", f"remap_{m}_i"))
                if pin != "always":
                    pins.append(pin)
                alias = (0x0000_0000, 4 * KB, base[onto], pin)  # below every block
            d.cfg.decode(m, miss, default=default, alias=alias)
            for t in fab.targets:
                if t != onto and rng.random() < 0.08:
                    d.cfg.access(m, t, rng.choice(("RO", "WO", "-")))
    d.cfg.data.wrapper["Extra top-level inputs"] = ", ".join(pins) or None


def _hierarchy(d: _Draw) -> None:
    """Sub-wrappers (a chain or two branches, depth up to 3) and placements, now and then."""
    rng = d.rng
    if rng.random() >= 0.3:
        return
    top = f"{d.cfg.name}_top"
    subs: list[str] = []
    for k in range(rng.randint(1, 3)):
        parent = rng.choice([top, *subs]) if subs else top
        name = f"ss{k}"
        d.cfg.sub(name, parent)
        subs.append(name)
    for fab in d.fabrics.values():
        if rng.random() < 0.6:
            d.cfg.place(fab.name, rng.choice(subs))
    for bridge in d.downstream:
        if rng.random() < 0.4:
            d.cfg.place(bridge, rng.choice([top, *subs]))


def check(cfg: Cfg, where: Path) -> tuple[Path, CheckResult]:
    """Write CFG's workbook (and its File header) into WHERE and run every check on it."""
    where.mkdir(parents=True, exist_ok=True)
    path = where / f"{cfg.name}.xlsx"
    build_workbook(data=cfg.data).save(path)
    (where / "header.txt").write_text(HEADER, encoding="utf-8", newline="\n")
    return path, run_checks(read_workbook(path), where)
