# Spec workbook schema changelog

The workbook's sheets, column order, dropdown lists and spare-row formulas are the schema. `bag check` refuses a workbook whose Project > Spec schema version differs from the release's.

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
