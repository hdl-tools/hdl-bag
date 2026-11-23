# Example workbooks

Three spec workbooks, each `bag check` clean with zero findings. Open one in Excel or LibreOffice
and edit it, or run it as it is:

```sh
bag flow examples/soc_4x8.xlsx -o out/     # check, generate, lint, elaborate, simulate
```

| Workbook | What it is |
| --- | --- |
| `apb_min.xlsx` | One APB4 master and three slaves: the smallest workbook that generates. Every sheet is filled once. |
| `mcu_ahb.xlsx` | A microcontroller bus: an AHB-Lite CPU, an SRAM, and an APB4 bus of three peripherals behind a `bag_ahb2apb` bridge. |
| `soc_4x8.xlsx` | An AXI4 crossbar with four masters (`cpu_i`, `cpu_d`, `dma` and an AXI4-Lite `dbg`) and eight slaves (`rom`, `ram` and six APB4 peripherals behind a `bag_axi2apb` bridge), on one clock, with two access-rule denials. |

`header.txt` is the file header all three name in Wrapper > File header. For a spec of your own,
copy a workbook, change its names, and run `bag check` after each sheet. For everything the
template can do (several clocks, CDC, width converters, aliases, secure regions) see the
`demo_ss` sample: `bag template --sample`.

These files are in the repository only; a `pip install` does not include them. They are built by
`tests/examples/build.py`, which a user of the tool never needs.
