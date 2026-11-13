# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Shared test setup: the cocotb helpers in tests/rtl (BFMs, runner) and the config DSL in
tests/configs, the mutation catalogue and controls in tests/mutation, and the formal tests in
tests/flows are importable (the formal corpus reads which jobs those run)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rtl"))
sys.path.insert(0, str(Path(__file__).parent / "configs"))
sys.path.insert(0, str(Path(__file__).parent / "mutation"))
sys.path.insert(0, str(Path(__file__).parent / "flows"))
sys.path.insert(0, str(Path(__file__).parent))


def pytest_collection_modifyitems(config: object, items: list) -> None:  # type: ignore[type-arg]
    """Mark the EDA tests of the per-PR smoke subset (tests/smoke.py). A deep test (nightly
    only, D74 and D76) is never in it, even under a pattern such as tests/flows/*."""
    import pytest

    from smoke import is_smoke

    for item in items:
        deep = item.get_closest_marker("deep")
        if item.get_closest_marker("eda") and not deep and is_smoke(item.nodeid):
            item.add_marker(pytest.mark.smoke)
