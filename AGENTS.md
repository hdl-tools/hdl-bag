# hdl-bag: instructions for coding agents

BAG (Bus Architecture Generator) turns an Excel spec workbook into AMBA fabric RTL (AXI4,
AXI4-Lite, AHB-Lite, APB), a wrapper, collateral, lint flows, generated cocotb tests and generated
SymbiYosys formal jobs. The canonical spec is the YAML IR, and every output is generated
deterministically from it. `bag regress` runs random configurations through the whole flow.

These rules apply to every person and coding agent working in this repository, on any machine.

## Where things are

The pipeline in `src/hdl_bag/`:

`reader/` (workbook to raw spec) → `checks/` (`bag check`, rule IDs in `checks/core.py`) → `ir/`
(resolve, YAML IR, `to_workbook`) → `gen/` (netlist, package, wrappers, collateral) → `flows/`
(lint, compile, sim and formal backends) → `tests_gen/` (generated cocotb tests, reference model,
bus models, coverage) and `formal_gen/` (generated SymbiYosys jobs).

| Path | Holds |
| --- | --- |
| `src/hdl_bag/templates/` | Fabric, adapter and bridge manifests, with Jinja instance templates |
| `src/hdl_bag/template/` | The spec workbook builder and the `demo_ss` sample |
| `src/hdl_bag/rtl/inhouse/` | Hand-written RTL |
| `src/hdl_bag/rtl/vendor/pulp/` | Vendored PULP IP. Never edit by hand: use `scripts/vendor_pulp.py` |
| `src/hdl_bag/formal/` | Property files. `formal/vendor/` is managed by `scripts/vendor_zipcpu.py` |
| `src/hdl_bag/shrink/` | `bag shrink`: the edits, the greedy engine and the oracle that runs one EDA step |
| `src/hdl_bag/regress/` | The workbook DSL, the random config generator and the per-seed runner |
| `src/hdl_bag/bursts.py` | AXI4 burst refusal logic, shared by `bag check` and the tests |
| `docs/manual/` | The LaTeX user manual |
| `tests/<area>/` | Tests, mirroring the source |
| `tests/configs/` | The gate corpora and their committed IR |
| `tests/golden/` | `demo_ss` (workbook, IR) and the collateral snapshots |
| `tests/mutation/` | The mutation harness |
| `tests/manual/` | The manual's rules and transcript replayer |
| `tests/smoke.py` | The per-PR EDA subset |
| `tests/test_workflows.py` | What the CI workflows promise |

`design/`, `plan/`, `reference/` and `CLAUDE.md` are private and git-ignored, so a fresh clone does
not have them. Nothing may depend on them, and tests that need them skip when they are absent.

## Hard rules

- **Never weaken, skip or waive a check or test to get green.** Fix the cause. If the expectation
  itself was wrong, say so and show why.
- **A failing test comes first** for every bug fix, including bugs found in review. Show it
  failing, locally or in CI for EDA tests, before the fix.
- **Determinism.** Generated output is byte-identical across runs and `PYTHONHASHSEED`: no
  timestamps, sorted iteration, LF line endings (`newline="\n"`).
- **The IR is the contract.** A schema change bumps `SCHEMA_VERSION`, updates
  `docs/schema-changelog.md`, and regenerates the corpus IR (`uv run python
  tests/configs/<pN>/corpus.py`) and the golden files.
- **Every check has a rule ID** in `checks/core.py` and a broken-workbook test in
  `tests/checks/test_checks.py`. What `bag generate` refuses should be a check too.
- **Generated tests.** Each `TESTS` entry's `applies` predicate uses the same helper as the test
  body, and every test asserts that it checked something. Checks must not import test code: shared
  logic goes in the package.
- **The manual.** Its rules are the docstring of `tests/manual/test_manual.py`. Every example is a
  file under `docs/manual/examples/` that a test replays; no example is typed into the text. A
  change of behaviour updates the manual in the same PR. Paths that docstring cites in `design/`,
  `plan/` or `../hdl-vet` are not in a clone: the code wins.
- **Template changes** (`template/builder.py`) need the golden workbook refreshed from the CI
  `golden-demo_ss` artifact (its `demo_ss.recalc.xlsx` replaces `tests/golden/demo_ss.xlsx`), then
  the golden IR (`uv run bag ir tests/golden/demo_ss.xlsx -o tests/golden/demo_ss.ir.yaml`) and the
  collateral snapshots (`BAG_UPDATE_SNAPSHOTS=1`). The snapshot diffs should be hash lines only.
  The maintainer also runs the private reference-workbook comparison for template changes.
- **Licences.** Only permissive third-party RTL. Every `.py`, `.sv` and Dockerfile carries an SPDX
  header (`scripts/check_spdx.py`); vendored files keep their upstream headers.

## Code style

- **Python 3.11+**, `ruff` (line length 100, rules E F W I B UP SIM RUF), `ruff format`, and
  `mypy --strict` over `src tests scripts`. Pydantic v2 models are frozen.
- **Docstrings** say what and why, in plain sentences. Comments explain intent, not mechanics.
- **Formatting.** `# fmt: skip` keeps hand-aligned tables and long literal lists as written. Rule
  messages and schema text stay on one line (per-file E501 ignores).
- **Errors** are user-facing and specific: they name the sheet and row, the value, and what to do
  about it. The CLI never shows a raw traceback.
- **Tests** name the behaviour (`test_a_denied_default_slave_is_still_reached_on_a_miss`), assert
  exact messages, and say why in a docstring when it isn't obvious. EDA tests are marked
  `@pytest.mark.eda`; nightly-only ones are also marked `deep` or `alone`.
- **SystemVerilog (in-house):** parameterised modules with flat packed-array ports, and an
  elaboration `$error` or `$fatal` for illegal parameters, each with a case in
  `tests/rtl/test_negative_elaboration.py`. Lint-clean under slang, Verilator and Verible. A
  `$clog2` (signed) assigned to an unsigned parameter takes an `unsigned'()` cast.

## Commands

You need `uv`, and for the manual TeX Live or MiKTeX with `latexmk`.

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run mypy src tests scripts
uv run python scripts/check_spdx.py \
  $(git ls-files '*.py' '*.sv' ':(exclude)src/hdl_bag/rtl/vendor') tools/Dockerfile
uv run pytest -m "not eda" -n 4          # host tests, no EDA tools
cd docs/manual && latexmk -pdf -interaction=nonstopmode -halt-on-error hdl-bag.tex
```

Run the full host suite before every push, not only the tests you changed.

`BAG_MANUAL_UPDATE=1 uv run pytest tests/manual` rewrites the manual's host transcripts and
`docs/manual/checks.tex` from what the code prints now. Transcripts under
`docs/manual/examples/console/eda/` need the same variable with `-m eda` in the tools image.
`BAG_UPDATE_SNAPSHOTS=1` rewrites the collateral snapshots. Read the diff before committing either.

EDA tests need the pinned tools in `tools.lock`. Build the tools image once, then run in it. The
named volume keeps the container's Python environment apart from the host's `.venv`:

```sh
docker build -t hdl-bag-tools:local tools
docker run --rm -v "$PWD:/work" -v hdl-bag-venv:/venv -e UV_PROJECT_ENVIRONMENT=/venv \
  -e UV_LINK_MODE=copy -w /work hdl-bag-tools:local \
  sh -c 'uv run pytest -m eda -n 2 -q <targets>'
```

CI is the EDA evidence. Dispatching a workflow needs write access to the repository; without it,
ask the maintainer to run it.

| Command | Runs |
| --- | --- |
| A pull request | Host tests and the smoke subset (`eda and smoke`) |
| The `full-eda` label on a pull request | The full list, on that push and every later one |
| `gh workflow run pr.yml --ref <branch> -f suite=full` | The full list (`eda and not deep`) |
| `gh workflow run pr.yml --ref <branch> -f select=<pytest -k>` | Only the matching EDA tests |
| `gh workflow run pr.yml --ref <branch> -f suite=deep` | Only the deep tests |
| `gh workflow run nightly.yml --ref <branch>` | The corpus with deep and alone tests, and 50 random seeds |

`scripts/eda_durations.py` rewrites all of `tests/eda_durations.json` from one log, so give it a
complete run only. After a partial run, set the measured entries by hand. A new EDA test gets a
provisional entry until CI measures it.

`uv run bag regress --seed S -o out/regress` reruns one random seed. Run it in the tools image;
`--no-formal` skips formal. Mutations on hand-written RTL go on a scratch copy, never in the
repository.

## Workflow

- **One PR per topic**, on a `phase<N>/<topic>` branch from an up-to-date `main` (ask the
  maintainer for the current phase). The maintainer merges. State your next item to the maintainer,
  and don't start it until your current PR is merged.
- **Reviews.** Every RTL, verification or documentation PR gets an independent, review-only pass by
  a person or agent that did not write it. Fix the findings in the same PR, with a failing test
  first for bugs, and summarise the review in the PR description.
- **CI.** A PR is ready only when CI passes on its final commit. Record the run link and the slowest
  EDA shard in the PR description.
- **Attribution.** None in commit messages or PR descriptions.
- **Reporting.** State results plainly, including your own mistakes, and say what was verified
  locally and what in CI.
- **Decisions.** Decisions cited in the code as D-numbers are settled and are not re-argued. Their
  full record is private, so ask the maintainer if the code comment does not explain one. Ask too
  when a choice is genuinely theirs, such as user-visible behaviour, and offer options with a
  recommendation.
- **Privacy.** This repository is public. Keep personal details, local paths, credentials and
  private planning material out of commits, PRs and issues.
