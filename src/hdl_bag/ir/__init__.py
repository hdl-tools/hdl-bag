# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""The YAML IR: canonical spec, resolved from a checked workbook."""

from hdl_bag.ir.io import dump, dumps, load, loads
from hdl_bag.ir.model import Spec
from hdl_bag.ir.resolve import ResolveError, resolve

__all__ = ["ResolveError", "Spec", "dump", "dumps", "load", "loads", "resolve"]
