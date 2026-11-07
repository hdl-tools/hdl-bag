# Reset sequence

This page is for integrators whose resets may not all assert together: several
reset sources, a reset controller that sequences them, power domains, or a warm
reset of part of the chip. With one reset source fanned out to every reset port
of a BAG design, the order below holds by construction.

`bag generate` writes `doc/reset.md` for each design (with **Generate memory-map
doc**). It lists that design's domains, crossings and reset groups, and the
order below with its own reset names.

## Why the order matters

A BAG design has one reset port per clock domain. Each wrapper conditions it in
`bag_rst_sync` (2 stages): an async assertion takes effect at once, and a sync
deassertion takes 2 edges of the domain's own clock.

A clock-domain crossing keeps state on both sides:

- a `cdc` adapter (PULP `axi_cdc`) keeps the write and read pointers of its
  FIFOs, gray-coded, one in each domain;
- a bridge between fabrics in different domains (the D10 CDC inside it) keeps
  the request on one side and the acknowledge on the other of a four-phase
  handshake.

If one side is reset and the other is not, the two disagree about what is in
flight. A transfer can be lost or repeated, or one can arrive that no master
sent. BAG's reset stress test injected exactly this fault, by removing the reset
from one side of an in-house bridge, and the bridge replayed a stale transfer
after reset.

## Reset groups

Domains joined by a chain of crossings form a **reset group**: its domains must
reset together. A domain with no crossing is a group of its own and may reset
alone, with the masters and slaves in it. In demo_ss every domain crosses to
D_SYS, so all four domains are one group.

## The sequence, per group

1. Assert every reset of the group, in any order.
2. Keep each one asserted until all of them have taken effect: an async
   assertion at once, a sync one after 2 edges of its domain's clock. Release
   none of them before then.
3. Release them, in any order. Each domain leaves reset 2 edges of its own clock
   after its release.
4. Start traffic once every domain of the group has left reset.
5. Reset the masters and slaves of the group with it. BAG abandons every
   transfer in flight, so a master that is not reset waits for a response that
   never comes.

```text
rst_a_n  ‾‾‾\______________________________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
rst_b_n  ‾‾‾‾‾‾‾‾‾\______________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
                  |              |                |
                  all asserted   any release      every domain out of
                  (step 2)       order (step 3)   reset: traffic (step 4)
```

## What BAG checks, and what it tests

- **`bag check`**: every reset on either side of a crossing needs async assertion
  and sync deassertion (`CLK-CDC-RESET`). It cannot see whether the resets of a
  group assert together; that is the integrator's part of the contract.
- **Generated tests** (`bag sim`): every reset is asserted at once, with traffic
  in flight on every path. While reset is held, no request or response may reach
  any model, checked on every clock edge in every domain. After release, nothing
  left over may appear, and fresh traffic must return the right data. The
  clock-ratio sweep repeats the traffic at 1:3, 3:1, 10:7 and 7:10.
- **Not tested**: asserting or releasing the resets of a group at different
  times (steps 1 and 3 above). That follows from the RTL: async-reset flops on
  both sides of each crossing, and synchronizers that see an idle handshake
  until both sides are out of reset. Resetting one domain of a group alone is
  not supported.
