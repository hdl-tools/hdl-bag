# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The fixed-priority muxes (P3-3) are exactly scripts/derive_mux.py applied to the vendored
PULP muxes, so a PULP bump that changes the upstream mux shows up here."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).parents[2]


def _derive() -> ModuleType:
    spec = importlib.util.spec_from_file_location("derive_mux", REPO / "scripts" / "derive_mux.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["derive_mux"] = module
    spec.loader.exec_module(module)
    return module


def test_derived_muxes_are_up_to_date() -> None:
    assert _derive().stale() == [], "run `uv run python scripts/derive_mux.py`"


def test_derivation_changes_only_what_it_documents() -> None:
    derive = _derive()
    for path, text in derive.expected().items():
        assert text.startswith("// SPDX-License-Identifier: SHL-0.51\n")
        assert "Copyright (c) " in text and "Solderpad Hardware" in text  # upstream notice kept
        assert text.count("bag_prio_arb #(") == 2 and "rr_arb_tree" not in text.split("\n", 9)[9]
        assert "_intf" not in text.split("module ", 1)[1].split("(", 1)[0]
        assert f"endmodule : {path.stem}" in text
