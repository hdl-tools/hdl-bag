# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Structural connectivity oracle for generated wrappers (flat style), no EDA tools.

Adapted from the PR 4 verification review. The expectation comes from the IR alone:
the wrapper tree and placements (spec.hierarchy), each bus's two ends, their LCA by
plain ancestor-set intersection, and the ports every wrapper on the way down must have.
It deliberately does not use gen/hierarchy.py. The generated modules are parsed into a
union-find over (instance path, net) and checked:

1. every sub-wrapper has exactly the expected boundary ports (name, direction, width);
2. each (bus, signal) is one flat net, and distinct (bus, signal) pairs never share one;
3. every child port is connected, and connections name existing ports and nets;
4. clocks, conditioned resets and extra inputs are one net each and reach exactly the
   sub-wrappers whose subtree uses them;
5. no instance name repeats within a module.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from hdl_bag.gen.netlist import Bus, build_netlist
from hdl_bag.ir import Spec
from hdl_bag.naming import flat_signal_name, subwrapper_signal_name
from hdl_bag.protocols import PROTO_TOKEN, signal_width, towards_slave

PORT = re.compile(r"^\s+(input|output)\s+logic\s+(?:\[(\d+):0\]\s+)?(\w+),?$")
CONN = re.compile(r"^\s+\.(\w+)\((.+?)\),?$")
# An instantiation: "  mod [#(...)] u_name (" ... "  );" (an empty one has no lines).
INSTANCE = re.compile(
    r"^  (\w+)(?: #\(.*?\n  \))? (u_\w+) \((?:\n(      .*?))?\n  \);", re.M | re.S
)


@dataclass
class Module:
    ports: dict[str, tuple[str, int]]
    decls: set[str]
    kids: dict[str, tuple[str, dict[str, str]]]  # instance -> (module, {port: expr})
    body: str
    instance_names: list[str] = field(default_factory=list)


def parse(text: str) -> tuple[str, Module]:
    start = text.index("\nmodule ")
    head = text[start : text.index("\n);", start)]
    name = head.split()[1]
    ports = {}
    for line in head.splitlines():
        if m := PORT.match(line):
            ports[m[3]] = (m[1], int(m[2]) + 1 if m[2] else 1)
    body = text[text.index("\n);", start) :]
    decls = set(re.findall(r"^\s+logic\s+(?:\[[^\]]+\]\s*)*(\w+);", body, re.M))
    kids: dict[str, tuple[str, dict[str, str]]] = {}
    names = []
    for m in INSTANCE.finditer(body):
        names.append(m[2])
        conns = {c[1]: c[2] for line in (m[3] or "").splitlines() if (c := CONN.match(line))}
        kids[m[2]] = (m[1], conns)
    return name, Module(ports, decls, kids, body, names)


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        self.parent[self.find(a)] = self.find(b)


def check(spec: Spec, rtl: Path) -> list[str]:
    errs: list[str] = []
    netlist = build_netlist(spec)
    top = spec.hierarchy.top
    parent = {w.name: w.parent for w in spec.hierarchy.wrappers}

    def up(w: str) -> list[str]:
        out = [w]
        while out[-1] != top:
            out.append(parent[out[-1]])
        return out

    mods: dict[str, Module] = {}
    for f in sorted(rtl.glob("*.sv")):
        name, mod = parse(f.read_text())
        mods[name] = mod
        for dup in sorted({n for n in mod.instance_names if mod.instance_names.count(n) > 1}):
            errs.append(f"{name}: duplicate instance name {dup}")
    if top not in mods:
        return [f"top module {top} missing"]

    # ---- instance tree and connectivity
    tree: dict[str, str] = {}  # instance path -> module
    uf = UnionFind()

    def walk(path: str, name: str) -> None:
        tree[path] = name
        mod = mods[name]
        for inst, (child, conns) in mod.kids.items():
            if child not in mods:
                continue  # an in-house cell
            cpath = f"{path}/{inst}"
            walk(cpath, child)
            cports = mods[child].ports
            errs.extend(f"{cpath}: port {p} unconnected" for p in cports if p not in conns)
            for p, expr in conns.items():
                if p not in cports:
                    errs.append(f"{cpath}: connection to missing port {p}")
                    continue
                if expr not in mod.ports and expr not in mod.decls:
                    errs.append(f"{path}: {expr} (to {cpath}.{p}) is not declared")
                uf.union((cpath, p), (path, expr))
                if expr in mod.ports and mod.ports[expr] != cports[p]:
                    errs.append(f"{cpath}.{p} {cports[p]} != parent port {expr} {mod.ports[expr]}")

    walk("T", top)
    paths_of: dict[str, list[str]] = defaultdict(list)
    for path, name in tree.items():
        paths_of[name].append(path)

    # ---- expected buses
    buses: dict[str, Bus] = {}
    ends: dict[str, list[tuple[str, bool]]] = defaultdict(list)  # (wrapper, is master end)
    for b in netlist.ports:
        buses[b.name] = b
        if b.external:
            ends[b.name].append((top, not b.is_slave))
    for i in netlist.instances:
        for b, master in ((b, False) for b in i.masters):
            buses[b.name] = b
            ends[b.name].append((i.wrapper, master))
        for b in i.slaves:
            buses[b.name] = b
            ends[b.name].append((i.wrapper, True))
        if i.up is not None:
            buses[i.up.name] = i.up
            ends[i.up.name].append((i.wrapper, False))
        if i.down is not None:
            buses[i.down.name] = i.down
            ends[i.down.name].append((i.wrapper, True))

    prefixes = {p.port: p.prefix for p in spec.wrapper.ports}
    expected: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
    nodes: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for name, es in ends.items():
        b = buses[name]
        if len(es) != 2:
            errs.append(f"bus {name}: {len(es)} ends")
            continue
        chains = [up(w) for w, _ in es]
        lca = next(w for w in chains[0] if w in chains[1])
        signals = [s for s in b.signals if s not in b.ties]
        for (_, is_master), chain in zip(es, chains, strict=True):
            for w in chain[: chain.index(lca)]:
                for s in signals:
                    port = subwrapper_signal_name(spec.wrapper.subwrapper_port_naming, name,
                                                  PROTO_TOKEN[b.protocol], s)  # fmt: skip
                    direction = "output" if towards_slave(s, b.is_slave) == is_master else "input"
                    width = signal_width(s, b.addr_width, b.data_width, b.id_width, b.user_width)
                    if port in expected[w]:
                        errs.append(f"{w}: port {port} expected twice")
                    expected[w][port] = (direction, width)
                    nodes[(name, s)].append((w, port))
        for s in signals:
            if lca == top and b.external:
                pattern, proto = spec.wrapper.signal_pattern, PROTO_TOKEN[b.protocol]
                local = flat_signal_name(pattern, name, proto, s, prefixes.get(name))
            else:
                local = f"{name}_{s}"
            nodes[(name, s)].append((lca, local))

    # Exposed user ports of user-supplied fabrics (D41): a top-level port <inst>_<port>,
    # the same port on every sub-wrapper from the instance's home up, and one net down to
    # the instance's pin.
    homes = {p.instance: (p.wrapper, p.rtl_name) for p in spec.hierarchy.placement}
    for u in spec.user_ports:
        if u.connection != "expose":
            continue
        name = f"{u.instance}_{u.port}"
        home, rtl_name = homes.get(u.instance, (top, f"u_{u.instance}"))
        for w in up(home)[:-1]:
            expected[w][name] = (u.direction, u.width)
        nodes[(f"user {u.instance}", u.port)] = [(w, name) for w in up(home)]
        # Leaf instances pad pin names into a column: ".irq_o    (noc_irq_o)".
        block = re.search(rf"^  \w+ {rtl_name} \((.*?)\n  \);", mods[home].body, re.M | re.S)
        pins = dict(re.findall(r"\.(\w+)\s*\((.+?)\)", block[1])) if block else {}
        pin = pins.get(u.port)
        if pin != name:
            errs.append(f"{home}.{rtl_name}.{u.port} is connected to {pin}, expected {name}")

    fixed = [c.name for c in spec.clocks] + [f"bag_rst_{c.domain}_n" for c in spec.clocks]
    fixed += list(spec.wrapper.extra_inputs)

    # 1. sub-wrapper boundary ports
    for w in set(tree.values()) - {top}:
        got = {k: v for k, v in mods[w].ports.items() if k not in fixed}
        exp = expected.get(w, {})
        errs += [f"{w}: spurious port {k}" for k in sorted(set(got) - set(exp))]
        errs += [f"{w}: missing port {k}" for k in sorted(set(exp) - set(got))]
        errs += [f"{w}: port {k} is {got[k]}, expected {exp[k]}"
                 for k in sorted(set(exp) & set(got)) if exp[k] != got[k]]  # fmt: skip

    # 2. one flat net per (bus, signal)
    owner: dict[tuple[str, str], tuple[str, str]] = {}
    for key, places in nodes.items():
        reps = set()
        for w, local in places:
            for path in paths_of.get(w, []):
                if local not in mods[w].ports and local not in mods[w].decls:
                    errs.append(f"{w}: {local} ({key[0]}.{key[1]}) is neither port nor net")
                reps.add(uf.find((path, local)))
        if len(reps) != 1:
            errs.append(f"{key[0]}.{key[1]}: {len(reps)} separate nets")
        for r in reps:
            if r in owner and owner[r] != key:
                errs.append(f"{key[0]}.{key[1]} shares a net with {owner[r][0]}.{owner[r][1]}")
            owner[r] = key

    # 4. clocks, resets, inputs: used by leaf instances, routed only where needed
    users: dict[str, set[str]] = defaultdict(set)
    for name, mod in mods.items():
        leaves = mod.body
        for inst, (child, _) in mod.kids.items():
            if child in mods:  # a sub-wrapper instance: not a use by this module
                leaves = re.sub(
                    rf"^  {child} {inst} \((?:\n      .*?)?\n  \);", "", leaves, flags=re.M | re.S
                )
        for n in fixed:
            if re.search(rf"\(\s*{re.escape(n)}\s*\)", leaves):
                users[n].add(name)

    def subtree_uses(name: str, n: str) -> bool:
        return name in users[n] or any(
            subtree_uses(child, n) for child, _ in mods[name].kids.values() if child in mods
        )

    for n in fixed:
        classes = set()
        for path, name in tree.items():
            has = n in mods[name].ports
            if name != top and has != subtree_uses(name, n):
                errs.append(f"{name}: {'unneeded' if has else 'missing'} port {n}")
            if has or (name == top and (n in mods[top].ports or n in mods[top].decls)):
                classes.add(uf.find((path, n)))
        if len(classes) > 1:
            errs.append(f"{n}: {len(classes)} separate nets")
    return errs
