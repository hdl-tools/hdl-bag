# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
"""Formal flow: SymbiYosys (the open-source backend of design/plan.md §6.4).

``run_sby`` runs one SBY job file and turns its output into findings. ``prove`` writes a
job for a list of files and properties and runs it, so callers never handle SBY syntax and
a commercial backend can later sit behind the same call. Parsing is pure, so it is tested
on captured output without the tools installed.

Findings, each an error:
    FORMAL-ASSERT     an assertion fails; the message names the counterexample trace
    FORMAL-INDUCTION  an assertion holds to the depth but is not inductive (mode prove)
    FORMAL-COVER      a cover is not reached within the depth (the property may be vacuous)
    FORMAL-TIMEOUT    the job ran out of time
    FORMAL-ERROR      the tool stopped on an error (a parse or elaboration error, ...)
    FORMAL-UNKNOWN    the job ended without a verdict
    TOOL-MISSING / TOOL-FAILED  as for the other flows
"""

import contextlib
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from hdl_bag.flows.backends import _same_version, _version
from hdl_bag.flows.base import Finding, Report, tool_version

if TYPE_CHECKING:
    from hdl_bag.gen import Outputs

Mode = Literal["bmc", "prove", "cover"]
MODES: Final = ("bmc", "prove", "cover")

# SBY v0.69 is the SymbiYosys of the pinned OSS CAD Suite (tools.lock OSS_CAD_SUITE_DATE).
_LINE: Final = re.compile(r"^SBY \d\d:\d\d:\d\d \[(?P<job>[^\]]+)\] (?P<msg>.*)$")
_DONE: Final = re.compile(r"^DONE \((?P<status>[A-Z]+), rc=\d+\)$")
_TRACE: Final = re.compile(r"^summary: counterexample trace(?: \[(?P<step>\w+)\])?: (?P<path>\S+)")
_FAILED: Final = re.compile(r"^summary:\s+failed assertion (?P<name>\S+) at (?P<loc>\S+)")
_UNREACHED: Final = re.compile(r"^summary:\s+(?P<name>\S+) at (?P<loc>\S+)$")
_LOC: Final = re.compile(r"^(?P<file>.+?):(?P<line>\d+)\.\d+(?:-\d+\.\d+)?$")


class SymbiYosys:
    name = "sby"
    executable = "sby"
    pinned = "v0.69"  # the SBY in tools.lock's OSS_CAD_SUITE_DATE (2026-10-01)

    def parse_version(self, output: str) -> str | None:
        return _version(r"^SBY v?(\d+\.\d+(?:\.\d+)?)", output)

    def is_pinned(self, version: str | None) -> bool:
        return _same_version(self.pinned, version)

    def parse(self, output: str, sources: dict[str, str], job_dir: str = "") -> list[Finding]:
        """Findings from SBY's OUTPUT. SOURCES maps a file name as the job sees it (a [files]
        destination, or a prefix of one) to its path relative to the run directory; JOB_DIR
        is the job file's directory relative to it, where SBY writes its traces."""
        out: list[Finding] = []
        done: dict[str, str] = {}
        errors: dict[str, str] = {}
        trace: dict[str, tuple[str | None, str]] = {}
        unreached: set[str] = set()
        jobs: list[str] = []
        for raw in output.splitlines():
            m = _LINE.match(raw)
            if not m:
                continue
            job, msg = m["job"], m["msg"]
            if job not in jobs:
                jobs.append(job)
            if d := _DONE.match(msg):
                done[job] = d["status"]
            elif t := _TRACE.match(msg):
                trace[job] = (t["step"], f"{job_dir}/{t['path']}" if job_dir else t["path"])
            elif f := _FAILED.match(msg):
                step, path = trace.get(job, (None, ""))
                file, line = _where(f["loc"], sources)
                short = _short(f["name"])
                if step == "induction":
                    out.append(Finding(self.name, file, line, "FORMAL-INDUCTION", "error",
                                       f"{job}: assertion {short} is not inductive "
                                       f"(trace {path})"))  # fmt: skip
                else:
                    out.append(Finding(self.name, file, line, "FORMAL-ASSERT", "error",
                                       f"{job}: assertion {short} fails (counterexample "
                                       f"{path})"))  # fmt: skip
            elif msg == "summary: unreached cover statements:":
                unreached.add(job)  # the list follows, one cover per line
                continue
            elif job in unreached and (u := _UNREACHED.match(msg)):
                file, line = _where(u["loc"], sources)
                out.append(Finding(self.name, file, line, "FORMAL-COVER", "error",
                                   f"{job}: cover {_short(u['name'])} not reached"))  # fmt: skip
                continue
            elif "ERROR:" in msg and job not in errors:
                errors[job] = msg
            unreached.discard(job)
        for job in jobs:
            status = done.get(job)
            mine = [f for f in out if f.message.startswith(f"{job}: ")]
            # A failed (FAIL) or non-inductive (UNKNOWN) property was named above.
            if status == "PASS" or (status in ("FAIL", "UNKNOWN") and mine):
                continue
            rule, text = {
                None: ("TOOL-FAILED", "ended without a verdict line"),
                "FAIL": ("FORMAL-UNKNOWN", "failed without naming a property"),
                "ERROR": ("FORMAL-ERROR", errors.get(job, "the tool stopped on an error")),
                "TIMEOUT": ("FORMAL-TIMEOUT", "ran out of time"),
            }.get(status, ("FORMAL-UNKNOWN", f"ended {status} (not proved)"))
            out.append(Finding(self.name, "", 0, rule, "error", f"{job}: {text}"))
        # A property that fails outright is not also reported as not inductive.
        failed = {(f.file, f.line) for f in out if f.rule == "FORMAL-ASSERT"}
        return [f for f in out if f.rule != "FORMAL-INDUCTION" or (f.file, f.line) not in failed]


def _short(name: str) -> str:
    """The property's own label from a hierarchical name (top.\\u_props.quiet_x)."""
    return name.rsplit(".", 1)[-1].lstrip("\\")


def _where(loc: str, sources: dict[str, str]) -> tuple[str, int]:
    m = _LOC.match(loc)
    if not m:
        return "", 0
    file = m["file"]
    for dest in sorted(sources, key=len, reverse=True):
        if file == dest or file.startswith(f"{dest}/"):
            file = sources[dest] + file[len(dest) :]
            break
    return file, int(m["line"])


def job_sources(job: Path, root: Path) -> dict[str, str]:
    """A job file's [files] section: each destination -> its source relative to ROOT."""
    out: dict[str, str] = {}
    section = None
    for raw in job.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
            continue
        if section != "[files]" or not line or line.startswith("#"):
            continue
        dest, _, src = line.partition(" ")
        path = (job.parent / (src.strip() or dest)).resolve()
        try:
            out[dest] = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            out[dest] = path.as_posix()
    return out


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """Stop PROC and everything it started (POSIX); only PROC itself elsewhere."""
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        proc.kill()
        return
    with contextlib.suppress(ProcessLookupError):
        killpg(proc.pid, getattr(signal, "SIGKILL", 9))  # SIGKILL is POSIX-only


def run_sby(job: Path, root: Path, *, timeout: float | None = None) -> Report:
    """Run the SymbiYosys JOB (a .sby file) in its own directory; ROOT is what finding paths
    are relative to. Each task's work directory is replaced (``sby -f``)."""
    backend = SymbiYosys()
    exe = shutil.which(backend.executable)
    if exe is None:
        missing = Finding(backend.name, "", 0, "TOOL-MISSING", "error",
                          f"'{backend.executable}' not found on PATH")  # fmt: skip
        return Report("formal", (missing,), (backend.name,))
    # SBY's own timeout option crashes in v0.69, so the time limit is enforced here. SBY
    # starts Yosys and the solvers as children: the whole process group goes on a timeout.
    with subprocess.Popen([exe, "-f", job.name], cwd=job.parent, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          start_new_session=True) as proc:  # fmt: skip
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            late = Finding(backend.name, "", 0, "FORMAL-TIMEOUT", "error",
                           f"{job.stem}: no verdict within {timeout:g} s")  # fmt: skip
            return Report("formal", (late,), (backend.name,))
    job_dir = Path(os.path.relpath(job.parent.resolve(), root.resolve())).as_posix()
    findings = backend.parse(output, job_sources(job, root), "" if job_dir == "." else job_dir)
    if proc.returncode != 0 and not findings:
        tail = output.strip().splitlines()[-1:] or ["no output"]
        message = f"{job.stem}: exit status {proc.returncode}: {tail[0]}"
        findings.append(Finding(backend.name, "", 0, "TOOL-FAILED", "error", message))
    version = tool_version(backend)
    notes = () if backend.is_pinned(version) else (
        f"sby {version or '(version unknown)'} is not the pinned {backend.pinned}",)  # fmt: skip
    return Report("formal", tuple(sorted(findings)), (backend.name,), notes=notes)


def sby_job(*, top: str, files: Sequence[str], incdirs: Sequence[str], depths: dict[Mode, int],
            copy: Sequence[tuple[str, str]], engine: str = "smtbmc yices",
            multiclock: bool = True) -> str:  # fmt: skip
    """The text of a SymbiYosys job: one task per mode of DEPTHS, each at its depth. FILES are
    read with yosys-slang in compile order, as named in the work directory; COPY lists the
    (destination, source) pairs of its [files] section, sources relative to the job file."""
    bad = sorted(set(depths) - set(MODES))
    if bad or not depths:
        raise ValueError(f"formal modes must be some of {', '.join(MODES)}: {bad or 'none'}")
    for mode, depth in depths.items():
        if depth < 1:
            raise ValueError(f"formal depth must be at least 1, not {depth} ({mode})")
    read = " ".join([*(f"-I {d}" for d in incdirs), "--top", top, *files])
    tasks = "".join(f"{m}\n" for m in depths)
    options = "".join(f"{m}: mode {m}\n{m}: depth {d}\n" for m, d in depths.items())
    copied = "".join(f"{dest} {src}\n" if src != dest else f"{dest}\n" for dest, src in copy)
    return (
        f"[tasks]\n{tasks}\n"
        f"[options]\n{options}{'multiclock on' if multiclock else 'multiclock off'}\n\n"
        f"[engines]\n{engine}\n\n"
        f"[script]\nplugin -i slang\nread_slang {read}\nprep -top {top}\n\n"
        f"[files]\n{copied}"
    )


def prove(
    files: Sequence[Path],
    top: str,
    root: Path,
    *,
    properties: Sequence[Path],
    depth: int,
    mode: Mode = "bmc",
    incdirs: Sequence[Path] = (),
    timeout: float | None = None,
) -> Report:
    """Prove PROPERTIES (SystemVerilog with assert, assume and cover; bound or instantiated
    into TOP) over FILES, in compile order, relative to ROOT. The job is
    ROOT/formal/<top>.sby; its task, and so its work directory, is ROOT/formal/<top>_<mode>."""
    rel = [Path(f).as_posix() for f in [*files, *properties]]
    if any(Path(r).is_absolute() or r.startswith("..") for r in rel):
        raise ValueError(f"formal files must be relative to {root} and inside it: {rel}")
    job = root / "formal" / f"{top}.sby"
    job.parent.mkdir(parents=True, exist_ok=True)
    text = sby_job(top=top, files=rel, incdirs=[Path(d).as_posix() for d in incdirs],
                   depths={mode: depth}, copy=[(r, f"../{r}") for r in rel])  # fmt: skip
    for d in incdirs:  # headers are found through the include directories: copy them too
        text += f"{Path(d).as_posix()} ../{Path(d).as_posix()}\n"
    with job.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return run_sby(job, root, timeout=timeout)


def run_formal(out: "Outputs", *, timeout: float | None = None) -> Report:
    """Run every SymbiYosys job `bag generate` wrote into OUT, as one report. Raises
    ValueError if it wrote none."""
    if not out.formal:
        raise ValueError(
            f"{out.root}: no generated formal (Generate formal (SymbiYosys) is N, a "
            "user-supplied fabric, or generated by an older hdl-bag)"
        )
    root = out.root.resolve()
    findings: list[Finding] = []
    notes: list[str] = []
    for rel in out.formal:
        report = run_sby(root / rel, root, timeout=timeout)
        findings += report.findings
        notes += [n for n in report.notes if n not in notes]
    return Report("formal", tuple(sorted(findings)), (SymbiYosys.name,), notes=tuple(notes))
