# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Memory_Map, bridge windows, Access_Rules, Address_Decode and secure regions."""

from dataclasses import dataclass

from hdl_bag.checks.basics import extra_inputs
from hdl_bag.checks.context import Context
from hdl_bag.protocols import AXI_FAMILY, MISS_RESPONSES, OPTIONAL_SIGNALS
from hdl_bag.reader.model import Row


@dataclass(frozen=True)
class Region:
    row: Row
    target: str
    name: str
    fabric: str
    base: int
    size: int
    access: str | None
    secure: bool | None

    @property
    def end(self) -> int:
        return self.base + self.size - 1

    def overlaps(self, base: int, end: int) -> bool:
        return self.base <= end and base <= self.end

    def span(self) -> str:
        return f"{_hex(self.base)}..{_hex(self.end)}"


def _hex(v: int) -> str:
    """0x4000_0000 style, at least 8 digits, grouped by 4."""
    h, parts = f"{v:08X}", list[str]()
    while h:
        parts.insert(0, h[-4:])
        h = h[:-4]
    return "0x" + "_".join(parts)


def _pow2(v: int) -> bool:
    return v > 0 and v & (v - 1) == 0


def check_memory_map(ctx: Context) -> list[Region]:
    regions: list[Region] = []
    names: dict[tuple[str, str], int] = {}
    for row in ctx.raw.memory_map:
        target = ctx.text(row["Slave / bridge"], required=True)
        rname = ctx.name(row["Region"])
        base = ctx.hexval(row["Base (hex)"])
        size = ctx.hexval(row["Size (hex)"])
        access = ctx.enum(row["Access"], ["RW", "RO", "WO"])
        secure = ctx.yes_no(row["Secure only"])
        ctx.text(row["Notes"])
        if target is None:
            continue
        if ctx.kinds.get(target) not in ("slave", "bridge"):
            ctx.report(
                "MM-UNKNOWN-SLAVE",
                row["Slave / bridge"],
                f"{target} is not defined on Slaves or Bridges",
            )
            continue
        fabric = ctx.slave_fabric.get(target)
        if fabric is None:
            ctx.report(
                "MM-NOT-CONNECTED",
                row["Slave / bridge"],
                f"{target} has no Topology link as a Destination",
            )
            continue
        if rname is not None:
            if (target, rname) in names:
                ctx.report(
                    "NAME-UNIQUE",
                    row["Region"],
                    f"region {rname} of {target} repeats row {names[target, rname]}",
                )
            else:
                names[target, rname] = row.row
        if base is None or size is None or rname is None:
            continue
        if not _pow2(size):
            ctx.report(
                "MM-SIZE-NOT-POW2", row["Size (hex)"], f"size {_hex(size)} is not a power of 2"
            )
            continue
        if base % size:
            ctx.report(
                "MM-MISALIGNED",
                row["Base (hex)"],
                f"base {_hex(base)} is not aligned to size {_hex(size)}",
            )
            continue
        r = Region(row, target, rname, fabric, base, size, access, secure)
        frow = ctx.fabric_row(fabric)
        f_addr = ctx.integer(frow["Addr width (bits)"], minimum=1) if frow else None
        if isinstance(f_addr, int) and r.end >= 1 << f_addr:
            ctx.report("MM-ADDR-RANGE", row["Base (hex)"],
                       f"{target} {r.span()} exceeds the {f_addr}-bit address space of {fabric}")  # fmt: skip
            continue
        if ctx.kinds[target] == "slave":
            s_addr = ctx.integer(ctx.defs[target]["Addr width (bits)"], minimum=1)
            if isinstance(s_addr, int) and size > 1 << s_addr:
                ctx.report("MM-ADDR-RANGE", row["Size (hex)"],
                           f"size {_hex(size)} exceeds the {s_addr}-bit address space of {target}")  # fmt: skip
                continue
        clash = next((o for o in regions if o.fabric == fabric and o.overlaps(r.base, r.end)), None)
        if clash:
            ctx.report("MM-OVERLAP", row["Base (hex)"],
                       f"{target} {r.span()} overlaps {clash.target} ({clash.row.where}) on {fabric}")  # fmt: skip
            continue
        if size < 4096 and ctx.fabric_protocol(fabric) in AXI_FAMILY:
            ctx.report("MM-SMALL-AXI", row["Size (hex)"],
                       f"{target} region {rname} is smaller than 4 KB on AXI fabric {fabric}")  # fmt: skip
        regions.append(r)

    listed = {r["Slave / bridge"].value for r in ctx.raw.memory_map}
    for row in ctx.raw.slaves:
        name = ctx.text(row["Slave"])
        if name and ctx.defs.get(name) is row and name in ctx.slave_fabric and name not in listed:
            ctx.report(
                "MM-PATH-NO-REGION", row["Slave"], f"slave {name} has no region in Memory_Map"
            )
    return regions


def check_bridge_windows(ctx: Context, regions: list[Region]) -> None:
    for row in ctx.raw.bridges:
        name = ctx.text(row["Bridge"])
        base = ctx.hexval(row["Window base (hex)"])
        if not name or ctx.defs.get(name) is not row or name not in ctx.slave_fabric:
            continue
        own = [r for r in regions if r.target == name]
        listed = [r for r in ctx.raw.memory_map if r["Slave / bridge"].value == name]
        if not listed:
            ctx.report(
                "BRIDGE-WINDOW-BASE",
                row["Window base (hex)"],
                f"bridge {name} has no region (window) in Memory_Map",
            )
            continue
        if base is not None and own and base != min(r.base for r in own):
            ctx.report("BRIDGE-WINDOW-BASE", row["Window base (hex)"],
                       f"Window base {_hex(base)} differs from {name}'s lowest region "
                       f"{_hex(min(r.base for r in own))}")  # fmt: skip
        down = ctx.master_fabric.get(name)
        if down is None:
            continue
        for r in own:
            if not any(o.fabric == down and o.overlaps(r.base, r.end) for o in regions):
                ctx.report("BRIDGE-WINDOW-EMPTY", r.row["Base (hex)"],
                           f"{name} window {r.span()} reaches no region on {down}")  # fmt: skip


def access_rules(ctx: Context) -> dict[tuple[str, str], str]:
    """(master end, slave end) -> RO | WO | - for valid rules (after check_access_rules)."""
    out: dict[tuple[str, str], str] = {}
    for row in ctx.raw.access_rules:
        m, s = ctx.text(row["Master"]), ctx.text(row["Slave / bridge"])
        a = ctx.enum(row["Access"], ["RO", "WO", "-"])
        if m and s and a and (m, s) not in out and ctx.master_fabric.get(m) is not None \
                and ctx.master_fabric.get(m) == ctx.slave_fabric.get(s):  # fmt: skip
            out[m, s] = a
    return out


def check_access_rules(ctx: Context) -> None:
    seen: dict[tuple[str, str], int] = {}
    for row in ctx.raw.access_rules:
        m = ctx.text(row["Master"], required=True)
        s = ctx.text(row["Slave / bridge"], required=True)
        ctx.enum(row["Access"], ["RO", "WO", "-"])
        ctx.text(row["Notes"])
        if m is None or s is None:
            continue
        mf, sf = ctx.master_fabric.get(m), ctx.slave_fabric.get(s)
        bad = [n for n, f in ((m, mf), (s, sf)) if f is None]
        if bad:
            for n in bad:
                ctx.report(
                    "ACC-UNKNOWN-NAME",
                    row,
                    f"{n} is not a connected {'master' if n == m else 'slave'} or bridge",
                )
            continue
        if mf != sf:
            ctx.report("ACC-NOT-SAME-FABRIC", row, f"{m} is on {mf} but {s} is on {sf}")
            continue
        if (m, s) in seen:
            ctx.report("ACC-DUPLICATE", row, f"rule {m} -> {s} repeats row {seen[m, s]}")
            continue
        seen[m, s] = row.row

    rules = access_rules(ctx)
    for link in ctx.links:
        if link.side != "slave":
            continue
        masters = ctx.fabric_masters.get(link.fabric, [])
        if masters and all(rules.get((m, link.end)) == "-" for m in masters):
            ctx.report(
                "ACC-UNREACHABLE-SLAVE",
                link.row,
                f"no master on {link.fabric} can reach {link.end}",
            )


def check_address_decode(ctx: Context, regions: list[Region]) -> None:
    inputs = set(extra_inputs(ctx))
    seen: dict[str, int] = {}
    for row in ctx.raw.address_decode:
        m = ctx.text(row["Master"], required=True)
        miss = ctx.enum(
            row["Miss response"], ["DECERR", "SLVERR", "HRESP_ERROR", "PSLVERR", "default-slave"]
        )
        default = ctx.text(row["Default slave"])
        alias = {
            k: row[k]
            for k in ("Alias base (hex)", "Alias size (hex)", "Alias maps to (hex)", "Alias enable")
        }
        has_alias = any(c.value is not None for c in alias.values())
        a_base = ctx.hexval(alias["Alias base (hex)"], required=has_alias)
        a_size = ctx.hexval(alias["Alias size (hex)"], required=has_alias)
        a_to = ctx.hexval(alias["Alias maps to (hex)"], required=has_alias)
        a_en = ctx.text(alias["Alias enable"], required=has_alias)
        ctx.text(row["Notes"])
        if m is None:
            continue
        fabric = ctx.master_fabric.get(m)
        if fabric is None:
            ctx.report(
                "DEC-UNKNOWN-MASTER", row["Master"], f"{m} is not a connected master or bridge"
            )
            continue
        if m in seen:
            ctx.report("DEC-DUPLICATE", row["Master"], f"{m} already has a row ({seen[m]})")
            continue
        seen[m] = row.row
        proto = ctx.fabric_protocol(fabric)
        if miss and proto and miss not in MISS_RESPONSES[proto]:
            ctx.report("DEC-MISS-RESP", row["Miss response"],
                       f"{miss} does not suit {proto} fabric {fabric} "
                       f"({', '.join(sorted(MISS_RESPONSES[proto]))})")  # fmt: skip
        if miss == "default-slave":
            if default is None:
                ctx.report(
                    "DEC-DEFAULT-SLAVE",
                    row["Default slave"],
                    "Miss response default-slave needs a Default slave",
                )
            elif ctx.slave_fabric.get(default) != fabric:
                ctx.report(
                    "DEC-DEFAULT-SLAVE",
                    row["Default slave"],
                    f"default slave {default} is not a slave on {fabric}",
                )
        elif default is not None and miss is not None:
            ctx.report(
                "DEC-DEFAULT-SLAVE",
                row["Default slave"],
                f"Default slave is only used with default-slave, not {miss}",
            )

        if not has_alias or a_base is None or a_size is None or a_to is None or a_en is None:
            continue
        if not _pow2(a_size) or a_base % a_size or a_to % a_size:
            ctx.report("DEC-ALIAS-SIZE", alias["Alias size (hex)"],
                       f"alias size {_hex(a_size)} must be a power of 2 with base and target aligned to it")  # fmt: skip
            continue
        end = a_base + a_size - 1
        if hit := next(
            (r for r in regions if r.fabric == fabric and r.overlaps(a_base, end)), None
        ):
            ctx.report("DEC-ALIAS-OVERLAP", alias["Alias base (hex)"],
                       f"alias {_hex(a_base)}..{_hex(end)} overlaps {hit.target} {hit.name} on {fabric}")  # fmt: skip
        t_end = a_to + a_size - 1
        if not any(r.fabric == fabric and r.base <= a_to and t_end <= r.end for r in regions):
            ctx.report("DEC-ALIAS-TARGET", alias["Alias maps to (hex)"],
                       f"alias target {_hex(a_to)}..{_hex(t_end)} is not inside one region on {fabric}")  # fmt: skip
        if a_en != "always" and a_en not in inputs:
            ctx.report("DEC-ALIAS-ENABLE", alias["Alias enable"],
                       f"Alias enable {a_en!r} is neither 'always' nor a Wrapper > Extra top-level input")  # fmt: skip

    listed = {r["Master"].value for r in ctx.raw.address_decode}
    for link in ctx.links:
        if link.side == "master" and link.end not in listed:
            ctx.report(
                "DEC-MISSING-ROW",
                "Address_Decode",
                f"master port {link.end} on {link.fabric} has no row",
            )


def _prot_tied(ctx: Context, port: str) -> bool:
    protocol = ctx.port_protocol(port, "master")
    if protocol is not None and protocol not in OPTIONAL_SIGNALS["PROT"]:
        return True  # the port cannot express secure / non-secure (D21)
    for row in ctx.raw.port_options:
        if row["Port"].value == port:
            v = ctx.enum(row["PROT"], ("drive", "tie-0", "tie-default", "n/a"), required=False)
            return v in ("tie-0", "tie-default", "n/a")
    return False


def check_secure_prot(ctx: Context, regions: list[Region]) -> None:
    """A Secure-only region must not be reachable from a port whose PROT is tied (D6, D21)."""
    rules = access_rules(ctx)
    windows = {
        b: [r for r in regions if r.target == b] for b in ctx.kinds if ctx.kinds[b] == "bridge"
    }
    for region in regions:
        if not region.secure:
            continue
        tied: set[str] = set()
        # (fabric, target on that fabric, addresses that must be reachable)
        todo = [(region.fabric, region.target)]
        visited: set[tuple[str, str]] = set()
        while todo:
            fabric, target = todo.pop()
            if (fabric, target) in visited:
                continue
            visited.add((fabric, target))
            for m in ctx.fabric_masters.get(fabric, []):
                if rules.get((m, target)) == "-":
                    continue
                if _prot_tied(ctx, m):
                    tied.add(m)
                    continue
                if ctx.kinds.get(m) == "bridge":
                    up = ctx.slave_fabric.get(m)
                    if up and any(w.overlaps(region.base, region.end) for w in windows.get(m, [])):
                        todo.append((up, m))
        for port in sorted(tied):
            ctx.report("SEC-PROT-TIED", region.row["Secure only"],
                       f"secure-only {region.target} {region.name} is reachable from {port}, whose PROT is tied")  # fmt: skip
