# Third-party RTL

hdl-bag ships a subset of [PULP](https://pulp-platform.org/) RTL in
`src/hdl_bag/rtl/vendor/pulp/`. `bag generate` copies the files a design uses
into `rtl/vendor/pulp/` of the output, and adds their include directories to
the file list.

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
