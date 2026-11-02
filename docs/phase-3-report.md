# Phase 3 report: AXI fabrics, adapters, bridges, PULP integration

Status: **gate passed in CI on `main`** at `494df94` (through #29), 2026-11-02, and locally with this report's two fixes (see the review below). The CI run of this report's PR is linked in its description. Phase 4 is waiting for owner review.

## What was built

| Area | Where | Summary |
|---|---|---|
| Vendored PULP | `src/hdl_bag/rtl/vendor/pulp/` | pulp-platform/axi v0.39.11, common_cells 1.39.0 and tech_cells_generic 0.2.11, copied by `scripts/vendor_pulp.py` and checked in CI. `bender:<pkg>:<module>` sources resolve through `index.json` to file closures, so `bag generate` copies only what a design needs, offline. |
| Lint scope | `src/hdl_bag/flows/` | Every tool parses and elaborates vendored code. Its warnings go to a third-party section and are not judged; Verible skips those files. In-house and generated code stay at zero unwaived findings. |
| AXI fabrics | `rtl/inhouse/`, `rtl/vendor/derived/` | A per-master guard (`bag_addr_decode`) is the only decoder and writes route bits above the address. Crossbar: PULP `axi_xbar` / `axi_lite_xbar` on the map "route bits = slave index". Shared bus: `bag_axi_mux` / `bag_axil_mux` (PULP derivatives, round robin or fixed priority), then FIFO, cut or multicut, then demux. |
| Adapters | `rtl/inhouse/` | Flat wrappers for pc (both directions), dw, cdc, iw, fifo, cut and multicut, in AXI4 and AXI4-Lite variants. Chain order D9 plus D33. |
| PULP-backed bridges | `rtl/inhouse/` | `bag_axi2apb`, `bag_axil2apb`, `bag_axi2axil`. Width, FIFO and CDC inside the bridge (D10). |
| In-house bridges | `rtl/inhouse/` | `bag_axi2ahb` (PULP width, FIFO and CDC in front of an in-house core: IDs serialized, INCR bursts as AHB INCR with 1 KB restarts, WRAP and FIXED as singles, partial strobes as byte writes). `bag_ahb2axi`, `bag_apb2axi`, `bag_apb2ahb` (4-phase handshake with `bag_sync`, single-transfer engines). |
| User-supplied fabrics | `gen/external.py` | `bag_external` is instantiated as a module `<project>_<fabric>` with a defined port contract and a generated stub. The new User_Ports sheet (schema 0.4) declares its extra ports and how each connects. |
| Generator | `src/hdl_bag/gen/` | ID and user widths, width fitting, chained pass-through windows, adapter chains in nested sub-wrappers. Template guards (a misspelled signal, or a bus signal a template loses, stops generation). New `doc/tieoffs.md`. |
| Checks | `src/hdl_bag/checks/` | 5 new rules, bringing the catalogue to **86** (79 errors, 7 warnings): `CLK-CDC-RESET` and the four `USERPORT-*` rules. Every rule has a broken-workbook test. |
| Gate corpus | `tests/configs/p3/` | 25 configs written at workbook level and resolved through `bag check` with zero findings, 16 of them mixed-protocol. demo_ss joins every corpus test. |
| Verification | `tests/rtl/`, `tests/gen/` | cocotbext-axi models. The route model gained exact AXI response codes, PROT and AxCACHE through bridge chains, and read coverage. Directed tests per in-house bridge. AHB burst and AXI handshake monitors. All VALID/READY orderings on every channel a model answers (D38). |
| CI | `.github/workflows/pr.yml` | Elaboration-only gate compile, pytest-xdist, and three `eda-shard` jobs balanced by recorded durations, behind one `eda` check. |

Pull requests: #18 (vendoring), #19 (generator for AXI), #20 (fabrics), #21 (D32), #22 (adapters), #23 (D33), #24 (PULP bridges), #25 (faster eda), #26 (in-house bridges), #27 (`bag_external`), #28 (user ports), #29 (gate corpus), and this report's PR. #17 updated the README for Phase 2.

## Gate evidence

CI run on `main` after #29: <https://github.com/hdl-tools/hdl-bag/actions/runs/41439215953>. Jobs: `test` on Ubuntu with Python 3.11 and 3.14 and on Windows with 3.11; `golden`, `vendor` and three `eda-shard` jobs in the tools image, behind one `eda` check. Locally, on this report's tree: 188 eda tests pass (6 min 38 s on 4 CPUs); ruff, ruff format and mypy --strict are clean. Manual: `bag flow` on the golden workbook exits 0 in both port styles, and `doc/memmap.html` renders correctly in Chrome (all five fabrics, windows, secure flags, the `cpu0` alias, miss responses).

| Gate item (plan §8 Phase 3) | Evidence |
|---|---|
| The full demo_ss golden spec generates, lints with zero unwaived findings and elaborates in both port styles | `test_corpus.py::test_gate_lint_and_compile[golden/demo_ss-*]`. Also `bag flow tests/golden/demo_ss.xlsx` in each style: exit 0, 0 unwaived findings, 88 waived (all unused package parameters), 529 third-party warnings not judged, Verilator build clean. demo_ss also passes the connectivity oracle and every route-simulation test. |
| 15 hand-written mixed-protocol configs elaborate, together covering every bridge, a fixed-priority shared bus, a CDC on a master and on a slave, and width up and down | `test_gate_lint_and_compile` in both port styles. `test_phase_3_gate_coverage` computes the list from the IR: at least 15 configs with more than one protocol, every bridge and fabric template in the registry, both arbitration policies on both shared buses, cdc, fifo and cut on each side, iw, pc in both directions, multicut, aliases and secure-only regions on AXI, adapters in nested sub-wrappers. |
| The parameter mapping of each PULP-backed template is reviewed against the sources at v0.39.11 | The review below. It found one bug and the design-agent review of this report a second (both fixed in this PR), and a wording error in the workbook (known gap 1). |
| Each in-house bridge passes directed tests, including error propagation | `tests/rtl/test_inhouse.py`: `test_ahb2apb` 5 configurations, `test_apb2ahb` 6, `test_apb2axi` 6, `test_ahb2axi` 7, `test_axi2ahb` 8. They vary widths, AXI4 and AXI4-Lite, offsets, FIFO, ID width, and clock ratios 1:1 without and with CDC, 1:3, 3:1 and 10:7. Errors are injected on the first part only and the last part only of split transfers, and mid-burst. |
| Byte-identical generation; ruff, ruff format and mypy --strict clean; existing tests green | `test_generate.py`, `test_wrapper_rules.py`, CI. |

Beyond the gate, every routed config has an IR → workbook → IR identity check, the connectivity oracle, the IR-driven route simulation, arbitration tests on multi-master fabrics, and the handshake-ordering test. `bag_external` configs are not route-simulated, because BAG does not model the user's fabric: `test_external.py` drives traffic through pass-through fabrics built from the port contract instead, and a meta-test pins which configs are excluded.

## PULP parameter-mapping review

Reviewed against `src/hdl_bag/rtl/vendor/pulp/` at axi v0.39.11 (commit 42ca270) and common_cells 1.39.0 (commit 9ca8a76). Line numbers are in the vendored files. Parameter names and lines were extracted mechanically from every PULP instantiation in `rtl/inhouse/` and each was located in its declaring file: all exist. `tests/vendor/` also pins the assumed names in CI. The meaning of each was then read against PULP's comments, assertions and logic. "Direct" means the value passes straight through with the same meaning. Type parameters (`*_t`) are omitted: they come from the `AXI_TYPEDEF_*` macros.

### Findings

1. **Bug, fixed in this PR.** `bag_axi2apb` from an AXI4-Lite upstream onto an 8-bit APB passed `bag check` and failed elaboration, because `axi_lite_dw_converter` cannot convert to 8 bits (a size-changing cast to zero at `axi_lite_dw_converter.sv:128`; the assumption `AxiMstPortDataWidth > 8` at `:463` says the same, but sits inside `ifndef VERILATOR`). No config exercised narrow APB behind this bridge. That path now runs on AXI4: the template connects a Lite upstream to `bag_axi2apb` with single-beat defaults. New corpus config `mixed_narrow_apb` covers AXI4 and AXI4-Lite upstreams at 8 and 16 bits, and a 64-bit AXI4-Lite upstream onto an 8-bit APB3 with a FIFO and a CDC; it failed before the fix. The route test `narrow_apb_behind_axi2apb_keeps_lanes_strobes_and_errors` checks addresses, lanes and strobes, and an error on every beat, and the coverage test requires these paths.
2. **Workbook wording, not fixed (known gap 1).** The notes on Fabrics > Max mst trans and Max slv trans do not match PULP:
   - Max mst trans reads "per connected master". On an AXI4 crossbar it reaches the demux's counters, which are **per ID** (`axi_xbar_unmuxed.sv:175`, `axi_demux_id_counters.sv`). On an AXI4-Lite crossbar the note is right: it is a FIFO depth per master (`axi_lite_xbar.sv:151`).
   - Max slv trans reads "per ID towards each connected slave". No path has that meaning. On an AXI4 crossbar it sets the depth of the mux's write-order FIFO, so it limits **writes per slave whose data is not finished** (`axi_xbar.sv:139`, `axi_mux.sv:319`, popped on WLAST at `:365`); reads are not limited by it. On an AXI4-Lite crossbar it is a FIFO depth per slave and limits reads too (`axi_lite_xbar.sv:223`).
   - **Other paths differ again.** On an AXI4 shared bus, Max slv trans sets the single demux's per-ID counters, so it limits reads and writes per ID across all slaves (`bag_axi_shared_bus.sv:422`), and Max mst trans sets the guards' counters and the shared mux's write FIFO. A bridge takes its upstream fabric's Max slv trans as `AxiMaxReads` and as the read and write limits of `axi_to_axi_lite` (`bag_axi2apb.sv:187`, `:221-222`), so reads are limited there. The corrected notes must cover each of these.

   PULP's own comment (`axi_pkg.sv:489-494`) is also loose. BAG passes the values where it should (with finding 5's exception); the note is wrong. Comments are part of the workbook schema, so correcting them needs a schema bump.
3. **Deliberate differences from a plain mapping.** `MaxTrans + 1` on `axi_demux` and `axi_demux_simple`, because their per-ID counters stall at the maximum (found in the PR 3 review, with a test; the crossbar was missed, see finding 5). So a value of N admits at least N per ID, and up to `2^clog2(N+1) - 1` (4 admits 7). The AXI4-Lite demux has no such counter: its `MaxTrans` is a FIFO depth (`axi_lite_demux.sv:272`), so no +1.
4. **PULP limits that remain** are documented in manifests and go to the user guide (D34): WRAP and non-modifiable bursts through pc and dw get SLVERR.
5. **Bug, fixed in this PR, found by the design-agent review of this report.** `bag_axi_xbar` passed Max mst trans to PULP without the `+ 1` of finding 3, and this review's first version called that mapping direct. `axi_demux_simple` sizes its counters as `idx_width(MaxTrans)` bits and stops at all ones (`axi_demux_simple.sv:69`, `axi_demux_id_counters.sv:125`), so a crossbar set to 4 admitted 3 per ID, and one set to 2 admitted 1. A throughput loss, not a deadlock. New route test `axi4_crossbars_admit_max_mst_trans_per_id`; it failed before the fix ("3 reads on one ID reached ram, Max mst trans is 4").

### Crossbar configuration

`axi_pkg::xbar_cfg_t` (`axi/src/axi_pkg.sv`). The PULP naming trap is confined here: PULP "slave ports" are where BAG masters attach.

| `xbar_cfg_t` field | Line | `bag_axi_xbar` | `bag_axil_xbar` | Review |
|---|---|---|---|---|
| `NoSlvPorts` | 485 | `NoMasters` | `NoMasters` | PULP slave ports face BAG masters. |
| `NoMstPorts` | 488 | `NoSlaves` | `NoSlaves` | PULP master ports face BAG slaves. |
| `MaxMstTrans` | 491 | `MaxMstTrans + 1` | `MaxMstTrans` | **+1 on AXI4 only** (finding 5). Finding 2: per ID on AXI4. |
| `MaxSlvTrans` | 494 | `MaxSlvTrans` | `MaxSlvTrans` | Finding 2: unfinished writes per slave on AXI4; a FIFO depth per slave on AXI4-Lite. |
| `FallThrough` | 498 | `1'b0` | `1'b0` | Registered FIFOs. |
| `LatencyMode` | 502 | package `LatencyMode` | package `LatencyMode` | `gen/package.py` builds the 10-bit values with the same OR expressions as `axi_pkg.sv:472-478`. |
| `PipelineStages` | 505 | `32'd0` | `32'd0` | No extra crossing stages. |
| `AxiIdWidthSlvPorts` | 508 | `IdWidthMst` | `32'd0` | Master-side ID. PULP widens it by clog2 of the master count, which is how BAG derives the slave-side width. AXI4-Lite has no ID. |
| `AxiIdUsedSlvPorts` | 511 | `IdWidthMst` | `32'd0` | All ID bits take part in ordering. |
| `UniqueIds` | 513 | `1'b0` | `1'b0` | BAG does not promise unique IDs. |
| `AxiAddrWidth` | 515 | `ReqAddrWidth` | `ReqAddrWidth` | Address plus route bits, written by the guard. |
| `AxiDataWidth` | 517 | `DataWidth` | `DataWidth` | Direct. |
| `NoAddrRules` | 521 | `NoSlaves` | `NoSlaves` | The trivial map: route bits = slave index. Real decoding is in the guard. |

### Every other instantiation

**`axi_cdc`** (`axi/src/axi_cdc.sv`), used by `bag_axi2ahb`, `bag_axi2apb`, `bag_axi2axil`, `bag_axi_cdc`, `bag_axil2apb`, `bag_axil_cdc`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `LogDepth` | 33 | `2`; `LogDepth` | FIFO depth 2^LogDepth. Bridges use 2 (depth 4); the cdc adapter's parameter defaults to 2. |
| `SyncStages` | 35 | `2`; `SyncStages` | 2, the PULP default. |

**`axi_cut`** (`axi/src/axi_cut.sv`), used by `bag_axi_cut`, `bag_axi_shared_bus`, `bag_axil_cut`, `bag_axil_shared_bus`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `Bypass` | 22 | `1'b0` | 0: the cut is real; a slice of none instantiates nothing. |

**`axi_demux`** (`axi/src/axi_demux.sv`), used by `bag_axi_shared_bus`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiIdWidth` | 43 | `IdWidthSlv` | Direct. |
| `AtopSupport` | 44 | `1'b0` | 0: v1 ties AWATOP to 0 (plan; D35 for v2). |
| `NoMstPorts` | 52 | `NoSlaves` | Direct. |
| `MaxTrans` | 53 | `MaxSlvTrans + 1` | **+1 on purpose**, as for the guard: the counters are per ID and stall at their maximum. |
| `AxiLookBits` | 54 | `IdWidthSlv` | All slave-side ID bits: ordering is tracked per full ID. |
| `UniqueIds` | 55 | `1'b0` | 0: BAG does not promise unique IDs. |
| `SpillAw` | 56 | `1'b0` | 0: the mid-point before it already registers the path. |
| `SpillAr` | 59 | `1'b0` | 0: as SpillAw. |

**`axi_demux_simple`** (`axi/src/axi_demux_simple.sv`), used by `bag_axi_guard`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiIdWidth` | 44 | `IdWidth` | Direct. |
| `AtopSupport` | 45 | `1'b0` | 0: v1 ties AWATOP to 0. |
| `NoMstPorts` | 48 | `2` | 2: pass, or the error slave. |
| `MaxTrans` | 49 | `MaxTrans + 1` | **+1 on purpose.** The per-ID counter stalls a further request at its maximum, so +1 lets the configured number be in flight. Found in the PR 3 review. |
| `AxiLookBits` | 50 | `LookBits` | The guard's ID bits: ordering is tracked per ID. |
| `UniqueIds` | 51 | `1'b0` | 0: BAG does not promise unique IDs. |

**`axi_dw_converter`** (`axi/src/axi_dw_converter.sv`), used by `bag_axi2ahb`, `bag_axi2apb`, `bag_axi2axil`, `bag_axi_dw`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiMaxReads` | 19 | `MaxReads`; `MaxTrans` | The upstream fabric's Max slv trans (bridges) or the adapter's trans count. |
| `AxiSlvPortDataWidth` | 20 | `SlvDataWidth`; `UpDataWidth` | Direct. |
| `AxiMstPortDataWidth` | 21 | `DownDataWidth`; `MstDataWidth` | Direct. |
| `AxiAddrWidth` | 22 | `AddrWidth` | Direct. |
| `AxiIdWidth` | 23 | `IdWidth`; `SlvIdWidth` | Same ID width on both ports, as PULP requires. |

**`axi_err_slv`** (`axi/src/axi_err_slv.sv`), used by `bag_axi_guard`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiIdWidth` | 20 | `IdWidth` | Direct. |
| `Resp` | 23 | `ErrResp` | DECERR or SLVERR from the master's Miss response. |
| `RespWidth` | 24 | `32'd64` | Read data for error responses: don't-care value. |
| `RespData` | 25 | `64'h0` | Zero. |
| `ATOPs` | 26 | `1'b0` | 0: as above. |
| `MaxTrans` | 27 | `1` | 1: errors are answered one at a time; enough, since nothing else waits on them. |

**`axi_fifo`** (`axi/src/axi_fifo.sv`), used by `bag_axi2ahb`, `bag_axi2apb`, `bag_axi2axil`, `bag_axi_fifo`, `bag_axi_shared_bus`, `bag_axil2apb`, `bag_axil_fifo`, `bag_axil_shared_bus`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `Depth` | 22 | `Depth`; `FifoDepth` | Direct. |
| `FallThrough` | 23 | `1'b0` | 0: registered; a FIFO adapter also cuts timing. |

**`axi_iw_converter`** (`axi/src/axi_iw_converter.sv`), used by `bag_axi_iw`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiSlvPortIdWidth` | 46 | `SlvIdWidth` | Direct. |
| `AxiMstPortIdWidth` | 48 | `MstIdWidth` | Direct. |
| `AxiSlvPortMaxUniqIds` | 55 | `UniqIds` | `min(2^slave ID, 2^master ID, 8)`: never above 2^master ID, so a narrowing converter uses the remap path, never the serializer. These `Max*` parameters apply only when the master ID is narrower; widening and equal widths use none of them. |
| `AxiSlvPortMaxTxnsPerId` | 61 | `MaxTrans` | Passed to axi_id_remap (the path used). No +1: its counter is sized as `clog2(N + 1)` (`axi_id_remap.sv:555`). |
| `AxiSlvPortMaxTxns` | 67 | `MaxTrans * UniqIds` | Only for the serializer path, which is not used; a legal value. |
| `AxiMstPortMaxUniqIds` | 74 | `2 ** MstIdWidth` | Only for the serializer path; a legal value. |
| `AxiMstPortMaxTxnsPerId` | 80 | `MaxTrans` | Only for the serializer path; a legal value. |
| `AxiAddrWidth` | 83 | `AddrWidth` | Direct. |
| `AxiDataWidth` | 85 | `SlvDataWidth` | Direct. |
| `AxiUserWidth` | 87 | `UW` | Direct. |

**`axi_lite_demux`** (`axi/src/axi_lite_demux.sv`), used by `bag_axil_guard`, `bag_axil_shared_bus`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `NoMstPorts` | 35 | `2`; `NoSlaves` | 2 in the guard (pass, or error); the slave count on the shared bus. |
| `MaxTrans` | 36 | `MaxSlvTrans`; `MaxTrans` | No +1: this is a FIFO depth, not a saturating ID counter (`axi_lite_demux.sv`). |
| `FallThrough` | 37 | `1'b0` | 0: registered. |
| `SpillAw` | 38 | `1'b0` | 0: the guard or mid-point registers the path. |
| `SpillAr` | 41 | `1'b0` | 0: as SpillAw. |

**`axi_lite_dw_converter`** (`axi/src/axi_lite_dw_converter.sv`), used by `bag_axil2apb`, `bag_axil_dw`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiAddrWidth` | 64 | `AddrWidth` | Direct. |
| `AxiSlvPortDataWidth` | 66 | `SlvDataWidth`; `UpDataWidth` | Direct. |
| `AxiMstPortDataWidth` | 68 | `DownDataWidth`; `MstDataWidth` | **PULP asserts more than 8 bits.** Found by this review: see finding 1 above. |

**`axi_lite_to_apb`** (`axi/src/axi_lite_to_apb.sv`), used by `bag_axi2apb`, `bag_axil2apb`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `NoApbSlaves` | 51 | `32'd1` | 1: the downstream APB fabric does the decoding. |
| `NoRules` | 52 | `32'd1` | 1: one rule with `end_addr = 0`, which PULP's decoder reads as the end of the space. |
| `AddrWidth` | 53 | `AddrWidth` | Direct. |
| `DataWidth` | 54 | `DownDataWidth` | PULP asserts it equals the APB width, so width conversion comes first. |
| `PipelineRequest` | 55 | `1'b0` | 0: the FIFO and CDC in front provide any pipelining. |
| `PipelineResponse` | 56 | `1'b0` | 0: as above. |

**`axi_lite_to_axi`** (`axi/src/axi_lite_to_axi.sv`), used by `bag_axil_to_axi`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiDataWidth` | 19 | `SlvDataWidth` | Direct. |

**`axi_lite_xbar`** (`axi/src/axi_lite_xbar.sv`), used by `bag_axil_xbar`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `Cfg` | 23 | `Cfg` | See the crossbar configuration table. |

**`axi_multicut`** (`axi/src/axi_multicut.sv`), used by `bag_axi_cut`, `bag_axi_shared_bus`, `bag_axil_cut`, `bag_axil_shared_bus`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `NoCuts` | 22 | `CutStages`; `Stages` | Direct. |

**`axi_to_axi_lite`** (`axi/src/axi_to_axi_lite.sv`), used by `bag_axi2apb`, `bag_axi2axil`, `bag_axi_to_axil`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `AxiAddrWidth` | 19 | `AddrWidth` | Direct. |
| `AxiDataWidth` | 20 | `DownDataWidth`; `SlvDataWidth` | Direct. |
| `AxiIdWidth` | 21 | `IdWidth`; `SlvIdWidth` | Direct. |
| `AxiUserWidth` | 22 | `UW` | Direct. |
| `AxiMaxWriteTxns` | 23 | `MaxTrans` | Direct. |
| `AxiMaxReadTxns` | 24 | `MaxTrans` | Direct. |
| `FullBW` | 25 | `1'b0` | 0: low-area ID queue in the burst splitter; one transfer per ID at a time is acceptable behind a protocol conversion. |
| `FallThrough` | 26 | `1'b0` | 0: registered ID-reflect FIFOs. |

**`axi_xbar`** (`axi/src/axi_xbar.sv`), used by `bag_axi_xbar`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `Cfg` | 22 | `Cfg` | See the crossbar configuration table. |
| `ATOPs` | 24 | `1'b0` | 0: v1 ties AWATOP to 0. |
| `Connectivity` | 26 | `connectivity()` | From Access_Rules `none`: the crossbar omits the path. |

**`rr_arb_tree`** (`common_cells/src/rr_arb_tree.sv`), used by `bag_prio_arb`.

| PULP parameter | Line | BAG value | Review |
|---|---|---|---|
| `NumIn` | 51 | `NumIn` | Direct. |
| `ExtPrio` | 62 | `1'b1` | 1 in the fixed-priority arbiter, whose priority input comes from the package table (P3-3). |
| `AxiVldRdy` | 69 | `AxiVldRdy` | Set by the mux: AXI valid/ready semantics. |
| `LockIn` | 76 | `1'b0`; `1'b1` | 1 for round robin: the grant holds until the handshake. 0 under ExtPrio, where `bag_prio_arb` holds the grant itself. |

## Owner decisions taken during the phase

| # | Decision |
|---|---|
| P3-1 | PULP is vendored into the wheel. |
| P3-2 | Vendored code is linted as a dependency only; its warnings are not judged. |
| P3-3 | The fixed-priority shared-bus mux is an in-house derivative of the PULP muxes. |
| P3-4 | The AXI route simulation is extended now, with cocotbext-axi. |
| D32 | A denial by a master's own default slave answers with an error. |
| D33 | Width conversion sits on the AXI4 side of a protocol adapter. |
| D34 | PULP WRAP and non-modifiable limits stay a manifest note, and go in the user guide. |
| D35 | Protocol versions stay out of v1. v2 candidates, in order: AXI5 atomics and user/wakeup, APB5, AHB5 exclusives and write strobes, AXI3 only as a port adapter. Full AMBA 2 AHB is dropped. |
| D36 | PULP stays the v1 backend. v2 replaces it block by block, each block passing the same tests as its PULP counterpart. |
| D37 | No Perl RTL templates. |
| D38 | VALID/READY orderings are a compliance requirement verified in v1, not a spec feature. |
| D39 | `bag_external` is an instantiated user module, with ports named `{fabric}_{port}`. |
| D40 | Ports BAG understands keep generator-chosen defaults; transparency through the tie-off report and template guards. |
| D41 | Extra ports of user modules are declared on User_Ports (schema 0.4). |
| D42 | Max mst trans and Max slv trans mean different things on AXI4 and AXI4-Lite because PULP does. v1 documents the difference; the in-house crossbars (v2, D36) give both protocols one meaning. |
| CI | Split `eda` into parallel jobs always, not only above 15 minutes. |

## Design-agent reviews

Each RTL or verification PR was reviewed (review-only) by `chip-design-rtl:rtl-design-orchestrator` and/or `chip-design-verification:verification-orchestrator`. Findings were fixed in the same PR, with a failing test first for bugs, or are listed under known gaps. PR descriptions hold the full lists.

| PR | Main findings fixed |
|---|---|
| #20 (fabrics) | PULP ID counters saturate: `MaxTrans + 1`. A denial by a master's own default slave had no defined answer (D32, #21). |
| #22 (adapters) | Slave-side protocol adapter ID widths. Narrow AXI4 accesses turning into full-width Lite accesses (D33, #23). |
| #24 (PULP bridges) | Chained pass-through windows were wrong (latent since Phase 2). A crash on narrow upstream fabrics. Hard-coded bridge FIFO depth in the test DSL. |
| #26 (in-house bridges) | RTL: `ack` was a state decode feeding a synchronizer; AXI→AHB write bursts never used SEQ. Verification: no CDC at 1:1, no AXI back-pressure, no AXI monitor, no sparse or zero strobes, errors indistinguishable by part. |
| #29 (corpus) | Ports the ordering test skipped silently. Mislabelled directions in the coverage test. The "mixed-protocol" count. Guards that recorded a call rather than a connection. Vacuous starvation stimulus. No test that WVALID does not wait for AWREADY. |
| #30 (this report) | Finding 5, the crossbar's missing `+ 1`. Nine directed tests lost in #26 (see deviations). No test could see strobe, lane or later-beat errors on the narrow APB path, and no gate item pinned it (test added, config widened). Corrections to this report's first version. |

## Deviations from the plan

- **tech_cells_generic** is pinned at 0.2.11, not 0.2.2, as axi v0.39.11 resolves it. No files of it are used.
- **`axi_to_apb`** does not exist at v0.39.11. `bag_axi2apb` uses `axi_to_axi_lite` plus `axi_lite_to_apb`, which the plan allowed for.
- **Verilator** runs with `-fno-dfg`: its DFG pass runs out of memory on `axi_dw_downsizer`.
- **UNOPTFLAT in vendored code:** a loop is attributed to vendored code only when every other node on it is vendored, in-house or the reporting node. Such warnings go to the third-party section.
- **Crossbar map:** the plan built the crossbar's address map from the rule table. The guard is instead the only decoder and writes route bits, so the crossbar sees a trivial map, and aliases and access rules live in one place.
- **`bag_axi2ahb`** uses PULP `axi_cdc` and `axi_dw_converter`, not the `bag_sync` handshake the plan named for every in-house bridge: a handshake per beat would serialize AXI bursts. It is also one transaction at a time overall, not one per direction.
- **`bag_external`** is an instantiated user module (D39), not boundary ports on the wrapper as the plan's PR 7 described.
- **Schema 0.4 and User_Ports (D41)** were not in the plan; the owner added them. The two legacy IR files were migrated by hand.
- **D38 is narrower than first recorded.** Orderings are required where a model answers. Where the design drives READY, no model can force the order, so those channels are logged. `w_first` goes beyond D38.
- **Gate compile is elaboration only** (#25). Each config's C++ model is built by its route simulation in its own port style, so a C++ compile failure in the other style would not show per PR. Elaboration errors still do.
- **Directed tests lost and restored.** PR 6 (#26) deleted `test_apb_mux` (2), `test_ahb_lite_bus` (2) and `test_ahb2apb` (5) from `tests/rtl/test_inhouse.py` by mistake while adding the new bridges, so they did not run from #26 to #29. The verification review of this report found it, and they are back and passing. So through #29, `bag_ahb2apb` had no directed test, only routing and pass-through simulation.
- **Corpus:** 25 configs, not 15. Many fabric configs are single-protocol, so more were added to meet "mixed-protocol" literally.
- **Schedule:** the plan estimated about 41 engineer-days against a 45-day budget. The work took about two days; the scope grew by the items above without changing the gate.

## Known gaps (carried forward)

1. **Workbook notes on Max mst trans and Max slv trans are wrong** (finding 2). Needs a schema bump (0.4 to 0.5). Owner decision: fix it in the first PR of Phase 4, as no 0.4 workbooks are in use outside this repo. The notes will state each protocol's meaning. The difference itself goes away with the in-house crossbars (D42).
2. **FIFO depth is silently ignored** on `bag_ahb2apb`, `bag_ahb2axi`, `bag_apb2axi` and `bag_apb2ahb` (one transfer at a time). The manifests say so; `bag check` does not warn.
3. **CDC integration:** no SDC is generated. Handshake bridges need max-delay constraints on payload paths, and `axi_cdc` on its pointer and data paths. Both sides of a crossing must reset together; `CLK-CDC-RESET` checks the style, not the timing.
4. **`bag_axi2ahb`:** a master must not make RREADY wait for a later write's progress (documented in the RTL and manifest). AxLOCK gets OKAY, so exclusives fail. USER is not carried.
5. **Handshake bridges:** a partial PSTRB becomes byte writes, so it is not atomic at the destination. Contiguous strobes could use larger transfers.
6. **Report and test limits:**
   - `doc/tieoffs.md` omits literal constants a template writes without `ctx`, and constant alias enables.
   - The route test allows extra beats on the right slave, because `Seen` does not log WSTRB.
   - DECERR and EXOKAY are not injected at the directed level.
   - No negative elaboration tests.
   - WRAP is tested only as 4 beats, full width and aligned, because cocotbext-axi does not compute WRAP addresses.
   - The waived-finding count is not snapshotted.
7. **Async reset on data flops** in the in-house bridges (area, no function). Consistent with existing RTL.
8. **Carried from Phase 2:** alias pins must be quasi-static; no synthesis attributes on `bag_sync`; no PREADY timeout. The Phase 2 "nested config with adapters" gap is closed by `nested_adapters`.
9. **User-guide updates for Phase 6** (with D31's list): D32, D33, D34, D38's rules for user fabrics, the `bag_external` contract, and `doc/tieoffs.md`. `design/spec-workbook-guide.md` already has the User_Ports section, in the owner's private tree.
10. **CI margin:** the slowest shard took 12 min 33 s on #29 against the 15-minute budget, and the shards are uneven in CI. Refreshing `tests/eda_durations.json` from CI timings, or a fourth shard, is the remedy.
11. **PULP-backed bridges have no directed tests.** `bag_axi2apb`, `bag_axil2apb` and `bag_axi2axil` are covered by the route simulation only, not by a directed bench as the in-house bridges are.
12. **Narrow APB, unaligned and failing accesses.** From a Lite upstream at an unaligned address, an 8-bit APB accesses from that address on (`axi_dw_downsizer.sv:419`), while a 16-bit APB accesses the whole aligned word, so its lower lanes behave differently. Both are legal AXI. A word split into beats does not stop at an error: a write can land in the other bytes while the master gets SLVERR, as on any bridge that splits.
13. **Docker:** Docker Desktop must be running for local EDA work; it does not start by itself after a restart on the development machine. Local only.

## Effect on later phases

- **Phase 4** (generated unit tests) starts with known gap 1, the workbook notes and schema 0.5. It builds on the route model, which becomes the scoreboard; on `Orderings`, `AxiWatch` and `AhbRules`; and on the raw AXI driver. It adds, per D38, an SV shim driving READY combinationally from VALID, plus bursts and random traffic with a scoreboard, reset during traffic and CDC stress.
- **Phase 5** (formal and nightly) adds per-channel properties on every in-house AXI block (D38): VALID and payload stable until READY, and a cover of VALID rising with READY held low. The nightly can restore full C++ builds of both port styles.
- **Phase 6** picks up known gap 9 and the template author guide note on protocol feature flags (D35).
- **v2:** D35, D36, D42, plug-ins, and whole-bus user ports (D41).
