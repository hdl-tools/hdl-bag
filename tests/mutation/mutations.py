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
# Each takes the package text and the fabric's prefix (e.g. "Xbar") and returns the mutant.


def decode_short(pkg: str, f: str) -> str:
    """Decode off-by-one: rule 0's mask is size - 2, so its end is one byte short; with a mask
    decoder that also drops every other odd byte of the region."""
    masks = get(pkg, f"{f}RuleMask")
    masks[0] = _with(masks[0], masks[0].value - 1)
    return put(pkg, f"{f}RuleMask", masks)


def decode_short_word(pkg: str, f: str) -> str:
    """Decode off-by-one, in words: rule 0's mask is size - 1 - 4, so its last word misses
    (with every other word). The byte variant cannot show on a fabric whose masters address
    whole words (APB, AXI4-Lite): only this one can."""
    masks = get(pkg, f"{f}RuleMask")
    masks[0] = _with(masks[0], masks[0].value - 4)
    return put(pkg, f"{f}RuleMask", masks)


def decode_long(pkg: str, f: str) -> str:
    """Decode off-by-one: rule 0's mask is size instead of size - 1."""
    masks = get(pkg, f"{f}RuleMask")
    masks[0] = _with(masks[0], masks[0].value + 1)
    return put(pkg, f"{f}RuleMask", masks)


def swapped_port_index(pkg: str, f: str) -> str:
    """Swapped port index: the first two rules that decode to different slave ports swap
    their ports."""
    ports = get(pkg, f"{f}RuleSlave")
    i = 0
    j = next((k for k in range(1, len(ports)) if ports[k].value != ports[i].value), None)
    if j is None:
        raise MutationError("every rule decodes to one slave port")
    ports[i], ports[j] = ports[j], ports[i]
    return put(pkg, f"{f}RuleSlave", ports)


def dropped_access_rule(pkg: str, f: str) -> str:
    """Dropped access rule: the first Access_Rules none (a 0 in Connect) becomes a 1; without
    one, the first RO or WO rule (a 1 in MstNoWrite, then MstNoRead) is cleared."""
    for name, to in ((f"{f}Connect", 1), (f"{f}MstNoWrite", 0), (f"{f}MstNoRead", 0)):
        rows = get(pkg, name)
        for m, row in enumerate(rows):
            bits = [(row.value >> s) & 1 for s in range(row.width)]
            if (1 - to) in bits:
                s = bits.index(1 - to)
                rows[m] = _with(row, row.value ^ (1 << s))
                return put(pkg, name, rows)
    raise MutationError("no Access_Rules none, RO or WO rule")


def wrong_miss_response(pkg: str, f: str) -> str:
    """Wrong miss response: master 0's AXI miss response swaps DECERR and SLVERR. On AHB-Lite
    and APB (one error response), the first master whose misses go to a default slave gets
    an error instead; with no default slave, master 0's misses go to slave port 0."""
    try:
        resp = get(pkg, f"{f}MissResp")
    except MutationError:
        (to_default,) = get(pkg, f"{f}MissToDefault")
        bit = next((m for m in range(to_default.width) if to_default.value >> m & 1), 0)
        # Without any default slave, master 0's misses now go to its DefaultSlave entry.
        return put(pkg, f"{f}MissToDefault", [_with(to_default, to_default.value ^ (1 << bit))])
    resp[0] = _with(resp[0], resp[0].value ^ 1)  # 2'b11 DECERR <-> 2'b10 SLVERR
    return put(pkg, f"{f}MissResp", resp)


def inverted_secure_check(pkg: str, f: str) -> str:
    """Inverted secure check: every rule's secure-only flag inverted."""
    (secure,) = get(pkg, f"{f}RuleSecure")
    return put(pkg, f"{f}RuleSecure", [_with(secure, ~secure.value)])


def broken_alias(pkg: str, f: str) -> str:
    """Broken alias: alias 0 maps to the block after its target."""
    masks = get(pkg, f"{f}AliasMask")
    targets = get(pkg, f"{f}AliasTo")
    if masks[0].value == 0:
        raise MutationError("no alias")
    targets[0] = _with(targets[0], targets[0].value + masks[0].value + 1)
    return put(pkg, f"{f}AliasTo", targets)


def priority_inversion(pkg: str, f: str) -> str:
    """Priority inversion: fixed priorities reversed, so the lowest-priority master wins."""
    prio = get(pkg, f"{f}Priority")
    if len({p.value for p in prio}) < 2:
        raise MutationError("no fixed priorities")
    top = max(p.value for p in prio)
    return put(pkg, f"{f}Priority", [_with(p, top - p.value) for p in prio])


@dataclass(frozen=True)
class Mutation:
    bug: str  # the gate's name for the bug
    apply: Callable[[str, str], str]


MUTATIONS: dict[str, Mutation] = {
    "decode_short": Mutation("decode off-by-one", decode_short),
    "decode_short_word": Mutation("decode off-by-one", decode_short_word),
    "decode_long": Mutation("decode off-by-one", decode_long),
    "swapped_port_index": Mutation("swapped port index", swapped_port_index),
    "dropped_access_rule": Mutation("dropped access rule", dropped_access_rule),
    "wrong_miss_response": Mutation("wrong miss response", wrong_miss_response),
    "inverted_secure_check": Mutation("inverted secure check", inverted_secure_check),
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
BRIDGE_MUTATIONS: dict[str, RtlMutation] = {
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
