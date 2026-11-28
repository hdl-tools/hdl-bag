# Changelog

All notable changes to hdl-bag are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

**Upgrading from 1.0.0:** a workbook that 1.0.0 accepted can now fail `bag check` and
`bag generate`. Three new rules are errors (`FAB-APB3-SPLIT`, `PORTOPT-PSTRB-SPLIT`,
`MM-SMALL-AHB`), and `WRAP-FILE-HEADER` refuses a File header outside the workbook's folder.
The `demo_ss` sample now gives four warnings instead of two (two `PORTOPT-PSTRB-WORD`).

### Added

- New `bag check` rules:
  - `FAB-APB3-SPLIT` (error): a bus narrower than an APB3 bus's word, and than the master,
    splits each write into narrower writes. APB3 has no PSTRB, so each one stores the whole
    word, and even full-word writes are corrupted.
  - `AXI-SPLIT-REFUSED` (warning): a master or bridge has CACHE tied to 0, so the AXI4 to
    AXI4-Lite converter on its way answers SLVERR to bursts and to transfers it must split.
  - `PORTOPT-PSTRB-WORD` (warning): PSTRB is tied on an APB4 port wider than 8 bits. Tied to
    the default, a write of part of a word overwrites the rest of it; tied to 0, no write
    stores anything.
  - `PORTOPT-PSTRB-SPLIT` (error): PSTRB is tied on an APB4 port, and `bag_axi2ahb` (or a
    narrower bus) on the way turns writes into byte writes, each of which stores the whole
    word.
  - `MM-SMALL-AHB` (error): an AHB-Lite region, or alias, smaller than 1 KB.
  - `MM-REGION-WORD` (warning): a region smaller than its slave's data word.
- The template's Memory_Map status shows "AHB < 1 KB" for a small AHB-Lite region.
- The manual's install chapter covers installing from PyPI (`pip install hdl-bag`, with the
  `sim` extra for cocotb) and running the release's tools image with it.

### Changed

- `ACC-UNREACHABLE-SLAVE` also warns when no access of any master gets into a slave for
  another reason: a write-only region that every master may only read, a slave behind a
  bridge that no master may use, a secure-only region whose masters all have PROT tied, or a
  region above every master's address range. The generated tests leave such a slave out.
- `WRAP-FILE-HEADER` refuses a File header path outside the workbook's folder: an absolute
  path, or one that climbs out with `..`.
- `bag lint`, `bag sim` and `bag flow` refuse a file-list entry or include directory that
  starts with `-`, because the tool would take it as an option.
- A Verilator UNOPTFLAT warning is waived when Yosys finds no real loop in the flattened
  design. Without Yosys, or if it fails, the warning still counts, with a note.
- `TOOL-FAILED` is reported for an exit status other than 0 or 1, or for 1 with no finding
  BAG could read, even when the tool printed a finding first.
- A `TEST-FAILED` finding of `bag sim` gives the first line of the test's failure message.
- `bag shrink` keeps a smaller configuration only when it fails the same step, rules and test
  or formal task, with at least one of the same messages (addresses and other numbers aside).
- The template's 4 KB warning on Memory_Map shows on AXI4 and AXI4-Lite fabrics only.
- Formal: protocol cover tasks search 1.5 times as deep as the depth BAG estimates for the
  path (at least 24 steps), and the reset job's covers search to depth 80.
- `bag regress` records each seed's configuration dimensions in `summary.json`.

### Fixed

- `bag_axi_dw` no longer gives a Verilator UNOPTFLAT on its request and response structs.
- `bag_ahb2axi` and `bag_apb2axi` no longer give a Verilator UNUSEDSIGNAL when the AXI side
  has fewer address bits than the APB or AHB side.
- Generated tests and formal jobs no longer fail falsely on some configurations, among them:
  an APB3 bus on the path (it drops PROT and stores whole words), AHB read alignment behind an
  AXI4-Lite master, an access split across a region's end, a master with no target on a
  channel, and an AXI4-Lite converter that sends one write at a time.

<!-- At a release, rename this section and point this link at the new tag. -->
[Unreleased]: https://github.com/hdl-tools/hdl-bag/compare/v1.0.0...HEAD

## [1.0.0] - 2026-11-23

First release. hdl-bag (BAG, the Bus Architecture Generator) turns an Excel spec workbook into
AMBA bus-fabric RTL (AXI4, AXI4-Lite, AHB-Lite, APB) inside a wrapper you define, with the
collateral, lint flows, tests and formal jobs to go with it. The canonical spec is a YAML IR, and
every output is generated deterministically from it.

### Added

**Spec workbook and checks**
- `bag template` writes a blank spec workbook, or the `demo_ss` sample with its file header.
- `bag check` reads the workbook and runs every check. Each check has a rule ID, and the CLI exits 1 on
  any error. The checks cover protocols, address maps and aliases, access rules, clocks and
  resets, adapters, bridges and user-supplied fabrics.
- Three example workbooks in `examples/` (`apb_min`, `mcu_ahb`, `soc_4x8`), each `bag check`
  clean and run end to end in CI.

**Canonical YAML IR**
- `bag ir` writes the IR. It is byte-identical across runs, carries a schema version, and is
  version-controlled in place of the workbook. `bag generate` accepts either.

**Fabrics, bridges and adapters**
- AXI4 and AXI4-Lite crossbars and shared buses, an AHB-Lite single-layer bus, and an APB3/APB4
  decoder-mux.
- Bridges: `axi2apb`, `axi2ahb`, `ahb2axi`, `ahb2apb`, `apb2axi`, `apb2ahb`, `axi2axil`.
- Per-port adapters: protocol, data width, clock-domain crossing, ID width, FIFO, register slice.
- User-supplied fabrics (`bag_external`) with declared extra ports. BAG writes a stub for you to
  replace with your RTL.
- Address decode with aliases, access rules, secure-only regions and a default slave.
- Third-party RTL is vendored and copied into the output with its licence files: PULP axi and
  common_cells, and the ZipCPU protocol checkers for formal.

**Wrapper**
- Flat or SystemVerilog-interface port style (`--style` overrides the workbook), and nested
  sub-wrappers.

**Collateral**
- C header, memory-map document (Markdown and HTML), per-fabric port map, tie-off report, and a
  reset-sequence document per design.
- CDC timing constraints (`constraints/<project>_cdc.sdc` and `.xdc`), with `ASYNC_REG` and
  `DONT_TOUCH` on the synchronizer flops.

**Module prefix**
- A prefix applied to every unit, macro and header name BAG emits, so two subsystems and an
  unmodified PULP copy compile together (tested with slang and Verilator).

**Flows**
- `bag lint` (slang, Verilator, Verible, with built-in waivers), a Verilator elaboration, `bag sim`,
  `bag formal` and `bag flow`, which runs check, generate, lint, elaborate and sim, and formal
  with `--formal`.
- `bag sim` runs generated cocotb tests, chosen per configuration: byte-level routing, errors,
  arbitration, bursts, back-pressure, handshake orderings, reset during traffic and clock-ratio
  sweeps. It writes a functional coverage report.
- `bag formal` runs generated SymbiYosys jobs: decode and quiet-in-reset (proofs), port protocol
  on every AXI4, AXI4-Lite, AHB-Lite and APB port, and routing per fabric and bridge (bounded).
- A container image with the pinned EDA tools (`tools.lock`) for `linux/amd64` and `linux/arm64`.

**Regression and debugging**
- `bag regress` draws random configurations and runs each through check, generate, lint, compile,
  the generated tests and formal. `--seed S` reruns one, and each seed leaves a `result.json` with
  the commands that reproduce it.
- `bag shrink` reduces a failing IR to a small one that fails the same way.

**Documentation**
- A LaTeX user manual in `docs/manual/`, whose examples are replayed by tests, and which lists
  every check rule.
- A template author guide, and a generated bill of materials (`docs/bom.md`) of every vendored
  file and Python package with its licence.

### Known limitations

The user manual's chapter "Limits and known problems" is the full list. In short:

- **Protocols.** AXI4, AXI4-Lite, AHB-Lite, APB4 and APB3 only. No AXI3, AXI5, full AHB or
  AXI-Stream, and no atomic transactions. AHB-Lite is a single-layer bus, an APB fabric has one
  master, and crossbars arbitrate round-robin only.
- **Bursts.** WRAP bursts through a width converter or into AXI4-Lite or APB are answered with
  SLVERR, and so are some non-modifiable bursts. `bag check` warns about the width-converter case
  only (`AXI-WRAP-REFUSED`).
- **Bridges.** `axi2ahb` handles one transaction at a time, and a master must not make RREADY
  wait on a later write. Partial writes into AHB-Lite become byte writes, and into APB3 become
  full-word writes. Exclusive accesses fail through the bridges.
- **Clock-domain crossings.** The CDC constraints are generated, not tool-verified: BAG runs no
  synthesis, timing or CDC tool. Resets of a reset group must assert together, and alias-enable
  pins must change only in reset.
- **No timeouts.** A slave that never answers stops its master.
- **Formal is bounded**, except the decode and reset proofs. The protocol and route jobs check to
  a fixed depth, there is no liveness check, and nothing is proved inside the vendored PULP
  blocks. Route jobs compare only address, direction, secure bit and slave port.
- **Tests.** Simulation coverage is a report with no closure target, and a run uses one seed.
  User-supplied fabrics get no generated tests and no formal jobs.
- **Tools.** BAG is tested with the tool versions in `tools.lock`, and only on Verilator for
  simulation. The EDA flows are supported in the tools image. On macOS there is no supported
  native EDA path. The deep formal proofs and `bag regress` have not been run on `linux/arm64`.
- **Not provided.** Functional-safety mechanisms or qualification evidence, plug-in templates,
  IP-XACT, and area or timing reports.

### Licensing

hdl-bag is licensed under the [Apache License 2.0](LICENSE). Files that `bag generate` writes from
your specification belong to you and are not covered by that licence, see
[docs/licensing.md](docs/licensing.md). Generated output can include vendored third-party RTL,
which keeps its own licence. [docs/bom.md](docs/bom.md) lists every vendored file and Python
package with its licence.

[1.0.0]: https://github.com/hdl-tools/hdl-bag/releases/tag/v1.0.0
