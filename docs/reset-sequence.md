# Reset sequence

This page is for integrators whose resets may not all assert together: several
reset sources, a reset controller that sequences them, or power domains. With
one reset source fanned out to every reset port of a BAG design, steps 1 to 3
below hold by construction; steps 4 and 5 are still yours.

`bag generate` writes `doc/reset.md` for each design (with **Generate memory-map
doc**). It lists that design's domains, crossings and reset groups, and the
sequence below with its own reset names.

## Why the order matters

A BAG design has one reset port per clock domain. The top wrapper conditions
each reset with sync deassertion in `bag_rst_sync` (2 stages): an async assertion
takes effect at once, and the deassertion takes 2 edges of the domain's own
clock. A reset with async deassertion is connected as it is.

A clock-domain crossing keeps state on both sides:

- a `cdc` adapter, and the bridges from AXI (`bag_axi2apb`, `bag_axi2axil`,
  `bag_axi2ahb`, `bag_axil2apb`), use PULP `axi_cdc`: gray-coded FIFO pointers,
  the write pointer in one domain and the read pointer in the other;
- the bridges from AHB-Lite and APB (`bag_ahb2apb`, `bag_ahb2axi`, `bag_apb2ahb`,
  `bag_apb2axi`) use a four-phase handshake: the request and its payload on one
  side, the acknowledge on the other.

Both sides must enter reset at the same moment. PULP's `cdc_fifo_gray` says so
in its own header: its two resets "must be asserted SIMULTANEOUSLY", or
spurious transactions can occur in the domain whose reset arrives later. If one
side is reset while the other keeps running, the running side sees the FIFO
pointer drop to zero, or the handshake payload cleared. Within a couple of its
clock edges it presents a transfer that no master sent, with zero address and
data: a request to the slaves in its domain, or a response to its masters.
Nothing needs to be in flight for this; earlier traffic leaves the pointers
where it happens. A spurious read of a register with read side effects (a FIFO
pop, a clear-on-read status) is not undone by the reset that follows.

During Phase 4, removing the reset from one side of an in-house bridge, by hand
on a copy of the RTL, made the bridge replay a stale transfer after reset; the
generated reset test caught it.

## Reset groups

Domains joined by a chain of crossings form a **reset group**, and the domains
of a group reset together. A domain with no crossing is a group of its own and
may reset alone, with the masters and slaves in it. In demo_ss every domain
crosses to D_SYS, so all four domains are one group.

So a warm reset of part of the chip is possible only for whole reset groups.
PULP's CDC FIFOs do not support resetting one side alone.

BAG sees only its own crossings. A user-supplied fabric (`bag_external`) that
User_Ports gives the clock or reset of another domain is a crossing in your RTL:
treat the domains it joins as one group.

## The sequence, per group

1. Assert every reset of the group at the same moment: from one source, or
   with less skew than the shortest clock period in the group. If the resets
   cannot assert together, stop the clocks of every domain in the group first,
   assert the resets in any order, and restart the clocks with all of them still
   asserted. That is the only staggered assertion PULP allows for its CDC FIFOs.
2. Keep each reset asserted until all of them have taken effect. An async
   assertion takes effect at once, without a clock; a sync one takes 2 edges of
   its domain's clock when its deassertion is sync too (through `bag_rst_sync`),
   and is wired as it is otherwise. (`bag check` requires async assertion on both sides of
   every crossing, so only a group of one domain can have a sync assertion.)
   Release none of them before then.
3. Release them, in any order. Each domain leaves reset on its own clock: 2
   edges after its release, with sync deassertion.
4. Start traffic once every domain of the group has left reset.
5. Reset the masters and slaves of the group with it. BAG abandons every
   transfer in flight, so a master that is not reset waits for a response that
   never comes.

```text
               together (step 1)        any order (step 3)
rst_a_n  ‾‾‾‾‾\____________________________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
rst_b_n  ‾‾‾‾‾\_____________________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
                                                     |
                                     every domain out of reset:
                                     traffic may start (step 4)
```

## What BAG checks, and what it tests

- **`bag check`**: every reset on either side of a crossing needs async
  assertion and sync deassertion (`CLK-CDC-RESET`). It cannot see whether the
  resets of a group assert together; that is the integrator's part of the
  contract.
- **Generated tests** (`bag sim`): every reset is asserted at the same moment,
  with traffic from every master, once for each slave, timed while a request to
  that slave is in its handshake. While reset is held, no request or response
  VALID may reach a master or slave port, checked on every clock edge in every
  domain. After release, nothing may reach a slave or return to a master during
  a quiet window of 20 cycles of every clock, and fresh traffic must return the
  right data. The clock-ratio sweep repeats the traffic at 1:3, 3:1, 10:7 and
  7:10.
- **Not tested**:
  - Releasing the resets of a group at different times (step 3). That follows
    from the RTL: both sides of each crossing are empty and idle in reset, and
    every flop on them has an async reset.
  - Asserting with the clocks stopped (step 1).
  - Staggered assertion with the clocks running. This is not supported (see
    above).
