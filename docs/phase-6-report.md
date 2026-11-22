# Phase 6 report: module prefix, CDC constraints, examples, guides, bill of materials

Status: **closed by the maintainer; the release is not done.** The v1.0.0 tag, the changelog and the release links belong to Phase 7 and do not exist yet (see "Release"). **Phase 5's gate is still outstanding, and Phase 6 closing does not close it:** the nightly is not green. This report was written from the repository, the closed issues and the merged pull requests. Where a figure comes from a pull request or issue comment rather than a run I read, it says so.

## What was built

| Area | Where | Summary |
|---|---|---|
| Module prefix (#106) | `gen/prefix.py`, `checks/`, `ir/`, `template/builder.py` | `Project > Module prefix` (schema 0.7, optional, blank by default). Set, it prefixes every design unit BAG emits and its file name: in-house and derived modules, sub-wrappers, every vendored PULP unit, BAG's formal units and the vendored ZipCPU units. It also prefixes every macro a packaged file defines, the include guards and the four vendored header basenames with their include lines. Each renamed upstream file gets a modification note. The rewrite is token-aware: strings, comments and escaped identifiers are left alone. New rules: `PROJ-MODULE-PREFIX`, `PROJ-PREFIX-COLLISION`, `PROJ-NAME-LENGTH` (warning) and `NAME-RESERVED`. |
| CDC constraints (#62) | `gen/constraints.py` | `constraints/<project>_cdc.sdc` (portable: `set_max_delay` plus `set_false_path -hold`) and `constraints/<project>_cdc.xdc` (Vivado: `set_max_delay -datapath_only`), one entry per crossing, the delay being the smaller of the two clock periods from Clocks > `Freq (MHz)`. `(* ASYNC_REG = "TRUE", DONT_TOUCH = "TRUE" *)` on the flops of `bag_sync` and `bag_rst_sync`. A design with no crossing still gets both files. |
| Example workbooks (#64) | `examples/`, `tests/examples/` | `apb_min` (one APB4 master, three slaves), `mcu_ahb` (AHB-Lite master, SRAM, `bag_ahb2apb` to three peripherals) and `soc_4x8` (AXI4 crossbar, 4 masters, `bag_axi2apb` to six APB peripherals: 4 masters and 8 slaves in all). Each is `bag check` clean with zero findings and runs through `bag flow` in CI. The build script strips the building machine's locale from the workbook. |
| Complete tie-off report (#64) | `gen/collateral.py` | `doc/tieoffs.md` lists the constant bits of a fabric's alias-enable vector (#161). The manual's "the report is not complete" limit is gone. |
| User guide (#63) | `docs/manual/` | The LaTeX manual: quick start, the workbook reference, the check catalogue generated from `checks/core.py`, outputs, flows and a limits chapter. It landed on 2026-11-17, before the other Phase 6 items, and #63 was closed without a comment, so I judged it against the issue's four bullets from the files (see the criteria). |
| macOS tiers (#123) | `README.md`, manual install and limits chapters | States where macOS is supported and where it is not (#140). |
| Template author guide (#65) | `docs/template-author-guide.md` | Manifest format, instance templates, how in-house blocks are verified, D35, D36 and D46 quoted from the Phase 3 and 4 reports. It says twice that the plug-in format is unpublished until v2. |
| Bill of materials (#66, first half) | `scripts/bom.py`, `docs/bom.md` | `docs/bom.md` is generated from the vendor trees on disk, not from `index.json`, and a test fails when it is stale. It lists the vendored file sets and the Python closure of `uv.lock`. `Apache-2.0 WITH SHL-2.1` joined `PERMISSIVE` and `docs/licensing.md`. |
| Process rules (#167) | `AGENTS.md`, `.gitignore` | `.claude/` is git-ignored, and `AGENTS.md` gained two rules (reproduce a review's finding before accepting or dismissing it, and never record a count, duration or verdict you have not read). |
| Checks | `src/hdl_bag/checks/` | The catalogue is **97** rules, the number a test now ties to `len(RULES)` (#176). `FAB-NONE` (#169) refuses a spec that lists no fabric. |
| Schema | `docs/schema-changelog.md` | 0.7 (Module prefix). It regenerated all 37 committed corpus IRs, the golden IR and workbook, the snapshot hash lines and the manual transcripts that print an IR hash (from #106's comments). |

Pull requests, by topic. Module prefix: #146 (stage 1, field and checks), #150 (stage 2), #153 (stage 3), #158 (stage 4), #159 (stage 5, the two-project test and docs), with #148, #155 and #157 (reserve every emitted unit name, and a doc correction) found on the way. CDC: #164. Examples and tie-offs: #161, #165. Guides: #171 (author guide), #176 (usability findings). BOM: #173. Process and README: #166, #167. Fixes: #154 (#149), #160 (#156), #168 (#151), #169 (#162). Test: #163 (`bag shrink` against real formal tools). Phase 5 work that merged while Phase 6 ran (`bag shrink` #143, the macOS and arm64 tool installer #131 to #140, #145) is Phase 5 and is not counted here. #175 (#170) and #177 (#172) are Phase 7.

## Gate evidence

Phase 6 had no gate of its own beyond #68's usability gate. The evidence for each piece:

- **Module prefix.** `tests/gen/test_two_projects.py` compiles two prefixed subsystems beside the PULP tree BAG packages, staged verbatim, with the include path ordered upstream-first so a shadowing header would win. slang and Verilator both accept it. Two controls must fail, and are pinned: a blank prefix, and both prefixed with the header names restored. From the closing comment on #106 (figures I did not re-measure): on `demo_ss` with prefixes `alpha` and `beta`, 193 macros defined by each with no overlap, and 76 generated trees byte-identical to the previous `main` at a blank prefix. CI on the final commit passed on both architectures, per the same comment.
- **CDC constraints.** `tests/gen/test_constraints.py`, 42 tests per the PR description, 41 of them failing before the code. CI on the final commit of #164: 31 checks green, including the full EDA list, in `pr` run 44854482931 and `full-eda` run 44854485688, slowest shard 1415 s (read from the PR's comments, not re-run). The six manual transcripts that need the tools image were regenerated there, and the diff was the file counts only.
- **Examples.** `bag flow` on each in CI, on both architectures, in 6.1 s, 14.6 s and 83.7 s (the figures in #64's closing comment).
- **Bill of materials.** `tests/vendor/test_bom.py` holds `docs/bom.md` to the tree. #66's comment gives 63 vendored files in six sets and a 22-package closure.
- **`main` after the last Phase 6 merge.** Run 44934024310 (#173) succeeded. The `main` run after #164 (44927239760) failed in one EDA shard on a `FORMAL-TIMEOUT` in a mutation test (#151, which I read in the failed log); #168 raised that test's budget to 1800 s, and the `main` runs after it succeeded. #151's own data (774 s to over 1200 s for the same deterministic job) says the cause was shard load, so the budget is headroom and the run time is not reduced.

Locally, on this report's tree (rebased onto `main` at `96acc16`): `pytest -m "not eda" -n 4` gave 1817 passed and 3 skipped in 263 s, and `ruff check`, `ruff format --check` and `mypy --strict src tests scripts` each exited 0. CI is the evidence for the EDA tests; I ran none.

## Acceptance criteria

Each criterion is from the issue's own text. A verdict is mine, from the source named.

| Issue | Criterion | Verdict | Source |
|---|---|---|---|
| #106 | Field in template, reader, IR and schema changelog (0.7), with migration notes for 0.6 | Met | `docs/schema-changelog.md` 0.7: a 0.6 workbook is refused, set the version and add a blank row |
| #106 | Generated RTL contains no unprefixed design unit or macro, checked by a post-generate slang elaboration that lists the units | **Not met as written.** It holds only when a prefix is set (the default is blank, decision 2). It is met by host tests over the written files and the joint compile, not by a slang unit listing | Maintainer's closing comment on #106 |
| #106 | Two-project test, two prefixes, with an unmodified PULP copy, no duplicate definitions or macro redefinitions | Met, with two controls that must fail | `tests/gen/test_two_projects.py`; #106 closing comment |
| #106 | Lint, sim and formal pass on `demo_ss` with the default prefix | **Not met literally.** The default prefix is blank, so "with the default prefix" is the unprefixed design that every gate already covers. A prefixed `demo_ss` has lint and elaboration (`test_a_prefixed_design_lints_and_elaborates`, both port styles); prefixed simulation runs on `p2/ahb_fixed` and prefixed formal on `p2/ahb_fixed` and `p3/mixed_apb_masters`. A prefixed `demo_ss` route simulation is about 940 s and its formal hours, so only a nightly could run it. The maintainer recorded this and closed | #106 closing comment |
| #106 | Generation deterministic | Met | 76 trees byte-identical at a blank prefix; prefixed files restore byte for byte when the prefix and note are removed (closing comment) |
| #106 | README and output-layout docs show prefixed names | Met, in my reading | README status row; `outputs.tex` section "The module prefix" |
| #106 | Dropped by decision: the 16-character prefix advice, a configurable length limit, a `<project>_` default, renaming the top wrapper, prefixing the generated `<project>_<proto>_if` interface | Not delivered, by decision | #106 comments |
| #62 | `.sdc` per crossing: max delay on the `axi_cdc` and 4-phase payload and handshake paths, false paths into the first synchronizer stage, clocks from the Clocks sheet | Met, with a deviation: the false path is `set_false_path -hold`, because a full false path overrides the max delay on the same path. SDC and XDC both | #62 planning comment; PR #164 |
| #62 | Attributes on `bag_sync` and `bag_rst_sync` | Met. The `axi_cdc` crossings contain PULP's `sync`, which already carries them | PR #164 |
| #62 | Tests: lists every crossing and nothing else, names instances that exist, deterministic | Met | `tests/gen/test_constraints.py` |
| #62 | A CDC tool run is out of scope | Out of scope as stated. Nothing in the repository checks these files against a synthesis, timing or CDC tool (#104, v2) | PR #164 "Limits"; `limits.tex` |
| #64 | Example workbooks including 4 masters and 8 slaves, each `bag check` clean and tested in CI (generate, lint, smoke simulation) | Met | `examples/`; #64 closing comment |
| #64 | `doc/tieoffs.md` lists every tied, defaulted, fitted or unused port it omitted, failing tests first | Met for the alias-enable constants, the omission the issue's investigation found. Whether any other omission exists I did not establish | #161 |
| #65 | The author guide: manifest, how in-house blocks are verified, D35, D36, D46, and that the plug-in format is unpublished until v2 | Met | `docs/template-author-guide.md` |
| #63 | User guide current with the schema, with every check ID and message generated from `checks/core.py` and a test keeping them in sync | Met | `docs/manual/checks.tex` is generated; `tests/manual` fails on a rule ID that is not real |
| #63 | Quick start | Met | `docs/manual/chapters/first.tex`; #176 added a minimal worked example |
| #63 | Limits page | Met, as of the closing date | `docs/manual/chapters/limits.tex` |
| #63 | README status table | Met | `README.md`, corrected in #166 |
| #66 | Generated BOM with a test that it matches; Python runtime and sim dependencies | Met | #173 |
| #66 | Release workflow: PyPI trusted publishing, GHCR tools image tag, `CHANGELOG.md`, version 1.0.0 | **Not met.** `release.yml` (added 2026-10-31) and `tools-image.yml` exist; `CHANGELOG.md` does not exist on `main` (draft PR #174); the version is `0.0.1`. Whether `docs/bom.md` belongs in `license-files` is undecided, and no wheel was built to check | `pyproject.toml`; #66 comment |
| #66 | The maintainer tags `v1.0.0` | **Not met.** Phase 7 | |
| #67 | A Claude Code skill for the `bag` CLI, in the agents repository | **Not done, and not verifiable from this repository.** The issue is open | #67 |
| #68 | Usability gate: a person new to the tool, template to a lint-clean, test-passing 4-master 8-slave fabric in under an hour; record the time and problems; fix the docs before tagging | **Not met.** No timed human run happened. A docs-only proxy ran instead (below), and the docs fixes it found are in #176 | #68 comment |
| #68 | This report, with the release links | This report, with the links pending | |

## The usability gate

**What ran was a proxy.** A fresh agent was given the gate's task and forbidden from reading `src/`, `tests/`, `scripts/` and `AGENTS.md`: only `README.md`, `docs/`, `examples/README.md` and the tool's own output. It was told not to start from `examples/soc_4x8.xlsx`. It reached a clean `bag check` and `bag generate` (60 files) in one pass with no error loop, then a clean container lint (0 unwaived, 36 waived). The check messages, which name the sheet, row and rule ID, carried it. An agent is not a person and "under an hour" means nothing for one, so this shows the docs are sufficient and where they leave a reader guessing. **It is not the gate #68 specifies, and the timed human run is still unrun.**

Its findings landed in #176: a container user could not run `bag lint` from the docs at all (the command is now in the README and the manual); the README said 96 checks when there are 97, with nothing pinning the number (a test now does); `TOOL-MISSING` said what was missing but not what to do; there was no minimal worked example (verified by building a real 2-master, 2-slave workbook); terms were used before they were defined; the quick start assumed a `pip install` rather than a checkout; and two workbook points of meaning. One finding was declined on purpose: guidance on scripting the workbook, which is an artefact of an agent having no GUI and would be a second hand-maintained description of a layout `template/builder.py` can change.

**The gate's premise has aged.** #68 was written before #165 shipped `examples/soc_4x8.xlsx`, which is itself a 4-master, 8-slave AXI4 design. A newcomer would reasonably open and adapt that example rather than fill a blank template, so the gate as written times a route the examples were added to replace. The #68 comment recommends re-scoping the gate to the path users will take (adapt `soc_4x8` to a stated different target) and running it after #176's fixes. That decision is the maintainer's.

Not verified by anyone: the documented container path end to end. The proxy used a locally built `hdl-bag-tools:arm64`, not a pull of `ghcr.io/hdl-tools/hdl-bag-tools:main`, and nobody executed the `docker run` in the docs; a test only pins its image to the one CI pulls.

## Phase 5's gate is still outstanding

Phase 6 started before Phase 5's gate (two consecutive weeks of green nightly runs, #60) and report (#61) were met, and closing Phase 6 does not close them.

- **The nightly is red.** The last four scheduled runs (2026-11-18, 11-20 twice, 11-21) all concluded `failure`. In the latest (run 44958449418), all ten `regress` shards failed, and one `corpus-alone` shard failed in `uv sync --locked` on a 403 from a GitHub download. That last failure is infrastructure, distinct from #128's seed failures. I did not read the `regress` logs; #128 describes its failures.
- **#128 is open:** 29 of 50 random seeds fail in six distinct classes. **#129 is open:** `tests/eda_durations.json` is to be refreshed from a green nightly. Both are owned by draft PR #112, "Phase 5: fix the first nightly's random-seed failures".
- **#60 and #61 are open** and blocked on #128. The README's status table says the same.

## Owner decisions taken during the phase

| Issue | Decision |
|---|---|
| #106 | The top wrapper keeps its name. The prefix is blank by default and opt in, so a 0.6 workbook keeps its names (this is what turned two acceptance criteria into the deviations above). Macros and guards are renamed, and the header basenames too, after the first decision (keep the paths) was found to block the issue's own two-project criterion. Generated file names match their prefixed modules. Units that already carry the project name are skipped. Five stages, each green on its own. The prefix is the bare word and BAG joins the underscore. Strings and comments are never rewritten. The formal units are in scope. |
| #62 | SDC and XDC both. Max delay is the smaller of the two clock periods, with no schema change. `ASYNC_REG` plus `DONT_TOUCH`, one spelling for every tool. |
| #66 | `Apache-2.0 WITH SHL-2.1` is an accepted licence. Fixing #172 is its own pull request, because it changes generated output. |
| #162 | A spec that lists no fabric is an error (`FAB-NONE`), not a warning, and "at least one fabric" is the whole condition. |
| #106 | No release is tagged between prefix stages 1 and 4. After stage 5 the gate is lifted: the next tag is v1.0.0. |
| Phase boundary | Phase 6 is closed. #172, #170 and the release are Phase 7. |

## Reviews

Each pull request had an independent review-only pass; the pull request descriptions hold the full lists, and I read only some of them. Two I read in full:

| PR | Main findings fixed |
|---|---|
| #164 | The `.xdc` would have been rejected by Vivado: `-datapath_only` was emitted with `-through` and no `-from`. The constraints files began `# // Copyright`, the SystemVerilog file header, in a Tcl file. The implementer's own test caught `gen_cdc/u_req_sync` where the label and instance are joined by a dot. The reviewer re-derived the 4-phase payload registers from the RTL rather than from the table. |
| #176 | `docker-lint.sh` satisfied manual rule M4 only because the manual shows it, and nothing held it true: a test now pins its image to `eda-shards.yml`, and was shown failing when the image is stale. |

## What Phase 6 found, and what it fixed

- **#172: a licence-notice defect in shipped output.** Writing the bill of materials, which had to state a licence per file set and could not take the index's word for it, showed that `scripts/vendor_pulp.py` assumed one licence per package. Two vendored `common_cells` files say otherwise: `assertions.svh` is Apache-2.0 (lowRISC) and `read.sv` is Apache-2.0 WITH SHL-2.1 (EPFL), and neither text shipped beside its file. A generated design therefore carried an Apache-2.0 file with only the Solderpad text next to it. The assumption had been there since the tree was first vendored. It was fixed in Phase 7: PR #177 merged (`96acc16`) and #172 is closed.
- **A third vendored file set nobody recorded:** `rtl/vendor/derived/`, the two multiplexers written by `scripts/derive_mux.py`, absent from `index.json`, `docs/third-party.md` and `NOTICE`. A test now walks the vendor directories on disk.
- **scapy is GPL-2.0-only,** two levels down the `sim` closure (`cocotbext-axi`, `cocotb-bus`). Nothing redistributes it; it was invisible in a table of direct dependencies.
- **#170 (Phase 7, #175):** five manifest keys, including `licence`, were set and read by no code; the keys no code reads were removed.
- **Bugs closed on the way:** #149 (`bag check` crashed on a second parse of a refused cell, because the cache was keyed on the cell, not the parser), #152 (a project named `addr` collided with `bag_addr_decode`, and so did prefix `bag` with project `addr`), #156 (`resolve` reported `VALUE-ILLEGAL` on a non-AXI fabric's Max mst/slv trans that no check validated), #147 (a sub-wrapper named after a PULP module shared a design-unit name silently), #151 (a mutation test's budget), #162 (a spec with no bus generated a bus-less wrapper).
- **The README had drifted:** it said Phase 5 was under way and called shipped features "not yet" (#166), and its check count was stale with nothing pinning it (#176).

## My own errors during the phase

Stated so the record is complete; each was caught and corrected.
- **#106:** the plan said stage 1 would not regenerate the corpus and goldens; it does, because a schema bump moves every committed IR. The first decision on header files (keep the paths) blocked the issue's own criterion and was amended. The reasoning in #148 that `assert_rpt_pkg` cannot collide held only for BAG compiled alone. A third class of emitted unit (`rtl/vendor/derived/`) and a fourth (the formal units) had been left out of the inventory.
- **#62:** the issue asked for a max delay and a full false path on the same path, which cancel each other; the planning pass caught it before implementation.
- **#167:** a planner's report of the `addr` collision was dismissed after reading a line that names the file, not the module, and was real (#152). A brief stated that the vendored tree does no token pasting; it pastes 483 tokens on 210 lines. A pull request body gave a test count before the run finished. Three `.claude/` worktrees reached a pushed commit as embedded repositories.
- **#68 and #176:** the brief listed "one memory region per slave" as required, and building the workbook showed that removing a region is only the warning `MM-PATH-NO-REGION`.

## Deviations from the plan

- **#106's two criteria** and its dropped items, above.
- **Schema 0.7 was not additive in practice.** The blank default keeps generated RTL unchanged, which is narrower than keeping every committed artefact unchanged.
- **The CDC false path is `-hold` only** in the SDC.
- **#66 is split.** The BOM shipped; the release did not.
- **#67 did not ship** (it lives in another repository).
- **#68's gate was replaced by a proxy.**
- **Phase 6 began with Phase 5's gate open,** and Phase 5 work (the macOS port, `bag shrink`) ran alongside it.

## Known gaps (carried forward)

1. **The CDC constraints are not tool-verified.** The selectors name post-synthesis objects (hierarchical pins, `_reg*` cells, `gen_cdc.` labels). Tests prove the paths exist in the RTL under real parameters and nothing more; nothing proves synthesis keeps them, and the pin selectors need `set_ungroup false`. The 4-phase payload register list is hand-derived, and a register missing from it is a silently unconstrained path that no test here would catch. `create_clock` assumes the clocks are top-level ports. A CDC tool run is #104 (v2).
2. **A prefixed `demo_ss` has no route simulation or formal run** (about 940 s and hours), only lint and elaboration; other configs carry the prefixed simulation and formal.
3. **Prefix limits:** an escaped identifier and token pasting are skipped (both tripwired by tests), and the environment's own macros (`VERILATOR`, `SYNTHESIS`, `UVM`, `QUESTA` and the rest) are shared by design.
4. **The usability gate is unrun,** and may want re-scoping before it is.
5. **The documented container path is unexercised** by a person or an agent on a clean machine.
6. **The route-job budget (1800 s, #168) is headroom for a loaded runner,** not a reduction of the job's run time.
7. **Phase 5's gate** (above).
8. **No Claude Code skill** (#67).
9. **Earlier phases' known gaps:** the Phase 3 and 4 reports list gaps that Phase 6 was to document or close. I did not re-audit which of them the manual's limits chapter now covers.

## Release

Phase 6 does not include the release. `pyproject.toml` still says version `0.0.1` and `Development Status :: 2 - Pre-Alpha`, and `v1.0.0` is not tagged. The entries below are placeholders for the maintainer to fill in one pass; none is a real link.

| Item | Link |
|---|---|
| Tag `v1.0.0` | _pending: v1.0.0 is not tagged (see #66)_ |
| GitHub release page | _pending: v1.0.0 is not tagged (see #66)_ |
| PyPI project and release | _pending: v1.0.0 is not tagged (see #66)_ |
| Tools image tag on GHCR | _pending: v1.0.0 is not tagged (see #66)_ |
| `CHANGELOG.md` entry for 1.0.0 | _pending: v1.0.0 is not tagged (see #66); draft is PR #174_ |
| Release workflow run that published it | _pending: v1.0.0 is not tagged (see #66)_ |
| CI run on the tagged commit | _pending: v1.0.0 is not tagged (see #66)_ |

## Effect on later phases

- **Phase 7:** the release (changelog, version, tag, PyPI, the image tag), and the packaging call on whether `docs/bom.md` ships in the wheel's `license-files`. Fill in the release table above, and update the README status to match. #172 is already fixed (#177).
- **Phase 5 (still open):** #128, #129, #60 and #61, with PR #112. The gate needs two consecutive green weeks, which have not started.
- **The usability gate:** decide the scope (adapt `soc_4x8`, or the blank template with the examples out of bounds, or both), then run it with someone who has not worked on BAG, after #176's fixes.
- **v2:** #104 (a CDC tool run on the generated constraints) is the one that closes known gap 1.
