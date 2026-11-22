# Template author guide

This guide is for someone who wants to add a fabric, adapter or bridge template to BAG, or to
replace a vendored PULP block with their own RTL. It describes what the code does today
(api_version 1) and where v2 is headed.

**The plug-in format is unpublished until v2.** Today a template is a directory inside the `hdl_bag`
package, and `bag generate` reads templates from nowhere else. Adding one means changing hdl-bag
itself, through a pull request. The manifest format below is the one the built-in templates use, and
v2 plug-ins may use the same format, but nothing here is a promise to plug-in authors: how a plug-in
is found, packaged and trusted is not decided in a form this guide can state (see [The plug-in
direction](#the-plug-in-direction-d46)). The code is the authority where this guide and it disagree.

Three block quotes below are gaps: each is a question about a settled decision whose reasoning is
not in the repository. They all start with a bold marker and can be found with one search:
`grep -n "^> .*Maintainer note needed" docs/template-author-guide.md`.

## What a template is

A template tells BAG how to instantiate one block in the generated wrapper. There are three
kinds, set by the manifest's `kind`:

| Kind | What it is | Buses the template sees |
| --- | --- | --- |
| `fabric` | An interconnect: crossbar, shared bus, single-layer or decoder-mux | `inst.masters`, `inst.slaves`: the buses at its master-side and slave-side slots |
| `bridge` | Joins two fabrics of different protocols | `inst.up`, `inst.down` |
| `adapter` | Converts one property of a port towards its fabric: protocol (`pc`), data width (`dw`), clock (`cdc`), ID width (`iw`), FIFO (`fifo`) or register slice (`cut`) | `inst.up`, `inst.down` |

A template is a directory `src/hdl_bag/templates/builtin/<name>/` with two files:

- `manifest.yaml`: what the template is and which files it needs;
- `instance.sv.j2`: a Jinja template that renders one instantiation of the block.

The in-house RTL it instantiates is in `src/hdl_bag/rtl/inhouse/`. The vendored PULP RTL is in
`src/hdl_bag/rtl/vendor/pulp/`.

## The manifest

`Manifest` in `src/hdl_bag/templates/manifest.py` is a frozen pydantic model with
`extra="forbid"`: an unknown key is an error, and so is a wrong type. This is `bag_axi2apb`, a
bridge, abridged:

```yaml
name: bag_axi2apb
kind: bridge
api_version: 1
description: 'AXI4/AXI4-Lite to APB4/APB3 from PULP blocks: ...'
upstream:
  - AXI4
  - AXI4-Lite
downstream:
  - APB4
  - APB3
sources:
  - bender:axi:axi_cdc
  - bender:axi:axi_dw_converter
  - rtl/inhouse/bag_axi2apb.sv
  - rtl/inhouse/bag_axi_from_flat.sv
qualification: unqualified
rtl: true
```

### Keys

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | string | yes | Must equal the directory name (`builtin_registry` raises if not) and be unique in the registry |
| `kind` | `fabric`, `bridge` or `adapter` | yes | See above |
| `api_version` | `1` | yes | The only legal value is the integer 1 (`Literal[1]`). See below |
| `description` | string | yes | Free text. No generator or check reads it. Built-in manifests also use it to record the template's limits, for example the PULP burst restrictions of `bag_axi2apb` |
| `protocols` | list of protocols | fabric and adapter | Fabric: the protocols it can implement. Adapter: the protocols it works on. Not used by a bridge |
| `topologies` | list | fabric | Any of `crossbar`, `shared-bus`, `single-layer`, `decoder-mux` |
| `arbitration` | list | fabric | Any of `round-robin`, `fixed-priority`, `single-master` |
| `upstream`, `downstream` | list of protocols | bridge | The protocols of the fabric on the master side and on the slave side |
| `adapter_type` | `pc`, `dw`, `cdc`, `iw`, `fifo` or `cut` | adapter | Which adapter this is. `Registry.adapter(type)` returns the first manifest with the type, so there is one template per adapter type |
| `sources` | list of strings | no | Files the template needs, see below |
| `rtl` | bool, default `false` | no | `true` when the template has RTL and an `instance.sv.j2`. `bag generate` stops with `MissingRtl` for a template that has neither `rtl` nor `opaque` |
| `opaque` | bool, default `false` | no | The user supplies the RTL. See below |
| `fifo_depth` | bool, default `true` | no | `false` for a bridge that handles one transfer at a time: `bag check` then warns (`BRIDGE-FIFO-IGNORED`) when the workbook sets a FIFO depth above zero for it |
| `down_id_width` | int | no | Bridge only: the AXI ID width the bridge drives on an AXI4 downstream bus, where it is a master. `build_netlist` uses it for the bridge's downstream bus, and the checks' ID-width derivation assumes the same value. `bag_ahb2axi` and `bag_apb2axi` set it to 1 |
| `qualification` | `unqualified` (default) or `qualified` | no | Provenance for a human reader; no code reads it. Every built-in template is `unqualified` |

A manifest key the model does not name is refused when the file is loaded (`extra="forbid"`).
`licence`, `data_widths`, `param_map` and `port_map` were removed because no code read them and they
implied behaviour that does not exist. Licence information is not in the manifest: it lives in
`docs/bom.md`, generated from each file's SPDX header. The keys that gate behaviour are `kind`,
`protocols`, `topologies`, `arbitration`, `upstream`, `downstream`, `adapter_type`, `sources`,
`rtl`, `opaque`, `fifo_depth` and `down_id_width`.

### What each kind must have

`Manifest._by_kind` enforces it when the file is loaded:

- a `fabric` needs `protocols`, `topologies` and `arbitration`;
- a `bridge` needs `upstream` and `downstream`;
- an `adapter` needs `protocols` and `adapter_type`.

What `bag check` does with them (`checks/ports.py`): a Fabrics row whose Backing template is not
a fabric template, or does not list the row's protocol or topology, gets `TMPL-FABRIC`; an
arbitration the template does not list gets `FAB-ARBITRATION`. A bridge whose upstream or
downstream fabric has a protocol outside the manifest's `upstream` or `downstream` gets
`TMPL-BRIDGE`.

### `api_version`

`api_version` is `Literal[1]` in the model. Pydantic rejects any other value, so a manifest of a
newer format fails to load with a validation error. Nothing else compares it: there is no range,
no compatibility rule and no migration. What a v2 manifest looks like, and whether version 1
manifests keep loading, is not decided.

### `sources`

`sources` lists what `bag generate` copies into the output and puts in the file list. An entry is
one of:

- a package-relative path, for in-house RTL: `rtl/inhouse/bag_axi_xbar.sv`;
- `bender:<package>:<module>`, for the vendored PULP tree: `bender:axi:axi_xbar`.

`resolve_sources` in `templates/sources.py` expands a Bender reference to the module's file and
everything it needs, using `src/hdl_bag/rtl/vendor/pulp/index.json`, which
`scripts/vendor_pulp.py` writes (see `docs/third-party.md`). It raises `SourceError` for a module
that is not in the vendored tree, so a template can only name PULP modules that are already
vendored; adding one means adding it to `ROOTS` in `scripts/vendor_pulp.py` and re-vendoring. The
result is ordered: vendored files in index order, then in-house files sorted by path.

`bag generate` takes the union of the `sources` of every template the design uses, plus
`rtl/inhouse/bag_rst_sync.sv`, which every wrapper needs. Two places read `sources` for meaning
rather than only copying:

- `checks/ports.py` treats a fabric whose `sources` contain `rtl/inhouse/bag_axi_guard.sv` as
  guarded, and warns (`FAB-GUARD-ID-WIDTH`) when its master-side ID width is large;
- `formal_gen/decode.py` generates decode properties for a fabric whose `sources` contain
  `rtl/inhouse/bag_addr_decode.sv`.

So listing a file there can be a statement about the block, not only a file list.

### `opaque`

`opaque: true` means BAG does not supply the RTL; the user does. Only `bag_external` is opaque.
Its manifest has no `sources` and no `rtl`, and lists every protocol, topology and arbitration,
because the user's module can be anything. What `opaque` changes in the code:

- `bag generate` accepts the template without `rtl` (`m.rtl or m.opaque`);
- it writes a stub of the user's module, `rtl/ext/<module>.sv`, with the contract's ports and
  outputs tied to zero, so the design lints and elaborates (`gen/external.py`);
- it does not generate cocotb tests or formal jobs for a design that has an opaque fabric,
  because BAG does not know the fabric's behaviour (D8). The memory map, decode tables and the
  other collateral are still generated;
- `bag check` accepts the External module column and User_Ports rows only on a fabric whose
  template is opaque (`FAB-NA-SETTING`, `USERPORT-INSTANCE`).

`bag_external` is the nearest thing to a plug-in today; see
[User-supplied fabrics](#user-supplied-fabrics-bag_external).

### Adding a template to the workbook

Fabric and bridge templates are chosen by name in the workbook, from a dropdown. The dropdown is
the list `LISTS["Template"]` in `src/hdl_bag/template/builder.py`, a hand-kept Python list, not
generated from the registry. `tests/templates/test_registry.py` requires the fabric and bridge
manifests and that list to match. So a new fabric or bridge template changes the workbook
template, and AGENTS.md's rule for template changes then applies: refresh the golden workbook
from the CI `golden-demo_ss` artifact, then the golden IR, the collateral snapshots and the
`examples/` workbooks. Adapters are not in the list: the workbook asks for an adapter by type,
and `ir/resolve.py` finds the manifest with `Registry.adapter(type)`.

## How a template is instantiated

`gen/wrapper.py::render_instance_ctx` renders `<name>/instance.sv.j2` with a Jinja environment
from `gen/render.py`: `StrictUndefined` (a misspelt name is an error, never an empty string),
`trim_blocks`, `lstrip_blocks`, `keep_trailing_newline` and no autoescape. Output is
deterministic, as everything BAG generates must be.

### Names available in a template

| Name | What it is |
| --- | --- |
| `inst` | The `Instance` of `gen/netlist.py`: `name` (the IR name), `kind`, `template`, `rtl_name` (the instance name in the RTL), `wrapper`, `fabric`, `bridge`, `adapter`, and the buses `masters`, `slaves`, `up`, `down` |
| `name` | `inst.rtl_name`: use it as the instance name |
| `fabric`, `bridge`, `adapter` | The IR object of the instance's kind, `None` for the others. Fields are those of `ir/model.py`: for example `fabric.addr_width`, `fabric.arbitration`, `fabric.domain`, `bridge.upstream`, `bridge.window_base`, `bridge.cdc`, `bridge.fifo_depth`, `adapter.fabric` |
| `spec` | The whole IR. A bridge or adapter template uses it to find its fabric: `spec.fabrics \| selectattr("name", "equalto", adapter.fabric) \| first` |
| `pkg`, `p` | The parameter package name, and the CamelCase instance name. The generated package declares `<Instance><Param>` constants, so `{{ pkg }}::{{ p }}DataWidth` is the data width of this instance. `bag_axi_xbar` builds that prefix as `{% set t = pkg ~ "::" ~ p %}` |
| `tables` | The decode tables of a fabric (`None` otherwise) |
| `bridge_offset(name)` | The pass-through address offset of bridge `name` |
| `lit(width, value)` | A sized SystemVerilog literal; `-1` means all ones; a value that does not fit is an error |
| `unit(name)` | The emitted name of an in-house or PULP module, with the project's Module prefix applied. Instantiate a module as `{{ unit("bag_axi_xbar") }}`, never by its bare name |
| `opaque_module(fabric)`, `opaque_ports(inst)`, `user_ports(inst)` | Used by `bag_external` only |
| `ctx` | The `InstanceContext`, below |

A `Bus` (`gen/netlist.py`) carries `name`, `protocol`, `addr_width`, `data_width`, `id_width`,
`user_width`, `domain`, `is_slave` (the attached component acts as a slave on this bus),
`signals` (the signals that exist on it) and `ties` (signals a Port_Options row ties off, with
the value).

### Connecting signals: `ctx`

A template never writes a net name. It asks `ctx`, which returns an expression for each signal
and records what it handed out:

- `ctx.i(bus, "awaddr", default, width)`: the value for a component **input**. The net, or a tie
  constant if Port_Options tied the signal, or `default` if the bus does not have the signal (for
  example AxLEN on AXI4-Lite). A bus narrower than `width` is zero-extended and a wider one
  truncated; each such fit is recorded in `doc/tieoffs.md`;
- `ctx.o(bus, "awready", width)`: the target for a component **output**. The net, or an unused
  sink when the signal is tied or absent;
- `ctx.vec(port, buses, sig, direction, default, width)`: a packed `[N-1:0][W-1:0]` array for one
  port of a multi-port component such as a crossbar, wired element by element to the buses;
- `ctx.clk(domain)` and `ctx.rst(domain)`: the clock, and the conditioned active-low reset, of a
  clock domain;
- `ctx.alias_en()`: the alias enable vector of a fabric.

An example, from `bag_axi2apb`:

```jinja
.s_awaddr_i  ({{ ctx.i(up, "awaddr", 0, up.addr_width) }}),
.s_awsize_i  ({{ ctx.i(up, "awsize", full_size, 3) }}),
.m_paddr_o   ({{ ctx.o(dn, "paddr", dn.addr_width) }}),
.m_pready_i  ({{ ctx.i(dn, "pready", 1, 1) }}),
```

### What the generator enforces on a template

- **A signal name must belong to the bus's protocol family.** `ctx` raises `GenerateError` for a
  name outside `family_signals(bus.protocol)`. The families are AXI (AXI4 and AXI4-Lite), AHB and
  APB (APB4 and APB3), so a template may ask for `awlen` on an AXI4-Lite bus and take its
  default, but cannot ask for an AXI signal on an APB bus.
- **Every signal on every attached bus must be connected** (D40). After rendering,
  `InstanceContext.check_connected` requires that each signal in `bus.signals`, tied ones
  included, was requested through `ctx` and appears in the rendered text. Lint cannot see a
  forgotten signal, so the generator refuses it: `template <t> (<inst>) does not connect
  <signals> of <bus>`. Ask for every signal, and give a default for the ones your block lacks.
- **Parameters come from the generated package or from the buses.** A template connects
  `pkg::<Instance><Param>` constants, or computes a value from `inst`, its buses and its
  `fabric` or `bridge`. The package is written by `gen/package.py`.

### Where the buses come from

`gen/netlist.py::build_netlist` builds one `Bus` per link from the IR. For a fabric,
`inst.masters` and `inst.slaves` are the buses at its slots **after** each port's adapters, so
the widths and protocol the template sees are those at the fabric, not at the port. For a bridge
or adapter, `up` is the side towards the master and `down` the side towards the slave. A bridge's
Port_Options apply to its downstream end. `Bus.signals` comes from
`protocols.port_signals(protocol, is_slave, has_id, has_user)`, which is why an AXI4 bus without
IDs lacks `awid` and why a template must supply defaults.

### Module style

In-house SystemVerilog follows AGENTS.md: parameterised modules with flat packed-array ports (the
`bag_axi_to_flat` and `bag_axi_from_flat` modules adapt the PULP struct ports to that style, so
the instance template connects plain vectors), and an elaboration `$error` or `$fatal` for each
illegal parameter. A module must be lint-clean under slang, Verilator and Verible, and a
`$clog2` assigned to an unsigned parameter takes an `unsigned'()` cast. Every `.sv` and `.py`
file carries an SPDX header (`scripts/check_spdx.py`), and every `.j2` file starts with the two
Jinja comment lines of the existing templates.

## How an in-house block is verified

A new in-house block meets four layers. Together they are what "verified" means in this
repository.

1. **Lint.** `tests/rtl/test_inhouse.py` lints every stand-alone block in
   `src/hdl_bag/rtl/inhouse/` with every lint tool, and lints each template's `sources`
   with a small testbench (`_template_sources` resolves the manifest's sources exactly as
   `bag generate` does). The AXI blocks that build on vendored PULP RTL (`bag_axi*`,
   `bag_prio_arb`) are linted and simulated inside generated designs instead, by
   `tests/gen/test_corpus.py`. These tests are `eda`: they need the pinned tools, so run them in
   the tools image (see AGENTS.md).
2. **Negative elaboration.** `tests/rtl/test_negative_elaboration.py` has one case per parameter
   check: the module elaborates cleanly with its defaults, and each tool (slang and Verilator)
   reports the check's message for one illegal override. A meta-test requires a case for every
   `$error` and `$fatal` message in `rtl/inhouse/`, so a new check without a case fails.
3. **Directed tests**, for the blocks that have a bench: `tests/rtl/cocotb_<block>.py` with a
   testbench in `tests/rtl/tb/`, for the AHB and APB blocks and the bridges between them.
4. **Generated tests and formal jobs.** Every config in the corpus (`tests/configs/`) is generated
   and simulated through its top, and where it applies proved with SymbiYosys. The pieces are
   below.

### Generated tests

`bag generate` writes `tests/<project>_tests.py` and `bag sim` runs it. The tests themselves live
in `hdl_bag.tests_gen` (D44), not in the output. The test list is the tuple `TESTS` in
`tests_gen/__init__.py`. Each entry is a `GeneratedTest(name, timeout_us, applies)`:

- `name` is a function in `tests_gen/route_tests.py`;
- `timeout_us` is a few times the longest run seen, so a deadlocked design fails within minutes;
- `applies` takes the IR and is `False` only when the test could check nothing on this config.

Two of AGENTS.md's rules govern new entries. The `applies` predicate uses the same helper as the
test body, so a test runs exactly when it has something to check; and every test asserts that it
checked something, so a test that matches nothing cannot pass silently. Checks must not import
test code: shared logic goes in the package (`bursts.py` is the example, shared by `bag check`
and the tests).

The tests drive a design through its top against `tests_gen/route_model.py`, the reference model
of what each master should reach and what each response should be. A new template is therefore
tested without new tests if the model already describes its behaviour; a block whose behaviour
the model does not describe needs the model, and a `TESTS` entry, extended. This is also why an
opaque fabric gets no generated tests: the model cannot know what the user's RTL does.

### The mutation harness

`tests/mutation/` answers a different question: do the tests notice a wrong block? `mutations.py`
is a catalogue of bugs a generator could make, each injected as one exact edit, either of one
table in the generated parameter package (`decode_short`, `decode_long`, `swapped_port_index`,
`dropped_access_none`, `dropped_access_ro`, `dropped_region_ro`, `wrong_miss_response`,
`secure_dropped`, `secure_spurious`, `broken_alias`, `priority_inversion`) or of one in-house RTL
file, for the bridges (`BRIDGE_MUTATIONS`, for example `axi2apb_pstrb`: PSTRB tied to all ones).

`test_mutations.py` runs a config's whole generated suite on each mutant. A mutant is
**detected** only if at least one generated test fails, every failing test fails on an
`AssertionError` (not a timeout, not another exception, not during bring-up), and nothing else
goes wrong. A mutant that survives is a hole in the tests. `test_decode_mutations.py` requires
that the same package mutants also fail a formal property. Each mutation runs on several fabric
kinds, and on the first and last entry of a table where the index could matter; a control runs
the unmutated suite, which must pass.

For a new in-house block this means: if a plausible one-line edit can break its behaviour, add
that edit to `BRIDGE_MUTATIONS` (or a table mutation, if the block is table-driven) with a config
in `tests/configs/` that exercises it. The edit's text must match exactly once; the harness
refuses an edit that changes nothing.

### Formal

`formal_gen/` generates SymbiYosys jobs from the IR: decode properties, route jobs per fabric or
bridge, and (D38) per-channel protocol properties on the in-house AXI blocks. The vendored ZipCPU
protocol checkers in `src/hdl_bag/formal/vendor/` are managed by `scripts/vendor_zipcpu.py` and
are never edited by hand.

## Protocol versions: a family plus feature flags (D35)

D35, quoted verbatim from `docs/phase-3-report.md`:

> Protocol versions stay out of v1. v2 candidates, in order: AXI5 atomics and user/wakeup, APB5,
> AHB5 exclusives and write strobes, AXI3 only as a port adapter. Full AMBA 2 AHB is dropped.

What the code does today, which is the shape a template author works in:

- `protocols.Protocol` has five members: `AXI4`, `AXI4-Lite`, `AHB-Lite`, `APB4`, `APB3`. A
  template lists some of them in `protocols`, `upstream` or `downstream`.
- Protocols belong to families: AXI (AXI4 and AXI4-Lite), AHB (AHB-Lite) and APB (APB4 and
  APB3). `family_signals` returns every signal name of a protocol's family, and `ctx` accepts any
  of them on a bus of the family, whether or not that bus has it. That is how one
  `instance.sv.j2` serves both AXI4 and AXI4-Lite, or both APB versions.
- Within a protocol, optional signals are flags on the bus. Port_Options can tie `PROT`, `QOS`,
  `REGION`, `CACHE`, `LOCK / excl`, `PSTRB` and `PSLVERR` (`OPTIONAL_SIGNALS` in `protocols.py`),
  and a port can lack IDs and user signals. The bus's `signals` and `ties` carry that into the
  template, which sees an absent signal as its `default`.
- One AMBA 5 signal already enters this way: `hnonsec` is the AMBA 5 AHB security signal in the
  AHB-Lite signal list (D27), and `hmastlock` is not forwarded to slaves in v1 (D26).

> **Maintainer note needed:** D35 says protocol versions stay out of v1 and gives a v2 order, and
> the Phase 3 report (line 310) says this guide carries "the template author guide note on
> protocol feature flags (D35)". The one-line decision does not say what a feature flag is.
> Question: in v2, is a protocol version (AXI5, APB5, AHB5) a new member of a family with its own
> flags, or a flag set on the existing family member (for example an atomics flag on AXI4)? And
> how does a template declare the flags it supports in its manifest and read them in
> `instance.sv.j2`? Until this is answered the guide states only the current mechanism above and
> promises no manifest key for it.

What follows from D35 for an author today: do not add a protocol version as a new `Protocol`
member. A block that needs a signal outside the five protocols' lists is a v2 item and is not
supported. AXI3 is only ever a port adapter, never a fabric protocol.

## Replacing a PULP block (D36)

D36, quoted verbatim from `docs/phase-3-report.md`:

> PULP stays the v1 backend. v2 replaces it block by block, each block passing the same tests as
> its PULP counterpart.

Two related decisions name behaviour changes that replacement brings. D42 says Max mst trans and
Max slv trans mean different things on AXI4 and AXI4-Lite because PULP does, that v1 documents the
difference, and that the in-house crossbars of v2 give both protocols one meaning. D49 says WRAP
bursts that PULP's width converters refuse (`AXI-WRAP-REFUSED`) get in-house support in v2, with
the in-house width converters.

How a replacement is done with the machinery above. This is a reading of the code, not a
description of a decided process:

1. Write the in-house module with the same ports as the block it replaces, in the flat style of
   `bag_axi_to_flat` and `bag_axi_from_flat`. Several built-in blocks already wrap one PULP
   module this way (`bag_axi_cdc` around `axi_cdc`).
2. In the template's manifest, replace the `bender:` entry in `sources` with the in-house file.
   The instance template changes only if the ports do.
3. The generated tests, the mutation harness and the formal jobs of every config that uses the
   template then run against the new block unchanged, because they test the top and the IR's
   model. That is what "passing the same tests as its PULP counterpart" can mean in this
   repository. The replacement is not done until the corpus passes, the mutants are still
   detected, and the block has its lint and negative-elaboration cases.
4. When no template lists a vendored module any more, it leaves `ROOTS` in
   `scripts/vendor_pulp.py` and the vendored tree is re-made (`docs/third-party.md`).

> **Maintainer note needed:** D36 says each replaced block passes "the same tests as its PULP
> counterpart" but not which tests. Question: is that the generated cocotb suite plus the
> mutation harness plus the formal jobs on the corpus (as step 3 assumes), or a per-block
> directed bench, which the PULP-backed blocks do not have (Phase 3 known gap 11)? And may a
> replacement change behaviour where PULP's was a known limit (D42, D49), or must it first match
> PULP and change in a separate step?

## The plug-in direction (D46)

D46, quoted verbatim from `docs/phase-4-report.md`:

> Plug-ins are the first v2 item.

The Phase 4 report lists it first among the v2 items, ahead of D36's block replacement and D35's
protocol versions. The Phase 3 report lists "plug-ins" among the v2 items without an order.

What exists today that a plug-in could build on, from the code:

- the manifest format, which a v2 plug-in could reuse;
- `Registry`, which takes a list of `Manifest` objects and so does not depend on where they come
  from. `run_checks` takes a `registry` argument for the same reason;
- a template interface that is already the contract between generator and block: `ctx`, `Bus`
  and the names listed above;
- `bag_external`, which needs no change to hdl-bag.

What does not exist: `builtin_registry()` reads only `hdl_bag/templates/builtin/`;
`gen/__init__.py` and `gen/netlist.py` call it directly rather than taking a registry; the Jinja
loader is rooted at the same directory; `resolve_sources` can name only vendored PULP modules and
package-relative paths. There is no discovery, no packaging and no versioning rule beyond
`Literal[1]`.

**The plug-in format is unpublished until v2.** Do not write a template outside the repository
and expect it to load, and do not treat the keys in this guide as stable for third parties. A
template added to hdl-bag by pull request follows the rules in this guide and is reviewed like
any other RTL pull request (AGENTS.md: an independent review pass).

> **Maintainer note needed:** D46 makes plug-ins the first v2 item, but the repository does not
> say what a plug-in is. Question: is a plug-in a directory a user points BAG at (a manifest, an
> `instance.sv.j2` and RTL, as the built-ins are), a Python package that registers templates, or
> something else? And why first: what does it unblock for D35 and D36?

## User-supplied fabrics (`bag_external`)

Until plug-ins exist, a user who needs a fabric BAG does not have supplies a module and keeps
everything else BAG generates. This is how it works, from `gen/external.py` and
`checks/wrapper.py`.

In the workbook, a Fabrics row takes Backing template `bag_external`. BAG then instantiates one
module in the wrapper, in the place a generated fabric would take. Its name is the Fabrics >
External module cell, or `<project>_<fabric>` when that is blank. Its ports, in order:

1. `clk_i` and `rst_ni`: the fabric's clock and conditioned reset;
2. for each attached port, in Port index order (master side, then slave side), the bus signals,
   named by the Wrapper signal pattern with `{port}` = `<fabric>_<port>` (D39), for example
   `noc_cpu0_axi_awaddr`. Directions are the fabric's: on a master's attachment the fabric is
   the slave end. Widths are those of the bus at the fabric, after the port's adapters;
3. the User_Ports of that fabric (D41), named as declared. Each is exposed at the top as
   `<fabric>_<port>`, or connected as an extra input, a constant, a clock, a conditioned reset,
   or left open.

With an External module name (D45), `{port}` is by position instead: `s<k>` for the k-th master
attachment and `m<k>` for the k-th slave attachment (`s0_axi_awaddr`), so fabrics of the same
shape can share one module. `bag check` rejects a module name that is not an identifier, is a
SystemVerilog keyword, clashes with a module BAG generates or starts with `bag_`, or differs from
another only in case (`EXT-MODULE-NAME`), and requires fabrics that share a module to have the
same shape (`EXT-MODULE-SHARED`). `bag generate` also fails when two fabrics that share a module
end up with different ports, for example through different ID widths.

BAG writes `rtl/ext/<module>.sv`, a stub with exactly these ports and outputs tied to zero.
Replace that file in the file list with the real RTL. There are no generated tests and no formal
for such a design (D8), so the user owns the fabric's verification.

## Checklist for a new template

1. Directory `src/hdl_bag/templates/builtin/<name>/` with `manifest.yaml` and `instance.sv.j2`;
   `name` equals the directory.
2. SPDX header on every file; `docs/bom.md` takes the licence from it.
3. RTL in `rtl/inhouse/`, lint-clean under slang, Verilator and Verible, with a negative
   elaboration case per `$error` and `$fatal`.
4. `sources` lists every in-house file and every `bender:` module; a new vendored module goes
   through `scripts/vendor_pulp.py`.
5. The instance template asks `ctx` for every signal of every attached bus and uses `unit()` for
   module names.
6. A fabric or bridge is added to `LISTS["Template"]`, and the golden workbook, IR, snapshots and
   examples are refreshed (AGENTS.md); the schema is versioned if the workbook changed.
7. The route model describes the block, `TESTS` entries have `applies` predicates, at least one
   mutation exercises the block, and a corpus config uses it.
8. `bag check` rules for what the block refuses, each with a rule ID in `checks/core.py` and a
   broken-workbook test.
9. The manual changes in the same pull request when behaviour users see changes.
