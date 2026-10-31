# Phase 1 report: repo, reader, checks, IR, manifests

Status: **gate passed in CI on `main`**, 2026-10-31. Phase 2 is waiting for owner review.

## What was built

| Area | Where | Summary |
|---|---|---|
| Repository (Phase 0) | `pyproject.toml`, `tools/`, `.github/workflows/` | Apache-2.0 relicence with a generated-output exception (`docs/licensing.md`). uv + hatchling + click; ruff, mypy `--strict`, pytest, hypothesis. Line endings forced to LF. Pinned tools image (`tools.lock`: Verilator v5.052, slang v12.0, Verible v0.0-4296, OSS CAD Suite 2026-10-01, Bender v0.32.1, LibreOffice) on GHCR. Trusted-publishing release workflow; `hdl-bag` 0.0.1 is on PyPI. |
| `bag template` | `src/hdl_bag/template/` | Port of the reference `build_spec_template.py`. The builder renders any `SpecData`: the blank template, the demo_ss sample, or a workbook written from an IR. |
| Reader | `src/hdl_bag/reader/` | Workbook → `RawSpec`. Input columns are found by header name. Every value keeps its sheet/row/column. Derived columns and cached values are never trusted. |
| Protocols and manifests | `src/hdl_bag/protocols.py`, `src/hdl_bag/templates/` | Protocol facts (widths, responses, optional signals, signal lists, `{proto}` tokens). Manifest schema (api_version 1) and registry, with manifests for all 20 built-in templates: 7 fabrics, 7 bridges and 6 adapters (`pc/dw/cdc/iw/fifo/cut`). |
| `bag check` | `src/hdl_bag/checks/` | **76 rule IDs** (71 errors, 5 warnings). They cover every row of the user guide's Validation checks table, every Check-column value, and the cross-sheet checks of plan §8. Each input cell is parsed once, so one mistake gives one message. |
| Resolve and IR, `bag ir` | `src/hdl_bag/ir/` | Checked spec → frozen pydantic IR (schema 0.3), with a deterministic YAML dump/load and IR → workbook writing. |

Pull requests: #1 (Phase 0), #2 (template + golden), #3 (reader), #4 (protocols + manifests), #5 (checks), #6 (IR).

## Gate evidence

CI run on `main` at `11ca484`: <https://github.com/hdl-tools/hdl-bag/actions/runs/41162238513>. All jobs pass: `test` on Ubuntu with Python 3.11 and 3.14 and on Windows with 3.11, plus `golden` in the tools image. Locally: 134 passed, 2 skipped (the CI-only recalculated-golden checks).

| Gate item (plan §8 Phase 1) | Evidence |
|---|---|
| The golden workbook passes with zero errors and zero warnings, and its IR snapshot is committed | `tests/checks/test_checks.py::test_golden_has_no_findings`, `tests/test_cli.py::test_check_golden_passes`; snapshot `tests/golden/demo_ss.ir.yaml`, `tests/ir/test_ir.py::test_golden_snapshot` |
| Every check ID has a broken-workbook test with the exact sheet, row and rule (≥ 40) | 76 rule IDs, 78 broken-workbook cases asserting the exact message list, `test_broken_workbook[*]`. `test_every_rule_has_a_broken_workbook` fails if any rule lacks a case. (The PR #5 description said 82 cases; 78 is correct.) |
| Two runs give a byte-identical IR | `test_byte_identical_across_processes` (fresh processes, `PYTHONHASHSEED` 0 and 12345) |
| The golden workbook's derived values match the resolved IR for every port | `test_derived_values_match_workbook_cache`: fabric, # master/slave ports, ID widths (main_xbar 6/8), pc/dw/cdc adapter instance names and max parallel transfers, read from the LibreOffice-recalculated cache |
| `bag template` output matches the reference workbook structurally | `tests/template/test_builder.py`: the only differences from the 0.2 reference are the approved 0.3 delta (this test runs locally, because the reference is private). In CI, `tests/golden/test_golden_workbook.py` regenerates the workbook, recalculates it with LibreOffice and compares it with the committed golden. |
| IR → workbook → IR is the identity | `test_ir_workbook_ir_round_trip`; hypothesis `test_row_order_never_matters` (shuffling every table's rows never changes the IR) |

## Deviations from the plan

### Owner decisions taken during the phase

| # | Decision |
|---|---|
| Naming | Full rebrand: `hdl-bag` / `hdl_bag` / CLI `bag`, templates `bag_*`, workbook schema **0.3** (see `docs/schema-changelog.md`). |
| Licence | Apache-2.0. Generated files belong to the user (output exception). |
| Python | Python kept (Rust re-evaluated); minimum 3.11. |
| Secure PROT | Reachability is transitive across bridge windows. PROT `n/a` counts as tied. |
| Bridge window | The window is the union of the bridge's regions. Window base = lowest region; each region must reach a downstream region. |
| `bag_external` | An opaque user fabric: ports and wiring only. |
| Adapters | Chain port → cdc → pc → dw → iw → fifo → cut → fabric, with `iw` added as a sixth type. Adapters exist only within the AXI family. Bridges do width and CDC conversion internally. |
| n/a settings | Settings that don't apply to a fabric must read n/a (warning). The golden spec was fixed to match. |
| Port index | A blank Port index is an error; row order never numbers ports. |
| Miss response | Each protocol's native error, or default-slave. |
| Widths | AXI4-Lite data width is 32 or 64. Port address width ≤ the fabric's. ID width on AXI4 only. User width > 0 on AXI4 only. |
| AHB masters | AHB-Lite fabrics may have several masters with round-robin or fixed-priority arbitration. **This adds an AHB-Lite arbiter to Phase 2.** APB fabrics take exactly one master. |
| Fixed priority | Allowed on shared buses and AHB-Lite. Crossbars are round-robin only (no fork of `axi_xbar`). |
| `{proto}` | `axi`, `axil`, `ahb`, `apb4`, `apb3`. |
| LOCK | Applies to AXI4 ports and AHB-Lite masters only. |
| Reporting | Every independent failure is reported, without cascades. |
| Line endings | LF everywhere. |

### Implementation deviations

- **Reader structure:** the plan suggests one reader module per sheet. One header-driven table reader, configured by a column list per table, serves every sheet. Per-sheet modules would have been near-identical. A test keeps the reader's columns equal to the builder's input columns.
- **Rule IDs** differ from the illustrative `TOPO-INDEX-RANGE` in plan §6.1. The catalogue is `hdl_bag.checks.core.RULES`.
- **Workbook-only checks added:** `VALUE-MISSING` / `VALUE-ILLEGAL` (dropdown and format values), `FAB-TOPOLOGY`, `FAB-EMPTY-SIDE`, `MM-ADDR-RANGE`, the `ACC-` / `DEC-` / `PORTOPT-` / `WRAP-` duplicate rules, and `HIER-TOP`.
- **Golden image tag:** the `golden` job pulls `hdl-bag-tools:main` rather than a content-hash tag. Images are only pushed from `main`, so a PR that bumps `tools.lock` could not pull its own hash-tagged image. `:main` changes only when `tools.lock` changes on `main`.
- **Phase order:** manifests and protocol tables (1.5) landed before the checks (1.3), because the template checks read the registry.

## Known gaps (carried forward)

1. **Sub-wrapper boundary port names** (`WRAP-NAME-COLLISION`) are checked only at the top level. Boundary ports need the Phase 2 hierarchy generator.
2. **Pass-through address = N:** the IR keeps system addresses plus `window_base` and `pass_through`. Translating to offsets is left to the Phase 2/3 generators. The golden spec uses Y throughout, so no test exercises N yet.
3. **PULP names in manifests** (`bag_axi_xbar` param map, module lists) are marked `unqualified`. Phase 3 verifies them at the pinned commit (plan §10: stop and ask if they differ).
4. **In-house bridge ID width:** in-house bridges into AXI4 fabrics are assumed to issue ID width 1 (stated for `bag_ahb2axi`, assumed for `bag_apb2axi`). Revisit when the bridges are written in Phase 3.
5. **Hypothesis property:** it currently permutes the golden spec. Random IR generation across the configuration space is Phase 5 work.

## Effect on later phases

- **Phase 2** gains an AHB-Lite arbiter (round-robin and fixed-priority) inside `bag_ahb_lite_bus`. AHB-Lite slaves get no HMASTLOCK.
- **Phase 3** needs an in-house priority arbiter at the shared-bus mux. Crossbars stay round-robin only.
