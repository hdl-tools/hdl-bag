# Phase 2 report: AHB-Lite/APB fabrics, wrapper, hierarchy, collateral, lint

Status: **gate passed in CI on `main`**, 2026-10-31. Phase 3 is waiting for owner review.

## What was built

| Area | Where | Summary |
|---|---|---|
| Lint and compile flows | `src/hdl_bag/flows/` | A tool-abstraction layer. `Finding` and `Report` objects, and one backend each for slang (`-Weverything` plus `config/slang.flags`), Verilator (`--lint-only -Wall`) and Verible, each with a pure output parser. Versioned YAML waivers need a reason and report themselves when stale. `compile_design` runs a Verilator elaborate + build. |
| In-house IP | `src/hdl_bag/rtl/inhouse/` | Parameterized SystemVerilog. `bag_addr_decode` covers aliases, regions, RO/WO, secure-only, Access_Rules, and default slave or error. `bag_apb_mux` (APB3/APB4). `bag_ahb_lite_bus`: per-master input stages, round-robin or fixed priority, a grant frozen in wait states, burst/HMASTLOCK hold, the 2-cycle ERROR default slave. `bag_ahb2apb`: burst split, width down-conversion, pass-through N offset, PPROT mapping, optional async CDC. `bag_sync`, `bag_rst_sync`. Shipped in the wheel. |
| Generator | `src/hdl_bag/gen/` | Netlist from the IR. Generic decode tables as typed localparams in `rtl/pkg/<package>.sv`. One module per wrapper, in flat or sv-interface port style; the sv-interface style adds one interface per protocol and a flat harness. Tie-offs, narrow-port truncation and zero-extension, reset conditioning, D4 header. Template instantiation lives in `templates/builtin/<name>/instance.sv.j2`. |
| Hierarchy | `gen/hierarchy.py` | A pure routing plan. Each bus is a net in the lowest common wrapper of its ends, with flat boundary ports on the way down. Clocks, conditioned resets and extra inputs go only where they are used. Validates hand-written IR. |
| Collateral | `gen/collateral.py` | `sw/<project>_memmap.h`, `doc/memmap.md` and `.html` (self-contained), and `doc/port_map.md`. Written before the RTL step, so the AXI-heavy demo_ss already gets them. |
| CLI | `src/hdl_bag/cli.py` | `bag generate`, `bag lint`, `bag flow` (check → generate → lint → compile). Generator errors are reported cleanly. |
| Checks | `src/hdl_bag/checks/` | 5 new rules, bringing the catalogue to **81** (74 errors, 7 warnings): `MM-SLAVE-ALIGN` (D29), `HIER-RTL-NAME`, `HIER-RESERVED-NAME`, `HIER-EMPTY` (warning), `PORTOPT-ERROR-MASKED` (warning). Sub-wrapper boundary-name collisions now go to `WRAP-NAME-COLLISION`. `MM-ADDR-RANGE` understands pass-through N windows. |
| Gate corpus | `tests/configs/p2/` | 11 APB/AHB-only configs. 9 are written at workbook level in `corpus.py` and resolved through `bag check` with zero findings; 2 are earlier IR files. |
| Verification | `tests/rtl/`, `tests/gen/` | In-house cocotb BFMs with protocol checks and junk on don't-care signals. Directed smoke tests per in-house block. A structural connectivity oracle. An IR-driven reference model with a route simulation. An arbitration test on the generated tops. Golden collateral snapshots. |

Pull requests: #8 (lint flow), #9 (in-house RTL), #10 (RTL review fixes), #11 (generator core), #12 (hierarchy), #13 (collateral), #14 (gate corpus).

## Gate evidence

CI run on `main` after #14: <https://github.com/hdl-tools/hdl-bag/actions/runs/41205250295>. Jobs: `test` on Ubuntu with Python 3.11 and 3.14 and on Windows with 3.11; `golden` and `eda` in the tools image. Locally: 256 passed, 2 skipped (the CI-only recalculated-golden checks). `pytest -m eda` in the tools image: 73 passed in about 2 min 40 s.

| Gate item (plan §8 Phase 2) | Evidence |
|---|---|
| 10 hand-written APB/AHB-only configs pass lint and compile in both port styles, including one with nested sub-wrappers | `tests/gen/test_corpus.py::test_gate_lint_and_compile`: 11 configs × {flat, sv-interface}. Each has zero unwaived findings under slang, Verilator and Verible, and builds in Verilator. Nested: `ahb2apb_nested` (2 levels) and `nested_deep` (4 levels, including a wrapper that holds only children). `test_corpus_has_ten_configs_with_nesting` asserts both conditions. |
| Directed cocotb smoke tests pass on each in-house block | `tests/rtl/test_inhouse.py`. `bag_apb_mux`: 2 builds × 5 tests. `bag_ahb_lite_bus`: 2 builds × 11 tests. `bag_ahb2apb`: 5 configurations × 9 tests, covering 32→32, 64→32 N, 32→8 at CDC 1:3, 64→32 at CDC 3:1, and 64→8 at CDC 10:7. Each block is also lint-clean on its own. |
| The C header and docs are checked against the IR by a test | `tests/gen/test_collateral.py` parses the header, Markdown and HTML back and compares them with the IR for demo_ss and the gate configs. demo_ss collateral snapshots are byte-exact. In the tools image, gcc `-std=c11 -Wall -Wextra -Werror -pedantic` compiles the header and `_Static_assert`s every base. |
| Determinism (kept from Phase 1) | Byte-identical generation across runs and across `PYTHONHASHSEED` values: single-domain, multi-tie, two-domain hierarchy and golden configs (`test_generate.py`, `test_wrapper_rules.py`, `test_hierarchy_oracle.py`). |

Beyond the gate, every config also has:
- an IR → workbook → IR identity check;
- the structural connectivity oracle;
- an **IR-driven route simulation** through its generated top: 56–1,260 checked accesses per config, covering destination, slave-visible address, PROT, injected slave errors, fabric errors and read-back data;
- an arbitration test on every multi-master AHB fabric.

## Owner decisions taken during the phase

| # | Decision |
|---|---|
| P2-1 | In-house blocks are hand-written parameterized SV; only the instantiation is generated. |
| P2-2 | The APB/AHB decoders enforce the full rules now: Access_Rules, RO/WO, secure-only and aliases. |
| P2-3 | Templates without RTL (all AXI until Phase 3): collateral is written, then `MissingRtl` names them. |
| D27 | AHB-Lite ports carry AMBA 5 `hnonsec` under the PROT column. |
| D28 | A tied security attribute fails closed: HNONSEC=1 and PPROT[1]=1 (AxPROT[1]=1 in Phase 3). |
| D29 | Narrow slaves see the low address bits. `MM-SLAVE-ALIGN` checks that all of a slave's regions lie in one 2^width-aligned block (relaxed at the phase review, #16). |
| D30 | A slave sees the address after alias translation (confirmed at the phase review; this is the current behaviour). |
| D31 | The user-guide notes found in Phase 2 are written together in Phase 6 (known gap 7). |
| Review | Every RTL or verification stage is reviewed by the chip-design agent suite, and findings are fixed in the same PR. |
| Test split | The stub connectivity oracle and the route test belong in PR 6; PR 3 took the review fixes plus the structural tests. |

## Design-agent reviews

Each RTL or verification PR was reviewed (review-only) by `chip-design-rtl:rtl-design-orchestrator` and/or `chip-design-verification:verification-orchestrator`. Every finding was fixed in the same PR, or documented below as a known gap.

| PR | Main findings fixed |
|---|---|
| #10 (in-house RTL) | **Bugs.** ahb2apb PSTRB was 0 for odd byte addresses on an 8-bit APB bus. The AHB grant could change during a wait state (an AHB-Lite violation). **Robustness.** Slave HREADYOUT was on the arbiter→HSEL path. A silent OKAY was possible on a hole in the decoder. APB PENABLE was not gated. **Tests.** Protocol checks, junk on don't-care signals and 13 new directed tests. 13 of 15 mutants are killed; the other 2 are equivalent. |
| #11 (generator) | Pass-through N decoded nothing. Bridge Port_Options were applied to both ends. Tie order was nondeterministic. Reset nets could collide with ports. PSTRB tie-default was all ones on reads. Sub-wrappers were silently flattened. The BFM tolerated missing signals. 60 of 66 generator mutants survived; a follow-up spot-check of 16 mutants killed all of them. |
| #12 (hierarchy) | Duplicate instance names. A sub-wrapper named like a design unit was silently replaced or overwritten. Hand-written IR could hang or crash the generator. A structural connectivity oracle was added: 12 of 13 surviving mutants killed, and the remaining one is a near-equivalent. |
| #14 (corpus) | Slave errors, single alias pins, arbitration and PROT at the slave were not exercised, and several feature combinations were missing. The model used the resolver's tables. All 6 top expected-surviving mutants are killed. |

## Deviations from the plan

- **RTL location:** in-house RTL lives in `src/hdl_bag/rtl/inhouse/` (in the package), not in a top-level `rtl/inhouse/`, so the wheel ships it. Manifest `sources` paths are package-relative.
- **Lint configuration:**
  - Waivers live in `src/hdl_bag/flows/config/waivers/`, not in a top-level `lint/`.
  - slang runs with `-Weverything` minus 4 warnings, each disabled with a reason in `config/slang.flags`.
  - The only waivers are unused package parameters in `rtl/pkg/*.sv`; the package exports the memory map for the SoC.
- **Manifest schema** stays at `api_version 1`, with a new `rtl` field. Instantiation templates live next to each manifest (`instance.sv.j2`). The plug-in format is unpublished until v2.
- **Generated layout:**
  - The package is in `rtl/pkg/`.
  - Sub-wrappers use flat boundary ports in both port styles.
  - The sv-interface style adds a flat harness `<top>_flat`, which is the lint and compile top: Verilator cannot compile a top-level module with interface ports.
- **cocotb** is in the `dev` group, not a separate `sim` group, so mypy type-checks the cocotb tests on every OS. The tools image gains `ccache`.
- **Corpus:** 11 configs instead of 10. Nine are written as a workbook-level DSL and resolved through `bag check`; this replaced hand-writing IR, which would have duplicated derived fields.
- **Adapters:** the "nested config with adapters" from the plan is not possible yet, because adapters have no RTL before Phase 3. Adapter routing is covered only at plan level, on the golden IR.
- **Schedule:** the plan estimated about 29 engineer-days (flagged as about 45% over the 4-week budget). The work was done in one session.

## Known gaps (carried forward)

1. **Owner questions from the phase review (answered):** D29 is relaxed to one aligned block (#16). Slaves see the translated alias address (D30). Guide notes go to Phase 6 (D31).
2. **ahb2apb reset:** both sides of a CDC bridge must be reset together. This is documented in the RTL but not checked.
3. **Alias pins** must be quasi-static or synchronous to the fabric clock. This is documented in the RTL, with no synchronizer.
4. **Synchronizers:** no synthesis attributes or technology-cell hook on `bag_sync` / `bag_rst_sync`.
5. **Not implemented:** a PREADY timeout, and a rotate-mask round-robin arbiter (an area optimization for many masters).
6. **APB3:** absent PPROT defaults to secure in the RTL. Only `SEC-PROT-TIED` keeps an APB3 path away from secure-only regions.
7. **User-guide updates (Phase 6):**
   - `{instance}` in Sub-wrapper port naming is the link name.
   - Sub-wrappers use flat ports.
   - Sub-wrappers receive the conditioned `bag_rst_<domain>_n`.
   - D28 and D29.
   - Under D29, a pass-through N offset never makes two of a narrow slave's addresses look the same.
   - Slaves see the address after alias translation (D30).
   - The Hierarchy Check-column list.
8. **Phase 4 scope** (per the reviews): protocol monitors, bursts and pipelined back-to-back AHB, sub-word and 64-bit transfers, randomized traffic with a scoreboard, lock behaviour, reset during traffic, CDC stress and coverage.
9. **Docker:** the Docker Desktop WSL integration on the development machine dropped twice (Resource Saver). This affects only local EDA runs, not CI.

## Effect on later phases

- **Phase 3** adds AXI templates through the same mechanism: a manifest with `rtl: true` plus an `instance.sv.j2`. It needs:
  - AXI interface variants for ID/USER widths;
  - adapter RTL, after which the adapter chain becomes routable (the netlist already orders it, D9);
  - D28 ties for AxPROT.

  The route model and the connectivity oracle extend to AXI. The golden demo_ss is the Phase 3 gate config.
- **Phase 4** builds on the BFMs, the route model (which becomes the scoreboard), the corpus and the arbitration test.
