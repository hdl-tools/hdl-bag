# Phase 4 report: generated unit tests

Status: **gate passed in CI on `main`** at `503ffc8` (through #43), 2026-11-07: run <https://github.com/hdl-tools/hdl-bag/actions/runs/42385746139>, all 317 EDA tests passed. Phase 4 closes when the owner has reviewed this report; Phase 5 starts only on the owner's go-ahead.

## What was built

| Area | Where | Summary |
|---|---|---|
| Generated tests | `src/hdl_bag/tests_gen/` | `bag generate` writes a cocotb test module per design, and `bag sim` runs it. The module imports the test bodies, reference model and bus models from the installed package, and refuses another `hdl-bag` version (D44). 20 tests in `TESTS`, each with an `applies` predicate that shares its helper with the test body, and each asserting it checked something. |
| Reference model and scoreboard | `tests_gen/route_model.py` | The IR-driven route model from Phase 3 became the scoreboard. It checks every access at the slave byte by byte, including partial writes after an error beat, refused and split bursts (`hdl_bag/bursts.py`, shared with `bag check`), narrow APB lanes and strobes, and exact response codes through bridge chains. |
| Bus models | `tests_gen/bfm.py`, `bfm_axi.py` | AHB-Lite and APB masters and slaves; AXI4 and AXI4-Lite on cocotbext-axi with a raw driver. Back-pressure on every AXI channel (each model stalls the channels it receives), every VALID/READY ordering a model answers (D38), forced DECERR and EXOKAY, and a READY shim that drives READY combinationally from VALID. |
| Test list | `tests_gen/route_tests.py` | Routing and errors, single bytes at both ends of every region, arbitration and starvation, concurrent traffic, back-pressure, head-of-line blocking, AXI4 burst shapes against the model, Max mst trans per ID, exclusives, the shared-bus midpoint, unaligned writes, errors on any beat of a split access, APB byte writes, reset during traffic, and traffic across clock domains at each ratio of four fixed clock periods. |
| Runner | `flows/sim.py` | `bag sim` builds with Verilator and reports `SIM-EXIT`, `TEST-SKIPPED` and `TEST-SET` as findings, so a crash in teardown, a skipped test or a missing test can't pass. |
| Coverage report (D52) | `tests_gen/coverage.py` | `bag sim` writes `sim/coverage.json` and `sim/coverage.md`: master-to-slave paths by direction, response codes per slave, burst shapes refused or passed, secure accesses. A report only, with no closure target, and circular by construction (the bins come from the probes the tests use). |
| Mutation harness | `tests/mutation/` | 43 package mutants (each edits one table of the generated package) and 12 RTL mutants (bridge wrappers and the secure-check polarity in `bag_addr_decode`). Detection means a generated test fails on an AssertionError (from the scoreboard or a bus model's protocol check); a timeout, any other exception, or a failure during bring-up doesn't count. Each config has its unmutated control. |
| Negative elaboration | `tests/rtl/test_negative_elaboration.py` | 28 cases: every elaboration `$error`/`$fatal` in the in-house RTL fires under slang and Verilator, keyed by module and message. |
| Hardening (D47) | `cli.py`, `ir/io.py`, `flows/`, `gen/` | The six confirmed bugs from the external review: the sample's `header.txt`, the vendored LICENSE in generated output, waivers of tools that didn't run, a clear error for an IR of another schema, checks on `bag generate <ir>`, and Project > Lint tools and Simulator honoured. |
| Collateral | `gen/collateral.py`, `docs/reset-sequence.md` | A generated `doc/reset.md` per design (D51): reset groups, crossings, and the required sequence. |
| Checks | `src/hdl_bag/checks/` | 4 new rules, bringing the catalogue to **90** (81 errors, 9 warnings): the ignored-FIFO-depth warning, `EXT-MODULE-NAME`, `EXT-MODULE-SHARED` and `AXI-WRAP-REFUSED` (D49, warning, width converters only). |
| Schema | `docs/schema-changelog.md` | 0.5 (corrected Max mst/slv trans notes) and 0.6 (Fabrics > External module, D45). |
| CI | `.github/workflows/` | ccache kept between runs and shards balanced from CI timings (#35). Pull requests run the smoke subset (`tests/smoke.py`, 205 tests); the full EDA list (317 tests) runs on every push to `main`, nightly, on demand, and through the `full-eda` label (`full-eda.yml`). The required check is still `eda`. |

Pull requests: #32 (schema 0.5, ignored-FIFO warning, waived snapshot, demo_ss route parts), #33 (generated tests and `bag sim`), #34 (byte-level scoreboard), #35 (CI time), #36 (burst shapes), #37 (back-pressure, head-of-line, DECERR and EXOKAY, READY shim), #38 (External module, D45), #39 (reset during traffic, clock-ratio sweeps), #40 (hardening, WRAP warning, reset sequence), #41 (mutation harness), #42 (coverage report), #43 (smoke subset), and this report's PR.

## Gate evidence

CI on `main` after #43, with the full EDA list: <https://github.com/hdl-tools/hdl-bag/actions/runs/42385746139> (317 EDA tests passed; slowest shard 13:56, with the cache still partly cold after the key change). Jobs: `test` on Ubuntu with Python 3.11 and 3.14 and on Windows with 3.11; `golden`, `vendor`, and four EDA shards in the tools image behind one `eda` check. The previous `main` runs, after #41 (<https://github.com/hdl-tools/hdl-bag/actions/runs/42334772855>) and #42 (<https://github.com/hdl-tools/hdl-bag/actions/runs/42336687741>, 317 EDA tests passed), were also green. The `main` run after #40 failed in every job before any step ran (a GitHub billing hold on the account at the time); #41's run, which includes all of #40, passed.

| Gate item (plan §8 Phase 4) | Evidence |
|---|---|
| Every generated test passes on the golden config and the Phase 3 configs | `tests/gen/test_corpus.py::test_route_simulation` runs the generated module on demo_ss (in four parts) and on every routed config of `tests/configs/p2` and `p3` (35 configs: 11 and 24). The two `bag_external` configs are excluded because BAG doesn't model the user's fabric (D8); a meta-test pins that list. `test_ready_driven_from_valid_settles_and_carries_traffic` reruns traffic with the READY shim. Every test asserts it checked something, and the runner fails a skipped or missing test. |
| Every listed mutation is detected: decode off-by-one, swapped port index, dropped access rule, wrong miss response, inverted secure check, broken alias, priority inversion | `tests/mutation/test_mutations.py`: 55 mutants, all detected. Each gate bug runs on several fabric kinds, and on the first and the last entry of a table where the index could matter; the matrix has holes (known gap 3). Decode off-by-one is modelled by mask proxies: `decode_short` makes every odd byte of a region miss, `decode_short_word` every odd word, and `decode_long` grows the region. Wrong miss response swaps DECERR and SLVERR on AXI, and flips the miss-to-default policy on AHB-Lite and APB. The inverted secure check is mutated both in the package tables and in `bag_addr_decode`'s polarity. The RTL mutants also edit the bridge wrappers (offsets, responses, PSLVERR). `test_every_gate_bug_has_a_mutation_and_a_target` pins the list. |
| A short smoke subset runs per PR in under 15 minutes | #43. With an empty cache (run <https://github.com/hdl-tools/hdl-bag/actions/runs/42337245734>, 168 tests) the slowest shard took 6:52. After the review fixes (run <https://github.com/hdl-tools/hdl-bag/actions/runs/42384688746>, 205 tests, cache partly warm) the slowest shard job took 4:52. The final 205-test subset hasn't run cold; scaling the cold run by recorded duration (1180 s to 1473 s) puts its slowest shard near 8.5 minutes, an estimate. `tests/test_smoke.py` keeps the subset honest: every pattern selects a test, demo_ss is in it whole, every mutant has its control, and the recorded total stays under 2000 s. |
| Carried over: byte-identical generation; ruff, ruff format and mypy --strict clean; existing tests green | `test_generate.py`, `test_wrapper_rules.py`, and the `test` jobs in CI. |

Locally, on this report's tree: ruff, ruff format and mypy --strict are clean, and the 648 host tests pass. The machine's memory doesn't allow the full EDA list locally (the harness stopped full runs three times during the phase), so CI is the evidence for the EDA tests.

## Owner decisions taken during the phase

| # | Decision |
|---|---|
| D44 | Generated output imports the test code from `hdl_bag.tests_gen` rather than copying it; the generated module refuses another tool version. |
| D45 | `bag_external` fabrics can name their module (Fabrics > External module, schema 0.6); when set, ports take position-based names, so fabrics of the same shape can share a module. |
| D46 | Plug-ins are the first v2 item. |
| D47 | A hardening PR (#40) before the mutation harness, with the external review's six confirmed bugs. |
| D48 | QoS arbitration is a v2 candidate; `` `timescale `` is skipped. |
| D49 | WRAP refusal is a `bag check` warning now (`AXI-WRAP-REFUSED`), narrowed by the owner to width converters; in-house WRAP support comes in v2 with the in-house width converters. The demo_ss sample keeps its warning (cpu0's 128-to-64 converter). |
| D50 | The external review reported X-value crashes in the bus models under Xcelium and VCS. The owner reassesses this on another machine; no change until then. |
| D51 | A reset-sequence reference for users whose resets may not all assert together: `docs/reset-sequence.md` and a generated `doc/reset.md`. |
| D52 | Functional coverage in v1 is a report only, with no closure target. |
| CI | CI-time work moved ahead of PR 4. The smoke subset moves the full EDA list off pull requests (#43). |

## Design-agent reviews

Each RTL or verification PR was reviewed (review only) by `chip-design-rtl:rtl-design-orchestrator` and/or `chip-design-verification:verification-orchestrator`. Findings were fixed in the same PR, with a failing test first for bugs, or are listed under known gaps. PR descriptions hold the full lists. **No RTL bug was found in Phase 4**, by the reviews, the mutation harness or the new tests.

| PR | Main findings fixed |
|---|---|
| #32 | A note claimed Max slv trans limits writes through `bag_axi2ahb`. A filter selecting no test passed. The waived snapshot compared only counts. |
| #33 | A simulator crash after clean results passed; a skipped test passed; only the test count was compared (now `SIM-EXIT`, `TEST-SKIPPED`, `TEST-SET`). `bag flow` no longer built the C++ model without tests. The generated module now refuses another tool version. |
| #34 | The scoreboard was weaker than claimed: partial writes after an error beat are now modelled, a masked write that stored nothing no longer passes, reads may not repeat an address, an APB write with no strobes fails, and error injection reads the live error set. |
| #36 | A false failure I had introduced on configs with no usable words. Silently dropped burst shapes (every kind is now required, both refused and passing). Adapter order in `burst_refused`. Two manifest errors (non-modifiable bursts are INCR of 2 to 17 beats, not 2 to 16). |
| #37 | The "masters only" back-pressure pass really ran as "both". Back-pressure never covered bursts. `--ready-shim --filter` ignored the filter. |
| #38 | A real bug in my D45 change: `USERPORT-NAME` still reserved D39's names, so a user port named `s0_axi_awaddr` produced invalid SystemVerilog. Module names that broke the output (keywords, harness and interface names, vendored modules, another fabric's default, case-only differences). My clash check read "Package name" for "Parameter package". |
| #39 | Both new tests checked less than their names said: no quiet window after release, a sparse reset watch, traffic only to first regions, clock pairs that stayed 1:1, resets that missed transfers. |
| #40 | RTL: my "assert in any order" in the reset documents was wrong; PULP `cdc_fifo_gray` needs both sides to assert simultaneously. Verification: `wrap_refusals` dropped the default slave on Access_Rules `none`, so a refusing path could go unwarned (fixed with a failing test first). |
| #41 | The mutants were weaker than claimed: RO rules never mutated, a secure mutant flipping every flag, entry 0 only, timeouts counting as detection, `decode_long` collapsing the region, no AXI4-Lite fabric mutated, `bag_axi2axil`'s offset never mutated. |
| #42 | A truncated hits file made `bag sim` crash and lose the real results. A stale `coverage.md` survived a run with no hits. `save_coverage` in a `finally` could hide a test failure. |
| #43 | A label event posted a second, skipped `eda` check. No AXI mutant ran per PR. The docstring overstated what demo_ss covers. A mutant without its control. No budget check. The interim nightly saved four cache entries a day. |

## Test holes found and closed

- **Single bytes at region ends (#41).** No test sent a byte at an odd address, so a decoder one byte short at a region's end got past every fabric. The mutation harness found it; `single_bytes_reach_both_ends_of_every_region` closes it.
- **Equivalent mutants,** found and documented rather than counted: byte decode on APB, and the `bag_axi2axil` offset on `bridges_pulp_cdc` (retargeted to `mixed_cdc_chain`).
- **D38 outcome (#37).** A VALID that waits for READY isn't flagged as a combinational loop, as D38 expected: the loop settles at 0 and the traffic test times out. A mutation confirmed it is caught.
- **Reset mutations (#39),** on temporary copies of `bag_ahb2apb`: removing reset from the AHB-side request state is caught (a stale APB access after release). Removing it from the APB-side state machine isn't caught; I believe it can't be seen in this design, because the fabric masks PSEL and returns PREADY during reset, but that is reasoning, not a proof.
- **Documentation errors found by tests:** the FIXED-burst note ("when the widths differ", really only when narrowing) and the non-modifiable burst range (#36).

## My own errors during the phase

Stated so the record is complete; each was fixed before merge.
- **D45:** I first told the owner a rename would let fabrics share a module. Under D39's port names it couldn't; the owner chose position names.
- **#38:** the `USERPORT-NAME` bug and the wrong settings key above.
- **#39:** one version sent every master to the same word; one drove resets in cocotb's read-only phase, which first looked like a caught mutant; one always reset on the busiest path.
- **#40:** "assert in any order" in the reset documents.
- **#41:** an early byte test "caught" `decode_short` only through a PROT default bug in the test itself; it first skipped every region's last byte; and I mistook a configured PSTRB tie for an RTL bug.
- **#41:** the harness's docstring said detection excluded "another exception", but the code only excluded timeouts and bring-up failures (cocotb writes `<failure>` for every exception). The review of this report found it; this PR fixes the check with a failing test first, and the full mutation set reran with it (see this PR's CI).
- **#43:** the description gave the full EDA list as 963 tests; it is 317 (963 was close to the whole suite with host tests). Corrected after merge.

## Deviations from the plan

- **D44:** `design/plan.md` §5 has `bag generate` copying the tests, scoreboard and bus models into the output. They are imported from the installed package instead, so output stays small and can't drift; running them needs `hdl-bag[sim]`.
- **The smoke subset moves the full EDA list off pull requests.** A PR runs 205 of the 317 EDA tests, including 14 of the 55 mutants; the rest run after merge on `main`, nightly, and on PRs labelled `full-eda`. A PR that breaks only the full list, or opens a test hole that only a skipped mutant exposes, is found after merge.
- **Verilator USERERROR and USERFATAL** are now errors in lint and compile, but not in `bag sim`, which builds with `-Wno-fatal` (v2 backlog).
- **Coverage (D52)** is a report with no closure target, and circular by construction.
- **Interim nightly:** a cron in `pr.yml` runs the full EDA list daily until Phase 5's `nightly.yml` replaces it.
- **PR 4 was split in two** (#36 and #37) to keep it reviewable, and **PR 5b (#40) and PR 6b (#42)** were added by owner decisions D47 and D52.
- **Schedule:** the plan estimated about 25 engineer-days against a 5-week budget. The work took five days, 2026-11-03 to 2026-11-07.

## Known gaps (carried forward)

1. **Narrow WRAP and FIXED bursts are untested.** cocotbext-axi places their lanes as if bursts only increment. Full-width WRAP is tested (#36). Phase 5's formal properties can close it.
2. **The 4 KB edge burst** can't run on 4 KB regions (it would hit the error word): it runs on 62 of 98 paths.
3. **Mutation matrix holes.** Alias window edges aren't byte-probed, and `AliasMask`/`AliasBase` aren't mutated. Not every bug runs on every fabric kind: `broken_alias` only on the AXI4 crossbar and AHB-Lite, `swapped_port_index` not on AHB-Lite, `wrong_miss_response` not on APB, the secure mutants not on AXI4-Lite or shared buses, `priority_inversion` only on shared buses and AHB-Lite. `dropped_access_*`, `priority_inversion` and most `decode_long` targets mutate the first entry only. Alias pins must be quasi-static.
4. **A single seed** for every random choice in the tests.
5. **PULP bridge internals and CDC:** the in-bridge width converter and FIFO depth of the PULP-backed bridges have no mutants. CDC correctness (synchronizers, gray pointers) can't be detected in zero-delay RTL simulation at all; it needs a CDC tool and Phase 6's constraints (D43). The bridges themselves are covered by route simulation and the offset and response mutants (Phase 3 gap 11 is closed at that level).
6. **Resets:** all domains reset together in the tests; a single domain resetting alone isn't tested (D51 documents the sequence users must follow). The quiet window after release sees only what a slave model logs.
7. **Model limits (#37):** the AHB-Lite and APB master models add no gaps; head-of-line covers reads only; demo_ss doesn't run the crossbar, head-of-line or exclusive tests (adapters and bridges on its paths); forced DECERR proves only "an error" for AHB-Lite and APB masters.
8. **An extra AXI write beat with no strobes** stays allowed, because width converters legitimately add them.
9. **`AXI-WRAP-REFUSED`** ignores a master's address width that can't reach a region, and `bursts.py` keeps an assert for unknown bridge templates.
10. **Coverage** bins come from the probes the tests use, so the report can't show what the tests never try. It has no bins for back-pressure per channel, arbitration contention, alias on and off, or narrow sizes.
11. **Xcelium and VCS** behaviour of the bus models is unassessed (D50).
12. **Carried from Phase 3, unchanged:** CDC constraints and `bag_sync` attributes (gap 3, Phase 6, D43); `bag_axi2ahb`'s limits and byte-write splitting (gaps 4 and 5, documented in Phase 6, changed in v2); async reset on data flops (gap 7); no PREADY timeout (gap 8); the user guide (gap 9); `doc/tieoffs.md` omissions (gap 6); narrow APB behaviour (gap 12, modelled in #34, documented in Phase 6).

## Effect on later phases

- **Phase 5** (formal and nightly regression) builds `bag formal` on the same Finding and Report types, adds D38's per-channel properties on every in-house AXI block, and generates decode properties from the IR. Every package mutant should also fail a decode property. Gaps 1 to 5 are candidates for formal rather than simulation. Its `nightly.yml` replaces the interim cron and runs the full corpus, the mutation harness and random configs; the coverage-gap ideas (bins from the topology, alias on and off, contention, back-pressure per channel, narrow sizes) feed its configuration-space report.
- **Phase 6** generates the CDC constraints and `bag_sync` attributes (D43), documents gaps 4, 5, 8 and 12 and D49's WRAP limits, completes `doc/tieoffs.md`, and publishes the user guide with the check catalogue.
- **v2:** D46 plug-ins first; D36's block-by-block PULP replacement, with in-house WRAP support (D49) and aligned Max trans (D42); D35's protocol versions; D48 QoS; a PREADY timeout; Xcelium and VCS backends after D50; `bag sim` judging USERERROR; the mutation and coverage extensions above.
