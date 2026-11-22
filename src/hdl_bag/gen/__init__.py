# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generators: IR -> RTL, wrapper and collateral.

Output layout (relative to the output directory)::

    rtl/pkg/<package>.sv       parameter package
    rtl/<top>.sv               wrapper (flat or sv-interface ports)
    rtl/<prefix>_<sub-wrapper>.sv  one per Hierarchy sub-wrapper (flat boundary ports)
    rtl/<top>_flat.sv          sv-interface style only: flat harness, the lint/compile top
    rtl/<project>_<proto>_if.sv  sv-interface style only: one interface per protocol
    rtl/inhouse/<prefix>_*.sv  copies of the in-house IP the design uses, with their modules
                               renamed
    rtl/vendor/derived/<prefix>_*.sv  the modules BAG derives from PULP, likewise
    rtl/vendor/pulp/...        copies of the vendored PULP files the design uses, with their units
                               and macros renamed; the headers are ``<prefix>_*.svh``
    rtl/vendor/pulp/<pkg>/LICENSE  the licence of each vendored package those files are from
    rtl/vendor/licences/<id>.txt   the text of any other licence a vendored file is under
    <project>.f                ``+incdir+<dir>`` lines, then the file list in compile order
    sw/<project>_memmap.h      C header: <SLAVE>_<REGION>_BASE / _SIZE (Generate C header)
    doc/memmap.md, .html       memory-map document (Generate memory-map doc)
    doc/port_map.md            per-fabric port map (Generate memory-map doc)
    doc/reset.md               reset sequence: domains, crossings, reset groups (D51)
    constraints/<project>_cdc.sdc, .xdc  clocks and CDC timing exceptions, one per crossing
                               (hdl_bag.gen.constraints); only with RTL, they name its instances
    tests/<project>_tests.py   cocotb tests for this config (Generate cocotb unit tests)
    tests/<project>.ir.yaml    the IR those tests read
    tests/<top>_ready_shim.sv  drives AXI READY from VALID, for `bag sim --ready-shim` (D38)
    formal/<project>_reset.sv, .sby  quiet-in-reset properties and their SymbiYosys job
                               (Generate formal; see hdl_bag.formal_gen)
    formal/<project>_protocol.sv, .sby  AXI4-Lite and APB port-protocol checkers (D60)
    formal/vendor/zipcpu/      the ZipCPU checkers they use, with their LICENSE and SOURCE.md
    formal/<prefix>_bag_step.v, <prefix>_bag_faxi4.sv, <prefix>_bag_fahb.sv  BAG's own checkers
    bag_outputs.json           every file written (with sha256), the top and the file list

With a blank Project > Module prefix the ``<prefix>_`` parts are absent and the copies are
byte for byte the packaged files. The top wrapper, the parameter package, the flat harness, the
generated interfaces and the generated formal files keep their names; every unit BAG packages,
vendored PULP and ZipCPU included, takes the prefix, and so do the macros the packaged files
define and the file names of the four vendored headers, with the include lines that name them
(``gen.prefix``). Include directories keep their names.
"""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from hdl_bag.gen.collateral import (
    c_header,
    memmap_html,
    memmap_md,
    port_map_md,
    reset_md,
    tieoffs_md,
)
from hdl_bag.gen.constraints import SDC, XDC, cdc_constraints, tcl_banner
from hdl_bag.gen.external import opaque_declarations, opaque_module, render_stub
from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.package import package
from hdl_bag.gen.prefix import out_path, source_note, transform
from hdl_bag.gen.render import GenerateError, header
from hdl_bag.gen.wrapper import TieOff, render_harness, render_interface, render_modules
from hdl_bag.ir import dumps
from hdl_bag.ir.model import Spec
from hdl_bag.protocols import PROTO_TOKEN
from hdl_bag.templates import builtin_registry
from hdl_bag.templates.sources import VENDOR, licence_texts, resolve_sources

# In-house cells the wrapper itself may instantiate (reset conditioning).
_WRAPPER_CELLS = ("rtl/inhouse/bag_rst_sync.sv",)


class MissingRtl(Exception):
    """Templates the spec uses that have no RTL yet; collateral was still written."""

    def __init__(self, templates: list[str]) -> None:
        super().__init__(f"no RTL yet for template(s): {', '.join(templates)}")
        self.templates = templates


@dataclass(frozen=True)
class Outputs:
    root: Path
    files: tuple[str, ...]  # posix paths relative to root
    top: str  # lint / compile top module
    filelist: str
    tests: str | None = None  # the generated cocotb module, if any
    lint_tools: tuple[str, ...] = ("slang", "verilator", "verible")  # Project > Lint tools
    simulator: str = "verilator"  # Project > Simulator
    formal: tuple[str, ...] = ()  # the generated SymbiYosys jobs (formal/*.sby), if any


def _write(root: Path, rel: str, text: str, written: list[str]) -> None:
    if rel in written:
        raise GenerateError(f"{rel} would be written twice (a design-unit name collision)")
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    written.append(rel)


def generate(spec: Spec, outdir: Path, spec_dir: Path, style: str | None = None) -> Outputs:
    """Write every output of SPEC under OUTDIR. Raises MissingRtl after the collateral.

    With Project > Generate RTL = N (D100), only the C header and the documents that need
    no RTL are written: no package, RTL, file list, tie-off report, tests or formal, and
    the manifest records no top, so lint, sim and formal say there is no RTL."""
    style = style or spec.wrapper.port_style
    user_header = None
    if spec.wrapper.file_header:
        user_header = (spec_dir / spec.wrapper.file_header).read_text(encoding="utf-8")
    hdr = header(spec, user_header)
    written: list[str] = []
    p = spec.project

    # The package first: its name check is the earliest guard against colliding names.
    pkg_rel = f"rtl/pkg/{spec.wrapper.package}.sv"
    if p.outputs.rtl:
        _write(outdir, pkg_rel, package(spec, hdr), written)
    if spec.project.outputs.c_header:
        _write(outdir, f"sw/{spec.project.name}_memmap.h", c_header(spec, user_header), written)
    if spec.project.outputs.doc:
        _write(outdir, "doc/memmap.md", memmap_md(spec, user_header), written)
        _write(outdir, "doc/memmap.html", memmap_html(spec, user_header), written)
        _write(outdir, "doc/port_map.md", port_map_md(spec, user_header), written)
        _write(outdir, "doc/reset.md", reset_md(spec, user_header), written)
    if not p.outputs.rtl:
        _manifest(outdir, written, spec, None, None, None)
        return Outputs(outdir, tuple(sorted(written)), "", "", None, p.lint_tools, p.simulator)

    registry = builtin_registry()
    netlist = build_netlist(spec)
    used = sorted({i.template for i in netlist.instances})
    # An opaque template (bag_external) has no RTL of BAG's own: the user supplies it.
    missing = [t for t in used if not (m := registry.get(t)) or not (m.rtl or m.opaque)]
    if missing:
        _manifest(outdir, written, spec, None, None, None)
        raise MissingRtl(missing)
    top = spec.hierarchy.top

    refs: list[str] = list(_WRAPPER_CELLS)
    for t in used:
        manifest = registry.get(t)
        assert manifest is not None
        refs += [s for s in manifest.sources if s not in refs]
    src = resolve_sources(refs)
    prefix = spec.project.module_prefix
    srcs = [out_path(rel, prefix) for rel in src.files]  # compile order is kept
    pkg_root = files("hdl_bag")
    for rel in sorted([*src.files, *src.headers]):
        text = transform(rel, (pkg_root / rel).read_text(encoding="utf-8"), prefix)
        _write(outdir, out_path(rel, prefix), text, written)
    # The licence of each vendored package travels with its files (not in the file list).
    vendored = {rel.split("/")[3] for rel in [*srcs, *src.headers] if rel.startswith(f"{VENDOR}/")}
    for name in sorted(vendored):
        rel = f"{VENDOR}/{name}/LICENSE"
        _write(outdir, rel, (pkg_root / rel).read_text(encoding="utf-8"), written)
    # A file under another licence than its package's brings that text too (not a source).
    for rel in licence_texts([*src.files, *src.headers]):
        _write(outdir, rel, (pkg_root / rel).read_text(encoding="utf-8"), written)

    # Stubs for opaque fabrics: the user replaces them with the fabric's RTL.
    stubs: dict[str, tuple[str, list[str]]] = {}
    for inst in netlist.instances:
        manifest = registry.get(inst.template)
        if inst.fabric is not None and manifest is not None and manifest.opaque:
            rel = f"rtl/ext/{opaque_module(spec, inst.fabric)}.sv"
            declared = opaque_declarations(spec, inst)
            if rel in stubs:  # a shared External module (D45): one module, so one contract
                first, ports = stubs[rel]
                if ports != declared:
                    differ = sorted(set(ports) ^ set(declared))[:4]
                    raise GenerateError(
                        f"{inst.name} and {first} share a module but their ports differ "
                        f"(for example through ID widths): {differ or 'their order'}"
                    )
                continue
            stubs[rel] = (inst.name, declared)
            _write(outdir, rel, render_stub(spec, inst, hdr), written)
            srcs.append(rel)

    # Sub-wrappers (children first), then the top.
    module_rels = []
    tieoffs: set[TieOff] = set()
    for name, text in render_modules(netlist, style, hdr, tieoffs):
        rel = f"rtl/{name}.sv"
        _write(outdir, rel, text, written)
        module_rels.append(rel)
    if spec.project.outputs.doc:
        _write(outdir, "doc/tieoffs.md", tieoffs_md(spec, tieoffs, user_header), written)
    top_rel = module_rels[-1]
    order = [pkg_rel, *srcs, *module_rels[:-1]]
    lint_top = top
    if style == "sv-interface":
        interfaces = []
        for proto in sorted({b.protocol for b in netlist.ports if b.external}):
            rel = f"rtl/{spec.project.name}_{PROTO_TOKEN[proto]}_if.sv"
            _write(outdir, rel, render_interface(spec, proto, hdr), written)
            interfaces.append(rel)
        order[1:1] = sorted(interfaces)
        lint_top = f"{top}_flat"
        harness_rel = f"rtl/{lint_top}.sv"
        _write(outdir, harness_rel, render_harness(netlist, hdr), written)
        order += [top_rel, harness_rel]
    else:
        order.append(top_rel)
    filelist = f"{spec.project.name}.f"
    incdirs = "".join(f"+incdir+{d}\n" for d in src.incdirs)
    _write(outdir, filelist, incdirs + "".join(f"{p}\n" for p in order), written)

    banner = tcl_banner(spec, user_header)
    for fmt in (SDC, XDC):
        text = cdc_constraints(spec, netlist, fmt, banner)
        _write(outdir, f"constraints/{spec.project.name}_cdc.{fmt}", text, written)
    # The tests drive the fabrics through the top, so a user-supplied fabric, whose behaviour
    # BAG does not know, has none (D8).
    opaque = any((m := registry.get(t)) and m.opaque for t in used)
    tests = None
    if spec.project.outputs.tests and not opaque:
        from hdl_bag.tests_gen import ready_shim_module, render_ready_shim, render_tests

        tests = f"tests/{spec.project.name}_tests.py"
        _write(outdir, tests, render_tests(spec, user_header), written)
        _write(outdir, f"tests/{spec.project.name}.ir.yaml", dumps(spec), written)
        top_text = (outdir / f"rtl/{lint_top}.sv").read_text(encoding="utf-8")
        shim = render_ready_shim(spec, lint_top, top_text, user_header)
        if shim is not None:
            _write(outdir, f"tests/{ready_shim_module(lint_top)}.sv", shim, written)
    # Formal proves the fabrics' behaviour, so a user-supplied fabric has none either (D8).
    sby: tuple[str, ...] = ()
    if spec.project.outputs.formal and not opaque:
        from hdl_bag.formal_gen import render_formal, vendored_files

        for rel, text in render_formal(spec, netlist, lint_top, order, list(src.incdirs),
                                       user_header):  # fmt: skip
            _write(outdir, rel, text, written)
        for rel in vendored_files(spec, netlist):  # the ZipCPU checkers, with their LICENSE
            text = transform(rel, (pkg_root / rel).read_text(encoding="utf-8"), prefix)
            if rel.endswith("/SOURCE.md"):
                text = source_note(text, prefix)
            _write(outdir, out_path(rel, prefix), text, written)
        sby = tuple(sorted(r for r in written if r.startswith("formal/") and r.endswith(".sby")))
    _manifest(outdir, written, spec, lint_top, filelist, tests, sby)
    return Outputs(outdir, tuple(sorted(written)), lint_top, filelist, tests, p.lint_tools,
                   p.simulator, tuple(sorted(sby)))  # fmt: skip


MANIFEST = "bag_outputs.json"


def load_outputs(root: Path) -> Outputs:
    """The Outputs that a previous generate() recorded in ROOT. Raises ValueError, with a
    message for the user, when ROOT is not an output directory of bag generate."""
    path = root / MANIFEST
    if not path.is_file():
        raise ValueError(
            f"{root}: no {MANIFEST} here: give the output directory that bag generate wrote"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{root}: {MANIFEST} cannot be read: run bag generate again") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{root}: {MANIFEST} cannot be read: run bag generate again")
    if not data.get("top"):
        raise ValueError(f"{root}: no RTL was generated (see {MANIFEST})")
    defaults = Outputs(root, (), "", "")
    tools = data.get("lint_tools", defaults.lint_tools)
    if not (isinstance(data["top"], str) and isinstance(data.get("filelist"), str)
            and isinstance(data.get("files"), dict | list)
            and isinstance(tools, list | tuple)
            and all(isinstance(t, str) for t in tools)):  # fmt: skip
        # lint_tools included: tuple("slang") is five one-character tool names, which reached
        # lint() as unknown tools and left bag lint showing the ValueError.
        raise ValueError(f"{root}: {MANIFEST} cannot be read: run bag generate again")
    return Outputs(
        root, tuple(sorted(data["files"])), data["top"], data["filelist"], data.get("tests"),
        tuple(tools),
        data.get("simulator", defaults.simulator), tuple(data.get("formal", ())),
    )  # fmt: skip


def _filelist(out: Outputs) -> list[str]:
    """The whitespace-separated tokens of OUT's file list.

    Raises ValueError, with a message for the user, when the manifest names a file list that
    is not there: lint, sim and flow all read it and each gave a raw FileNotFoundError. The
    check lives here, not in load_outputs, because `bag formal` never reads the file list --
    each generated job carries its own sources -- so a missing one must not stop a design it
    can still prove.
    """
    path = out.root / out.filelist
    try:
        return path.read_text().split()
    except OSError as exc:
        raise ValueError(
            f"{out.root}: the file list {out.filelist} cannot be read: run bag generate again"
        ) from exc


def sources(out: Outputs) -> list[Path]:
    """OUT's file list, relative to its root, in compile order."""
    return [Path(t) for t in _filelist(out) if t[0] != "+"]


def include_dirs(out: Outputs) -> list[Path]:
    """OUT's include directories (``+incdir+`` lines of the file list), relative to its root."""
    return [Path(t.removeprefix("+incdir+")) for t in _filelist(out) if t.startswith("+incdir+")]


def _manifest(
    root: Path,
    written: list[str],
    spec: Spec,
    top: str | None,
    filelist: str | None,
    tests: str | None,
    formal: tuple[str, ...] = (),
) -> None:
    # Remove files a previous run wrote that this run did not (e.g. after a style change).
    old = root / MANIFEST
    try:
        listed = (
            json.loads(old.read_text(encoding="utf-8")).get("files", {}) if old.is_file() else {}
        )
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        listed = {}  # an unreadable manifest lists nothing to clean up; this run replaces it
    for rel in listed if isinstance(listed, dict | list) else ():
        if isinstance(rel, str) and rel not in written:
            (root / rel).unlink(missing_ok=True)
    entries = {r: hashlib.sha256((root / r).read_bytes()).hexdigest() for r in sorted(written)}
    data = {
        "files": entries,
        "top": top,
        "filelist": filelist,
        "tests": tests,
        "formal": list(formal),
        "lint_tools": list(spec.project.lint_tools),
        "simulator": spec.project.simulator,
    }
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with (root / MANIFEST).open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
