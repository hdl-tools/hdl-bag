# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Derive the fixed-priority-capable muxes from the vendored PULP muxes (owner decision P3-3).

    uv run python scripts/derive_mux.py          # rewrite src/hdl_bag/rtl/vendor/derived/*.sv
    uv run python scripts/derive_mux.py --check  # fail if they differ from a fresh derivation

The transformation is mechanical and is the only modification of the upstream files:

* a header records the origin, the licence (SHL-0.51, upstream copyright kept) and the
  changes;
* the module is renamed (``axi_mux`` -> ``bag_axi_mux``, ``axi_lite_mux`` -> ``bag_axil_mux``)
  and gains ``parameter bit FixedPrio``;
* both ``rr_arb_tree`` arbiters (AW, AR) become ``bag_prio_arb`` with ``FixedPrio``
  instead of ``LockIn = 1``; bag_prio_arb is rr_arb_tree with LockIn when FixedPrio = 0;
* the ``*_intf`` interface wrapper module after the main module is dropped.

The result is almost entirely upstream code, so it lives next to the vendored tree in
``rtl/vendor/derived/`` and is linted as third-party RTL (P3-2). ``bag_prio_arb``, the
part hdl-bag wrote, is in ``rtl/inhouse/`` and is linted like all in-house code.
tests/vendor/test_derived_mux.py runs this check, so a PULP bump that changes the
upstream mux shows up as a failing test.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "src" / "hdl_bag" / "rtl" / "vendor"
DERIVED_DIR = VENDOR / "derived"

# upstream module -> derived module
DERIVED = {"axi_mux": "bag_axi_mux", "axi_lite_mux": "bag_axil_mux"}

HEADER = """\
// SPDX-License-Identifier: SHL-0.51
// Derived by The hdl-bag Authors from pulp-platform/axi v{version} src/{module}.sv,
// commit {revision},
// by scripts/derive_mux.py; do not edit by hand. Changes: module renamed {derived};
// parameter FixedPrio added; the AW and AR rr_arb_tree arbiters are replaced by
// bag_prio_arb (rr_arb_tree with LockIn when FixedPrio = 0; the lowest index wins when
// FixedPrio = 1); the {module}_intf wrapper is dropped. The upstream copyright and
// licence notice follow unchanged.
"""


def derive(module: str, text: str, version: str, revision: str) -> str:
    derived = DERIVED[module]
    body = text[: text.index("// interface wrap")].rstrip() + "\n"
    head = f"module {module} #(\n"
    assert body.count(head) == 1, f"{module}: module header not found"
    body = body.replace(
        head,
        f"module {derived} #(\n"
        "  parameter bit          FixedPrio     = 1'b0, // hdl-bag: lowest index wins\n",
    )
    arbiters = len(re.findall(r"\brr_arb_tree #\(", body))
    locks = len(re.findall(r"\.LockIn\s*\(\s*1'b1\s*\)", body))
    assert arbiters == locks == 2, f"{module}: expected 2 locked arbiters, found {arbiters}"
    body = re.sub(r"\brr_arb_tree #\(", "bag_prio_arb #(", body)
    body = re.sub(r"\.LockIn(\s*)\(\s*1'b1\s*\)", r".FixedPrio\1( FixedPrio  )", body)
    body = re.sub(r"^endmodule[ \t]*$", f"endmodule : {derived}", body, flags=re.M)
    hdr = HEADER.format(version=version, revision=revision, module=module, derived=derived)
    return hdr + body


def expected() -> dict[Path, str]:
    index = json.loads((VENDOR / "pulp" / "index.json").read_text(encoding="utf-8"))
    pkg = index["packages"]["axi"]
    out = {}
    for module, derived in DERIVED.items():
        text = (VENDOR / "pulp" / "axi" / "src" / f"{module}.sv").read_text(encoding="utf-8")
        out[DERIVED_DIR / f"{derived}.sv"] = derive(module, text, pkg["version"], pkg["revision"])
    return out


def stale() -> list[str]:
    """Derived files that differ from a fresh derivation."""
    return [
        p.name
        for p, text in expected().items()
        if not p.is_file() or p.read_text(encoding="utf-8") != text
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        names = stale()
        if names:
            print(f"stale derived mux(es): {', '.join(names)}; run scripts/derive_mux.py")
            return 1
        return 0
    for path, text in expected().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
