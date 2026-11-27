# hdl-bag

**BAG — Bus Architecture Generator.** Spec-based RTL bus-fabric generator.

hdl-bag turns an Excel spec workbook into AMBA bus-fabric RTL (AXI4,
AXI4-Lite, AHB-Lite, APB) inside a user-defined wrapper, together with lint,
unit tests, formal checks and collateral (C header, memory-map docs). The
workbook is converted to a YAML IR, which is the canonical, version-controlled
spec; every output is generated deterministically from it.

## Status

Version 1.0.0, on PyPI. Phases 1 to 4 and 6 are done, and v1.0.0 is released. Phase 5's
gate and report are outstanding (its nightly runs are not yet green).

| Works today | Not yet |
|---|---|
| Spec workbook template, reader and 103 validation checks | Two consecutive green nightly weeks, and the Phase 5 report |
| Canonical YAML IR, byte-identical across runs | |
| `bag regress`: random configurations through check, generate, lint, compile, the generated tests and formal | |
| Nightly regression workflow, and `bag shrink` to reduce a failing spec (checked against real formal tools) | |
| Example workbooks in [examples](examples), each `bag check` clean and run end to end in CI | |
| AXI4 and AXI4-Lite crossbars and shared buses (backed by [PULP axi](docs/third-party.md)), AHB-Lite single-layer bus, APB3/APB4 decoder-mux | |
| Bridges: axi2apb, axi2ahb, ahb2axi, ahb2apb, apb2axi, apb2ahb, axi2axil | |
| Per-port adapters: protocol, width, CDC, ID width, FIFO, register slice | |
| User-supplied fabrics (`bag_external`), with declared extra ports | |
| Wrapper in flat or SV-interface port style, nested sub-wrappers | |
| C header, memory-map doc (Markdown and HTML), port map, tie-off report | |
| CDC timing constraints (`constraints/<project>_cdc.sdc` and `.xdc`) and `ASYNC_REG` on the synchronizers; no tool checks them | |
| Lint (slang, Verilator, Verible) and Verilator compile flows | |
| Generated cocotb tests per config: byte-level routing, errors, arbitration, bursts, back-pressure, handshake orderings, reset during traffic, clock-ratio sweeps | |
| Functional coverage report from `bag sim`, and a reset-sequence document per design | |
| `bag formal`: SymbiYosys jobs per design: decode (a proof), quiet-in-reset (a proof), port protocol on every AXI4, AXI4-Lite, AHB-Lite and APB port, and routing per fabric and bridge (bounded) | |
| Module prefix: every unit, macro and header name BAG emits, so two subsystems and an unmodified PULP copy compile together (slang and Verilator) | |
| User manual (LaTeX, [docs/manual](docs/manual)) | |

The user manual is LaTeX in [docs/manual](docs/manual) (`latexmk -pdf hdl-bag.tex` there);
the `manual` workflow builds the PDF as the artifact `hdl-bag-manual`.

Phase reports: [Phase 1](docs/phase-1-report.md), [Phase 2](docs/phase-2-report.md), [Phase 3](docs/phase-3-report.md), [Phase 4](docs/phase-4-report.md), [Phase 6](docs/phase-6-report.md).

## Quick start

```sh
pip install hdl-bag
# or the unreleased main branch:
pip install git+https://github.com/hdl-tools/hdl-bag.git
# or, from a checkout (needs uv), and put `uv run` in front of every `bag` below:
uv sync

bag template --sample -o spec.xlsx      # the demo_ss sample and its header.txt (omit --sample for a blank one)
bag check spec.xlsx                     # every check; exit 1 on any error
bag ir spec.xlsx -o spec.ir.yaml        # write the canonical IR
bag generate spec.xlsx -o out/          # RTL, wrapper and collateral (.xlsx or .ir.yaml, checked either way)
bag lint out/                           # Project > Lint tools (slang, Verilator, Verible): zero unwaived findings
bag sim out/                            # generated cocotb tests (pip install "hdl-bag[sim]")
bag formal out/                         # generated SymbiYosys jobs: every assertion holds, every cover is reached
bag flow spec.xlsx -o out/              # check -> generate -> lint -> elaborate -> sim (add --formal for formal)
bag regress --seeds 10 -o out/regress   # random configs through the whole flow; --seed S reruns one
```

`bag regress` draws a random configuration per seed (seed j of a run is
`--seed-base` × 10000 + j; `--shard i/k` runs every k-th), writes its workbook
and IR, and runs it through check, generate, lint, compile, the generated tests
and formal. Formal per seed is the decode job, then the other jobs within
`--formal-budget` seconds (600 by default); a job cut off is reported as a
timeout, not a failure. Each seed's `result.json` holds the failing step, the
findings and the commands that reproduce it; `summary.md` lists the run.

`--style flat|sv-interface` on `generate` and `flow` overrides the workbook's
port style. `hdl-bag` is an alias for the `bag` command. The sample's two
warnings are real: two of its AXI4 masters reach width converters that answer
WRAP bursts with SLVERR (`AXI-WRAP-REFUSED`).

`bag formal` runs each job in turn (`--timeout` applies per job). The
quiet-in-reset job is a proof (k-induction). The protocol job is a bounded
check: it says no assertion fails within its depth, and its covers show the
check reached real transfers; the report notes which tasks are bounded. Both
hold under the environment the job states: AMBA reset and handshake rules, the
reset sequence of `doc/reset.md`, and alias pins that change only in reset.

Generated output (under the output directory):

```
rtl/pkg/<package>.sv     parameter package (widths, decode tables)
rtl/<top>.sv             top wrapper, plus one file per sub-wrapper
rtl/inhouse/*.sv         the in-house IP the design uses (names carry Module prefix, if set)
rtl/vendor/**            the vendored PULP files the design uses, with their LICENSE files and
                         the texts of any other licence they are under
                         (unit, macro and file names, headers included, carry Module prefix, if set)
rtl/ext/<module>.sv      stub of a user-supplied fabric: replace it with your RTL
<project>.f              file list in compile order
sw/<project>_memmap.h    C header
doc/memmap.md, .html     memory-map document
doc/port_map.md          per-fabric port map
doc/tieoffs.md           component ports tied, defaulted, fitted or unused, and why
doc/reset.md             reset sequence: domains, crossings, which resets must assert together
tests/<project>_tests.py cocotb tests that apply to this config
tests/<project>.ir.yaml  the IR those tests read
tests/<top>_ready_shim.sv  drives each AXI READY from its VALID, for bag sim --ready-shim
formal/<project>_reset.sv, .sby  quiet-in-reset properties and their SymbiYosys job (prove and cover)
formal/<project>_protocol.sv, .sby  AXI4-Lite and APB port-protocol checkers (ZipCPU) and their job
formal/bag_step.v, vendor/  the formal environment's step registers; the ZipCPU checkers, with LICENSE
                         (these file names carry Module prefix, if set)
sim/coverage.md          written by bag sim: what the tests exercised (paths, responses, secure, bursts); report only
```

## Examples

`examples/` in the repository holds three workbooks, each `bag check` clean: `apb_min.xlsx` (one
APB4 master, three slaves), `mcu_ahb.xlsx` (an AHB-Lite microcontroller bus with an APB bus behind
a bridge) and `soc_4x8.xlsx` (an AXI4 crossbar with 4 masters and 8 slaves). Try
`bag flow examples/soc_4x8.xlsx -o out/`. See `examples/README.md`.

## Requirements

- Python 3.11 or newer. Reading, checking, generating and the collateral need nothing
  else, on Linux, macOS and Windows.
- For `bag lint`, `bag flow`, `bag sim` and `bag formal`: Verilator, slang and Verible,
  plus SymbiYosys and Yosys with the yosys-slang plugin (OSS CAD Suite). The pinned
  versions are in [tools.lock](tools.lock), and the container image
  `ghcr.io/hdl-tools/hdl-bag-tools` ([tools/Dockerfile](tools/Dockerfile)) has all of
  them for `linux/amd64` and `linux/arm64`.
- **To lint (or sim, or formal) in that image**, run it from your checkout, after
  `bag generate` has written `out/`. A spec or output directory outside the checkout needs
  its own `-v` mount:

  ```sh
  docker run --rm \
    -v "$PWD:/work" \
    -v hdl-bag-venv:/venv \
    -e UV_PROJECT_ENVIRONMENT=/venv \
    -e UV_LINK_MODE=copy \
    -w /work \
    ghcr.io/hdl-tools/hdl-bag-tools:main \
    sh -c 'uv run bag lint out/'
  ```

- **On macOS the EDA flows run in that image**, which is native on Apple Silicon. The
  pinned Verilator cannot be built on macOS itself, so there is no supported native EDA
  path there; `scripts/install_tools.py` installs the pinned tools on a Linux or macOS
  host and says so if it cannot.

## Development

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run mypy src tests scripts
uv run pytest                 # everything except EDA tests
uv run pytest -m eda          # lint, compile, cocotb and formal tests (in the tools image)
uv run pytest -m "eda and smoke"  # the per-PR subset of those (tests/smoke.py)
```

## Licence

hdl-bag is licensed under the [Apache License 2.0](LICENSE).
Files generated by hdl-bag belong to you and are not covered by that
licence; see [docs/licensing.md](docs/licensing.md).
