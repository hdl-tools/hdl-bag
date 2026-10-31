# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Structural signature of a workbook, for comparing workbooks without byte equality.

Covers what the schema depends on: sheet order, cell values and formulas, header
comments, data validations, conditional formats, freeze panes and column widths.
Cached formula results, styles and zip metadata are ignored.
"""

import re
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

# Spreadsheet apps rewrite the constants TRUE/FALSE as functions when they re-save.
_BOOL_FN = re.compile(r"\b(TRUE|FALSE)\(\)")


def _norm(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("="):
        return _BOOL_FN.sub(r"\1", value)
    return value


def signature(wb: Workbook) -> dict[str, Any]:
    sheets: dict[str, Any] = {}
    for ws in wb.worksheets:
        cells = {
            c.coordinate: _norm(c.value)
            for row in ws.iter_rows()
            for c in row
            if c.value is not None and c.value != ""
        }
        comments = {
            c.coordinate: c.comment.text for row in ws.iter_rows() for c in row if c.comment
        }
        validations = sorted(
            (str(dv.sqref), dv.type or "", (dv.formula1 or "").lstrip("="), bool(dv.allow_blank))
            for dv in ws.data_validations.dataValidation
        )
        cond = sorted(
            (str(rng.sqref), tuple(r.formula or ()))
            for rng in ws.conditional_formatting
            for r in rng.rules
        )
        widths: dict[str, float] = {}
        for dim in ws.column_dimensions.values():  # saved files group equal-width columns
            if dim.width and dim.min is not None and dim.max is not None:
                for i in range(dim.min, dim.max + 1):
                    widths[get_column_letter(i)] = dim.width
        sheets[ws.title] = {
            "cells": cells,
            "comments": comments,
            "validations": validations,
            "conditional": cond,
            "freeze": ws.freeze_panes,
            "widths": dict(sorted(widths.items())),
        }
    return {"order": wb.sheetnames, "sheets": sheets}
