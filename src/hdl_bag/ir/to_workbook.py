# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""IR -> workbook, so every IR (including random ones) can be opened by a human.

Reading the written workbook back resolves to the same IR, apart from provenance.
Notes and descriptions are not part of the IR and come back empty.
"""

from openpyxl import Workbook

from hdl_bag.ir import model as ir
from hdl_bag.ir.model import _to_hex
from hdl_bag.template.builder import build_workbook
from hdl_bag.template.data import Cell, SpecData


def _yn(v: bool) -> str:
    return "Y" if v else "N"


def _na(v: Cell) -> Cell:
    return "n/a" if v is None else v


def to_spec_data(spec: ir.Spec) -> SpecData:
    p, w = spec.project, spec.wrapper
    data = SpecData(
        title=f"BAG spec: {p.name}",
        description="Written from the IR by bag; notes are not part of the IR.",
    )
    data.project.update({
        "Project name": p.name, "Spec revision": p.revision, "Author": p.author,
        "SystemVerilog standard": p.sv_std, "Output directory": p.output_dir,
        "Generate RTL": _yn(p.outputs.rtl), "Generate C header": _yn(p.outputs.c_header),
        "Generate memory-map doc": _yn(p.outputs.doc), "Generate cocotb unit tests": _yn(p.outputs.tests),
        "Generate formal (SymbiYosys)": _yn(p.outputs.formal),
        "Lint tools": ", ".join(p.lint_tools), "Simulator": p.simulator,
    })  # fmt: skip
    data.wrapper.update({
        "Top module name": w.top, "Port style": w.port_style, "Signal naming pattern": w.signal_pattern,
        "Clock/reset ports": w.clock_reset_ports, "Parameter package": w.package,
        "File header": w.file_header, "Extra top-level inputs": ", ".join(w.extra_inputs) or None,
        "Adapter placement": w.adapter_placement, "Adapter instance naming": w.adapter_naming,
        "Sub-wrapper port naming": w.subwrapper_port_naming,
    })  # fmt: skip
    data.clocks = [[c.name, c.mhz, c.source, c.domain, c.reset, None] for c in spec.clocks]
    data.resets = [
        [r.name, r.polarity, r.assertion, r.deassertion, r.domain, r.source, None]
        for r in spec.resets
    ]
    for f in spec.fabrics:
        mid = f.midpoint
        data.fabrics.append([
            f.name, f.protocol, f.topology, f.data_width, f.addr_width, f.domain, f.arbitration,
            _na(f.latency_mode), _na(f.max_mst_trans), _na(f.max_slv_trans), f.template, f.backing_ip,
            mid.fifo_depth if mid else "n/a", mid.slice if mid else "n/a", None,
        ])  # fmt: skip
        for mp in f.master_ports:
            data.topology.append([mp.port, f.name, mp.index, mp.priority, None])
        for sp in f.slave_ports:
            data.topology.append([f.name, sp.port, sp.index, None, None])
        for ar in f.access_rules:
            data.access_rules.append(
                [ar.master, ar.slave, "-" if ar.access == "none" else ar.access, None]
            )
        for d in f.decode:
            a = d.aliases[0] if d.aliases else None
            data.address_decode.append([
                d.master, d.miss, d.default_slave,
                _to_hex(a.base) if a else None, _to_hex(a.size) if a else None,
                _to_hex(a.to) if a else None, a.enable if a else None, None,
            ])  # fmt: skip
    data.masters = [
        [m.name, m.protocol, m.data_width, m.addr_width, m.id_width, m.user_width, m.domain,
         m.max_outstanding_rd, m.max_outstanding_wr, None, None]
        for m in spec.masters
    ]  # fmt: skip
    data.slaves = [
        [s.name, s.protocol, s.data_width, s.addr_width, "auto" if s.id_width_auto else s.id_width,
         s.user_width, s.domain, s.error_response, s.wait_states, None, None]
        for s in spec.slaves
    ]  # fmt: skip
    data.bridges = [
        [b.name, b.template, _to_hex(b.window_base), _yn(b.pass_through), b.fifo_depth, None]
        for b in spec.bridges
    ]
    data.memory_map = [
        [r.slave, r.region, _to_hex(r.base), _to_hex(r.size), r.access, _yn(r.secure), None]
        for r in spec.memory_map
    ]
    data.port_options = [
        [o.port, o.fifo_depth, o.slice, o.prot, o.qos, o.region, o.cache, o.lock, o.pstrb, o.pslverr, None]
        for o in spec.port_options
    ]  # fmt: skip
    data.sub_wrappers = [[s.name, s.parent, None] for s in spec.hierarchy.wrappers]

    # Placement rows only where the IR differs from the default home or instance name.
    top = spec.hierarchy.top
    fabric_home = {
        pl.instance: pl.wrapper for pl in spec.hierarchy.placement if pl.kind == "fabric"
    }
    adapter_fabric = {a.name: a.fabric for a in spec.adapters}
    for pl in spec.hierarchy.placement:
        if pl.kind == "adapter":
            default = (
                fabric_home[adapter_fabric[pl.instance]]
                if w.adapter_placement == "with fabric"
                else top
            )
        else:
            default = top
        rtl = None if pl.rtl_name == f"u_{pl.instance}" else pl.rtl_name
        if pl.wrapper != default or rtl is not None:
            data.placement.append([pl.instance, pl.wrapper, rtl, None])
    data.exposed_ports = [
        [e.port, _yn(e.exposed), e.prefix, None if e.exposed else "internal", None] for e in w.ports
    ]
    return data


def to_workbook(spec: ir.Spec) -> Workbook:
    return build_workbook(data=to_spec_data(spec))
