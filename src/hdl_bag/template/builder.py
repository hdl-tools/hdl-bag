# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Build the BAG spec workbook (schema 0.3): blank template or the demo_ss sample.

Port of the reference ``build_spec_template.py``. Sheet names, column order, headers,
dropdown lists, formulas and validation ranges are part of the workbook schema; change
them only together with a schema version bump.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from hdl_bag.template.data import Cell, SpecData
from hdl_bag.template.sample_demo_ss import SAMPLE

SCHEMA_VERSION: Final = "0.3"
COMMENT_AUTHOR: Final = "BAG"

FONT = "Arial"
F_BASE = Font(name=FONT, size=10)
F_TITLE = Font(name=FONT, size=14, bold=True)
F_DESC = Font(name=FONT, size=10, italic=True, color="595959")
F_HDR = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_DRV = Font(name=FONT, size=10, italic=True, color="404040")
F_SEC = Font(name=FONT, size=11, bold=True)
FILL_HDR = PatternFill("solid", fgColor="1F4E78")
FILL_HDR_DRV = PatternFill("solid", fgColor="7F7F7F")
FILL_IN = PatternFill("solid", fgColor="FFF2CC")
FILL_DRV = PatternFill("solid", fgColor="EDEDED")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
HDR_ROW, FIRST = 4, 5
MAXR = 60  # rows covered by validation / formula ranges on most sheets
TMAX = 200  # Topology rows covered by lookups
SW_LAST = 12  # Hierarchy sub-wrapper rows 5..12
P_HDR = 15  # Hierarchy placement header row

SHEET_ORDER: Final = (
    "README", "Project", "Clocks", "Resets", "Fabrics", "Masters", "Slaves", "Bridges",
    "Topology", "Access_Rules", "Memory_Map", "Address_Decode", "Port_Options",
    "Hierarchy", "Wrapper", "Lists",
)  # fmt: skip

LISTS: Final[dict[str, list[str]]] = {
    "Protocol": ["AXI4", "AXI4-Lite", "AHB-Lite", "APB4", "APB3"],
    "Topology": ["crossbar", "shared-bus", "single-layer", "decoder-mux"],
    "Arbitration": ["round-robin", "fixed-priority", "single-master"],
    "LatencyMode": ["NO_LATENCY", "CUT_SLV_AX", "CUT_MST_AX", "CUT_ALL_AX",
                    "CUT_SLV_PORTS", "CUT_MST_PORTS", "CUT_ALL_PORTS", "n/a"],
    "Polarity": ["active-low", "active-high"],
    "Assertion": ["async", "sync"],
    "Access": ["RW", "RO", "WO"],
    "RuleAccess": ["RO", "WO", "-"],
    "YesNo": ["Y", "N"],
    "MissResp": ["DECERR", "SLVERR", "HRESP_ERROR", "PSLVERR", "default-slave"],
    "RegSlice": ["none", "cut", "multicut-2", "multicut-3", "n/a"],
    "PortStyle": ["flat", "sv-interface"],
    "Template": ["bag_axi_xbar", "bag_axil_xbar", "bag_axi_shared_bus", "bag_axil_shared_bus",
                 "bag_ahb_lite_bus", "bag_apb_mux", "bag_axi2apb", "bag_axi2ahb", "bag_ahb2axi",
                 "bag_ahb2apb", "bag_apb2axi", "bag_apb2ahb", "bag_axi2axil", "bag_external"],
    "SigDrive": ["drive", "tie-0", "tie-default", "n/a"],
    "AdapterPlace": ["with fabric", "with port", "top"],
}  # fmt: skip

# Topology lookups (source column A, destination column B)
T_SRC = f"Topology!$A${FIRST}:$A${TMAX}"
T_DST = f"Topology!$B${FIRST}:$B${TMAX}"
DOM_REF = f"=Clocks!$D${FIRST}:$D${MAXR}"
RST_REF = f"=Resets!$A${FIRST}:$A${MAXR}"
SW_RNG = f"$A${FIRST}:$A${SW_LAST}"

Kind = Literal["in", "drv"]
# (header, width, kind, header comment, formula template for derived columns)
Col = tuple[str, int, Kind, str, str | None]


def _in(header: str, width: int, note: str = "") -> Col:
    return (header, width, "in", note, None)


def _drv(header: str, width: int, note: str, formula: str) -> Col:
    return (header, width, "drv", note, formula)


def fabric_of_source(key: str) -> str:
    """Master or bridge (downstream end) -> fabric."""
    return f'IFERROR(INDEX({T_DST},MATCH({key},{T_SRC},0)),"?")'


def fabric_of_dest(key: str) -> str:
    """Slave or bridge (upstream end) -> fabric."""
    return f'IFERROR(INDEX({T_SRC},MATCH({key},{T_DST},0)),"?")'


def fab_lookup(col: str, key: str = "C{r}") -> str:
    return (
        f"IFERROR(INDEX(Fabrics!${col}${FIRST}:${col}${MAXR},"
        f'MATCH({key},Fabrics!$A${FIRST}:$A${MAXR},0)),"?")'
    )


def _rng(col: str) -> str:
    return f"${col}${FIRST}:${col}${TMAX}"


class _Builder:
    def __init__(self, data: SpecData | None) -> None:
        """``data`` None builds the blank template."""
        self.blank = data is None
        self.d = data or SpecData(
            title="BAG spec template",
            description="Blank template. Fill the sheets top to bottom, then run bag check.",
        )
        self.wb = Workbook()
        self.wb.remove(self.wb.active)  # type: ignore[arg-type]
        self.lref: dict[str, str] = {}

    # ------------------------------------------------------------ helpers
    def sheet(self, name: str, title: str, desc: str) -> Worksheet:
        ws: Worksheet = self.wb.create_sheet(name)
        ws["A1"] = title
        ws["A1"].font = F_TITLE
        ws["A2"] = desc
        ws["A2"].font = F_DESC
        ws.sheet_view.showGridLines = False
        return ws

    def table(
        self,
        ws: Worksheet,
        cols: Sequence[Col],
        key: str,
        hdr_row: int = HDR_ROW,
        freeze: bool = True,
        spare: int = 10,
        fixed: Sequence[Sequence[Cell]] = (),
    ) -> int:
        """Write a header row and data rows.

        ``key`` names the SpecData table; its rows hold input-column values only.
        ``fixed`` rows are full-width and always written first. Derived formulas are
        wrapped to stay blank while column A is empty, and also fill ``spare`` empty
        rows below the data, so rows the user adds are checked too. Every workbook
        keeps at least as many formula rows as the demo_ss sample.
        """
        tmpl = {
            c: '=IF($A{r}="","",' + f[1:] + ")"
            for c, (_, _, kind, _, f) in enumerate(cols)
            if kind == "drv" and f is not None
        }
        rows: list[list[Cell]] = getattr(self.d, key)
        n_spare = spare + max(0, len(getattr(SAMPLE, key)) - len(rows))
        full: list[list[Cell]] = [list(r) for r in fixed]
        for row in rows:
            vals = iter(row)
            full.append([tmpl[c] if kind == "drv" else next(vals)
                         for c, (_, _, kind, _, _) in enumerate(cols)])  # fmt: skip
        full += [[tmpl.get(c) for c in range(len(cols))] for _ in range(n_spare)]
        for c, (h, w, kind, note, _) in enumerate(cols, 1):
            cell = ws.cell(hdr_row, c, h)
            cell.font = F_HDR
            cell.fill = FILL_HDR if kind == "in" else FILL_HDR_DRV
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.border = BORDER
            if note:
                cell.comment = Comment(note, COMMENT_AUTHOR)
            if w:
                ws.column_dimensions[get_column_letter(c)].width = w
        ws.row_dimensions[hdr_row].height = 30
        for i, row in enumerate(full):
            r = hdr_row + 1 + i
            for c, val in enumerate(row, 1):
                kind = cols[c - 1][2]
                if isinstance(val, str) and "{r}" in val:
                    val = val.replace("{r}", str(r))
                cell = ws.cell(r, c, val)
                cell.font = F_BASE if kind == "in" else F_DRV
                cell.fill = FILL_IN if kind == "in" else FILL_DRV
                cell.border = BORDER
                cell.alignment = WRAP
        if freeze:
            ws.freeze_panes = ws.cell(hdr_row + 1, 2).coordinate
        return hdr_row + len(full)

    @staticmethod
    def dv(ws: Worksheet, ref: str, col: str, first: int = FIRST, last: int = MAXR) -> None:
        v = DataValidation(
            type="list", formula1=ref, allow_blank=True, showErrorMessage=True,
            errorTitle="Not in list", error="Pick a value from the dropdown (see Lists sheet).",
        )  # fmt: skip
        ws.add_data_validation(v)
        v.add(f"{col}{first}:{col}{last}")

    @staticmethod
    def check_format(ws: Worksheet, col: str, first: int = FIRST, last: int = MAXR) -> None:
        rng = f"{col}{first}:{col}{last}"
        ws.conditional_formatting.add(rng, FormulaRule(  # type: ignore[no-untyped-call]
            formula=[f'LEFT({col}{first},2)="ok"'],
            font=Font(name=FONT, color="006100", bold=True)))  # fmt: skip
        ws.conditional_formatting.add(rng, FormulaRule(  # type: ignore[no-untyped-call]
            formula=[f'AND({col}{first}<>"",LEFT({col}{first},2)<>"ok")'],
            fill=PatternFill("solid", fgColor="FFC7CE"),
            font=Font(name=FONT, color="9C0006", bold=True)))  # fmt: skip

    @staticmethod
    def kv(ws: Worksheet, start: int, items: Sequence[tuple[str, Cell, str]]) -> int:
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 34
        ws.column_dimensions["C"].width = 70
        for col, h in enumerate(["Setting", "Value", "Notes"], 1):
            cell = ws.cell(start, col, h)
            cell.font, cell.fill, cell.border = F_HDR, FILL_HDR, BORDER
        for i, (k, v, note) in enumerate(items, 1):
            r = start + i
            a, b, n = ws.cell(r, 1, k), ws.cell(r, 2, v), ws.cell(r, 3, note)
            a.font, b.font, n.font = Font(name=FONT, size=10, bold=True), F_BASE, F_DESC
            b.fill = FILL_IN
            for cell in (a, b, n):
                cell.border, cell.alignment = BORDER, WRAP
        return start + len(items)

    def project_value(self, key: str) -> Cell:
        return self.d.project.get(key)

    def wrapper_value(self, key: str) -> Cell:
        return self.d.wrapper.get(key)

    # ------------------------------------------------------------ sheets
    def lists(self) -> None:
        ws = self.wb.create_sheet("Lists")
        ws["A1"] = (
            "Dropdown lists used by data validation. "
            "Extend a list by adding rows below it, then widen the validation range."
        )
        ws["A1"].font = F_DESC
        for c, (name, vals) in enumerate(LISTS.items(), 1):
            col = get_column_letter(c)
            h = ws.cell(3, c, name)
            h.font, h.fill = F_HDR, FILL_HDR
            for i, v in enumerate(vals):
                ws.cell(4 + i, c, v).font = F_BASE
            self.lref[name] = f"=Lists!${col}$4:${col}${4 + len(vals) - 1}"
            ws.column_dimensions[col].width = 18

    def readme(self) -> None:
        ws = self.sheet("README", self.d.title, self.d.description)
        ws.column_dimensions["A"].width = 24
        ws.column_dimensions["B"].width = 110
        legend = [
            ("Legend", ""),
            ("Yellow cell", "Input. Edit these. Dropdown cells only accept values from the Lists sheet."),
            ("Grey cell / grey header", "Derived by formula. Do not edit; shows what BAG will infer (fabric attachment, adapters, checks)."),
            ("Check column", "ok in green, anything else in red. BAG runs the same checks and more."),
            ("", ""),
            ("Sheet order", "Define names first (Fabrics, Masters, Slaves, Bridges), then wire them up in Topology."),
            ("Project", "Project-wide settings and which outputs to generate."),
            ("Clocks / Resets", "Clock domains and reset behaviour. Every port lands in exactly one domain."),
            ("Fabrics", "Each bus instance, its parameters and backing template. Port counts come from Topology. Topology column: crossbar or shared-bus for AXI."),
            ("Masters / Slaves", "External ports. Their fabric is derived from Topology; grey columns show inserted adapters and their instance names."),
            ("Bridges", "Fabric-to-fabric links. Upstream and downstream fabrics are derived from Topology."),
            ("Topology", "THE connection list: one row per link, Source -> Destination, with explicit Port index (and Priority for fixed-priority fabrics). Single source of truth for what attaches where."),
            ("Access_Rules", "Exceptions only. By default every master on a fabric reaches every slave on it (RW, subject to Memory_Map Access)."),
            ("Memory_Map", "Regions per slave or bridge, as full system addresses. Fabric is derived; Check flags unknown names, overlap, size, alignment."),
            ("Address_Decode", "Per-master miss response, default slave and aliases/remaps."),
            ("Port_Options", "Per-port FIFO depth, register slices and optional-signal handling."),
            ("Hierarchy", "Sub-wrappers and which fabric, bridge or adapter instance goes in each."),
            ("Wrapper", "Top-level wrapper: name, port style, naming patterns, adapter placement default, exposed ports."),
            ("", ""),
            ("Formats", "Addresses and sizes as hex text with 0x prefix and optional underscores: 0x4000_0000. Widths in bits. Frequencies in MHz."),
            ("Names", "Lowercase SystemVerilog identifiers, unique across Masters, Slaves, Bridges, Fabrics and sub-wrappers."),
            ("Canonical spec", "BAG converts this workbook to a YAML IR checked into git. RTL, tests and docs are generated from the IR."),
            ("Bridge templates", "axi2apb, axi2ahb, ahb2axi, ahb2apb, apb2axi, apb2ahb, plus axi2axil. Never link two fabrics in both directions."),
        ]  # fmt: skip
        for i, (k, v) in enumerate(legend, 4):
            a, b = ws.cell(i, 1, k), ws.cell(i, 2, v)
            a.font = Font(name=FONT, size=10, bold=True)
            b.font = F_BASE
            b.alignment = Alignment(wrap_text=True, vertical="top")
        for ref in ("A4", "A9", "A23"):
            ws[ref].font = F_SEC
        ws["A5"].fill = FILL_IN
        ws["A6"].fill = FILL_DRV

    def project(self) -> None:
        ws = self.sheet("Project", "Project", "Project-wide settings. One value per row.")
        name = self.project_value("Project name")
        header_note = f"{name or '<project name>'}_memmap.h from Memory_Map."
        self.kv(ws, HDR_ROW, [
            ("Project name", self.project_value("Project name"), "Prefix for generated module and package names."),
            ("Spec schema version", SCHEMA_VERSION, "Must match the schema version the BAG release expects."),
            ("Spec revision", self.project_value("Spec revision"), "Free text; stamped into every generated file header."),
            ("Author", self.project_value("Author"), ""),
            ("SystemVerilog standard", self.project_value("SystemVerilog standard"), "Generated RTL stays inside the synthesizable subset."),
            ("Output directory", self.project_value("Output directory"), "Relative to the workbook."),
            ("Generate RTL", self.project_value("Generate RTL"), ""),
            ("Generate C header", self.project_value("Generate C header"), header_note),
            ("Generate memory-map doc", self.project_value("Generate memory-map doc"), "Markdown + HTML."),
            ("Generate cocotb unit tests", self.project_value("Generate cocotb unit tests"), "Per-config directed + random tests."),
            ("Generate formal (SymbiYosys)", self.project_value("Generate formal (SymbiYosys)"), "Decode and port-protocol properties."),
            ("Lint tools", self.project_value("Lint tools"), "All must pass with zero un-waived warnings."),
            ("Simulator", self.project_value("Simulator"), "Pluggable; commercial simulators via the tool-abstraction layer."),
        ])  # fmt: skip
        v = DataValidation(type="list", formula1=self.lref["YesNo"], allow_blank=False)
        ws.add_data_validation(v)
        v.add("B11:B15")

    def clocks(self) -> None:
        ws = self.sheet(
            "Clocks",
            "Clocks",
            "One row per clock. Domain names are referenced by every port and fabric.",
        )
        self.table(ws, [
            _in("Clock", 16, "Top-level clock port name."),
            _in("Freq (MHz)", 12, "Nominal frequency; used for docs and CDC sanity checks."),
            _in("Source", 22, "Top port, PLL output, divider."),
            _in("Domain", 14, "Domain name used elsewhere in the spec. One clock per domain."),
            _in("Reset", 16, "Reset paired with this domain (from Resets sheet)."),
            _in("Notes", 40),
        ], "clocks")  # fmt: skip
        self.dv(ws, RST_REF, "E")

    def resets(self) -> None:
        ws = self.sheet(
            "Resets",
            "Resets",
            "One row per reset. BAG inserts reset synchronizers where Deassertion = sync.",
        )
        self.table(ws, [
            _in("Reset", 16, "Top-level reset port name."),
            _in("Polarity", 13, "active-low | active-high"),
            _in("Assertion", 12, "async | sync"),
            _in("Deassertion", 13, "async | sync (sync = synchronizer inserted per domain)"),
            _in("Domain", 12, "Clock domain this reset applies to."),
            _in("Source", 22),
            _in("Notes", 40),
        ], "resets")  # fmt: skip
        self.dv(ws, self.lref["Polarity"], "B")
        self.dv(ws, self.lref["Assertion"], "C")
        self.dv(ws, self.lref["Assertion"], "D")
        self.dv(ws, DOM_REF, "E")

    def fabrics(self) -> None:
        ws = self.sheet(
            "Fabrics", "Fabrics", "One row per bus instance. Port counts are derived from Topology."
        )
        id_s = (
            f'=IF(B{{r}}="AXI4",_xlfn.MAXIFS(Masters!$F${FIRST}:$F${MAXR},'
            f'Masters!$C${FIRST}:$C${MAXR},A{{r}}),"n/a")'
        )
        id_m = '=IF(B{r}="AXI4",Q{r}+IF(O{r}<=1,0,CEILING(LOG(O{r},2),1)),"n/a")'
        self.table(ws, [
            _in("Fabric", 14, "Instance name."),
            _in("Protocol", 11, "Fabric-internal protocol."),
            _in("Topology", 13, "crossbar (N x M, parallel paths) | shared-bus (N:1 mux, mid-point, 1:M demux; one transfer at a time) | single-layer (AHB-Lite) | decoder-mux (APB)"),
            _in("Data width (bits)", 10, "Fabric-internal data width. Ports at other widths get converters."),
            _in("Addr width (bits)", 10, ""),
            _in("Clock domain", 11, ""),
            _in("Arbitration", 14, "Crossbar: per slave port. Shared-bus: one arbiter at the N:1 mux."),
            _in("Latency mode", 15, "Crossbar only: PULP xbar spill-register placement. n/a otherwise."),
            _in("Max mst trans", 9, "Max in-flight transactions per connected master (PULP MaxMstTrans)."),
            _in("Max slv trans", 9, "Max in-flight transactions per ID towards each connected slave (PULP MaxSlvTrans)."),
            _in("Backing template", 20, "BAG template that implements this fabric."),
            _in("Backing IP", 22, "Informational: IP behind the template."),
            _in("Mid-point FIFO depth", 10, "Shared-bus only: FIFO between the N:1 mux and the 1:M demux; buffers every master. n/a otherwise."),
            _in("Mid-point register slice", 12, "Shared-bus only: none | cut | multicut-N between mux and demux; breaks the mux-to-demux timing path. n/a otherwise."),
            _drv("# master ports", 10, "Topology rows with this fabric as Destination. Sets the template's master-port count.", f"=COUNTIF({T_DST},A{{r}})"),
            _drv("# slave ports", 10, "Topology rows with this fabric as Source. Sets the template's slave-port count.", f"=COUNTIF({T_SRC},A{{r}})"),
            _drv("Master-side ID width", 11, "AXI4 only: widest master ID on this fabric.", id_s),
            _drv("Slave-side ID width", 11, "AXI4 only: master-side ID width + clog2(# master ports).", id_m),
            _drv("Max parallel transfers", 10, "Crossbar: min(# master ports, # slave ports). Every other topology: 1.", '=IF(C{r}="crossbar",MIN(O{r},P{r}),1)'),
            _in("Notes", 30, ""),
        ], "fabrics")  # fmt: skip
        self.dv(ws, self.lref["RegSlice"], "N")
        self.dv(ws, self.lref["Protocol"], "B")
        self.dv(ws, self.lref["Topology"], "C")
        self.dv(ws, DOM_REF, "F")
        self.dv(ws, self.lref["Arbitration"], "G")
        self.dv(ws, self.lref["LatencyMode"], "H")
        self.dv(ws, self.lref["Template"], "K")

    @staticmethod
    def port_drv(direction: Literal["in", "out"]) -> list[Col]:
        """Columns M..S. 'in' = port -> fabric (master); 'out' = fabric -> port (slave)."""
        a, b = ("B{r}", "M{r}") if direction == "in" else ("M{r}", "B{r}")
        w1, w2 = ("D{r}", "O{r}") if direction == "in" else ("O{r}", "D{r}")
        d1, d2 = ("H{r}", "Q{r}") if direction == "in" else ("Q{r}", "H{r}")
        frm, to = ("A{r}", "C{r}") if direction == "in" else ("C{r}", "A{r}")
        inst = (
            f'=TRIM(IF(N{{r}}="none","",{frm}&"_pc_2_"&{to}&" ")'
            f'&IF(P{{r}}="none","",{frm}&"_dw_2_"&{to}&" ")'
            f'&IF(R{{r}}="none","",{frm}&"_cdc_2_"&{to}))'
        )
        return [
            _drv("Fabric protocol", 11, "From Fabrics.", "=" + fab_lookup("B")),
            _drv("Protocol adapter", 18, "Inserted when port and fabric protocols differ.",
                 f'=IF(B{{r}}=M{{r}},"none",{a}&" -> "&{b})'),
            _drv("Fabric data width", 10, "From Fabrics.", "=" + fab_lookup("D")),
            _drv("Width adapter", 18, "Inserted when port and fabric data widths differ.",
                 f'=IF(D{{r}}=O{{r}},"none",IF({w1}>{w2},"downsize ","upsize ")&{w1}&" -> "&{w2})'),
            _drv("Fabric domain", 10, "From Fabrics.", "=" + fab_lookup("F")),
            _drv("CDC", 20, "Async CDC inserted when port and fabric domains differ.",
                 f'=IF(H{{r}}=Q{{r}},"none","async CDC "&{d1}&" -> "&{d2})'),
            _drv("Adapter instances", 34,
                 "Instance names per Wrapper > Adapter instance naming: {from}_{type}_2_{to}; "
                 "type = pc (protocol), dw (width), cdc. "
                 "Use these names on the Hierarchy sheet to override placement.", inst),
        ]  # fmt: skip

    def masters(self) -> None:
        ws = self.sheet(
            "Masters",
            "Masters",
            "Ports that issue transactions. Fabric is derived from Topology "
            "(NOT CONNECTED = no Topology row).",
        )
        fm = "=" + fabric_of_source("A{r}").replace('"?"', '"NOT CONNECTED"')
        self.table(ws, [
            _in("Master", 12, "Port name; becomes the wrapper port prefix."),
            _in("Protocol", 11, ""),
            _drv("Fabric", 14, "Derived: Topology row with this master as Source.", fm),
            _in("Data width (bits)", 10, ""),
            _in("Addr width (bits)", 10, ""),
            _in("ID width (bits)", 9, "AXI4 only; blank for others."),
            _in("User width (bits)", 9, "AXI only; 0 = no USER signals."),
            _in("Clock domain", 11, ""),
            _in("Max outstanding rd", 11, ""),
            _in("Max outstanding wr", 11, ""),
            _in("Description", 24, ""),
            _in("Notes", 16, ""),
            *self.port_drv("in"),
        ], "masters")  # fmt: skip
        self.dv(ws, self.lref["Protocol"], "B")
        self.dv(ws, DOM_REF, "H")

    def slaves(self) -> None:
        ws = self.sheet(
            "Slaves",
            "Slaves",
            "Ports that receive transactions. Fabric is derived from Topology; "
            "ID width auto = fabric slave-side ID width.",
        )
        fs = "=" + fabric_of_dest("A{r}").replace('"?"', '"NOT CONNECTED"')
        self.table(ws, [
            _in("Slave", 12, "Port name; becomes the wrapper port prefix."),
            _in("Protocol", 11, ""),
            _drv("Fabric", 14, "Derived: Topology row with this slave as Destination.", fs),
            _in("Data width (bits)", 10, ""),
            _in("Addr width (bits)", 10, ""),
            _in("ID width (bits)", 9, "auto = fabric slave-side ID width (see Fabrics)."),
            _in("User width (bits)", 9, ""),
            _in("Clock domain", 11, ""),
            _in("Error response", 13, "How the slave signals errors upstream."),
            _in("Wait states", 9, "Informational; used by test timing."),
            _in("Description", 24, ""),
            _in("Notes", 16, ""),
            *self.port_drv("out"),
        ], "slaves")  # fmt: skip
        self.dv(ws, self.lref["Protocol"], "B")
        self.dv(ws, DOM_REF, "H")
        self.dv(ws, self.lref["MissResp"], "I")

    def bridges(self) -> None:
        ws = self.sheet(
            "Bridges",
            "Bridges",
            "Fabric-to-fabric links. Upstream / downstream fabrics are derived from Topology.",
        )
        self.table(ws, [
            _in("Bridge", 14, "Name; appears in Topology twice: as Destination (from upstream fabric) and as Source (to downstream fabric)."),
            _in("Template", 18, "BAG bridge template."),
            _drv("Upstream fabric", 12, "Derived: Topology row with this bridge as Destination.", "=" + fabric_of_dest("A{r}")),
            _drv("Downstream fabric", 12, "Derived: Topology row with this bridge as Source.", "=" + fabric_of_source("A{r}")),
            _in("Window base (hex)", 14, "Must match the bridge's first region in Memory_Map."),
            _in("Pass-through address", 11, "Y = downstream sees full system address; N = offset from window base."),
            _in("FIFO depth", 8, "Requests buffered in the bridge."),
            _in("Notes", 34, ""),
            _drv("Upstream protocol", 11, "", "=" + fab_lookup("B", "C{r}")),
            _drv("Downstream protocol", 11, "", "=" + fab_lookup("B", "D{r}")),
            _drv("Upstream domain", 10, "", "=" + fab_lookup("F", "C{r}")),
            _drv("Downstream domain", 10, "", "=" + fab_lookup("F", "D{r}")),
            _drv("CDC", 20, "", '=IF(K{r}=L{r},"none","async CDC "&K{r}&" -> "&L{r})'),
        ], "bridges")  # fmt: skip
        self.dv(ws, self.lref["Template"], "B")
        self.dv(ws, self.lref["YesNo"], "F")

    def topology(self) -> None:
        ws = self.sheet(
            "Topology",
            "Topology",
            "One row per link, Source -> Destination. Legal links: master -> fabric, "
            "fabric -> slave, fabric -> bridge, bridge -> fabric.",
        )
        names = [
            ("Masters", "master"),
            ("Slaves", "slave"),
            ("Bridges", "bridge"),
            ("Fabrics", "fabric"),
        ]

        def kind(key: str) -> str:
            f = '"UNKNOWN"'
            for sh, k in reversed(names):
                f = f'IF(COUNTIF({sh}!$A${FIRST}:$A${MAXR},{key})>0,"{k}",{f})'
            return "=" + f

        arb = (
            f"IFERROR(INDEX(Fabrics!$G${FIRST}:$G${MAXR},"
            f'MATCH(H{{r}},Fabrics!$A${FIRST}:$A${MAXR},0)),"")'
        )
        idx_bad = 'IF(C{r}="",FALSE,IF(ISNUMBER(C{r}),OR(C{r}<>INT(C{r}),C{r}<0),TRUE))'
        pri_bad = 'IF(D{r}="",FALSE,IF(ISNUMBER(D{r}),OR(D{r}<>INT(D{r}),D{r}<0),TRUE))'
        topo_check = (
            '=IF(OR(F{r}="UNKNOWN",G{r}="UNKNOWN"),"UNKNOWN NAME",'
            'IF(NOT(OR(AND(F{r}="master",G{r}="fabric"),AND(F{r}="fabric",G{r}="slave"),'
            'AND(F{r}="fabric",G{r}="bridge"),AND(F{r}="bridge",G{r}="fabric"))),"ILLEGAL LINK",'
            f'IF(COUNTIFS({_rng("A")},A{{r}},{_rng("B")},B{{r}})>1,"DUPLICATE",'
            f'IF(AND(F{{r}}<>"fabric",COUNTIF({_rng("A")},A{{r}})>1),"MULTI-ATTACH",'
            f'IF(AND(G{{r}}<>"fabric",COUNTIF({_rng("B")},B{{r}})>1),"MULTI-ATTACH",'
            f'IF({idx_bad},"INDEX NOT INTEGER",'
            'IF(J{r}>=K{r},"INDEX OUT OF RANGE",'
            f'IF(COUNTIFS({_rng("H")},H{{r}},{_rng("I")},I{{r}},{_rng("J")},J{{r}})>1,"INDEX REPEATED",'
            f'IF({pri_bad},"PRIORITY NOT INTEGER",'
            f'IF(AND(I{{r}}="master port",{arb}="fixed-priority",D{{r}}=""),"PRIORITY MISSING",'
            f'IF(AND(I{{r}}="master port",{arb}="fixed-priority",'
            f'COUNTIFS({_rng("H")},H{{r}},{_rng("I")},"master port",{_rng("D")},D{{r}})>1),"PRIORITY REPEATED",'
            '"ok")))))))))))'
        )  # fmt: skip
        res_idx = (
            f'=IF(C{{r}}<>"",C{{r}},COUNTIFS($H${FIRST}:H{{r}},H{{r}},$I${FIRST}:I{{r}},I{{r}})-1)'
        )
        n_side = f"=COUNTIFS({_rng('H')},H{{r}},{_rng('I')},I{{r}})"
        self.table(ws, [
            _in("Source", 16, "Master, fabric or bridge (downstream end)."),
            _in("Destination", 16, "Fabric, slave or bridge (upstream end)."),
            _in("Port index", 8,
                "Port number on the fabric side of this link: 0 .. (ports on that side - 1), no repeats. "
                "Blank = next free position in row order (not recommended)."),
            _in("Priority", 8,
                "Master-port links only, read when the fabric's Arbitration = fixed-priority. 0 = highest; no repeats per fabric."),
            _in("Notes", 32, ""),
            _drv("Source kind", 10, "master | fabric | bridge | slave | UNKNOWN", kind("A{r}")),
            _drv("Destination kind", 10, "", kind("B{r}")),
            _drv("Fabric", 12, "The fabric this link attaches to.", '=IF(F{r}="fabric",A{r},B{r})'),
            _drv("Port side", 11, "master port (link into the fabric) | slave port (link out of the fabric).",
                 '=IF(F{r}="fabric","slave port","master port")'),
            _drv("Resolved index", 9, "Port index used for generation: the explicit value, or row order when blank.", res_idx),
            _drv("Ports on side", 9, "Number of links on this fabric side; legal indices are 0 .. this - 1.", n_side),
            _drv("Check", 20,
                 "ok | UNKNOWN NAME | ILLEGAL LINK | DUPLICATE | MULTI-ATTACH | INDEX NOT INTEGER | INDEX OUT OF RANGE | "
                 "INDEX REPEATED | PRIORITY NOT INTEGER | PRIORITY MISSING | PRIORITY REPEATED", topo_check),
        ], "topology", spare=30)  # fmt: skip
        self.check_format(ws, "L", last=TMAX)

    def access_rules(self) -> None:
        ws = self.sheet(
            "Access_Rules",
            "Access rules",
            "Exceptions only. Default: every master on a fabric reaches every slave on it, "
            "limited by Memory_Map Access.",
        )
        self.table(ws, [
            _in("Master", 14, "Master or bridge (downstream end)."),
            _in("Slave / bridge", 14, "Slave or bridge (upstream end) on the same fabric."),
            _in("Access", 9, "RO | WO | - (no path; the crossbar omits the connection)."),
            _in("Notes", 34, ""),
            _drv("Master fabric", 12, "", "=" + fabric_of_source("A{r}")),
            _drv("Slave fabric", 12, "", "=" + fabric_of_dest("B{r}")),
            _drv("Check", 16, "ok | UNKNOWN NAME | NOT SAME FABRIC",
                 '=IF(OR(E{r}="?",F{r}="?"),"UNKNOWN NAME",IF(E{r}<>F{r},"NOT SAME FABRIC","ok"))'),
        ], "access_rules")  # fmt: skip
        self.dv(ws, self.lref["RuleAccess"], "C")
        self.check_format(ws, "G")

    def memory_map(self) -> None:
        ws = self.sheet(
            "Memory_Map",
            "Memory map",
            "One row per region, full system addresses. Fabric is derived. "
            "Check runs on every row.",
        )
        hexd = 'HEX2DEC(SUBSTITUTE(SUBSTITUTE(LOWER({c}{{r}}),"0x",""),"_",""))'
        end_hex = '="0x"&LEFT(DEC2HEX(L{r},8),4)&"_"&RIGHT(DEC2HEX(L{r},8),4)'
        mm_check = (
            f'=IF(COUNTIF(Slaves!$A${FIRST}:$A${MAXR},A{{r}})+COUNTIF(Bridges!$A${FIRST}:$A${MAXR},A{{r}})=0,"UNKNOWN SLAVE",'
            'IF(I{r}="?","NOT CONNECTED",'
            f'IF(SUMPRODUCT(($I${FIRST}:$I${MAXR}=I{{r}})*(ROW($I${FIRST}:$I${MAXR})<>ROW(I{{r}}))'
            f'*($J${FIRST}:$J${MAXR}<=L{{r}})*($L${FIRST}:$L${MAXR}>=J{{r}}))>0,"OVERLAP",'
            'IF(ABS(LOG(K{r},2)-ROUND(LOG(K{r},2),0))>0.000001,"SIZE NOT 2^n",'
            'IF(MOD(J{r},K{r})<>0,"MISALIGNED",IF(K{r}<4096,"WARN < 4 KB","ok"))))))'
        )  # fmt: skip
        self.table(ws, [
            _in("Slave / bridge", 14, "Must exist on Slaves or Bridges and be connected in Topology."),
            _in("Region", 10, "Region name; a slave may have several."),
            _in("Base (hex)", 14, "Full system address, e.g. 0x4000_0000."),
            _in("Size (hex)", 14, "Power of two, e.g. 0x0000_1000 = 4 KB."),
            _drv("End (hex)", 14, "Base + size - 1.", end_hex),
            _in("Access", 8, "RW | RO | WO for every master; Access_Rules narrows it per master."),
            _in("Secure only", 8, "Y = non-secure access returns the miss response."),
            _in("Notes", 26, ""),
            _drv("Fabric", 12, "Derived from Topology. Decides which decoder holds the region and scopes the overlap check.",
                 "=" + fabric_of_dest("A{r}")),
            _drv("Base (dec)", 13, "Helper for checks.", "=" + hexd.format(c="C")),
            _drv("Size (dec)", 13, "Helper for checks.", "=" + hexd.format(c="D")),
            _drv("End (dec)", 13, "Helper for checks.", "=J{r}+K{r}-1"),
            _drv("Check", 15, "UNKNOWN SLAVE | NOT CONNECTED | OVERLAP (same fabric) | SIZE NOT 2^n | MISALIGNED | WARN < 4 KB | ok",
                 mm_check),
        ], "memory_map")  # fmt: skip
        self.dv(ws, self.lref["Access"], "F")
        self.dv(ws, self.lref["YesNo"], "G")
        self.check_format(ws, "M")

    def address_decode(self) -> None:
        ws = self.sheet(
            "Address_Decode",
            "Address decode",
            "Per-master decode behaviour: what a miss returns, default slave, "
            "aliases/remaps. Fabric is derived.",
        )
        self.table(ws, [
            _in("Master", 13, "Master or bridge (downstream end)."),
            _drv("Fabric", 12, "Derived from Topology.", "=" + fabric_of_source("A{r}")),
            _in("Miss response", 14, "Returned for unmapped, blocked or non-secure-violating addresses."),
            _in("Default slave", 13, "Only when Miss response = default-slave."),
            _in("Alias base (hex)", 14, "Address the master issues."),
            _in("Alias size (hex)", 14, ""),
            _in("Alias maps to (hex)", 14, "Address decoded instead."),
            _in("Alias enable", 16, "always | top-level input pin name"),
            _in("Notes", 40, ""),
        ], "address_decode")  # fmt: skip
        self.dv(ws, self.lref["MissResp"], "C")

    def port_options(self) -> None:
        ws = self.sheet(
            "Port_Options",
            "Port options",
            "Per-port buffering and optional-signal handling. "
            "drive = exposed at wrapper; tie-* = tied inside.",
        )
        fpo = "=" + fabric_of_source("A{r}").replace('"?"', fabric_of_dest("A{r}"))
        self.table(ws, [
            _in("Port", 13, "Any master, slave or bridge end."),
            _drv("Fabric", 12, "Derived from Topology.", fpo),
            _in("FIFO depth", 8, "Extra buffering at this port (inserts axi_fifo). 0 = none."),
            _in("Register slice", 12, "none | cut | multicut-N (axi_cut / axi_multicut)."),
            _in("PROT", 11, "AxPROT / HPROT / PPROT"),
            _in("QOS", 11, "AXI AxQOS"),
            _in("REGION", 11, "AXI AxREGION"),
            _in("CACHE", 11, "AXI AxCACHE"),
            _in("LOCK / excl", 11, "AXI AxLOCK, AHB HMASTLOCK"),
            _in("PSTRB", 11, "APB4 only"),
            _in("PSLVERR", 9, "APB only"),
            _in("Notes", 36, ""),
        ], "port_options")  # fmt: skip
        self.dv(ws, self.lref["RegSlice"], "D")
        for col in "EFGHIJK":
            self.dv(ws, self.lref["SigDrive"], col)

    def hierarchy(self) -> None:
        ws = self.sheet(
            "Hierarchy",
            "Hierarchy",
            "Sub-wrappers, then which instance goes in each. Unlisted fabrics/bridges go "
            "in the top wrapper; adapters follow Wrapper > Adapter placement unless listed here.",
        )
        sw_check = (
            '=IF(B{r}="-","ok (top)",IF(B{r}=A{r},"SELF PARENT",'
            f'IF(COUNTIF({SW_RNG},B{{r}})>0,"ok","UNKNOWN PARENT")))'
        )
        top_row: list[Cell] = [
            "=Wrapper!B5",
            "-",
            "Top wrapper (derived from Wrapper > Top module name)",
            '=IF($A{r}="","",' + sw_check[1:] + ")",
        ]
        self.table(ws, [
            _in("Sub-wrapper", 22, "Module name of the sub-wrapper. Row 5 is the top wrapper (from Wrapper sheet)."),
            _in("Parent", 22, "Enclosing wrapper; - for the top. Nesting is allowed."),
            _in("Notes", 46, ""),
            _drv("Check", 18, "ok | UNKNOWN PARENT | SELF PARENT", sw_check),
        ], "sub_wrappers", spare=SW_LAST - 8, fixed=[top_row])  # fmt: skip
        ws["A5"].fill = FILL_DRV
        ws["A5"].font = F_DRV
        self.dv(ws, f"={SW_RNG}", "B", first=FIRST + 1, last=SW_LAST)
        self.check_format(ws, "D", last=SW_LAST)

        ws.cell(P_HDR - 1, 1, "Placement").font = F_SEC
        place_kind = (
            f'=IF(COUNTIF(Fabrics!$A${FIRST}:$A${MAXR},A{{r}})>0,"fabric",'
            f'IF(COUNTIF(Bridges!$A${FIRST}:$A${MAXR},A{{r}})>0,"bridge",'
            f'IF(ISNUMBER(SEARCH("_2_",A{{r}})),"adapter","UNKNOWN")))'
        )
        place_check = (
            '=IF(E{r}="UNKNOWN","UNKNOWN INSTANCE",'
            f'IF(COUNTIF({SW_RNG},B{{r}})=0,"UNKNOWN SUB-WRAPPER",'
            f'IF(COUNTIF($A${P_HDR + 1}:$A${P_HDR + 40},A{{r}})>1,"PLACED TWICE","ok")))'
        )
        self.table(ws, [
            _in("Instance", 22, "Fabric, bridge, or adapter instance name (see Masters/Slaves > Adapter instances)."),
            _in("Sub-wrapper", 22, "Where the instance lives."),
            _in("RTL instance name", 22, "Optional override; blank = u_<instance>."),
            _in("Notes", 46, ""),
            _drv("Kind", 18, "fabric | bridge | adapter | UNKNOWN", place_kind),
            _drv("Check", 18, "ok | UNKNOWN INSTANCE | UNKNOWN SUB-WRAPPER | PLACED TWICE", place_check),
        ], "placement", hdr_row=P_HDR, freeze=False, spare=15)  # fmt: skip
        self.dv(ws, f"={SW_RNG}", "B", first=P_HDR + 1, last=P_HDR + 40)
        self.check_format(ws, "F", first=P_HDR + 1, last=P_HDR + 40)
        ws.freeze_panes = "A5"

    def wrapper(self) -> None:
        ws = self.sheet(
            "Wrapper",
            "Wrapper",
            "Top-level wrapper around the generated fabric. Sub-wrappers are defined on Hierarchy.",
        )
        end = self.kv(ws, HDR_ROW, [
            ("Top module name", self.wrapper_value("Top module name"), "Also row 5 of the Hierarchy sheet."),
            ("Port style", self.wrapper_value("Port style"), "flat (default) | sv-interface. Flat = one port per signal."),
            ("Signal naming pattern", self.wrapper_value("Signal naming pattern"), "e.g. cpu0_axi_awaddr, uart0_apb_psel"),
            ("Clock/reset ports", self.wrapper_value("Clock/reset ports"), "clk_* / rst_* from the Clocks and Resets sheets."),
            ("Parameter package", self.wrapper_value("Parameter package"), "Widths, base addresses and region sizes as localparams."),
            ("File header", self.wrapper_value("File header"), "Text file prepended to every generated file (licence/copyright)."),
            ("Extra top-level inputs", self.wrapper_value("Extra top-level inputs"), "Inputs referenced by Address_Decode alias enables."),
            ("Adapter placement", self.wrapper_value("Adapter placement"), "Default home for inserted adapters: with fabric | with port (top) | top."),
            ("Adapter instance naming", self.wrapper_value("Adapter instance naming"),
             "type = pc (protocol), dw (width), cdc, iw (ID width), fifo, cut. e.g. main_xbar_dw_2_ddr0."),
            ("Sub-wrapper port naming", self.wrapper_value("Sub-wrapper port naming"), "Ports created where a link crosses a sub-wrapper boundary."),
        ])  # fmt: skip
        for rownum, ref in (
            (HDR_ROW + 2, self.lref["PortStyle"]),
            (HDR_ROW + 8, self.lref["AdapterPlace"]),
        ):
            v = DataValidation(type="list", formula1=ref, allow_blank=False)
            ws.add_data_validation(v)
            v.add(f"B{rownum}")
        start = end + 3
        ws.cell(start - 1, 1, "Exposed ports").font = F_SEC
        for c, h in enumerate(
            ["Port", "Exposed at top", "Top-level prefix", "If not exposed", "Notes"], 1
        ):
            cell = ws.cell(start, c, h)
            cell.font, cell.fill, cell.border = F_HDR, FILL_HDR, BORDER
        ws.column_dimensions["D"].width = 16
        ws.column_dimensions["E"].width = 30
        for i, row in enumerate(self.d.exposed_ports, 1):
            for c, val in enumerate(row, 1):
                cell = ws.cell(start + i, c, val)
                cell.font, cell.border, cell.fill = F_BASE, BORDER, FILL_IN
        v = DataValidation(type="list", formula1=self.lref["YesNo"], allow_blank=False)
        ws.add_data_validation(v)
        v.add(f"B{start + 1}:B{start + 40}")

    # ------------------------------------------------------------ assemble
    def build(self) -> Workbook:
        self.lists()
        self.readme()
        self.project()
        self.clocks()
        self.resets()
        self.fabrics()
        self.masters()
        self.slaves()
        self.bridges()
        self.topology()
        self.access_rules()
        self.memory_map()
        self.address_decode()
        self.port_options()
        self.hierarchy()
        self.wrapper()
        wb = self.wb
        for i, name in enumerate(SHEET_ORDER):
            wb.move_sheet(name, i - wb.sheetnames.index(name))
        for ws in wb.worksheets:
            ws.sheet_properties.tabColor = {"README": "1F4E78", "Lists": "7F7F7F"}.get(
                ws.title, "BF9000"
            )
            ws.page_setup.orientation = "landscape"
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.sheet_properties.pageSetUpPr.fitToPage = True  # type: ignore[union-attr]
        wb.active = 0
        return wb


def build_workbook(sample: bool = False, data: SpecData | None = None) -> Workbook:
    """The blank template, the demo_ss sample (``sample``), or a workbook for ``data``."""
    return _Builder(SAMPLE if sample else data).build()


def write_template(path: Path, sample: bool = False) -> None:
    build_workbook(sample).save(path)
