# Spec workbook schema changelog

The workbook's sheets, column order, dropdown lists and spare-row formulas are the schema. `bag check` refuses a workbook whose Project > Spec schema version differs from the release's.

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
