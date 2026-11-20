# Spec workbook schema changelog

The workbook's sheets, column order, dropdown lists and spare-row formulas are the schema. `bag check` refuses a workbook whose Project > Spec schema version differs from the release's.

## 0.7 (2026-11-20, hdl-bag 0.0.x)

Changes from 0.6, approved by the owner (issue 106):

- **New setting Project > Module prefix** (optional, the last Project row, so no other row moves). When set, BAG puts it and an underscore in front of every design unit BAG emits (the in-house modules, the modules BAG derives from PULP, the vendored PULP units, BAG's formal checkers, the vendored ZipCPU checkers and the sub-wrapper modules it writes), and their file names. **Not applied to macros, include guards and header file names.** The note of Project name no longer says it prefixes module names.
- Never renamed: the top wrapper, the parameter package, the generated `<project>_<proto>_if` interfaces and a `bag_external` user module.
- **New errors:** PROJ-MODULE-PREFIX (not a lowercase identifier, or ends with an underscore) and PROJ-PREFIX-COLLISION (a prefixed name equals the top wrapper, the flat harness, the package, an interface, a user module or an SV keyword, or a sub-wrapper named like a module, interface or package BAG can copy: PULP, in-house, derived or formal). **New warning:** PROJ-NAME-LENGTH (a generated name over 64 characters, only when a prefix is set).
- **IR:** new field `module_prefix` on `project`.
- No other sheet, column or dropdown-list changes. A 0.6 workbook is refused (PROJ-SCHEMA-VERSION); set the version to 0.7 and add the Module prefix row, left blank. The generated RTL is then unchanged.

## 0.6 (2026-11-05, hdl-bag 0.0.x)

Changes from 0.5, approved by the owner (D45):

- **New column Fabrics > External module** (optional, the last column, so no other column moves). On a `bag_external` fabric it names the user's module, and bus ports are then named by position: `s<k>_` for the fabric's k-th master attachment and `m<k>_` for its k-th slave attachment. Fabrics of the same shape can share a module. Blank keeps `<project>_<fabric>` and `<fabric>_<port>_`.
- **New errors:** EXT-MODULE-NAME (not an identifier, a keyword, a module BAG can generate, or differing from another only in case) and EXT-MODULE-SHARED. On any other fabric a value gives the FAB-NA-SETTING warning and is ignored; n/a means blank.
- USERPORT-NAME reserves the position names on a fabric with an External module.
- Fabrics sharing a module must also match in what `bag check` cannot see, such as ID widths: `bag generate` fails if their ports differ.
- **IR:** new field `external_module` on each fabric.
- No other sheet, column or dropdown-list changes. A 0.5 workbook is refused (PROJ-SCHEMA-VERSION); set the version to 0.6 and add the column header.
- **IR, Phase 5 (owner, 2026-11-09), no version change:** a decode entry may hold at most one alias, as the workbook always gave (one Address_Decode row per master, one alias per row). A hand-written IR with more is refused on load. The decoder lets the last matching alias win, and the route model and the formal reference the first, so with two overlapping aliases they would disagree.

## 0.5 (2026-11-03, hdl-bag 0.0.x)

Changes from 0.4, approved by the owner (known gap 1 of the Phase 3 report):

- **Notes:** Fabrics > Max mst trans and Max slv trans now state what each limits on each path. The old notes did not match PULP. Bridges > FIFO depth says that bridges handling one transfer at a time ignore it.
- **demo\_ss sample:** `br_ahb2main` FIFO depth is 0, not 2. `bag_ahb2axi` has no FIFO, so the RTL does not change.
- **New warning:** BRIDGE-FIFO-IGNORED, for a FIFO depth above 0 on `bag_ahb2apb`, `bag_ahb2axi`, `bag_apb2axi` or `bag_apb2ahb`.
- No sheet, column or dropdown-list changes, and the IR gains nothing. A 0.4 workbook is refused (PROJ-SCHEMA-VERSION); set the version to 0.5 and it reads the same, apart from that warning.

## 0.4 (2026-11-01, hdl-bag 0.0.x)

Changes from 0.3, approved by the owner (D41):

- **New sheet User\_Ports** (after Port\_Options): extra ports of user-supplied fabrics (`bag_external`) and how each connects. Input columns Instance, Port, Direction, Width (bits), Connection, Notes; derived Template and Check (ok | UNKNOWN INSTANCE | NOT USER RTL | DUPLICATE).
- **Lists:** new Direction list (input, output) for User\_Ports > Direction.
- **README:** a User\_Ports row after the last legend row.
- **IR:** new `user_ports` list (instance, port, direction, width, connection, target, tie).
- No other sheet, column or dropdown-list changes. A 0.3 workbook is refused (PROJ-SCHEMA-VERSION); add the empty User\_Ports sheet from `bag template` and set the version to 0.4.

## 0.3 (2026-10-31, hdl-bag 0.0.x)

Changes from 0.2, all approved by the owner:

- **Rebrand:** template IDs `busgen_*` → `bag_*` on Lists > Template, including `bag_external`. README, header comments and notes now say "BAG".
- **Adapter naming:** the Wrapper > Adapter instance naming note lists the new `iw` (ID-width) adapter type.
- **demo_ss sample data:**
  - Access_Rules gains `dma0 → br_main2cfg : -` ("DMA has no config-bus access"), so the tied-PROT DMA cannot reach the secure-only config registers.
  - Max mst trans and Max slv trans read `n/a` on the APB and AHB fabrics, where they don't apply.
- No sheet, column or other dropdown-list changes.

## 0.2

The reference `BusGen_spec_template_demo_ss.xlsx` (BusGen naming).
