# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Reference model: where does an access from a master go? Written from the IR only.

The spec semantics (workbook guide + owner decisions), not the generator's tables:
per fabric, apply the master's enabled alias, find the Memory_Map region (from
spec.memory_map, not the resolver's addr_rules), then deny on RO/WO, secure-only with a
non-secure access, or Access_Rules; a miss or denial goes to the master's default slave
or returns an error. A bridge forwards to its downstream fabric (as master). Memory_Map
addresses are system addresses throughout; a pass-through N bridge only changes what
the slaves behind it see (the offset from its window base). A slave sees the low bits
of its address (D29).

Error injection: the word at ERROR_WORD bytes below each region's end makes its slave
answer with an error, which reaches the master unless the slave's PSLVERR is tied.

Two semantics are modelled but cannot occur in a check-clean spec, so the corpus does
not reach them: a PROT-tied port reaching a secure-only region (SEC-PROT-TIED forbids
it; D28 makes it non-secure) and a master-side tied PSLVERR (PORTOPT-ERROR-MASKED warns,
and the corpus is warning-free).
"""

from dataclasses import dataclass

from hdl_bag.ir import Spec

ERROR_WORD = 8  # region end - 8 answers with a slave error


@dataclass(frozen=True)
class Outcome:
    slave: str | None  # None: the fabric answers (error, or a masked one)
    address: int = 0  # the address as the slave sees it
    masked: bool = False  # an error that never reaches the master: it sees OKAY
    slave_error: bool = False  # the slave answers with an error that reaches the master
    nonsec: bool = False  # the non-secure bit as the slave sees it
    bridges: tuple[str, ...] = ()  # bridges crossed on the way
    miss: str | None = None  # fabric error: the failing fabric's miss response (DECERR, ...)


def _option(spec: Spec, port: str, column: str) -> str | None:
    opt = next((o for o in spec.port_options if o.port == port), None)
    return None if opt is None else str(getattr(opt, column))


def prot_tied(spec: Spec, port: str) -> bool:
    return _option(spec, port, "prot") in ("tie-0", "tie-default")


def pslverr_tied(spec: Spec, port: str) -> bool:
    return _option(spec, port, "pslverr") in ("tie-0", "tie-default")


def route(spec: Spec, master: str, addr: int, write: bool, nonsec: bool,
          pins: dict[str, bool]) -> Outcome:  # fmt: skip
    fabrics = {f.name: f for f in spec.fabrics}
    bridges = {b.name: b for b in spec.bridges}
    slaves = {s.name: s for s in spec.slaves}
    fabric = next(f for f in spec.fabrics if any(p.port == master for p in f.master_ports))
    nonsec = nonsec or prot_tied(spec, master)
    masked = pslverr_tied(spec, master)  # a master-side tied PSLVERR hides errors
    offset = 0  # subtracted by pass-through N bridges on the way
    crossed: list[str] = []
    for _ in range(len(spec.fabrics)):
        decode = next(d for d in fabric.decode if d.master == master)
        for a in decode.aliases:
            on = a.enable == "always" or pins.get(a.enable, False)
            if on and a.base <= addr < a.base + a.size:
                addr = a.to + (addr - a.base)
                break
        target = None
        region = next((r for r in spec.memory_map
                       if r.fabric == fabric.name and r.base <= addr < r.base + r.size), None)  # fmt: skip
        if region is not None:
            denied = (write and region.access == "RO") or (not write and region.access == "WO")
            denied |= region.secure and nonsec
            ar = next((a for a in fabric.access_rules
                       if a.master == master and a.slave == region.slave), None)  # fmt: skip
            if ar is not None:
                denied |= ar.access == "none"
                denied |= (write and ar.access == "RO") or (not write and ar.access == "WO")
            target = None if denied else region.slave
        if target is None:
            if decode.miss != "default-slave" or decode.default_slave is None:
                return Outcome(None, masked=masked, bridges=tuple(crossed), miss=decode.miss)
            target = decode.default_slave
        if target in bridges:
            b = bridges[target]
            offset += 0 if b.pass_through else b.window_base
            fabric, master = fabrics[b.downstream], b.name
            nonsec = nonsec or prot_tied(spec, b.name)
            masked = masked or pslverr_tied(spec, b.name)
            crossed.append(b.name)
            continue
        word = slaves[target].data_width // 8  # a slave answers per data word
        error = any(r.slave == target and addr // word == (r.base + r.size - ERROR_WORD) // word
                    for r in spec.memory_map)  # fmt: skip
        error = error and not pslverr_tied(spec, target) and not masked
        width = slaves[target].addr_width
        return Outcome(target, (addr - offset) & ((1 << width) - 1), masked=masked,
                       slave_error=error, nonsec=nonsec, bridges=tuple(crossed))  # fmt: skip
    raise AssertionError("bridge chain longer than the number of fabrics")


def error_words(spec: Spec, slave: str) -> set[int]:
    """Slave-visible addresses at which SLAVE answers with an error (D29 low bits).

    None for a slave whose PSLVERR is tied: such a slave never errors, by definition.
    """
    if pslverr_tied(spec, slave):
        return set()
    width = next(s.addr_width for s in spec.slaves if s.name == slave)
    return {(r.base + r.size - ERROR_WORD) & ((1 << width) - 1)
            for r in spec.memory_map if r.slave == slave}  # fmt: skip


def probes(spec: Spec) -> list[int]:
    """Interesting addresses: per region its first, middle and last word, the error word,
    the words just before and after it; both edges of every alias window."""
    out: set[int] = set()
    for r in spec.memory_map:
        out |= {r.base, r.base + r.size // 2, r.base + r.size - 4, r.base + r.size - ERROR_WORD,
                r.base + r.size, r.base - 4}  # fmt: skip
    for f in spec.fabrics:
        for d in f.decode:
            for a in d.aliases:
                out |= {a.base - 4, a.base, a.base + a.size - 4, a.base + a.size}
    return sorted(a for a in out if 0 <= a < 1 << 32)
