# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Names shared by the checks and the generators, so both always agree."""

import re
from collections.abc import Mapping
from functools import cache
from importlib.resources import files
from typing import Final

# Longest generated unit name that PROJ-NAME-LENGTH accepts without a warning.
MAX_UNIT_NAME: Final = 64


_DECL = re.compile(r"^\s*(module|interface|package)\s+(?:automatic\s+)?(\w+)", re.MULTILINE)


@cache
def emitted_units() -> Mapping[str, str]:
    """Every design unit BAG can copy or write, name -> what it is.

    Static: the per-design units (wrappers, interfaces, the package) are not here. Interfaces and
    packages share the design-unit namespace with modules, so they are labelled as what they are.
    """
    from hdl_bag.templates.sources import VENDOR, vendored_units

    out: dict[str, str] = {}
    for path in sorted(set(vendored_units().values())):
        text = (files("hdl_bag") / VENDOR / path).read_text(encoding="utf-8")
        kinds = {name: kind for kind, name in _DECL.findall(text)}
        for unit, p in vendored_units().items():
            if p == path:
                out[unit] = f"a vendored PULP {kinds.get(unit, 'module')}"
    for sub, what in (
        ("inhouse", "an in-house module"),
        ("vendor/derived", "a module BAG derives from PULP"),
    ):
        out |= {
            f.name[:-3]: what
            for f in (files("hdl_bag") / "rtl" / sub).iterdir()
            if f.name.endswith(".sv")
        }
    return out


def unit_name(prefix: str | None, name: str) -> str:
    """NAME with Project > Module prefix in front, joined by an underscore; blank means NAME."""
    return f"{prefix}_{name}" if prefix else name


def flat_signal_name(pattern: str, port: str, proto: str, signal: str, prefix: str | None) -> str:
    """Wrapper port name of SIGNAL on PORT (Wrapper > Signal naming pattern).

    A Top-level prefix replaces "{port}_{proto}" in the pattern; patterns without that
    sequence ignore the prefix.
    """
    stem = pattern.replace("{port}_{proto}", prefix) if prefix else pattern
    return stem.replace("{port}", port).replace("{proto}", proto).replace("{signal}", signal)


def subwrapper_signal_name(pattern: str, instance: str, proto: str, signal: str) -> str:
    """Sub-wrapper boundary port name (Wrapper > Sub-wrapper port naming).

    ``{instance}`` is the link crossing the boundary: the bus name (an exposed port's
    name, or ``<from>_2_<to>`` for a link between two instances).
    """
    return (
        pattern.replace("{instance}", instance)
        .replace("{proto}", proto)
        .replace("{signal}", signal)
    )


# IEEE 1800-2017 Annex B: not usable as module names.
SV_KEYWORDS = frozenset(
    [
        "accept_on",
        "alias",
        "always",
        "always_comb",
        "always_ff",
        "always_latch",
        "and",
        "assert",
        "assign",
        "assume",
        "automatic",
        "before",
        "begin",
        "bind",
        "bins",
        "binsof",
        "bit",
        "break",
        "buf",
        "bufif0",
        "bufif1",
        "byte",
        "case",
        "casex",
        "casez",
        "cell",
        "chandle",
        "checker",
        "class",
        "clocking",
        "cmos",
        "config",
        "const",
        "constraint",
        "context",
        "continue",
        "cover",
        "covergroup",
        "coverpoint",
        "cross",
        "deassign",
        "default",
        "defparam",
        "design",
        "disable",
        "dist",
        "do",
        "edge",
        "else",
        "end",
        "endcase",
        "endchecker",
        "endclass",
        "endclocking",
        "endconfig",
        "endfunction",
        "endgenerate",
        "endgroup",
        "endinterface",
        "endmodule",
        "endpackage",
        "endprimitive",
        "endprogram",
        "endproperty",
        "endsequence",
        "endspecify",
        "endtable",
        "endtask",
        "enum",
        "event",
        "eventually",
        "expect",
        "export",
        "extends",
        "extern",
        "final",
        "first_match",
        "for",
        "force",
        "foreach",
        "forever",
        "fork",
        "forkjoin",
        "function",
        "generate",
        "genvar",
        "global",
        "highz0",
        "highz1",
        "if",
        "iff",
        "ifnone",
        "ignore_bins",
        "illegal_bins",
        "implements",
        "implies",
        "import",
        "incdir",
        "include",
        "initial",
        "inout",
        "input",
        "inside",
        "instance",
        "int",
        "integer",
        "interconnect",
        "interface",
        "intersect",
        "join",
        "join_any",
        "join_none",
        "large",
        "let",
        "liblist",
        "library",
        "local",
        "localparam",
        "logic",
        "longint",
        "macromodule",
        "matches",
        "medium",
        "modport",
        "module",
        "nand",
        "negedge",
        "nettype",
        "new",
        "nexttime",
        "nmos",
        "nor",
        "noshowcancelled",
        "not",
        "notif0",
        "notif1",
        "null",
        "or",
        "output",
        "package",
        "packed",
        "parameter",
        "pmos",
        "posedge",
        "primitive",
        "priority",
        "program",
        "property",
        "protected",
        "pull0",
        "pull1",
        "pulldown",
        "pullup",
        "pulsestyle_ondetect",
        "pulsestyle_onevent",
        "pure",
        "rand",
        "randc",
        "randcase",
        "randsequence",
        "rcmos",
        "real",
        "realtime",
        "ref",
        "reg",
        "reject_on",
        "release",
        "repeat",
        "restrict",
        "return",
        "rnmos",
        "rpmos",
        "rtran",
        "rtranif0",
        "rtranif1",
        "s_always",
        "s_eventually",
        "s_nexttime",
        "s_until",
        "s_until_with",
        "scalared",
        "sequence",
        "shortint",
        "shortreal",
        "showcancelled",
        "signed",
        "small",
        "soft",
        "solve",
        "specify",
        "specparam",
        "static",
        "string",
        "strong",
        "strong0",
        "strong1",
        "struct",
        "super",
        "supply0",
        "supply1",
        "sync_accept_on",
        "sync_reject_on",
        "table",
        "tagged",
        "task",
        "this",
        "throughout",
        "time",
        "timeprecision",
        "timeunit",
        "tran",
        "tranif0",
        "tranif1",
        "tri",
        "tri0",
        "tri1",
        "triand",
        "trior",
        "trireg",
        "type",
        "typedef",
        "union",
        "unique",
        "unique0",
        "unsigned",
        "until",
        "until_with",
        "untyped",
        "use",
        "uwire",
        "var",
        "vectored",
        "virtual",
        "void",
        "wait",
        "wait_order",
        "wand",
        "weak",
        "weak0",
        "weak1",
        "while",
        "wildcard",
        "wire",
        "with",
        "within",
        "wor",
        "xnor",
        "xor",
    ]
)
