# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
import shutil
from importlib import import_module
from pathlib import Path

import pytest

from hdl_bag.flows import Finding, Report, WaiverError, lint, load_waivers
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


STALE = "- {rule: WIDTH, file: 'rtl/*.sv', reason: written for the pinned tool}\n"


def _lint_with_a_stale_waiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                              found: list[Finding], version: str | None) -> Report:  # fmt: skip
    """Lint rtl/top.sv with Verilator, a waiver nothing will use, and a fake Verilator that
    reports FOUND and says it is VERSION."""
    lint_module = import_module("hdl_bag.flows.lint")
    waivers = tmp_path / "verilator.yaml"
    waivers.write_text(STALE)
    monkeypatch.setattr(lint_module, "run_backend", lambda *a, **k: list(found))
    monkeypatch.setattr(lint_module, "tool_version", lambda backend: version)
    return lint([Path("rtl/top.sv")], "top", tmp_path, tools=["verilator"], extra_waivers=[waivers])


def test_no_unused_waivers_for_a_tool_that_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = Finding("verilator", "", 0, "TOOL-MISSING", "error", "'verilator' not found on PATH")
    report = _lint_with_a_stale_waiver(tmp_path, monkeypatch, [missing], None)
    assert [f.rule for f in report.findings] == ["TOOL-MISSING"]


def test_no_unused_waivers_when_the_tool_stopped_on_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verilator stops at an error, before most warnings: an unused waiver proves nothing."""
    error = Finding("verilator", "rtl/top.sv", 3, "error", "error", "syntax error")
    report = _lint_with_a_stale_waiver(tmp_path, monkeypatch, [error], Verilator.pinned)
    assert [f.rule for f in report.findings] == ["error"]


def test_unused_waiver_on_the_pinned_version_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _lint_with_a_stale_waiver(tmp_path, monkeypatch, [], Verilator.pinned)
    assert [f.rule for f in report.findings] == ["WAIVER-UNUSED"]
    assert not report.ok and report.notes == ()


def test_unused_waiver_on_another_version_is_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The waivers were written against the pinned tools; on another version a waiver that
    matches nothing says so, rather than failing as stale."""
    report = _lint_with_a_stale_waiver(tmp_path, monkeypatch, [], "4.228")
    assert report.ok and report.findings == ()
    assert report.notes == (
        f"verilator 4.228 is not the pinned {Verilator.pinned}: 1 waiver(s) written for "
        f"{Verilator.pinned} matched nothing (WIDTH on rtl/*.sv)",
    )
    assert report.notes[0] in report.format()


@pytest.mark.parametrize(
    ("backend", "output", "version"),
    [
        (Verilator(), "Verilator 5.052 2026-09-05 rev v5.052\n", "5.052"),
        (Slang(), "slang version 12.0.0+545f88b2e\n", "12.0.0"),
        (Verible(), "Version\tv0.0-4296-g0f262651\nCommit-Timestamp\t2026-09-23T08:15:19Z\n",
         "v0.0-4296-g0f262651"),
        (Verilator(), "something else\n", None),
    ],
)  # fmt: skip
def test_version_parsing(
    backend: Slang | Verilator | Verible, output: str, version: str | None
) -> None:
    assert backend.parse_version(output) == version


def test_pinned_versions_match_tools_lock() -> None:
    lock = dict(
        line.split("=", 1)
        for line in (Path(__file__).parents[2] / "tools.lock").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert {b.name: b.pinned for b in BACKENDS.values()} == {
        "slang": lock["SLANG_VERSION"], "verilator": lock["VERILATOR_VERSION"],
        "verible": lock["VERIBLE_VERSION"],
    }  # fmt: skip


@pytest.mark.parametrize(
    ("backend", "version", "same"),
    [
        (Verilator(), "5.052", True), (Verilator(), "5.040", False), (Slang(), "12.0.0", True),
        (Slang(), "12.1.0", False), (Verible(), "v0.0-4296-g0f262651", True), (Verible(), None, False),
    ],
)  # fmt: skip
def test_is_pinned(backend: Slang | Verilator | Verible, version: str | None, same: bool) -> None:
    assert backend.is_pinned(version) is same


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
def test_tools_image_has_the_pinned_versions(tool: str) -> None:
    """Where the waivers are judged (CI, the tools image), every tool reports the pinned
    version, so an unused waiver is a WAIVER-UNUSED error there and never only a note."""
    from hdl_bag.flows.base import tool_version

    backend = BACKENDS[tool]
    version = tool_version(backend)
    assert backend.is_pinned(version), f"{tool} reports {version}, pinned {backend.pinned}"


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


def test_compile_reports_errors_only() -> None:
    from hdl_bag.flows.compile import VerilatorBuild

    found = VerilatorBuild("obj/top").parse(VERILATOR_OUT, ROOT)
    assert [(f.rule, f.severity) for f in found] == [("error", "error")]
