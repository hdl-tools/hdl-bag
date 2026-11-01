# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Workbook-level DSL for hand-written gate configs (tests/configs/p<N>/corpus.py).

A ``Cfg`` collects the same inputs a user types into the spec workbook. Each phase's
corpus builds its configs with it, then turns them into IR through the real path:
workbook -> ``bag check`` (zero findings) -> resolve.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hdl_bag.ir.model import Spec
from hdl_bag.template.data import PROJECT_DEFAULTS, WRAPPER_DEFAULTS, Cell, SpecData

# (protocol, topology) -> fabric template. TOPOLOGY gives each protocol's default.
TEMPLATE = {
    ("AHB-Lite", "single-layer"): "bag_ahb_lite_bus",
    ("APB4", "decoder-mux"): "bag_apb_mux",
    ("APB3", "decoder-mux"): "bag_apb_mux",
    ("AXI4", "crossbar"): "bag_axi_xbar",
    ("AXI4", "shared-bus"): "bag_axi_shared_bus",
    ("AXI4-Lite", "crossbar"): "bag_axil_xbar",
    ("AXI4-Lite", "shared-bus"): "bag_axil_shared_bus",
}
TOPOLOGY = {"AHB-Lite": "single-layer", "APB4": "decoder-mux", "APB3": "decoder-mux",
            "AXI4": "crossbar", "AXI4-Lite": "crossbar"}  # fmt: skip
ERROR = {"AHB-Lite": "HRESP_ERROR", "APB4": "PSLVERR", "APB3": "PSLVERR", "AXI4": "SLVERR",
         "AXI4-Lite": "SLVERR"}  # fmt: skip
AXI = ("AXI4", "AXI4-Lite")
OPTION_COLUMNS = ("fifo", "slice", "prot", "qos", "region", "cache", "lock", "pstrb", "pslverr")


@dataclass
class Cfg:
    name: str
    extra_inputs: tuple[str, ...] = ()
    style: str = "flat"
    signal_pattern: str = "{port}_{proto}_{signal}"
    subwrapper_naming: str = "{instance}_{proto}_{signal}"
    phase: int = 2
    data: SpecData = field(init=False)

    def __post_init__(self) -> None:
        self.data = SpecData(title=f"BAG Phase {self.phase} gate config: {self.name}",
                             description=f"Hand-written gate config (tests/configs/p{self.phase}/corpus.py).")  # fmt: skip
        self.data.project = {**PROJECT_DEFAULTS, "Project name": self.name, "Spec revision": f"p{self.phase}",
                             "Author": "BAG", "Output directory": f"out/{self.name}",
                             "Generate RTL": "Y", "Generate C header": "Y",
                             "Generate memory-map doc": "Y", "Generate cocotb unit tests": "Y",
                             "Generate formal (SymbiYosys)": "Y"}  # fmt: skip
        self.data.wrapper = {**WRAPPER_DEFAULTS, "Top module name": f"{self.name}_top",
                             "Parameter package": f"{self.name}_pkg", "File header": "header.txt",
                             "Port style": self.style, "Signal naming pattern": self.signal_pattern,
                             "Sub-wrapper port naming": self.subwrapper_naming,
                             "Extra top-level inputs": ", ".join(self.extra_inputs) or None}  # fmt: skip

    # ---- clocks and resets
    def domain(self, domain: str, clock: str, reset: str, polarity: str = "active-low",
               assertion: str = "async", deassertion: str = "sync") -> "Cfg":  # fmt: skip
        self.data.clocks.append([clock, 100, "top port", domain, reset, None])
        self.data.resets.append([reset, polarity, assertion, deassertion, domain, "top port", None])
        return self

    # ---- fabrics and ports
    def fabric(self, name: str, proto: str, dw: int, aw: int, domain: str,
               arb: str = "single-master", topology: str | None = None,
               latency: str = "NO_LATENCY", max_trans: tuple[int, int] = (4, 4),
               midpoint: tuple[int, str] = (2, "cut")) -> "Cfg":  # fmt: skip
        """A fabric row. AXI settings apply only where they are legal (D16): latency mode
        on crossbars, max transactions on AXI, the mid-point on shared buses."""
        topo = topology or TOPOLOGY[proto]
        axi = proto in AXI
        lat: Cell = latency if axi and topo == "crossbar" else "n/a"
        mst: Cell = max_trans[0] if axi else "n/a"
        slv: Cell = max_trans[1] if axi else "n/a"
        mid: tuple[Cell, Cell] = midpoint if topo == "shared-bus" else ("n/a", "n/a")
        backing = "PULP" if axi else "in-house template"
        self.data.fabrics.append([name, proto, topo, dw, aw, domain, arb, lat, mst, slv,
                                  TEMPLATE[(proto, topo)], backing, *mid, None])  # fmt: skip
        return self

    def master(self, name: str, proto: str, dw: int, aw: int, domain: str,
               prefix: str | None = None, exposed: bool = True, id_width: int | None = None,
               user: int = 0, outstanding: int = 1) -> "Cfg":  # fmt: skip
        """A master row; AXI4 masters need an ID width (default 4)."""
        idw = id_width if id_width is not None else (4 if proto == "AXI4" else None)
        self.data.masters.append([name, proto, dw, aw, idw, user, domain, outstanding,
                                  outstanding, None, None])  # fmt: skip
        return self._expose(name, prefix, exposed)

    def slave(self, name: str, proto: str, dw: int, aw: int, domain: str,
              prefix: str | None = None, id_width: int | str | None = None,
              user: int | None = None) -> "Cfg":  # fmt: skip
        """A slave row; AXI4 slaves default to ID width auto, AXI slaves to user width 0."""
        idw = id_width if id_width is not None else ("auto" if proto == "AXI4" else None)
        uw = user if user is not None else (0 if proto in AXI else None)
        self.data.slaves.append([name, proto, dw, aw, idw, uw, domain, ERROR[proto], 0, None,
                                 None])  # fmt: skip
        return self._expose(name, prefix, True)

    def bridge(self, name: str, window: int, pass_through: bool = True,
               template: str = "bag_ahb2apb", fifo_depth: int = 0) -> "Cfg":  # fmt: skip
        self.data.bridges.append([name, template, _hex(window), "Y" if pass_through else "N",
                                  fifo_depth, None])  # fmt: skip
        return self._expose(name, None, False)

    def _expose(self, port: str, prefix: str | None, exposed: bool) -> "Cfg":
        self.data.exposed_ports.append([port, "Y" if exposed else "N", prefix,
                                        None if exposed else "internal", None])  # fmt: skip
        return self

    # ---- connectivity and map
    def link(self, src: str, dst: str, index: int, priority: int | None = None) -> "Cfg":
        self.data.topology.append([src, dst, index, priority, None])
        return self

    def region(self, target: str, region: str, base: int, size: int, access: str = "RW",
               secure: bool = False) -> "Cfg":  # fmt: skip
        self.data.memory_map.append([target, region, _hex(base), _hex(size), access,
                                     "Y" if secure else "N", None])  # fmt: skip
        return self

    def decode(self, master: str, miss: str, default: str | None = None,
               alias: tuple[int, int, int, str] | None = None) -> "Cfg":  # fmt: skip
        base, size, to, enable = alias or (None, None, None, None)
        self.data.address_decode.append([
            master, miss, default, _hex(base) if base is not None else None,
            _hex(size) if size is not None else None, _hex(to) if to is not None else None,
            enable, None,
        ])  # fmt: skip
        return self

    def access(self, master: str, slave: str, access: str) -> "Cfg":
        self.data.access_rules.append([master, slave, access, None])
        return self

    def option(self, port: str, **values: Cell) -> "Cfg":
        """Port_Options row; unspecified optional signals default to drive / n/a."""
        row: dict[str, Cell] = {"fifo": 0, "slice": "none"}
        row.update(values)
        self.data.port_options.append([row.get(c, "drive") for c in OPTION_COLUMNS] + [None])
        self.data.port_options[-1].insert(0, port)
        return self

    # ---- hierarchy
    def sub(self, name: str, parent: str) -> "Cfg":
        self.data.sub_wrappers.append([name, parent, None])
        return self

    def place(self, instance: str, wrapper: str, rtl: str | None = None) -> "Cfg":
        self.data.placement.append([instance, wrapper, rtl, None])
        return self


def _hex(v: int) -> str:
    h = f"{v:08X}"
    return "0x" + "_".join(h[i : i + 4] for i in range(0, len(h), 4))


# Optional-signal values that do not exist on a protocol must be n/a.
APB4_NA = {"qos": "n/a", "region": "n/a", "cache": "n/a", "lock": "n/a"}
APB3_NA = {**APB4_NA, "prot": "n/a", "pstrb": "n/a"}
AHB_MASTER_NA = {"qos": "n/a", "region": "n/a", "cache": "n/a", "pstrb": "n/a", "pslverr": "n/a"}
AHB_SLAVE_NA = {**AHB_MASTER_NA, "lock": "n/a"}
AXI4_NA = {"pstrb": "n/a", "pslverr": "n/a"}
AXIL_NA = {**AXI4_NA, "qos": "n/a", "region": "n/a", "cache": "n/a", "lock": "n/a"}


HEADER = Path(__file__).parent / "p2" / "header.txt"


def resolve_cfg(cfg: Cfg, tmp: Path) -> tuple[Spec | None, list[str]]:
    """CFG through workbook -> bag check -> resolve in TMP: (IR, []) or (None, findings)."""
    import shutil

    from hdl_bag.checks import run_checks
    from hdl_bag.ir import resolve
    from hdl_bag.reader import read_workbook
    from hdl_bag.template.builder import build_workbook

    path = tmp / f"{cfg.name}.xlsx"
    build_workbook(data=cfg.data).save(path)
    shutil.copy(HEADER, tmp / "header.txt")
    result = run_checks(read_workbook(path), tmp)
    if result.findings:
        return None, [f"{f.severity}: {f.message}" for f in result.findings]
    return resolve(result), []


def write_all(built: dict[str, Callable[[], Cfg]], here: Path) -> None:
    """Build every config through workbook -> bag check -> resolve; write its IR to HERE."""
    import tempfile

    from hdl_bag.ir import dump

    with tempfile.TemporaryDirectory() as tmp:
        for name, make in built.items():
            spec, findings = resolve_cfg(make(), Path(tmp))
            if spec is None:
                print(f"{name}:")
                for f in findings:
                    print(f"  {f}")
                continue
            dump(spec, here / f"{name}.ir.yaml")
            print(f"{name}: ok")
