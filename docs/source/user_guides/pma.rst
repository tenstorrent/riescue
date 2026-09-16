Physical Memory Attributes (PMA)
================================

This guide explains how RiescueD models Physical Memory Attributes, how to give your test
memory with specific attributes, and how to use PMA randomization to stress a design's PMA
checking logic. It assumes you know roughly what a PMA is in RISC-V, but nothing about how
RiescueD handles them.

Background: PMAs in thirty seconds
----------------------------------

In a RISC-V system, every physical address range has *attributes*: is it normal cacheable
RAM? Uncached memory? A memory-mapped device? Can you fetch instructions from it? Can you
run atomic (AMO) instructions against it? The hardware checks every access against these
attributes, and an access that violates them raises an access-fault exception
(``LOAD_ACCESS_FAULT``, ``STORE_ACCESS_FAULT``, or ``INSTRUCTION_ACCESS_FAULT``).

The RISC-V privileged spec leaves *how* PMAs are configured up to the platform. RiescueD
targets a platform scheme where PMAs are software-programmable through a bank of CSR pairs.

How RiescueD models PMAs
------------------------

The target implements up to **64 PMA entries**. Each entry is a pair of registers:

- ``pmacfg`` — encodes the region's base address, size, and attributes (memory type,
  cacheability, permissions, AMO support, coherency routing)
- ``pmamask`` — an optional address-compare mask over physical address bits 51:12. A zero
  mask means "match the whole naturally-aligned region"; a nonzero mask makes the entry
  match a scattered pattern of pages instead of one contiguous range

The **first 16 entries are direct CSRs** (``pmacfg`` at ``0x7E0 + i``, ``pmamask`` at
``0x7F0 + i``). Entries 16 and above have no direct CSR addresses and are reached
*indirectly*: write the entry number to ``miselect``, then access the entry's ``pmacfg``
through ``mireg`` and its ``pmamask`` through ``mireg2``.

Two rules matter for everything below:

1. **First match wins.** When an access matches several entries, the lowest-numbered entry
   decides the attributes. Entry 0 has the highest priority.
2. **The top two entries are catch-alls.** RiescueD always keeps the last two entries
   (``num_pmas - 2`` and ``num_pmas - 1``) programmed as broad default regions — an
   IO/noncacheable region covering low addresses and a cacheable RWX region
   covering all of memory — so that any address not covered by a more specific entry still
   has sane attributes and the test can always run.

The number of implemented entries is configured with ``num_pmas`` (cpu config
``mmap.pma.num_pmas`` or the ``--num_pmas`` CLI flag, default 64) and **must match the ISS
configuration** — the generated boot code physically writes entry ``num_pmas - 1``, so a
mismatch means writing CSRs the simulator doesn't implement.

Enabling PMAs in a test
-----------------------

PMA support is off by default. Run with ``--needs_pma`` and the generated boot code
(the *loader*) will program one PMA entry per defined region before your test starts.
Where do regions come from? Three places, usable together:

1. **The cpu config** — fixed regions and hints under ``mmap.pma`` in the JSON
   configuration file. Use this for regions the platform always has. See the
   :doc:`/reference/config/configuration_schema` for the schema.

2. **The** ``;#pma_hint`` **directive** — asks the framework to create regions with the
   attributes you want at addresses it picks:

   .. code-block:: text

       ;#pma_hint(name=my_hint,
           memory_types=[memory],
           cacheability=[cacheable, noncacheable],
           rwx_combos=[rwx],
           adjacent=true
       )

   This generates one region per attribute combination (here: two adjacent RWX memory
   regions, one cacheable and one noncacheable).

3. **The** ``;#random_addr`` **directive with** ``in_pma=1`` — the most common way in
   practice. It gives you a random physical address *and* guarantees the address sits
   inside a PMA region with the attributes you asked for:

   .. code-block:: asm

       ;#random_addr(name=nc_buf, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=memory, pma_cacheability=noncacheable, pma_read=1, pma_write=1, pma_execute=0)

   Your test code can then load ``nc_buf`` into a register and know every access through it
   hits noncacheable memory.

**Region reuse.** PMA entries are a scarce resource. When an ``in_pma=1`` address requests
attributes that exactly match an existing region — one made by a ``;#pma_hint``, or by an
earlier ``in_pma`` address — the framework places the address inside that existing region
instead of creating a new one. Ten addresses with identical attributes cost one PMA entry,
not ten.

Full syntax for both directives is in the
:doc:`/reference/riescue_test_file/directives_reference`, and a complete reference test
ships at ``riescue/dtest_framework/tests/test_pma_hint.s``.

PMA randomization
-----------------

Everything above is *directed*: you say what you want and get exactly that. The
``--enable_pma_randomization`` flag adds a *random* layer on top, designed to stress the
design's PMA matching logic rather than your test's logic:

.. code-block:: bash

    riescued -t my_test.s --needs_pma \
        --enable_pma_randomization \
        --pma_random_regions 8 \
        --pma_random_mask_pct 25

Three things happen:

**1. Decoy regions.** The framework carves ``--pma_random_regions`` (default 8) extra PMA
regions — called *decoys* — at random naturally-aligned power-of-two locations in the
physical address space, sized between 4KB and 1GB. Each decoy gets random but *legal*
attributes (memory/io/ch0/ch1 types, permission combinations, AMO support, and so on),
including faulting flavors like no-access memory or read-only IO. Attribute draws are biased
toward real workloads: about 78% cacheable memory, 10% noncacheable, 10% IO, and 2% channel
types. Decoys are placed only in
*holes*: they never overlap your test's memory, the reset vector, IO devices such as the
HTIF or interrupt files, or page tables. Test address generation likewise avoids landing
inside decoys. The result is a PMA CSR bank full of live, randomly-shaped entries that the
hardware must correctly match (or correctly *not* match) on every single access your test
makes — while the test still passes.

**2. Random masks on decoys.** Each decoy has a ``--pma_random_mask_pct`` (default 25)
percent chance of receiving a nonzero ``pmamask``, turning it from one contiguous region
into a scattered match pattern. Mask shapes are drawn from several weighted strategies
(contiguous runs, single bits, dense and sparse patterns) and are constrained so the
scattered match windows never cover memory the test actually uses.

**3. Random masks on your regions.** Named regions created by your test's hints and
``in_pma`` addresses (internally called *carve-outs*) can also receive random masks with
``--pma_carveout_mask_pct`` (default 0 — off). A masked carve-out still matches its own
base window, so addresses placed inside it keep their attributes, while the extra scattered
match windows exercise masked comparison in the design. To *force* a mask onto one specific
region, use ``pma_masked=1`` on the ``in_pma`` address.

All randomness is drawn from the test's seed: the same ``--seed`` reproduces the same
decoys, attributes, and masks.

.. _pma-fixed-windows:

Region tags and fixed windows
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Two independent keys on a cpuconfig memory-map region control how randomization treats it and how
a test reaches it:

.. code-block:: json

   "dram": {
       "dram":          {"address": "0x8000_0000",   "size": "0xf_ffff_8000_0000"},
       "poison_window": {"address": "0x1_0000_0000", "size": "0x1_0000",
                         "tags": ["derr"], "pma_randomization": false},
       "scrub_window":  {"address": "0x1_1000_0000", "size": "0x1_0000",
                         "tags": ["nderr"], "pma_randomization": false},
       "tee_window":    {"address": "0x1_2000_0000", "size": "0x1_0000",
                         "tags": ["stee"], "pma_randomization": false},
       "spare_window":  {"address": "0x1_3000_0000", "size": "0x1_0000",
                         "tags": ["derr", "spare"], "pma_randomization": false},
       "low_bank":      {"address": "0x2_0000_0000", "size": "0x1000_0000", "tags": ["bank0"]}
   }

``tags`` is a list of free-form string labels. RiescueD attaches no meaning to a tag beyond making
the region selectable by it — see *Reaching a region from a test* below. Tags are also accepted on
``io`` and ``custom`` regions.

``pma_randomization: false`` makes the region a **fixed window**: something nothing random touches.

**What a fixed window buys you:**

- a named PMA region with default cacheable-RWX memory attributes, programmed in the high-priority
  carve-out tier so randomized decoy regions never shadow it
- skipped by decoy placement and by scattered random ``pmamask`` windows
- exempt from carve-out mask stress (``--pma_carveout_mask_pct``)
- kept out of the general DRAM pool, so address generation never lands test content there by chance

**The default.** ``pma_randomization`` defaults to ``true``, so a region is a fixed window only when
the config asks for one. No tag implies it: ``{"tags": ["derr"]}`` on its own leaves the region in
the general pool. RiescueD models no derr/nderr/stee behavior of its own; what such a label *means*
(poisoned data, scrub-on-read, a TEE window) is a contract between the DUT and the testbench. Note
that the ``secure`` *tag* does not make a region secure — the ``secure`` key, or a ``secure*`` region
name, does that.

Because the two keys are independent, ``low_bank`` above is selectable by its ``bank0`` tag while
remaining ordinary allocatable DRAM.

**Reaching a fixed window from a test.** Each one is published as ``pma_<region name>_base`` /
``_size`` / ``_end`` equates. Because nothing else can be placed there, the addresses are stable
across seeds:

.. code-block:: asm

   li t0, pma_poison_window_base       # 0x1_0000_0000
   li t1, pma_poison_window_end

**Reaching a region from a test.** To allocate an address *inside* a region on purpose, name it in
``custom_region=`` — the same mechanism used for ``custom`` memory-map regions. A ``custom_region``
spec may be a region name or a tag:

.. code-block:: asm

   ;#random_addr(name=phys_in_poison, type=physical, custom_region=poison_window, size=0x1000)
   ;#random_addr(name=phys_in_spare,  type=physical, custom_region=spare,         size=0x1000)
   ;#random_addr(name=phys_in_bank0,  type=physical, custom_region=bank0,         size=0x1000)

An exact region name always wins over a tag, so naming a region reaches that one region. When a tag
matches several regions, one is picked per address — so a handful of addresses naming ``derr`` spread
across every ``derr``-tagged window rather than piling into one. An unknown spec is an error listing
every accepted name and tag.

Without an explicit request nothing lands in a fixed window, which is the point: a test touches
these windows only when it means to.

A complete worked example lives in ``riescue/dtest_framework/tests/pma_carveout.s``, with its
memory map in ``riescue/dtest_framework/tests/cpu_config_pma_carveout.json``:

.. code-block:: bash

   riescued --testfile riescue/dtest_framework/tests/pma_carveout.s \
            --cpuconfig riescue/dtest_framework/tests/cpu_config_pma_carveout.json \
            --enable_pma_randomization --run_iss

AMO type (``pmacfg`` bits 6:5)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Bits 6:5 are one 2-bit ``amo_type`` field:

.. list-table::
   :header-rows: 1
   :widths: 12 30 58

   * - ``[6:5]``
     - ``amo_type``
     - Meaning
   * - ``00``
     - ``none``
     - AMONone -- no atomics
   * - ``01``
     - ``swap``
     - AMOSwap
   * - ``10``
     - ``logical``
     - AMOLogical
   * - ``11``
     - ``arithmetic``
     - AMOArithmetic

**Cacheable main memory must use** ``arithmetic`` (``0b11``) -- RiescueD raises a ``ValueError`` on
any other value for that shape. Every other region (noncacheable memory, ``io``, ``ch0``, ``ch1``)
may carry any of ``0b00``--``0b11``, but only on a target that opts in with
:ref:`allow_amos_in_pma_ncio <allow_amos_in_pma_ncio>`; off it -- the default -- every one of those
regions is programmed ``0b00`` and the randomizer never rolls anything else there.

.. note::

   Writing a nonzero ``[6:5]`` on a noncacheable or io region is only a *legal pmacfg value* when the
   whisper config sets ``allow_amo_in_non_cacheable_regions`` / ``allow_amo_in_io_regions`` (both are
   enabled in every whisper config shipped here except the Ascalon one). Whether it makes atomics
   *work* there is the ``legacy_pma`` question below: whisper's ``unpackPmacfg`` grants Amo/Rsrv
   outside cacheable main memory only in Babylon mode (``babylon_pma`` in the whisper config).

.. _allow_amos_in_pma_ncio:

``allow_amos_in_pma_ncio`` -- atomics outside cacheable memory
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``mmap.pma.allow_amos_in_pma_ncio`` (bool, default ``false``) decides whether NC/IO space may hold
a non-AMONone ``pmacfg[6:5]`` at all. It is the RiescueD-side mirror of whisper's
``allow_amo_in_non_cacheable_regions`` / ``allow_amo_in_io_regions``, and unlike ``legacy_pma`` it
is **cpuconfig-only** -- there is no CLI override.

Off -- the default -- every region that is not cacheable main memory is programmed
``pmacfg[6:5] = 0b00``. That is the whole field, so it takes bit 6 (``Rsrv``) with it: neither an
AMO nor an LR/SC works there. Cacheable main memory is untouched, still pinned to ``0b11``.

A request that the clamp overrides is **corrected, not rejected** -- an explicit
``pma_amo_type=`` on ``;#random_addr``, ``amo_types=`` on ``;#pma_hint``, an ``amo_type`` in a
cpu-config PMA region, or a ``PmaSpec(amo_type=...)`` all still parse, and RiescueD logs one
warning per distinct request saying what it programmed instead. That is deliberate: ``PmaSpec``
defaults ``amo_type`` to ``arithmetic``, so io pages inherit a request nobody typed.

It is an independent axis from :ref:`legacy_pma <legacy_pma>`; either one alone is enough to keep
atomics out of NC/IO:

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - ``allow_amos_in_pma_ncio: false`` (default)
     - ``allow_amos_in_pma_ncio: true``
   * - ``legacy_pma: false`` (default)
     - NC/IO programmed AMONone, so nothing to reach
     - NC/IO follow ``pmacfg[6:5]``: atomics work where the region grants them
   * - ``legacy_pma: true``
     - NC/IO programmed AMONone *and* unreachable
     - NC/IO keep their ``[6:5]``, but atomics stay confined to cacheable main memory

Tracking stays orthogonal: the AMO-vs-PMA gate reads the attributes actually programmed, so with
the knob off it classifies every NC/IO page atomic-hostile on its own and steers generated atomics
elsewhere. Tests are published the resolved value as the ``PMA_ALLOW_AMOS_IN_NCIO`` equate --
``riescue/dtest_framework/tests/pma_amo_ncio.s`` is the worked example, asserting the passing
atomics on one side and cause-7/cause-5 access faults on the other.

.. _legacy_pma:

``legacy_pma`` -- pre-Babylon targets
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``mmap.pma.legacy_pma`` (bool, default ``false``; ``--legacy_pma`` / ``--no_legacy_pma`` override it)
says the target predates Babylon PMA. It decides two things at once:

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - 
     - ``legacy_pma: true``
     - ``legacy_pma: false`` (default)
   * - AMO / LR-SC legality
     - cacheable main memory only. ``io``/``ch0``/``ch1`` and noncacheable memory can **never** host
       an atomic, whatever ``[6:5]`` says
     - follows ``pmacfg[6:5]``: bit 0 grants AMO, bit 1 grants Rsrv, in every region -- but NC/IO
       only carry a nonzero ``[6:5]`` under :ref:`allow_amos_in_pma_ncio <allow_amos_in_pma_ncio>`
   * - pmacfg bit 8
     - live: carries routing/coherency, defaulting to ``coherent``
     - reserved read-only-zero; never written, and any routing request is a hard error

Pair it with the ISS: ``legacy_pma: false`` needs ``babylon_pma: true`` in the whisper config (every
config shipped in ``riescue/dtest_framework/lib/`` sets it), and its bit-8 reading needs a whisper
build that treats bit 8 as reserved. ``legacy_pma: true`` matches a non-Babylon whisper -- the
Ascalon config, for one.

Bit 8 -- coherency routing
^^^^^^^^^^^^^^^^^^^^^^^^^^^

Bit 8 is the routing/coherency bit: ``1`` coherent, ``0`` noncoherent. It is live **only on a
``legacy_pma`` target**, where regions that do not name a routing default to ``coherent`` and
whisper's ``isLegalPmacfg`` is satisfied: it requires bit 8 on cacheable main memory and rejects it
on ``io``, which is exactly what RiescueD then emits (``pma_routing_to=``, ``routing=``, cpu-config
``routing``, ``PmaSpec.routing_to``).

Off a ``legacy_pma`` target -- the default -- bit 8 is **RESERVED** read-only-zero:

- it is never written on any path: direct ``pmacfg`` writes, PMA API calls, decoys and the DRAM
  catch-all all emit ``0`` there (``0xE0000000000000E7`` rather than ``0xE0000000000001E7``);
- *any* routing request is a hard error, not a silent drop: ``pma_routing_to=`` on
  ``;#random_addr``, ``routing=`` on ``;#pma_hint``, ``"routing"`` in a cpu-config PMA region, or
  ``PmaSpec(routing_to=...)`` in Voyager2 all raise ``ValueError``.

.. warning::

   The whisper build pinned in this repo has *not* caught up on the reserved reading: its
   ``isLegalPmacfg`` still **requires** bit 8 on cacheable main memory, and it drops illegal
   ``pmacfg`` writes silently (``legalizePmacfg`` keeps the previous value, and
   ``processPmacfgChange`` returns without redefining the region). So off a ``legacy_pma`` target the
   cacheable main-memory entries RiescueD programs are not accepted by the pinned ISS; that needs the
   roz whisper branch. Set ``legacy_pma: true`` for a test that must see its cacheable regions land.
   See ``PMACFG_ROUTING_BIT`` in ``riescue/dtest_framework/lib/pma.py``.

``legacy_pbmt`` -- PBMT versus atomicity
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``mmap.pma.legacy_pbmt`` (bool, default ``true``; ``--legacy_pbmt`` / ``--no_legacy_pbmt`` override
it) says what an Svpbmt leaf does to atomicity. With ``legacy_pbmt: true`` -- the default, and what
an absent key resolves to -- a page whose PTE carries ``pbmt=1`` (NC) or ``pbmt=2`` (IO) can host
**neither** an AMO nor an LR/SC, whatever the underlying PMA grants. That matches whisper's
``overridePmaWithPbmt``, which strips ``Rsrv`` unless ``babylon_pma`` and strips the Amo bits unless
the ``allow_amo_in_*`` knobs are set. Set ``legacy_pbmt: false`` to let the PMA alone decide.

.. note::

   ``legacy_pbmt: true`` -- now the default -- and RiescueD-wide PBMT randomization
   (``--pbmt_ncio_randomization``, or an ``svpbmt`` ``randomize`` in the cpuconfig) do not mix: the
   randomizer stamps NC/IO on *every* mapped leaf, which leaves no page able to host an atomic.
   Voyager2's atomic-safe pages ask for ``pbmt=0``, but the RiescueD-wide roll overrides even an
   explicit request. Pass ``--no_legacy_pbmt`` to combine the two.

Reserving entries for the test to program
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A test that programs pmacfg entries itself needs some left free. ``;#test.user_programmable_pmacfg``
states that requirement in the test file, where it belongs — a given ``.s`` needs a fixed number of
entries and cannot adapt to a smaller one:

.. code-block:: asm

   ;#test.user_programmable_pmacfg 2

Entries ``[0..N)`` are then reserved: RiescueD programs no region into them. Under
``--enable_pma_randomization`` it zeroes them once at boot (so a stale boot ``pmacfg14``/``15``
catchall cannot outrank every real entry) and never touches them again; with randomization off it
does not emit them at all.

The same number can come from ``mmap.pma.user_programmable_pmacfg`` in the cpuconfig or from
``--user_programmable_pmacfg``. The test header is a *floor*, not an override: when the sources
disagree the larger value wins, since a test can tolerate extra free entries but not fewer.

Moving bootrom PMA entries to the back
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Some bootroms program the low ``pmacfg`` entries themselves. Setting ``shift_pma_on_load: N``
in the cpu config's ``mmap.pma`` section (or passing ``--shift_pma_on_load N``, which overrides
it) makes the loader copy those entries —
``pmacfg0``..``pmacfgN-1`` and their ``pmamask`` companions — to the ``N`` slots directly
below the two catch-alls, preserving order: entry ``i`` lands at ``num_pmas - 2 - N + i``
(with the default 64 entries, ``N = 2`` moves ``pmacfg0`` to ``pmacfg60`` and ``pmacfg1`` to
``pmacfg61``). The copy runs before any other PMA write, so the bootrom values are read
before the loader reprograms or invalidates the vacated low entries. Moved entries keep
priority over the catch-alls but lose to every test-programmed region, and they consume ``N``
entries of capacity like any other group. Implies ``--needs_pma``.

Flag summary
^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 34 12 54

   * - Flag
     - Default
     - Effect
   * - ``--needs_pma``
     - off
     - Program PMA entries for all defined regions at boot
   * - ``--num_pmas <N>``
     - 64
     - Implemented PMA entries (2-64); must match the ISS config
   * - ``--enable_pma_randomization``
     - off
     - Generate decoy regions and random masks (implies ``--needs_pma``)
   * - ``--pma_random_regions <N>``
     - 8
     - Number of decoy regions
   * - ``--pma_random_mask_pct <P>``
     - 25
     - Percent of decoys that get a nonzero ``pmamask``
   * - ``--pma_carveout_mask_pct <P>``
     - 0
     - Percent of named test regions that get a random ``pmamask``
   * - ``--pma_indirect_access_pct <P>``
     - 50 when randomizing, else 0
     - Percent of entries 0-15 programmed via ``miselect``/``mireg``/``mireg2`` instead of direct CSRs
   * - ``--user_programmable_pmacfg <N>``
     - 0
     - Reserve entries ``[0..N)`` for the test to program at runtime; ``;#test.user_programmable_pmacfg`` raises this if it asks for more
   * - ``--shift_pma_on_load <N>``
     - 0
     - Relocate the first N bootrom entries to just below the catch-alls (implies ``--needs_pma``)
   * - ``--legacy_pma`` / ``--no_legacy_pma``
     - ``mmap.pma.legacy_pma``, itself ``false``
     - Pre-Babylon target: atomics only on cacheable main memory, and ``pmacfg`` bit 8 carries routing. Off it, ``[6:5]`` alone decides atomicity and bit 8 is reserved read-only-zero
   * - ``--legacy_pbmt`` / ``--no_legacy_pbmt``
     - ``mmap.pma.legacy_pbmt``, itself ``true``
     - A ``pbmt=1``/``pbmt=2`` leaf revokes AMO and LR/SC on that page whatever the PMA grants. Off it, PBMT does not constrain atomics

What the loader emits
---------------------

With ``--needs_pma``, the generated assembly contains a ``loader__setup_pma:`` block that
runs in machine mode at boot. Each entry is a commented CSR write sequence — direct
``csrw 0x7e0+i`` / ``csrw 0x7f0+i`` pairs for entries below 16, and
``miselect``/``mireg``/``mireg2`` sequences for entries 16 and above. With
``--pma_indirect_access_pct``, a random share of the low entries also uses the indirect
sequence, exercising both access paths. Because writing an
entry's ``pmacfg`` resets its mask, the ``pmamask`` write always follows the ``pmacfg``
write.

Entries are laid out from index 0 upward:

1. **User-reserved entries** (``user_programmable_pmacfg`` of them, default 0) — carry no
   region; the test programs them at runtime (see below). With randomization on they are
   zeroed once at boot, then left alone
2. **Your named regions** (carve-outs from hints and ``in_pma`` addresses)
3. **Decoy regions** (randomization only)
4. **Invalidated entries** — zeroed so no stale boot values linger (randomization only)
5. **Moved bootrom entries** (``shift_pma_on_load`` only) — the first N bootrom
   ``pmacfg``/``pmamask`` pairs, copied to the slots directly under the catch-alls
6. **The two catch-alls** at the very top

With randomization on, the loader writes the top catch-all entries *first* — the boot-time
catch-alls at entries 14/15 still cover memory at that moment, so there is never a window
where an access has no matching entry. Only then are the reserved entries zeroed, which
matters when more than 14 are reserved: entries 14/15 fall inside the reserved block and
their boot catch-all values would otherwise outrank every region below. Named regions come
before decoys, and decoys before broad memory-map regions, so the specific entries win
first-match-wins priority.

.. note::

   With randomization **off**, named carve-out regions are emitted after memory-map regions
   and can be shadowed by them (a known quirk of the legacy emission order). If your test
   depends on carve-out regions winning priority, run with ``--enable_pma_randomization``.

Runtime PMA programming by the test
-----------------------------------

Setting ``user_programmable_pmacfg: N`` in the cpu config's ``mmap.pma`` section — or passing
``--user_programmable_pmacfg N``, which overrides it — reserves entries ``0`` to ``N-1`` for the
*test itself*. The loader puts no region there — with
randomization on it zeroes them at boot and never writes them again, with randomization off
it leaves them alone entirely. Since entry 0 outranks everything, a machine-mode test can
write its own ``pmacfg`` value there at runtime to override the attributes of any page — for
example, flipping a cacheable RWX page to read-only IO, verifying that stores now fault, then
writing 0 to the entry to restore normal behavior. The reserved count must leave room for the
two catch-alls (``N <= max_regions - 2``).

Capacity and gotchas
--------------------

- Everything must fit: ``user_programmable_pmacfg`` + named regions + memory-map regions +
  2 catch-alls + any ``shift_pma_on_load`` relocation block must be at most ``num_pmas``, or
  generation fails with a clear error. Decoys
  are the flexible part — they are truncated (with a warning) to whatever space remains.
- ``--num_pmas`` and the ISS configuration must agree.
- ``pma_masked=1`` requires ``in_pma=1`` on the same ``;#random_addr``.
- Reuse regions where you can: request the same attribute combination rather than a new one
  per address.

Checking the output
-------------------

Useful signs of life when debugging a PMA test:

- The generation log prints one line per region
  (``Generated PMA region: <name> at 0x..., size 0x..., type=...``), the decoy summary
  (``Generated N randomized decoy PMA regions``), and each masked region
  (``Randomized PMA region: ... mask=0x...`` / ``Masked carve-out region <name>: ...``).
- The generated ``.S`` file's ``loader__setup_pma:`` block lists every entry with a comment
  naming the region and its attributes — this is the ground truth for what got programmed
  where.
