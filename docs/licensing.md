# Licensing

## hdl-bag itself

hdl-bag is licensed under the [Apache License, Version 2.0](../LICENSE).
Copyright 2026 The hdl-bag Authors (see [AUTHORS](../AUTHORS)).

Every source file in this repository carries an SPDX header:

```
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The hdl-bag Authors
```

## Generated output

Files that hdl-bag generates from your specification (RTL, wrappers,
parameter packages, C headers, documentation, tests, formal setups and IR
files) are **your** files. They are not covered by the Apache License of
hdl-bag, even where they contain text copied from hdl-bag templates, and
you may license them however you choose.

Each generated file starts with:

1. the text of the file named by **Wrapper > File header** in your workbook,
   copied verbatim (use it for your own copyright and licence notice); then
2. a tool banner naming the hdl-bag version, project, spec revision and the
   SHA-256 of the IR, plus a "DO NOT EDIT" notice. The banner holds no
   timestamp, so regenerating an unchanged spec gives byte-identical files.

Generated RTL may instantiate third-party IP (for example
pulp-platform/axi). `bag generate` copies the files of that IP into
`rtl/vendor/` of the output, with each package's `LICENSE` next to them. That
IP keeps its own licence and copyright headers, and it is not yours to
relicense; see [third-party.md](third-party.md).

## Third-party components

Only permissively licensed third-party RTL is used (Solderpad SHL-0.51,
MIT, BSD, Apache-2.0). Reciprocal licences such as CERN-OHL-S are never
vendored.
