# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import shutil
from pathlib import Path

import pytest

from hdl_bag.flows import Finding, WaiverError, lint, load_waivers
from hdl_bag.flows.backends import BACKENDS, Slang, Verible, Verilator, parse_flags, slang_flags
from hdl_bag.flows.waivers import Waiver, apply_waivers

ROOT = Path("/run")

SLANG_OUT = """\
rtl/top.sv:7:9: warning: unused variable 'spare' [-Wunused-variable]
    logic spare;
          ^
rtl/top.sv:12:3: error: use of undeclared identifier 'nope'
  nope = 1'b0;
  ^
rtl/top.sv:3:1: note: declared here
"""

VERILATOR_OUT = """\
%Warning-UNUSEDSIGNAL: rtl/top.sv:7:9: Signal is not used: 'spare'
                                     : ... note: In instance 'top'
    7 |   logic spare;
      |         ^~~~~
                       ... For warning description see https://verilator.org/warn/UNUSEDSIGNAL?v=5.052
%Error: rtl/top.sv:12:3: Can't find definition of variable: 'nope'
%Error: Exiting due to 1 error(s)
"""

VERIBLE_OUT = """\
rtl/top.sv:4:1-20: Module name does not match file name. [Style: file-names] [module-filename]
rtl/top.sv:9:31: Line length exceeds max: 100; is: 104 [Style: line-length] [line-length]
rtl/bad.sv:2:5: syntax error at token "endmodule"
"""


def test_slang_parse() -> None:
    assert Slang().parse(SLANG_OUT, ROOT) == [
        Finding("slang", "rtl/top.sv", 7, "unused-variable", "warning", "unused variable 'spare'"),
        Finding("slang", "rtl/top.sv", 12, "error", "error", "use of undeclared identifier 'nope'"),
    ]


def test_verilator_parse() -> None:
    assert Verilator().parse(VERILATOR_OUT, ROOT) == [
        Finding("verilator", "rtl/top.sv", 7, "UNUSEDSIGNAL", "warning", "Signal is not used: 'spare'"),
        Finding("verilator", "rtl/top.sv", 12, "error", "error", "Can't find definition of variable: 'nope'"),
    ]  # fmt: skip


def test_verible_parse() -> None:
    assert Verible().parse(VERIBLE_OUT, ROOT) == [
        Finding("verible", "rtl/top.sv", 4, "module-filename", "warning", "Module name does not match file name. [Style: file-names]"),
        Finding("verible", "rtl/top.sv", 9, "line-length", "warning", "Line length exceeds max: 100; is: 104 [Style: line-length]"),
        Finding("verible", "rtl/bad.sv", 2, "syntax", "error", 'syntax error at token "endmodule"'),
    ]  # fmt: skip


def test_absolute_paths_become_run_relative(tmp_path: Path) -> None:
    out = f"{(tmp_path / 'rtl' / 'a.sv').as_posix()}:3:1: warning: w [-Wx]\n"
    assert Slang().parse(out, tmp_path)[0].file == "rtl/a.sv"


def _waiver(**kw: object) -> Waiver:
    return Waiver.model_validate({"rule": "UNUSEDSIGNAL", "file": "rtl/*.sv", "reason": "r", **kw})


def test_waiver_marks_finding_and_reports_stale_ones() -> None:
    findings = Verilator().parse(VERILATOR_OUT, ROOT)
    used, stale = _waiver(match="spare"), _waiver(rule="WIDTH")
    out = apply_waivers("verilator", findings, [used, stale], ["rtl/top.sv"])
    assert [f.waived_by for f in out[:2]] == ["r", None]
    assert out[2].rule == "WAIVER-UNUSED" and "WIDTH" in out[2].message
    # A waiver for a file that was not linted is not stale.
    other = _waiver(file="rtl/other.sv")
    assert apply_waivers("verilator", [], [other], ["rtl/top.sv"]) == []


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("- {rule: X, file: a.sv}\n", "reason"),
        ("- {rule: X, file: a.sv, reason: '  '}\n", "reason is empty"),
        ("- {rule: X, file: a.sv, reason: r, colour: red}\n", "colour"),
        ("- {rule: X, file: a.sv, reason: r, match: '('}\n", "waiver 1"),
        ("rule: X\n", "expected a list"),
    ],
)
def test_bad_waiver_files(tmp_path: Path, text: str, needle: str) -> None:
    path = tmp_path / "verilator.yaml"
    path.write_text(text)
    with pytest.raises(WaiverError, match=needle):
        load_waivers(path)


def test_builtin_waivers_load() -> None:
    from hdl_bag.flows.lint import builtin_waivers

    for tool in BACKENDS:
        builtin_waivers(tool)


def test_slang_flags_need_reasons() -> None:
    assert "-Wno-unused-def" in slang_flags()
    assert parse_flags("# c\n\n-Wno-x  # why\n") == ["-Wno-x"]
    with pytest.raises(ValueError, match="'-Wno-y' has no reason"):
        parse_flags("-Wno-y\n")
    with pytest.raises(ValueError, match="has no reason"):
        parse_flags("-Wno-z  #   \n")


def test_missing_tool_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    report = lint([tmp_path / "a.sv"], "a", tmp_path, tools=["verilator"])
    assert not report.ok
    assert [f.rule for f in report.findings] == ["TOOL-MISSING"]


def test_unknown_tool() -> None:
    with pytest.raises(ValueError, match="unknown lint tool"):
        lint([], "a", ROOT, tools=["spyglass"])


# ----------------------------------------------------------------- real tools (eda)

CLEAN = """\
module clean (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) q_o <= '0;
    else q_o <= d_i;
  end
endmodule
"""

DIRTY = """\
module dirty (
    input  logic       clk_i,
    input  logic [7:0] d_i,
    output logic [3:0] q_o
);
  logic spare;\t
  always_ff @(posedge clk_i) q_o <= d_i;
endmodule
"""


@pytest.mark.eda
@pytest.mark.parametrize("tool", sorted(BACKENDS))
def test_tool_passes_clean_rtl(tmp_path: Path, tool: str) -> None:
    (tmp_path / "clean.sv").write_text(CLEAN)
    report = lint([Path("clean.sv")], "clean", tmp_path, tools=[tool])
    assert report.ok, report.format()


@pytest.mark.eda
@pytest.mark.parametrize("tool", sorted(BACKENDS))
def test_tool_flags_dirty_rtl(tmp_path: Path, tool: str) -> None:
    assert shutil.which(BACKENDS[tool].executable), f"{tool} not installed"
    (tmp_path / "dirty.sv").write_text(DIRTY)
    report = lint([Path("dirty.sv")], "dirty", tmp_path, tools=[tool])
    assert not report.ok
    assert all(f.file == "dirty.sv" for f in report.unwaived if f.file), report.format()
