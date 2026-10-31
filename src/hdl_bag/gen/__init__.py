# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Generators: IR -> RTL, wrapper and collateral.

Output layout (relative to the output directory)::

    rtl/pkg/<package>.sv       parameter package
    rtl/<top>.sv               wrapper (flat or sv-interface ports)
    rtl/<top>_flat.sv          sv-interface style only: flat harness, the lint/compile top
    rtl/<project>_<proto>_if.sv  sv-interface style only: one interface per protocol
    rtl/inhouse/*.sv           copies of the in-house IP the design uses
    <project>.f                file list in compile order
    bag_outputs.json           every file written (with sha256), the top and the file list
"""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from hdl_bag.gen.netlist import build_netlist
from hdl_bag.gen.package import package
from hdl_bag.gen.render import header
from hdl_bag.gen.wrapper import render_harness, render_interface, render_wrapper
from hdl_bag.ir.model import Spec
from hdl_bag.protocols import PROTO_TOKEN
from hdl_bag.templates import builtin_registry

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


def _write(root: Path, rel: str, text: str, written: list[str]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    written.append(rel)


def generate(spec: Spec, outdir: Path, spec_dir: Path, style: str | None = None) -> Outputs:
    """Write every output of SPEC under OUTDIR. Raises MissingRtl after the collateral."""
    style = style or spec.wrapper.port_style
    user_header = None
    if spec.wrapper.file_header:
        user_header = (spec_dir / spec.wrapper.file_header).read_text(encoding="utf-8")
    hdr = header(spec, user_header)
    written: list[str] = []

    pkg_rel = f"rtl/pkg/{spec.wrapper.package}.sv"
    _write(outdir, pkg_rel, package(spec, hdr), written)

    registry = builtin_registry()
    netlist = build_netlist(spec)
    used = sorted({i.template for i in netlist.instances})
    missing = [t for t in used if not (m := registry.get(t)) or not m.rtl]
    if missing:
        _manifest(outdir, written, None, None)
        raise MissingRtl(missing)

    srcs: list[str] = list(_WRAPPER_CELLS)
    for t in used:
        manifest = registry.get(t)
        assert manifest is not None
        srcs += [s for s in manifest.sources if s not in srcs]
    pkg_root = files("hdl_bag")
    for rel in sorted(srcs):
        _write(outdir, rel, (pkg_root / rel).read_text(encoding="utf-8"), written)

    top = spec.hierarchy.top
    top_rel = f"rtl/{top}.sv"
    _write(outdir, top_rel, render_wrapper(netlist, style, hdr), written)
    order = [pkg_rel, *sorted(srcs)]
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
    _write(outdir, filelist, "".join(f"{p}\n" for p in order), written)
    _manifest(outdir, written, lint_top, filelist)
    return Outputs(outdir, tuple(sorted(written)), lint_top, filelist)


MANIFEST = "bag_outputs.json"


def load_outputs(root: Path) -> Outputs:
    """The Outputs that a previous generate() recorded in ROOT."""
    data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if not data.get("top"):
        raise ValueError(f"{root}: no RTL was generated (see {MANIFEST})")
    return Outputs(root, tuple(sorted(data["files"])), data["top"], data["filelist"])


def sources(out: Outputs) -> list[Path]:
    """OUT's file list, relative to its root, in compile order."""
    return [Path(line) for line in (out.root / out.filelist).read_text().split()]


def _manifest(root: Path, written: list[str], top: str | None, filelist: str | None) -> None:
    entries = {r: hashlib.sha256((root / r).read_bytes()).hexdigest() for r in sorted(written)}
    data = {"files": entries, "top": top, "filelist": filelist}
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with (root / MANIFEST).open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
