# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Shared test setup: the cocotb helpers in tests/rtl (BFMs, runner) and the config DSL in
tests/configs and the mutation catalogue in tests/mutation are importable."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "rtl"))
sys.path.insert(0, str(Path(__file__).parent / "configs"))
sys.path.insert(0, str(Path(__file__).parent / "mutation"))
