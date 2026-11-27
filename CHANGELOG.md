# Changelog

All notable changes to hdl-bag are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- New `bag check` rules from the random regression:
  - `FAB-APB3-SPLIT` (error): a narrower bus splits writes on the way to an APB3 bus that has no PSTRB.
  - `AXI-SPLIT-REFUSED` (warning): a master with CACHE tied to 0 gets SLVERR wherever a converter must split its transfers.
  - `PORTOPT-PSTRB-WORD` (warning): a tied PSTRB on a path that carries partial writes.
  - `PORTOPT-PSTRB-SPLIT` (error): a tied PSTRB behind a converter that turns writes into byte writes.
  - `MM-SMALL-AHB` (error): an AHB-Lite region smaller than 1 KB.
  - `MM-REGION-WORD` (warning): a region smaller than its slave's data word.
- `ACC-UNREACHABLE-SLAVE` also covers read-only and write-only combinations, and paths through bridges.
- The template's Memory_Map status shows the AHB-Lite 1 KB limit. Its 4 KB warning now shows on AXI4 and AXI4-Lite fabrics only.

### Changed

- `WRAP-FILE-HEADER` refuses a File header path outside the workbook's folder: an absolute path, or one that climbs out with `..`.
- `bag lint`, `bag sim` and `bag flow` refuse a file-list entry, or an include directory, that starts with `-`, because the tool would take it as an option.
- A Verilator UNOPTFLAT finding counts only if Yosys finds the loop at bit level on the flattened design.
- A lint or compile tool that crashes is a failure, even when it printed a finding first.
- `bag shrink` keeps a candidate only when it fails with the same messages, not only with the same rule IDs.
- Formal: protocol cover tasks search to 1.5 times the path model's depth, and the reset job's covers search to depth 80.

### Removed

- The `qualification` key of template manifests, and the `tables` variable of the instance-template context. Neither was read by anything.

### Fixed

- `bag_axi_dw` no longer gives a Verilator UNOPTFLAT on its request and response structs.
- Generated tests and formal jobs: many reference-model and property fixes found by the nightly random seeds. A shrunk seed for each bug is now in the regression corpus (`tests/configs/regress/`).

[Unreleased]: https://github.com/hdl-tools/hdl-bag/compare/v1.0.0...main

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
