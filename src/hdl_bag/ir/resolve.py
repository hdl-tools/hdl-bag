# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Resolve: a checked spec (check Context without errors) -> IR.

Everything is derived from the same Context the checks used, so the IR can never
disagree with ``bag check``. Values are read from the Context's parse cache; resolve
never reports findings.
"""

from collections import defaultdict
from typing import Literal

from hdl_bag import __version__
from hdl_bag.checks import CheckResult
from hdl_bag.checks.basics import extra_inputs
from hdl_bag.checks.context import Context
from hdl_bag.checks.memory import access_rules
from hdl_bag.ir import model as ir
from hdl_bag.protocols import OPTIONAL_SIGNALS, signal_applies
from hdl_bag.reader.model import Cell
from hdl_bag.reader.values import parse_connection
from hdl_bag.template import SCHEMA_VERSION

_SIGNAL_FIELDS = {
    "PROT": "prot", "QOS": "qos", "REGION": "region", "CACHE": "cache",
    "LOCK / excl": "lock", "PSTRB": "pstrb", "PSLVERR": "pslverr",
}  # fmt: skip
_CUT_STAGES = {"cut": 1, "multicut-2": 2, "multicut-3": 3}


class ResolveError(Exception):
    """resolve() was called on a spec that has check errors."""


def _s(ctx: Context, cell: Cell | None) -> str | None:
    return ctx.text(cell) if cell is not None else None


def _req(value: object) -> str:
    assert isinstance(value, str), value
    return value


def _int(ctx: Context, cell: Cell, minimum: int = 0) -> int | None:
    v = ctx.integer(cell, minimum, required=False)
    return v if isinstance(v, int) else None


def resolve(result: CheckResult) -> ir.Spec:
    if result.errors:
        raise ResolveError(f"{len(result.errors)} check error(s); run bag check")
    ctx = result.context
    raw = ctx.raw
    p, w = raw.project, raw.wrapper
    rules = access_rules(ctx)

    project = ir.Project(
        name=_req(_s(ctx, p["Project name"])),
        revision=_s(ctx, p["Spec revision"]),
        author=_s(ctx, p["Author"]),
        sv_std=_req(_s(ctx, p["SystemVerilog standard"])),
        output_dir=_req(_s(ctx, p["Output directory"])),
        outputs=ir.Outputs(
            rtl=bool(ctx.yes_no(p["Generate RTL"])),
            c_header=bool(ctx.yes_no(p["Generate C header"])),
            doc=bool(ctx.yes_no(p["Generate memory-map doc"])),
            tests=bool(ctx.yes_no(p["Generate cocotb unit tests"])),
            formal=bool(ctx.yes_no(p["Generate formal (SymbiYosys)"])),
        ),
        lint_tools=tuple(t.strip() for t in _req(_s(ctx, p["Lint tools"])).split(",")),
        simulator=_req(_s(ctx, p["Simulator"])),
    )
    clocks = tuple(sorted(
        (ir.Clock(name=_req(ctx.text(r["Clock"])), mhz=r.value("Freq (MHz)"),  # type: ignore[arg-type]
                  source=ctx.text(r["Source"]), domain=_req(ctx.text(r["Domain"])),
                  reset=_req(ctx.text(r["Reset"])))
         for r in raw.clocks),
        key=lambda c: c.name,
    ))  # fmt: skip
    resets = tuple(sorted(
        (ir.Reset(name=_req(ctx.text(r["Reset"])), polarity=r.value("Polarity"),  # type: ignore[arg-type]
                  assertion=r.value("Assertion"), deassertion=r.value("Deassertion"),  # type: ignore[arg-type]
                  source=ctx.text(r["Source"]), domain=_req(ctx.text(r["Domain"])))
         for r in raw.resets),
        key=lambda r: r.name,
    ))  # fmt: skip

    regions = _regions(ctx)
    fabric_names = sorted(n for n, k in ctx.kinds.items() if k == "fabric")
    fabrics = tuple(_fabric(ctx, name, regions, rules) for name in fabric_names)

    masters = []
    for name in sorted(n for n, k in ctx.kinds.items() if k == "master"):
        r = ctx.defs[name]
        masters.append(ir.Master(
            name=name, protocol=_req(ctx.text(r["Protocol"])), fabric=ctx.master_fabric[name],
            data_width=_req_int(ctx, r["Data width (bits)"], 1), addr_width=_req_int(ctx, r["Addr width (bits)"], 1),
            id_width=_int(ctx, r["ID width (bits)"], 1), user_width=_int(ctx, r["User width (bits)"]) or 0,
            domain=_req(ctx.text(r["Clock domain"])),
            max_outstanding_rd=_int(ctx, r["Max outstanding rd"], 1),
            max_outstanding_wr=_int(ctx, r["Max outstanding wr"], 1),
        ))  # fmt: skip
    slaves = []
    for name in sorted(n for n, k in ctx.kinds.items() if k == "slave"):
        r = ctx.defs[name]
        fabric = ctx.slave_fabric[name]
        id_raw = ctx.integer(r["ID width (bits)"], 1, required=False, tokens=("auto",))
        ids = ctx.fabric_id_widths(fabric)
        wait = ctx.integer(r["Wait states"], 0, required=False, tokens=("variable",))
        slaves.append(ir.Slave(
            name=name, protocol=_req(ctx.text(r["Protocol"])), fabric=fabric,
            data_width=_req_int(ctx, r["Data width (bits)"], 1), addr_width=_req_int(ctx, r["Addr width (bits)"], 1),
            id_width=(ids[1] if ids else None) if id_raw == "auto" else (id_raw if isinstance(id_raw, int) else None),
            id_width_auto=id_raw == "auto", user_width=_int(ctx, r["User width (bits)"]) or 0,
            domain=_req(ctx.text(r["Clock domain"])), error_response=_req(ctx.text(r["Error response"])),
            wait_states=wait,  # type: ignore[arg-type]
        ))  # fmt: skip

    bridges = []
    for name in sorted(n for n, k in ctx.kinds.items() if k == "bridge"):
        r = ctx.defs[name]
        up, down = ctx.slave_fabric[name], ctx.master_fabric[name]
        uf, df = ctx.fabric_row(up), ctx.fabric_row(down)
        assert uf is not None and df is not None
        bridges.append(ir.Bridge(
            name=name, template=_req(ctx.text(r["Template"])), upstream=up, downstream=down,
            window_base=_req_hex(ctx.hexval(r["Window base (hex)"])),
            pass_through=bool(ctx.yes_no(r["Pass-through address"])),
            fifo_depth=_int(ctx, r["FIFO depth"]) or 0,
            upstream_protocol=_req(ctx.text(uf["Protocol"])), downstream_protocol=_req(ctx.text(df["Protocol"])),
            upstream_width=_req_int(ctx, uf["Data width (bits)"], 1),
            downstream_width=_req_int(ctx, df["Data width (bits)"], 1),
            cdc=ctx.text(uf["Clock domain"]) != ctx.text(df["Clock domain"]),
        ))  # fmt: skip

    top = _req(_s(ctx, w["Top module name"]))
    wrappers = tuple(sorted(
        (ir.SubWrapper(name=_req(ctx.text(r["Sub-wrapper"])), parent=_req(ctx.text(r["Parent"])))
         for r in raw.sub_wrappers[1:]),
        key=lambda s: s.name,
    ))  # fmt: skip
    overrides = {
        _req(ctx.text(r["Instance"])): (
            _req(ctx.text(r["Sub-wrapper"])),
            ctx.text(r["RTL instance name"]),
        )
        for r in raw.placement
    }
    adapters, placement = _adapters_and_placement(ctx, top, overrides)

    return ir.Spec(
        schema=SCHEMA_VERSION,
        project=project,
        clocks=clocks,
        resets=resets,
        fabrics=fabrics,
        masters=tuple(masters),
        slaves=tuple(slaves),
        bridges=tuple(bridges),
        adapters=adapters,
        port_options=_port_options(ctx),
        user_ports=_user_ports(ctx),
        memory_map=tuple(sorted(regions, key=lambda r: (r.base, r.fabric, r.slave, r.region))),
        hierarchy=ir.Hierarchy(top=top, wrappers=wrappers, placement=placement),
        wrapper=ir.Wrapper(
            top=top,
            port_style=_s(ctx, w["Port style"]),  # type: ignore[arg-type]
            signal_pattern=_req(_s(ctx, w["Signal naming pattern"])),
            clock_reset_ports=_req(_s(ctx, w["Clock/reset ports"])),
            package=_req(_s(ctx, w["Parameter package"])),
            file_header=_s(ctx, w["File header"]),
            extra_inputs=tuple(extra_inputs(ctx)),
            adapter_placement=_s(ctx, w["Adapter placement"]),  # type: ignore[arg-type]
            adapter_naming=_req(_s(ctx, w["Adapter instance naming"])),
            subwrapper_port_naming=_req(_s(ctx, w["Sub-wrapper port naming"])),
            ports=tuple(
                sorted(
                    (
                        ir.ExposedPort(
                            port=_req(ctx.text(r["Port"])),
                            exposed=bool(ctx.yes_no(r["Exposed at top"])),
                            prefix=ctx.text(r["Top-level prefix"]),
                        )
                        for r in raw.exposed_ports
                    ),
                    key=lambda e: e.port,
                )
            ),
        ),
        provenance=ir.Provenance(bag_version=__version__, source=raw.source, sha256=raw.sha256),
    )


def _req_int(ctx: Context, cell: Cell, minimum: int) -> int:
    v = ctx.integer(cell, minimum)
    assert isinstance(v, int), (cell, v)
    return v


def _req_hex(v: int | None) -> int:
    assert v is not None
    return v


def _regions(ctx: Context) -> list[ir.Region]:
    out = []
    for r in ctx.raw.memory_map:
        target = _req(ctx.text(r["Slave / bridge"]))
        out.append(ir.Region(
            slave=target, region=_req(ctx.text(r["Region"])),
            base=_req_hex(ctx.hexval(r["Base (hex)"])), size=_req_hex(ctx.hexval(r["Size (hex)"])),
            access=r.value("Access"),  # type: ignore[arg-type]
            secure=bool(ctx.yes_no(r["Secure only"])), fabric=ctx.slave_fabric[target],
        ))  # fmt: skip
    return out


def _fabric(
    ctx: Context, name: str, regions: list[ir.Region], rules: dict[tuple[str, str], str]
) -> ir.Fabric:
    r = ctx.fabric_row(name)
    assert r is not None
    topology = _req(ctx.text(r["Topology"]))
    arb = _req(ctx.text(r["Arbitration"]))
    links = [lk for lk in ctx.links if lk.fabric == name]
    mports = sorted((lk for lk in links if lk.side == "master"), key=lambda lk: lk.index or 0)
    sports = sorted((lk for lk in links if lk.side == "slave"), key=lambda lk: lk.index or 0)
    slave_index = {lk.end: lk.index or 0 for lk in sports}
    masters = [lk.end for lk in mports]
    ids = ctx.fabric_id_widths(name)

    def opt_int(col: str, minimum: int) -> int | None:
        v = r[col].value
        return None if v in (None, "n/a") else _int(ctx, r[col], minimum)

    midpoint = None
    if topology == "shared-bus":
        midpoint = ir.Midpoint(fifo_depth=_req_int(ctx, r["Mid-point FIFO depth"], 0),
                               slice=r.value("Mid-point register slice"))  # type: ignore[arg-type]  # fmt: skip
    decode = []
    for row in ctx.raw.address_decode:
        m = ctx.text(row["Master"])
        if m not in masters:
            continue
        aliases: tuple[ir.Alias, ...] = ()
        if row["Alias base (hex)"].value is not None:
            aliases = (ir.Alias(
                base=_req_hex(ctx.hexval(row["Alias base (hex)"])),
                size=_req_hex(ctx.hexval(row["Alias size (hex)"])),
                to=_req_hex(ctx.hexval(row["Alias maps to (hex)"])),
                enable=_req(ctx.text(row["Alias enable"])),
            ),)  # fmt: skip
        decode.append(ir.Decode(master=_req(m), miss=row.value("Miss response"),  # type: ignore[arg-type]
                                default_slave=ctx.text(row["Default slave"]), aliases=aliases))  # fmt: skip
    return ir.Fabric(
        name=name,
        protocol=_req(ctx.text(r["Protocol"])),
        topology=topology,  # type: ignore[arg-type]
        data_width=_req_int(ctx, r["Data width (bits)"], 1),
        addr_width=_req_int(ctx, r["Addr width (bits)"], 1),
        domain=_req(ctx.text(r["Clock domain"])),
        arbitration=arb,  # type: ignore[arg-type]
        latency_mode=None
        if r["Latency mode"].value in (None, "n/a")
        else ctx.text(r["Latency mode"]),
        max_mst_trans=opt_int("Max mst trans", 1),
        max_slv_trans=opt_int("Max slv trans", 1),
        midpoint=midpoint,
        template=_req(ctx.text(r["Backing template"])),
        backing_ip=ctx.text(r["Backing IP"]),
        external_module=ctx.text(r["External module"]),
        master_ports=tuple(
            ir.PortRef(
                index=lk.index or 0,
                port=lk.end,
                priority=lk.priority if arb == "fixed-priority" else None,
            )
            for lk in mports
        ),
        slave_ports=tuple(ir.PortRef(index=lk.index or 0, port=lk.end) for lk in sports),
        id_width=ir.IdWidth(master_side=ids[0], slave_side=ids[1]) if ids else None,
        max_parallel=min(len(mports), len(sports)) if topology == "crossbar" else 1,
        addr_rules=tuple(
            sorted(
                (
                    ir.AddrRule(
                        slave_port=slave_index[rg.slave],
                        slave=rg.slave,
                        region=rg.region,
                        base=rg.base,
                        end=rg.base + rg.size - 1,
                        access=rg.access,
                        secure=rg.secure,
                    )
                    for rg in regions
                    if rg.fabric == name
                ),
                key=lambda a: (a.base, a.slave_port),
            )
        ),
        access_rules=tuple(
            sorted(
                (
                    ir.AccessRule(master=m, slave=s, access="none" if a == "-" else a)  # type: ignore[arg-type]
                    for (m, s), a in rules.items()
                    if m in masters
                ),
                key=lambda a: (a.master, a.slave),
            )
        ),
        decode=tuple(sorted(decode, key=lambda d: d.master)),
    )


def _adapters_and_placement(
    ctx: Context, top: str, overrides: dict[str, tuple[str, str | None]]
) -> tuple[tuple[ir.Adapter, ...], tuple[ir.Placement, ...]]:
    placement: list[ir.Placement] = []
    home: dict[str, str] = {}
    for name in sorted(n for n, k in ctx.kinds.items() if k in ("fabric", "bridge")):
        wrapper, rtl = overrides.get(name, (top, None))
        home[name] = wrapper
        kind: Literal["fabric", "bridge"] = "fabric" if ctx.kinds[name] == "fabric" else "bridge"
        placement.append(
            ir.Placement(instance=name, kind=kind, wrapper=wrapper, rtl_name=rtl or f"u_{name}")
        )

    default = ctx.text(ctx.raw.wrapper["Adapter placement"])
    by_port: dict[tuple[str, str], list[str]] = defaultdict(list)
    found = ctx.adapters()
    for a in found:
        by_port[a.port, a.fabric].append(a.name)
    adapters = []
    for a in found:
        chain = by_port[a.port, a.fabric]
        side = (
            "master"
            if ctx.master_fabric.get(a.port) == a.fabric and ctx.kinds[a.port] != "slave"
            else "slave"
        )
        index = chain.index(a.name) if side == "master" else len(chain) - 1 - chain.index(a.name)
        wrapper, rtl = overrides.get(
            a.name, (home[a.fabric] if default == "with fabric" else top, None)
        )
        manifest = ctx.registry.adapter(a.type)
        assert manifest is not None
        adapters.append(ir.Adapter(
            name=a.name, type=a.type, port=a.port, fabric=a.fabric, chain_index=index,  # type: ignore[arg-type]
            template=manifest.name, params=_adapter_params(ctx, a.type, a.port, a.fabric, side),
            placed_in=wrapper, rtl_name=rtl or f"u_{a.name}",
        ))  # fmt: skip
        placement.append(
            ir.Placement(
                instance=a.name, kind="adapter", wrapper=wrapper, rtl_name=rtl or f"u_{a.name}"
            )
        )
    adapters.sort(key=lambda a: (a.fabric, a.port, a.chain_index))
    placement.sort(key=lambda pl: pl.instance)
    return tuple(adapters), tuple(placement)


def _adapter_params(
    ctx: Context, kind: str, port: str, fabric: str, side: str
) -> dict[str, str | int]:
    """Request-direction parameters: master -> fabric, or fabric -> slave."""
    frow = ctx.fabric_row(fabric)
    assert frow is not None
    prow = ctx.defs[port]

    def pair(col: str) -> tuple[str | int, str | int]:
        pv, fv = prow[col].value, frow[col].value
        assert (
            pv is not None
            and fv is not None
            and not isinstance(pv, float | bool)
            and not isinstance(fv, float | bool)
        )
        return (pv, fv) if side == "master" else (fv, pv)

    if kind == "pc":
        a, b = pair("Protocol")
        return {"from": a, "to": b}
    if kind == "dw":
        a, b = pair("Data width (bits)")
        return {"from": a, "to": b}
    if kind == "cdc":
        a, b = pair("Clock domain")
        return {"from": a, "to": b}
    if kind == "iw":
        ids = ctx.fabric_id_widths(fabric)
        assert ids is not None
        return {"from": ids[1], "to": _req_int(ctx, prow["ID width (bits)"], 1)}
    for row in ctx.raw.port_options:
        if row["Port"].value == port:
            if kind == "fifo":
                return {"depth": _req_int(ctx, row["FIFO depth"], 1)}
            return {"stages": _CUT_STAGES[_req(ctx.text(row["Register slice"]))]}
    raise AssertionError(f"no Port_Options row for {port}")


def _port_options(ctx: Context) -> tuple[ir.PortOption, ...]:
    """Options for every port end (bridges: their downstream end, as the workbook derives)."""
    listed = {r["Port"].value: r for r in ctx.raw.port_options}
    out = []
    for name in sorted(n for n, k in ctx.kinds.items() if k in ("master", "slave", "bridge")):
        side: Literal["master", "slave"] = "slave" if ctx.kinds[name] == "slave" else "master"
        protocol = ctx.port_protocol(name, side)
        assert protocol is not None
        row = listed.get(name)
        signals: dict[str, str] = {}
        for col, field in _SIGNAL_FIELDS.items():
            given = row[col].value if row is not None else None
            default = "drive" if signal_applies(col, protocol, side == "slave") else "n/a"
            signals[field] = str(given) if given is not None else default
        fifo = _int(ctx, row["FIFO depth"]) if row is not None else None
        sl = row["Register slice"].value if row is not None else None
        out.append(ir.PortOption(port=name, fifo_depth=fifo or 0, slice=sl or "none", **signals))  # type: ignore[arg-type]
    assert set(_SIGNAL_FIELDS) == set(OPTIONAL_SIGNALS)
    return tuple(out)


def _user_ports(ctx: Context) -> tuple[ir.UserPort, ...]:
    """User_Ports rows (D41), sorted by instance then port; tie-1 becomes all ones."""
    out = []
    for r in ctx.raw.user_ports:
        width = _req_int(ctx, r["Width (bits)"], 1)
        kind, target, tie = parse_connection(r["Connection"].value)
        if tie == -1:
            tie = (1 << width) - 1
        out.append(ir.UserPort(
            instance=_req(ctx.text(r["Instance"])), port=_req(ctx.text(r["Port"])),
            direction=_req(ctx.text(r["Direction"])),  # type: ignore[arg-type]
            width=width, connection=kind, target=target, tie=tie,  # type: ignore[arg-type]
        ))  # fmt: skip
    return tuple(sorted(out, key=lambda u: (u.instance, u.port)))
