# Third-party RTL

hdl-bag ships a subset of [PULP](https://pulp-platform.org/) RTL in
`src/hdl_bag/rtl/vendor/pulp/`. `bag generate` copies the files a design uses
into `rtl/vendor/pulp/` of the output, with the `LICENSE` of each package
they come from, and adds their include directories to the file list.

| Package | Version | Commit | Licence | Source |
|---|---|---|---|---|
| axi | 0.39.11 | `42ca270ebd8bbbc1c64ddc0ca712e722eb9e3f06` | SHL-0.51 | <https://github.com/pulp-platform/axi> |
| common_cells | 1.39.0 | `9ca8a7655f741e7dd5736669a20a301325194c28` | SHL-0.51 | <https://github.com/pulp-platform/common_cells> |

The Solderpad Hardware License v0.51 is permissive (Apache-2.0 style). Each
package's `LICENSE` is kept next to its files, and every file keeps its
upstream copyright header. `src/hdl_bag/rtl/vendor/pulp/index.json` is the
machine-readable record: version, commit and licence per package, and for
every file the design units it defines and the files it needs.

## How the tree is made

- `Bender.yml` pins the versions exactly. Each pin is the minimum version the
  dependency asks for: axi 0.39.11 needs common_cells ^1.39.0, and common_cells
  1.39.0 needs tech_cells_generic ^0.2.11 (pinned as well, but none of its
  files are needed). `Bender.lock` records the commits.
- `scripts/vendor_pulp.py`, run in the tools image, checks out the lock, takes
  the compile order from `bender script flist-plus`, and copies only the
  transitive closure of the PULP modules BAG's templates instantiate (the
  `ROOTS` list), with the headers they include.
- The `vendor` CI job re-runs the script with `--check` and fails if the
  committed tree differs.
- Never edit files under `rtl/vendor/` by hand. To bump a version, edit
  `Bender.yml`, run `bender update` and the script in the tools image, and
  review the diff.

## Lint policy

Vendored files are compiled by every lint tool, so BAG's use of them is
checked. Their **warnings** are counted in a separate "third-party" line and
never fail `bag lint`; **errors** anywhere still fail. Verible, which checks
style only, skips them. Generated and in-house code must still have zero
unwaived findings.

## Formal properties

hdl-bag ships four of [ZipCPU](https://github.com/ZipCPU/wb2axip)'s formal
property files in `src/hdl_bag/formal/vendor/zipcpu/`, unmodified: the AXI4-Lite
(`faxil_master.v`, `faxil_slave.v`) and APB (`fapb_master.v`, `fapb_slave.v`)
protocol properties. `bag generate` copies the ones a design needs into
`formal/vendor/zipcpu/` of the output, with the `LICENSE`.

| Package | Commit | Licence | Source |
|---|---|---|---|
| wb2axip (bench/formal) | `2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b` | Apache-2.0 | <https://github.com/ZipCPU/wb2axip> |

Each file carries ZipCPU's Apache-2.0 header, and the repository's README
licenses it under Apache 2. The repository has no licence text file, so the
vendored `LICENSE` is the standard Apache-2.0 text. The public full-AXI4
property files are deliberately non-functional subsets, so hdl-bag's AXI4
properties are its own. `scripts/vendor_zipcpu.py` fetches the files; the
`vendor` CI job re-runs it with `--check`. Never edit them by hand.

## Python packages for simulation

The `sim` extra (`pip install hdl-bag[sim]`) installs these for `bag sim`. They are not copied into generated output.

| Package | Version | Licence |
|---|---|---|
| cocotb | 2.1.0 | BSD-3-Clause |
| cocotbext-axi | 0.1.28 | MIT |
