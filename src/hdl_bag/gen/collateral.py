# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Collateral from the same IR as the RTL: C header, memory-map document, port map.

* ``sw/<project>_memmap.h``: ``<SLAVE>_<REGION>_BASE`` / ``_SIZE`` per Memory_Map region
  (system addresses), as ``ULL`` constants.
* ``doc/memmap.md`` and ``doc/memmap.html``: the memory map per fabric, bridge windows and
  per-master aliases; the HTML is self-contained (no external assets) and renders the
  same rows as the Markdown.
* ``doc/port_map.md``: per fabric, the resolved port indices, priorities, widths,
  adapters and placement.

Every file carries the D4 header (user text verbatim, then the tool banner); Markdown and
HTML put it in an HTML comment.
"""

import html
from dataclasses import dataclass

from hdl_bag.gen.decode import fabric_offset
from hdl_bag.gen.render import GenerateError, header
from hdl_bag.ir.model import Spec

MEMMAP_COLUMNS = ("Slave", "Region", "Base", "End", "Size", "Access", "Secure only")
ALIAS_COLUMNS = ("Master", "Alias base", "Size", "Maps to", "Enable")
PORT_COLUMNS = ("Index", "Port", "Kind", "Protocol", "Data width", "Addr width", "Domain",
                "Priority", "Adapters (port side first)")  # fmt: skip


def _hex(value: int, width: int) -> str:
    """0x1000_0000 style, zero-padded to WIDTH bits."""
    digits = f"{value:0{max(1, (width + 3) // 4)}X}"
    head = len(digits) % 4 or 4
    return "0x" + "_".join(
        [digits[:head], *(digits[i : i + 4] for i in range(head, len(digits), 4))]
    )


@dataclass(frozen=True)
class FabricMap:
    name: str
    summary: str
    regions: list[dict[str, str]]  # MEMMAP_COLUMNS -> text
    aliases: list[dict[str, str]]  # ALIAS_COLUMNS -> text
    notes: list[str]


def memmap(spec: Spec) -> list[FabricMap]:
    """The memory map per fabric, in Fabrics order; regions sorted by base address."""
    bridges = {b.name: b for b in spec.bridges}
    out = []
    for f in spec.fabrics:
        aw = f.addr_width
        regions = []
        for r in sorted((r for r in spec.memory_map if r.fabric == f.name), key=lambda r: r.base):
            b = bridges.get(r.slave)
            slave = f"{r.slave} (bridge to {b.downstream})" if b else r.slave
            regions.append({
                "Slave": slave, "Region": r.region, "Base": _hex(r.base, aw),
                "End": _hex(r.base + r.size - 1, aw), "Size": _hex(r.size, aw),
                "Access": r.access, "Secure only": "Y" if r.secure else "N",
            })  # fmt: skip
        aliases = [
            {"Master": d.master, "Alias base": _hex(a.base, aw), "Size": _hex(a.size, aw),
             "Maps to": _hex(a.to, aw), "Enable": a.enable}
            for d in sorted(f.decode, key=lambda d: d.master)
            for a in sorted(d.aliases, key=lambda a: a.base)
        ]  # fmt: skip
        notes = []
        offset = fabric_offset(spec, f)
        if offset:
            via = ", ".join(sorted(b.name for b in spec.bridges if b.downstream == f.name))
            notes.append(f"Reached through {via}: the fabric decodes addresses relative to "
                         f"{_hex(offset, aw)} (a pass-through N window); the table shows system "
                         "addresses.")  # fmt: skip
        for d in sorted(f.decode, key=lambda d: d.master):
            miss = f"default slave {d.default_slave}" if d.default_slave else d.miss
            notes.append(f"{d.master}: unmapped or blocked accesses get {miss}.")
        summary = (
            f"{f.protocol} {f.topology}, {f.data_width}-bit data, {aw}-bit address, {f.domain}"
        )
        out.append(FabricMap(f.name, summary, regions, aliases, notes))
    return out


def _md_table(columns: tuple[str, ...], rows: list[dict[str, str]]) -> list[str]:
    def cell(v: str) -> str:
        return v.replace("|", "\\|")

    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(cell(r[c]) for c in columns) + " |" for r in rows]
    return lines


def _banner(spec: Spec, user_header: str | None) -> str:
    return header(spec, user_header)


def memmap_md(spec: Spec, user_header: str | None) -> str:
    revision = spec.project.revision or "-"
    lines = [f"<!--\n{_banner(spec, user_header)}-->", "",
             f"# {spec.project.name} memory map", "",
             f"Revision {revision}. System addresses; End is inclusive.", ""]  # fmt: skip
    for fm in memmap(spec):
        lines += [f"## {fm.name}", "", fm.summary, ""]
        lines += [*_md_table(MEMMAP_COLUMNS, fm.regions), ""]
        if fm.aliases:
            lines += ["### Aliases", "", *_md_table(ALIAS_COLUMNS, fm.aliases), ""]
        lines += [f"- {n}" for n in fm.notes]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


_CSS = (
    "body{font-family:system-ui,sans-serif;margin:2rem;color:#1d1d1f;background:#fff}"
    "table{border-collapse:collapse;margin:0 0 1rem}"
    "th,td{border:1px solid #ccc;padding:.25rem .6rem;text-align:left}"
    "td.addr{font-family:ui-monospace,monospace}th{background:#f2f2f2}"
    "@media(prefers-color-scheme:dark){body{background:#1c1c1e;color:#eee}"
    "th{background:#2c2c2e}th,td{border-color:#444}}"
)


def _html_table(columns: tuple[str, ...], rows: list[dict[str, str]]) -> list[str]:
    mono = {"Base", "End", "Size", "Alias base", "Maps to"}
    out = ["<table>", "<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in columns) + "</tr>"]
    for r in rows:
        cells = "".join(
            f'<td class="addr">{html.escape(r[c])}</td>'
            if c in mono
            else f"<td>{html.escape(r[c])}</td>"
            for c in columns
        )
        out.append(f"<tr>{cells}</tr>")
    return [*out, "</table>"]


def memmap_html(spec: Spec, user_header: str | None) -> str:
    title = html.escape(f"{spec.project.name} memory map")
    lines = ["<!DOCTYPE html>", f"<!--\n{_banner(spec, user_header)}-->", '<html lang="en">',
             "<head>", '<meta charset="utf-8">', f"<title>{title}</title>",
             f"<style>{_CSS}</style>", "</head>", "<body>", f"<h1>{title}</h1>",
             f"<p>Revision {html.escape(spec.project.revision or '-')}. "
             "System addresses; End is inclusive.</p>"]  # fmt: skip
    for fm in memmap(spec):
        lines += [f"<h2>{html.escape(fm.name)}</h2>", f"<p>{html.escape(fm.summary)}</p>"]
        lines += _html_table(MEMMAP_COLUMNS, fm.regions)
        if fm.aliases:
            lines += ["<h3>Aliases</h3>", *_html_table(ALIAS_COLUMNS, fm.aliases)]
        if fm.notes:
            lines += ["<ul>", *(f"<li>{html.escape(n)}</li>" for n in fm.notes), "</ul>"]
    lines += ["</body>", "</html>"]
    return "\n".join(lines) + "\n"


def c_header(spec: Spec, user_header: str | None) -> str:
    project = spec.project.name
    guard = f"{project.upper()}_MEMMAP_H"
    widths = {f.name: f.addr_width for f in spec.fabrics}
    bridges = {b.name for b in spec.bridges}
    seen: dict[str, str] = {}
    body = []
    for f in spec.fabrics:
        regions = sorted((r for r in spec.memory_map if r.fabric == f.name), key=lambda r: r.base)
        if not regions:
            continue
        body += ["", f"/* {f.name}: {f.protocol} {f.topology} */"]
        for r in regions:
            stem = f"{r.slave}_{r.region}".upper()
            owner = f"{r.slave}.{r.region}"
            if stem in seen and seen[stem] != owner:
                raise GenerateError(f"C macro {stem}_BASE is claimed by {seen[stem]} and {owner}")
            seen[stem] = owner
            digits = max(1, (widths[r.fabric] + 3) // 4)
            what = [r.access, *(["secure only"] if r.secure else [])]
            if r.slave in bridges:
                what.append("bridge window")
            body += [
                f"#define {stem}_BASE 0x{r.base:0{digits}X}ULL /* {', '.join(what)} */",
                f"#define {stem}_SIZE 0x{r.size:0{digits}X}ULL",
            ]
    lines = [_banner(spec, user_header).rstrip("\n"), "", f"#ifndef {guard}", f"#define {guard}",
             "", f"/* Memory map of {project} (system addresses). */", *body, "",
             f"#endif /* {guard} */", ""]  # fmt: skip
    return "\n".join(lines)


def port_map_md(spec: Spec, user_header: str | None) -> str:
    masters = {m.name: m for m in spec.masters}
    slaves = {s.name: s for s in spec.slaves}
    bridges = {b.name: b for b in spec.bridges}
    placement = {p.instance: p for p in spec.hierarchy.placement}
    adapters: dict[tuple[str, str], list[str]] = {}
    for a in sorted(spec.adapters, key=lambda a: a.chain_index):
        adapters.setdefault((a.port, a.fabric), []).append(a.name)

    def row(
        index: int, port: str, priority: int | None, f_name: str, slave: bool
    ) -> dict[str, str]:
        if port in bridges:
            b = bridges[port]
            proto = b.upstream_protocol if slave else b.downstream_protocol
            width = b.upstream_width if slave else b.downstream_width
            kind, addr, domain = (
                "bridge",
                "-",
                next(f.domain for f in spec.fabrics if f.name == f_name),
            )
        else:
            e = slaves[port] if slave else masters[port]
            proto, width, addr, domain = e.protocol, e.data_width, str(e.addr_width), e.domain
            kind = "slave" if slave else "master"
        return {
            "Index": str(index), "Port": port, "Kind": kind, "Protocol": proto,
            "Data width": str(width), "Addr width": addr, "Domain": domain,
            "Priority": "-" if priority is None else str(priority),
            "Adapters (port side first)": ", ".join(adapters.get((port, f_name), [])) or "-",
        }  # fmt: skip

    lines = [f"<!--\n{_banner(spec, user_header)}-->", "", f"# {spec.project.name} port map", ""]
    for f in spec.fabrics:
        p = placement.get(f.name)
        where = f"{p.wrapper}, instance {p.rtl_name}" if p else f"{spec.hierarchy.top}, u_{f.name}"
        lines += [f"## {f.name}", "",
                  f"{f.protocol} {f.topology}, {f.arbitration}, template {f.template}; "
                  f"placed in {where}."]  # fmt: skip
        if f.id_width:
            lines.append(f"ID width: master side {f.id_width.master_side}, "
                         f"slave side {f.id_width.slave_side}.")  # fmt: skip
        lines += ["", "### Master ports", ""]
        rows = [row(r.index, r.port, r.priority, f.name, False)
                for r in sorted(f.master_ports, key=lambda r: r.index)]  # fmt: skip
        lines += [*_md_table(PORT_COLUMNS, rows), "", "### Slave ports", ""]
        rows = [row(r.index, r.port, None, f.name, True)
                for r in sorted(f.slave_ports, key=lambda r: r.index)]  # fmt: skip
        lines += [*_md_table(PORT_COLUMNS, rows), ""]
    return "\n".join(lines).rstrip("\n") + "\n"
