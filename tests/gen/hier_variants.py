# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Hierarchy variants of tests/configs/p2/ahb2apb_nested (from the PR 4 design review).

Each variant re-places the three instances (ahb_bus, aon_apb, br_ahb2aon) and may add a
second clock domain, an alias pin, ties or a different sub-wrapper port pattern.
"""

from pathlib import Path
from typing import Literal

from hdl_bag.ir import Spec, load
from hdl_bag.ir.model import Alias, Clock, Placement, Reset, SubWrapper

CONFIGS = Path(__file__).parents[1] / "configs" / "p2"
NESTED = CONFIGS / "ahb2apb_nested.ir.yaml"
TOP = "demo_ss_bus_top"
KINDS: tuple[tuple[str, Literal["fabric", "bridge"]], ...] = (
    ("ahb_bus", "fabric"), ("aon_apb", "fabric"), ("br_ahb2aon", "bridge"),
)  # fmt: skip


def _hier(spec: Spec, wrappers: list[tuple[str, str]], place: dict[str, tuple[str, str]],
          **wrapper: str) -> Spec:  # fmt: skip
    sw = tuple(SubWrapper(name=n, parent=p) for n, p in wrappers)
    pl = tuple(
        Placement(instance=i, kind=k, wrapper=place[i][0], rtl_name=place[i][1]) for i, k in KINDS
    )
    update: dict[str, object] = {
        "hierarchy": spec.hierarchy.model_copy(update={"wrappers": sw, "placement": pl})
    }
    if wrapper:
        update["wrapper"] = spec.wrapper.model_copy(update=wrapper)
    return spec.model_copy(update=update)


def _two_domains(spec: Spec) -> Spec:
    """aon_apb and its slaves move to a second domain; the bridge then crosses clocks."""
    clock = Clock(name="clk_per", mhz=100, source=None, domain="D_PER", reset="rst_per_n")
    reset = Reset(name="rst_per_n", polarity="active-low", assertion="async",
                  deassertion="sync", source=None, domain="D_PER")  # fmt: skip
    return spec.model_copy(update={
        "clocks": (*spec.clocks, clock),
        "resets": (*spec.resets, reset),
        "fabrics": tuple(f.model_copy(update={"domain": "D_PER"}) if f.name == "aon_apb" else f
                         for f in spec.fabrics),
        "slaves": tuple(s.model_copy(update={"domain": "D_PER"}) if s.name in ("rtc0", "wdt0")
                        else s for s in spec.slaves),
        "bridges": tuple(b.model_copy(update={"cdc": True}) for b in spec.bridges),
    })  # fmt: skip


def _alias_on_ahb(spec: Spec) -> Spec:
    """ahb_bus gets an alias enabled by the boot_remap_i pin."""
    alias = Alias(base=0x0000_8000, size=0x1000, to=0x5000_1000, enable="boot_remap_i")
    fabrics = tuple(
        f.model_copy(update={"decode": (f.decode[0].model_copy(update={"aliases": (alias,)}),)})
        if f.name == "ahb_bus" else f
        for f in spec.fabrics
    )  # fmt: skip
    return spec.model_copy(update={"fabrics": fabrics})


def _options(spec: Spec, port: str, **fields: str) -> Spec:
    opts = tuple(o.model_copy(update=fields) if o.port == port else o for o in spec.port_options)
    return spec.model_copy(update={"port_options": opts})


NESTED2 = [("aon_wrapper", TOP), ("per_wrapper", "aon_wrapper")]


def build(name: str) -> Spec:
    s = load(NESTED)
    variants = {
        "base": lambda: s,
        # top { aon { ahb_bus, per { bridge, aon_apb } } }: the AHB link's LCA is aon.
        "deep": lambda: _hier(s, NESTED2, {"ahb_bus": ("aon_wrapper", "u_ahb_bus"),
                                           "aon_apb": ("per_wrapper", "u_apb_mux"),
                                           "br_ahb2aon": ("per_wrapper", "u_br")}),
        # Three sibling sub-wrappers, two clock domains, the bridge on its own.
        "sibling2dom": lambda: _hier(_two_domains(s),
                                     [("aon_wrapper", TOP), ("per_wrapper", TOP), ("brw", TOP)],
                                     {"ahb_bus": ("aon_wrapper", "u_ahb_bus"),
                                      "aon_apb": ("per_wrapper", "u_apb_mux"),
                                      "br_ahb2aon": ("brw", "u_br")}),
        # The top holds nothing; the AHB link's LCA is aon, where neither end lives.
        "midlca": lambda: _hier(s, [("aon_wrapper", TOP), ("p1", "aon_wrapper"),
                                    ("p2", "aon_wrapper")],
                                {"ahb_bus": ("p1", "u_ahb_bus"), "aon_apb": ("p2", "u_apb_mux"),
                                 "br_ahb2aon": ("p2", "u_br")}),
        # aon uses no clock itself; its child does (pass-through clock and reset).
        "clkpass": lambda: _hier(s, NESTED2, {"ahb_bus": ("per_wrapper", "u_ahb_bus"),
                                              "aon_apb": ("aon_wrapper", "u_apb_mux"),
                                              "br_ahb2aon": (TOP, "u_br")}),
        # boot_remap_i used by ahb_bus two levels down (pass-through input).
        "inputpass": lambda: _alias_on_ahb(_hier(s, NESTED2, {
            "ahb_bus": ("per_wrapper", "u_ahb_bus"), "aon_apb": ("aon_wrapper", "u_apb_mux"),
            "br_ahb2aon": (TOP, "u_br")})),
        "inputonly_sub": lambda: _alias_on_ahb(s),
        # The same RTL instance name in three different wrappers is fine.
        "dup_rtl": lambda: _hier(s, NESTED2, {"ahb_bus": ("aon_wrapper", "u_x"),
                                              "aon_apb": ("per_wrapper", "u_x"),
                                              "br_ahb2aon": (TOP, "u_x")}),
        # Ties on the bridge's downstream end, the mux two levels below the bridge.
        "ties_internal": lambda: _options(s, "br_ahb2aon", pstrb="tie-default",
                                          pslverr="tie-0", prot="tie-0"),
        "naming_rev": lambda: _hier(s, NESTED2, {"ahb_bus": ("aon_wrapper", "u_ahb_bus"),
                                                 "aon_apb": ("per_wrapper", "u_apb_mux"),
                                                 "br_ahb2aon": (TOP, "u_br")},
                                    subwrapper_port_naming="{signal}_{proto}_{instance}"),
        "naming_noproto": lambda: _hier(s, NESTED2, {"ahb_bus": ("aon_wrapper", "u_ahb_bus"),
                                                     "aon_apb": ("per_wrapper", "u_apb_mux"),
                                                     "br_ahb2aon": (TOP, "u_br")},
                                        subwrapper_port_naming="{instance}{proto}{signal}"),
        "empty_sub": lambda: _hier(s, [*NESTED2, ("nothing", TOP)],
                                   {"ahb_bus": ("aon_wrapper", "u_ahb_bus"),
                                    "aon_apb": ("per_wrapper", "u_apb_mux"),
                                    "br_ahb2aon": (TOP, "u_br")}),
        # ---- invalid: the generator must refuse these
        "same_rtl_same_wrapper": lambda: _hier(s, NESTED2, {"ahb_bus": ("aon_wrapper", "u_x"),
                                                            "aon_apb": ("aon_wrapper", "u_x"),
                                                            "br_ahb2aon": (TOP, "u_br")}),
        "rtl_eq_child_inst": lambda: _hier(s, NESTED2, {
            "ahb_bus": ("aon_wrapper", "u_per_wrapper"), "aon_apb": ("per_wrapper", "u_apb_mux"),
            "br_ahb2aon": (TOP, "u_br")}),
        "sub_named_like_template": lambda: _hier(
            s, [("aon_wrapper", TOP), ("bag_apb_mux", "aon_wrapper")],
            {"ahb_bus": ("aon_wrapper", "u_ahb_bus"), "aon_apb": ("bag_apb_mux", "u_apb_mux"),
             "br_ahb2aon": (TOP, "u_br")}),
        "sub_named_like_harness": lambda: _hier(
            s, [("aon_wrapper", TOP), (f"{TOP}_flat", "aon_wrapper")],
            {"ahb_bus": ("aon_wrapper", "u_ahb_bus"), "aon_apb": (f"{TOP}_flat", "u_apb_mux"),
             "br_ahb2aon": (TOP, "u_br")}),
        "sub_named_like_top": lambda: _hier(s, [(TOP, TOP)], {
            "ahb_bus": (TOP, "u_a"), "aon_apb": (TOP, "u_b"), "br_ahb2aon": (TOP, "u_c")}),
        "parent_loop": lambda: _hier(s, [("a", "b"), ("b", "a")], {
            "ahb_bus": ("a", "u_a"), "aon_apb": ("b", "u_b"), "br_ahb2aon": (TOP, "u_c")}),
        "placed_nowhere": lambda: _hier(s, NESTED2, {
            "ahb_bus": ("ghost", "u_a"), "aon_apb": ("per_wrapper", "u_b"),
            "br_ahb2aon": (TOP, "u_c")}),
    }  # fmt: skip
    return variants[name]()


VALID = ["base", "deep", "sibling2dom", "midlca", "clkpass", "inputpass", "inputonly_sub",
         "dup_rtl", "ties_internal", "naming_rev", "naming_noproto", "empty_sub"]  # fmt: skip
INVALID = {
    "same_rtl_same_wrapper": "instance name u_x in aon_wrapper",
    "rtl_eq_child_inst": "instance name u_per_wrapper in aon_wrapper",
    "sub_named_like_template": "sub-wrapper bag_apb_mux has the name of an in-house module",
    "sub_named_like_harness": "has the name of the generated flat harness",
    "sub_named_like_top": "has the name of the top wrapper",
    "parent_loop": "parent loop",
    "placed_nowhere": "placed in ghost, which is not a wrapper",
}
