# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""CDC timing constraints: ``constraints/<project>_cdc.sdc`` and ``.xdc``.

Every clock-domain crossing gets a bound of ``min(source period, destination period)`` on its
asynchronous paths, so a gray-coded pointer or a handshake payload settles before either side
samples it, and the hold check a crossing cannot meet is removed. The SDC does that with
``set_max_delay`` and ``set_false_path -hold`` (PULP's own recipe for ``cdc_fifo_gray``); the XDC
with ``set_max_delay -datapath_only``, a Vivado extension that already drops the hold check.
That extension is why there are two files.

Which paths a crossing has depends on its template (the two tables below): the PULP ``axi_cdc``
FIFO, whose crossing nets are the ``async_data_*`` pins of its source half, or the in-house
4-phase handshake, whose crossing nets are the request and acknowledge through ``bag_sync`` and
the payload and response registers that are held stable while a handshake is open. Paths are
relative to the top wrapper. No tool reads these files here: nothing checks the names beyond
what the generated RTL contains, and the register patterns assume the ``_reg`` suffix.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from hdl_bag.gen.collateral import crossings
from hdl_bag.gen.hierarchy import plan
from hdl_bag.gen.netlist import Netlist
from hdl_bag.gen.render import GenerateError
from hdl_bag.ir.model import Clock, Spec

# FIFO crossings: the scope of the axi_cdc instance in the template's instance. The crossing
# nets are every async_data_master_* pin of its source half.
FIFO_SCOPE = {
    "bag_cdc": "i_cdc",
    "bag_axi2apb": "gen_cdc.i_cdc",
    "bag_axi2axil": "gen_cdc.i_cdc",
    "bag_axi2ahb": "gen_cdc.i_cdc",
}
FIFO_PINS = "i_axi_cdc_src/async_data_master_*"


@dataclass(frozen=True)
class Handshake:
    """A 4-phase crossing. ``sync`` holds u_req_sync and u_ack_sync. ``payload`` are the
    registers of the upstream clock that the downstream side reads, ``response`` those of the
    downstream clock that the upstream side reads; both are held stable while req or ack is
    high. Names are RTL registers, relative to the bridge instance."""

    sync: str
    payload: tuple[str, ...]
    response: tuple[str, ...]


_HS_PAYLOAD = tuple(f"u_apb/dst_{n}_o" for n in ("addr", "prot", "strb", "wdata", "write"))
HANDSHAKE = {
    "bag_ahb2apb": Handshake(
        "gen_cdc",
        ("req_addr_q", "req_count_q", "req_prot_q", "req_strb_q", "req_wdata_q", "req_write_q"),
        ("rsp_err_q", "rsp_rdata_q"),
    ),
    "bag_ahb2axi": Handshake(
        "gen_cdc",
        ("req_addr_q", "req_cache_q", "req_prot_q", "req_size_q", "req_wdata_q", "req_write_q"),
        ("u_axi/err_q", "u_axi/rdata_q"),
    ),
    "bag_apb2ahb": Handshake("u_apb/gen_cdc", _HS_PAYLOAD, ("err_q", "rdata_q")),
    "bag_apb2axi": Handshake("u_apb/gen_cdc", _HS_PAYLOAD, ("u_axi/err_q", "u_axi/rdata_q")),
}

SDC, XDC = "sdc", "xdc"


def _period(mhz: float) -> str:
    return f"{1000 / mhz:.3f}"


def _selector(kind: str, items: Iterable[str]) -> str:
    names = " ".join(items)
    return f"[get_{kind} {{{names}}}]" if " " in names else f"[get_{kind} {names}]"


def _max_delay(fmt: str, ns: str, where: str) -> list[str]:
    if fmt == XDC:
        return [f"set_max_delay -datapath_only {ns} {where}"]
    return [f"set_max_delay {ns} {where}", f"set_false_path -hold {where}"]


def _paths(template: str, path: str) -> tuple[list[str], list[str], list[str]]:
    """(through-pin selectors, payload cell patterns, response cell patterns) of one crossing
    instance at PATH."""
    if template in FIFO_SCOPE:
        return [f"{path}/{FIFO_SCOPE[template]}/{FIFO_PINS}"], [], []
    hs = HANDSHAKE.get(template)
    if hs is None:  # a new template with a synchronizer must say what its crossing is
        raise GenerateError(
            f"template {template} has a clock-domain crossing but no CDC constraint recipe "
            "(gen/constraints.py)"
        )
    pins = [f"{path}/{hs.sync}.{s}/d_i" for s in ("u_req_sync", "u_ack_sync")]
    payload, response = ([f"{path}/{r}_reg*" for r in group] for group in (hs.payload, hs.response))
    return pins, payload, response


def cdc_constraints(spec: Spec, netlist: Netlist, fmt: str, banner: str) -> str:
    """The constraint file of SPEC in format FMT (``sdc`` or ``xdc``), under BANNER."""
    where = plan(netlist)
    inst = {i.name: i for i in netlist.instances}
    first: dict[str, Clock] = {}
    for c in spec.clocks:  # the wrapper's clock of a domain is its first clock
        first.setdefault(c.domain, c)
    lines = [
        banner.rstrip("\n"),
        "",
        f"# {'Vivado' if fmt == XDC else 'Portable'} spelling. Paths are relative to "
        f"{where.top}, the module an SoC instantiates;",
        "# prefix them with that instance's hierarchical name. Nothing here is tool-verified.",
        "",
    ]
    for c in sorted(spec.clocks, key=lambda c: c.name):
        lines.append(f"create_clock -name {c.name} -period {_period(c.mhz)} [get_ports {c.name}]")
    found = crossings(spec)
    if not found:
        return "\n".join([*lines, "", "# No clock-domain crossings: nothing to constrain.", ""])
    for name, kind, a, b in found:
        i = inst[name]
        path = "/".join([*(f"u_{w}" for w in reversed(where.path_up(i.wrapper)[:-1])), i.rtl_name])
        ca, cb = first[a], first[b]
        ns = _period(max(ca.mhz, cb.mhz))
        lines += ["", f"# crossing {name}: {kind}, {a} ({ca.name}) -> {b} ({cb.name}), "
                      f"max delay {ns} ns"]  # fmt: skip
        pins, payload, response = _paths(i.template, path)
        for pin in pins:
            lines += _max_delay(fmt, ns, f"-through {_selector('pins', [pin])}")
        for cells, dst in ((payload, cb), (response, ca)):
            if cells:
                sel = f"-from {_selector('cells', cells)} -to [get_clocks {dst.name}]"
                lines += _max_delay(fmt, ns, sel)
    return "\n".join([*lines, ""])
