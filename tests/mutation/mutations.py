# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The Phase 4 gate's mutations (design/plan.md §8): bugs a generator could make, injected
into the generated parameter package of one fabric.

Every fabric decodes and arbitrates from the package's tables (``<Fabric>RuleBase``,
``RuleMask``, ``RuleSlave``, ``Connect``, ``MstNoRead`` / ``MstNoWrite``, ``RuleSecure``,
``MissResp`` / ``MissToDefault``, ``Alias*``, ``Priority``), so each mutation is one exact
edit of one table. The harness (test_mutations.py) then requires the config's generated
tests to fail: a mutant that survives is a hole in the tests.

A packed array literal ``'{a, b, c}`` lists the highest index first; INDEX below is the
array index (rule, master), not the position in the text.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

# <Name> = <literal or '{literals}>;
_PARAM = r"(?P<head>\b{name} = )(?P<value>'\{{[^}}]*\}}|\d+'[hb][0-9A-Fa-f_]+);"
_LITERAL = re.compile(r"(\d+)'([hb])([0-9A-Fa-f_]+)")


class MutationError(ValueError):
    """The package does not have what the mutation needs: pick another config."""


@dataclass(frozen=True)
class Literal:
    width: int
    base: str  # "h" or "b"
    value: int

    def __str__(self) -> str:
        if self.base == "b":
            return f"{self.width}'b{self.value:0{self.width}b}"
        digits = f"{self.value:0{(self.width + 3) // 4}X}"
        groups = [digits[max(0, i - 4) : i] for i in range(len(digits), 0, -4)][::-1]
        return f"{self.width}'h{'_'.join(groups)}"


def _parse(text: str) -> Literal:
    m = _LITERAL.fullmatch(text.strip())
    if m is None:
        raise MutationError(f"not a sized literal: {text!r}")
    return Literal(int(m[1]), m[2], int(m[3].replace("_", ""), 16 if m[2] == "h" else 2))


def get(pkg: str, name: str) -> list[Literal]:
    """NAME's values, by index (a scalar is a one-element list)."""
    m = re.search(_PARAM.format(name=re.escape(name)), pkg)
    if m is None:
        raise MutationError(f"no parameter {name}")
    value = m["value"]
    if value.startswith("'{"):
        return [_parse(v) for v in value[2:-1].split(",")][::-1]
    return [_parse(value)]


def put(pkg: str, name: str, values: list[Literal]) -> str:
    """PKG with NAME set to VALUES (by index), in NAME's own layout (array or scalar)."""
    pattern = re.compile(_PARAM.format(name=re.escape(name)))
    m = pattern.search(pkg)
    if m is None:
        raise MutationError(f"no parameter {name}")
    if m["value"].startswith("'{"):
        text = "'{" + ", ".join(str(v) for v in values[::-1]) + "}"
    else:
        (only,) = values
        text = str(only)
    new = pkg[: m.start("value")] + text + pkg[m.end("value") :]
    if new == pkg:
        raise MutationError(f"{name}: the mutation changes nothing")
    return new


def _with(v: Literal, value: int) -> Literal:
    return Literal(v.width, v.base, value & ((1 << v.width) - 1))


# ------------------------------------------------------------------------------- mutations
# Each takes the package text, the fabric's prefix (e.g. "Xbar") and LAST: the entry it edits is
# the first one that can show the bug, or with LAST the last one, so a bug in an index-dependent
# path (the last rule, the last master) is targeted too.


def _pick(n: int, last: bool, ok: Callable[[int], bool] = lambda i: True) -> int:
    order = range(n - 1, -1, -1) if last else range(n)
    i = next((i for i in order if ok(i)), None)
    if i is None:
        raise MutationError("no entry this mutation can edit")
    return i


def _mask_edit(pkg: str, f: str, last: bool, edit: Callable[[int], int], least: int) -> str:
    masks = get(pkg, f"{f}RuleMask")
    i = _pick(len(masks), last, lambda i: masks[i].value >= least)
    masks[i] = _with(masks[i], edit(masks[i].value))
    return put(pkg, f"{f}RuleMask", masks)


def decode_short(pkg: str, f: str, last: bool = False) -> str:
    """Decode off-by-one, short: a rule's mask is size - 2. A mask decoder cannot be short by
    just the last byte, so this proxy makes every odd byte of the region miss (bit 0 must equal
    the base's), the last byte among them. Only a single byte at an odd address shows it."""
    return _mask_edit(pkg, f, last, lambda m: m - 1, 1)


def decode_short_word(pkg: str, f: str, last: bool = False) -> str:
    """Decode off-by-one in words: a rule's mask is size - 1 - 4, so every odd word misses,
    the last word among them. The byte variant cannot show on a fabric whose masters address
    whole words (APB, AXI4-Lite): this one can."""
    return _mask_edit(pkg, f, last, lambda m: m - 4, 7)


def decode_long(pkg: str, f: str, last: bool = False) -> str:
    """Decode off-by-one, long: a rule's mask is 2 * size - 1, so the region also takes the
    window next to it (base ^ size). Only an access just outside the region shows it."""
    return _mask_edit(pkg, f, last, lambda m: (m << 1) | 1, 1)


def swapped_port_index(pkg: str, f: str, last: bool = False) -> str:
    """Swapped port index: a rule and the nearest rule after it (before it, with LAST) that
    decodes to another slave port swap their ports."""
    ports = get(pkg, f"{f}RuleSlave")
    i = _pick(len(ports), last, lambda i: any(p.value != ports[i].value for p in ports))
    step = -1 if last else 1
    j = next(k for k in range(i + step, -1 if last else len(ports), step)
             if ports[k].value != ports[i].value)  # fmt: skip
    ports[i], ports[j] = ports[j], ports[i]
    return put(pkg, f"{f}RuleSlave", ports)


def _flip_bit(pkg: str, name: str, last: bool, have: int) -> str:
    """NAME (a table of per-master rows, or one vector) with one bit that is HAVE flipped."""
    rows = get(pkg, name)
    found = [(r, b) for r, row in enumerate(rows) for b in range(row.width)
             if (row.value >> b) & 1 == have]  # fmt: skip
    if not found:
        raise MutationError(f"no {have} bit in {name}")
    r, b = found[-1] if last else found[0]
    rows[r] = _with(rows[r], rows[r].value ^ (1 << b))
    return put(pkg, name, rows)


def dropped_access_none(pkg: str, f: str, last: bool = False) -> str:
    """Dropped access rule: an Access_Rules none (a 0 in Connect) is lost."""
    return _flip_bit(pkg, f"{f}Connect", last, 0)


def dropped_access_ro(pkg: str, f: str, last: bool = False) -> str:
    """Dropped access rule: an Access_Rules RO (a 1 in MstNoWrite) is lost."""
    return _flip_bit(pkg, f"{f}MstNoWrite", last, 1)


def dropped_region_ro(pkg: str, f: str, last: bool = False) -> str:
    """Dropped access rule: a Memory_Map RO region (a 1 in RuleNoWrite) takes writes."""
    return _flip_bit(pkg, f"{f}RuleNoWrite", last, 1)


def wrong_miss_response(pkg: str, f: str, last: bool = False) -> str:
    """Wrong miss response. AXI: a master with no default slave (whose MissResp is used) swaps
    DECERR and SLVERR. AHB-Lite and APB have one error response, so the miss policy instead: a
    master whose misses go to a default slave gets an error, or with none anywhere, a master's
    misses go to slave port 0."""
    (to_default,) = get(pkg, f"{f}MissToDefault")
    try:
        resp = get(pkg, f"{f}MissResp")
    except MutationError:
        has = [m for m in range(to_default.width) if to_default.value >> m & 1]
        m = (has[-1] if last else has[0]) if has else _pick(to_default.width, last)
        return put(pkg, f"{f}MissToDefault", [_with(to_default, to_default.value ^ (1 << m))])
    m = _pick(len(resp), last, lambda m: not to_default.value >> m & 1)
    resp[m] = _with(resp[m], resp[m].value ^ 1)  # 2'b11 DECERR <-> 2'b10 SLVERR
    return put(pkg, f"{f}MissResp", resp)


def secure_dropped(pkg: str, f: str, last: bool = False) -> str:
    """Inverted secure check, as a table bug: a secure-only region loses its flag."""
    return _flip_bit(pkg, f"{f}RuleSecure", last, 1)


def secure_spurious(pkg: str, f: str, last: bool = False) -> str:
    """Inverted secure check, as a table bug: a region that is not secure-only gets the flag."""
    return _flip_bit(pkg, f"{f}RuleSecure", last, 0)


def broken_alias(pkg: str, f: str, last: bool = False) -> str:
    """Broken alias: an alias maps to the block after its target."""
    masks = get(pkg, f"{f}AliasMask")
    targets = get(pkg, f"{f}AliasTo")
    i = _pick(len(masks), last, lambda i: masks[i].value != 0)
    targets[i] = _with(targets[i], targets[i].value + masks[i].value + 1)
    return put(pkg, f"{f}AliasTo", targets)


def priority_inversion(pkg: str, f: str, last: bool = False) -> str:
    """Priority inversion: fixed priorities reversed, so the lowest-priority master wins."""
    prio = get(pkg, f"{f}Priority")
    if len({p.value for p in prio}) < 2:
        raise MutationError("no fixed priorities")
    top = max(p.value for p in prio)
    return put(pkg, f"{f}Priority", [_with(p, top - p.value) for p in prio])


@dataclass(frozen=True)
class Mutation:
    bug: str  # the gate's name for the bug
    apply: Callable[..., str]  # (package, fabric prefix, last=False) -> mutant


MUTATIONS: dict[str, Mutation] = {
    "decode_short": Mutation("decode off-by-one", decode_short),
    "decode_short_word": Mutation("decode off-by-one", decode_short_word),
    "decode_long": Mutation("decode off-by-one", decode_long),
    "swapped_port_index": Mutation("swapped port index", swapped_port_index),
    "dropped_access_none": Mutation("dropped access rule", dropped_access_none),
    "dropped_access_ro": Mutation("dropped access rule", dropped_access_ro),
    "dropped_region_ro": Mutation("dropped access rule", dropped_region_ro),
    "wrong_miss_response": Mutation("wrong miss response", wrong_miss_response),
    "secure_dropped": Mutation("inverted secure check", secure_dropped),
    "secure_spurious": Mutation("inverted secure check", secure_spurious),
    "broken_alias": Mutation("broken alias", broken_alias),
    "priority_inversion": Mutation("priority inversion", priority_inversion),
}


# ---------------------------------------------------------------------- bridge mutations
# Phase 3 known gap 11: the PULP-backed bridges have no directed bench, so these bugs in their
# wrappers (copied into the output's rtl/inhouse/) show whether the generated tests cover them.


@dataclass(frozen=True)
class RtlMutation:
    file: str  # under the output's rtl/inhouse/
    edits: tuple[tuple[str, str], ...]  # (exact text, replacement), each found exactly once
    bug: str

    def apply(self, text: str) -> str:
        for old, new in self.edits:
            if text.count(old) != 1:
                raise MutationError(f"{self.file}: {old!r} found {text.count(old)} times")
            text = text.replace(old, new)
        return text


_NO_OFFSET = (("off_req.aw.addr = cdc_req.aw.addr - AddrWidth'(AddrOffset);",
               "off_req.aw.addr = cdc_req.aw.addr;"),
              ("off_req.ar.addr = cdc_req.ar.addr - AddrWidth'(AddrOffset);",
               "off_req.ar.addr = cdc_req.ar.addr;"))  # fmt: skip
_NO_PSLVERR = (("assign apb_resp.pslverr = m_pslverr_i;", "assign apb_resp.pslverr = 1'b0;"),)

_OFFSET_BUG = "pass-through N offset not subtracted"
_PPROT = (("assign m_pprot_o      = apb_req.pprot;", "assign m_pprot_o      = 3'b000;"),)
_PSTRB = (("assign m_pstrb_o      = apb_req.pstrb;", "assign m_pstrb_o      = '1;"),)
BRIDGE_MUTATIONS: dict[str, RtlMutation] = {
    "axi2apb_pprot": RtlMutation("bag_axi2apb.sv", _PPROT, "PPROT tied to 0"),
    "axi2apb_pstrb": RtlMutation("bag_axi2apb.sv", _PSTRB, "PSTRB tied to all ones"),
    "axi2axil_offset": RtlMutation("bag_axi2axil.sv", _NO_OFFSET, _OFFSET_BUG),
    "axi2apb_offset": RtlMutation("bag_axi2apb.sv", _NO_OFFSET, _OFFSET_BUG),
    "axi2apb_pslverr": RtlMutation("bag_axi2apb.sv", _NO_PSLVERR, "PSLVERR dropped"),
    "axil2apb_offset": RtlMutation("bag_axil2apb.sv", _NO_OFFSET, _OFFSET_BUG),
    "axil2apb_pslverr": RtlMutation("bag_axil2apb.sv", _NO_PSLVERR, "PSLVERR dropped"),
    "axi2axil_bresp": RtlMutation(
        "bag_axi2axil.sv", ((".bresp_i(m_bresp_i),", ".bresp_i(2'b00),"),), "B response dropped"
    ),
    "axi2axil_rresp": RtlMutation(
        "bag_axi2axil.sv", ((".rresp_i(m_rresp_i),", ".rresp_i(2'b00),"),), "R response dropped"
    ),
}


# The inverted secure check as the RTL bug it names: the decoder's test of the non-secure bit
# (shared by every fabric) with its polarity flipped.
SECURE_POLARITY = RtlMutation(
    "bag_addr_decode.sv",
    (
        (
            "if (RuleSecure[r] && nonsec_i) deny = 1'b1;",
            "if (RuleSecure[r] && !nonsec_i) deny = 1'b1;",
        ),
    ),
    "inverted secure check (polarity of the non-secure bit)",
)
