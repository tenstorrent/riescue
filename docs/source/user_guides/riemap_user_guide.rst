RieMap User Guide
=================

This guide covers the :class:`~riescue.riemap.builder.PageTableBuilder` Python API:
single- and two-stage translations, aliasing, page attributes, and address
constraints. For the underlying concepts see :doc:`/reference/riemap/model`; for a
first example see the :doc:`tutorial </tutorials/riemap/index>`.

Typical build sequence:

1. Create a :class:`~riescue.riemap.memory.Memory`.
2. Create a :class:`~riescue.riemap.builder.PageTableBuilder`.
3. Add spaces, pages, and mappings (in any order), keeping the returned objects.
   Later lookups use those same instances.
4. Call :meth:`~riescue.riemap.builder.PageTableBuilder.build` and read the returned
   :class:`~riescue.riemap.result.AllocationResult` back using those same objects.

The examples below share this preamble:

.. code-block:: python

   import riescue.lib.enums as RV
   from riescue.lib.rand import RandNum
   from riescue.riemap.memory import Memory
   from riescue.riemap.builder import PageTableBuilder
   from riescue.riemap.request import LEAF, AddrSpec, Mapping, Page, PTGPage, PTNode, SameAs, Space, Stage

   memory = Memory.from_dict(
       {"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}}
   )

Single-stage mapping
--------------------

One VA space translating to physical memory. ``builder.phys``, the physical leaf
domain, is created by the builder, so the destination page just names it.

.. code-block:: python

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
   va_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
   code = builder.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
   code_pa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80010000)))
   builder.add_mapping(Mapping(src=code, dst=code_pa, pt_nodes={
       LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 0, "x": 1, "a": 1, "d": 1})}))
   result = builder.build()

   steps, pa = result.space(va_space).walk(0x1000)   # -> 0x80010000
   va, pa = result.address_of(code)                  # (0x1000, 0x80010000)

- ``Space`` declares an address domain. Because a mapping originates from it, it
  has a page table and a root register. ``Stage`` defaults to ``Stage.SINGLE``.
- The ``code`` page is drawn in ``va_space``'s pool; ``code_pa`` in the physical
  pool. ``AddrSpec(exact=...)`` pins each address.
- The ``Mapping`` is the leaf PTE tying VA ``0x1000`` to PA ``0x80010000``. PTE
  bits are declared per page-table node in ``pt_nodes``. ``LEAF`` resolves to the
  leaf level for the source pagesize, so callers need not name that level.
  See :ref:`page-table nodes <riemap-pt-nodes>` for node keys, frames, and sharing.
- Pass the same ``Space``/``Page`` instances to ``result.space(...)`` and
  ``result.address_of(...)``.

Free draws
~~~~~~~~~~

Omit ``addr`` (or give only masks) to let the builder pick an address from the
space's pool. A page's ``pagesize`` sets its alignment automatically; add
``AddrSpec(and_mask=..., or_mask=...)`` to constrain the draw further.

.. code-block:: python

   big = builder.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S2MB))
   big_pa = builder.add_page(Page(space=builder.phys, pagesize=RV.RiscvPageSizes.S2MB))

Two-stage mapping (VA to GPA to PA)
-----------------------------------

A two-stage translation uses two table-bearing spaces: a ``Stage.VS`` space and a
``Stage.G`` space. Because the ``G`` space is itself a mapping source (it originates
GPA-to-PA leaves) as well as a mapping target, it is rooted with an ``hgatp``.

General (non-identity) G-stage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Declare both spaces, a page in each, a physical page, and two explicit mappings. The
GPA and HPA are independent addresses, so the G-stage remaps the GPA to a different
PA.

.. code-block:: python

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
   vs_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.VS))
   g_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))

   va = builder.add_page(Page(space=vs_space, addr=AddrSpec(exact=0x3000)))
   gpa = builder.add_page(Page(space=g_space, addr=AddrSpec(exact=0x5000)))
   hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80040000)))

   leaf = {"v": 1, "r": 1, "w": 1, "a": 1, "d": 1}
   # Every VS table frame below the root needs an explicit G-stage identity
   # (PTGPage or a pinned Stage.G Page with its own GPA-to-HPA mapping).
   builder.add_mapping(Mapping(src=va, dst=gpa, pt_nodes={
       LEAF: PTNode(attrs=leaf),
       0: PTNode(page=PTGPage(identity=True)),
       1: PTNode(page=PTGPage(identity=True)),
   }))
   builder.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=leaf)}))
   result = builder.build()

   _steps, gpa_addr = result.space(vs_space).walk(0x3000)   # VA 0x3000 -> GPA 0x5000
   _steps, hpa_addr = result.space(g_space).walk(gpa_addr)  # GPA 0x5000 -> HPA 0x80040000
   assert result.space(g_space).is_gstage

Every VS-stage table frame below the root needs an explicit G-stage identity
(a ``PTGPage`` on that level, or a pinned ``Stage.G`` ``Page`` with its own
GPA-to-HPA mapping). :meth:`~riescue.riemap.builder.PageTableBuilder.add_two_stage_mapping`
attaches those ``PTGPage`` s; a hand-written ``add_mapping`` must declare
them or ``build()`` raises.

Identity G-stage
~~~~~~~~~~~~~~~~

When GPA should equal HPA, pin the GPA page ``SameAs`` the HPA page.
:meth:`~riescue.riemap.builder.PageTableBuilder.add_two_stage_mapping` adds the
physical HPA page, creates the GPA page pinned
``SameAs`` it, adds both the VA-to-GPA and GPA-to-HPA mappings, and derives the
G-stage leaf's PTE attributes.

.. code-block:: python

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
   va_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.VS))
   gpa_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))

   p = builder.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
   p_hpa = Page(space=builder.phys, addr=AddrSpec(exact=0x80030000))

   gpa_page = builder.add_two_stage_mapping(
       va_page=p,
       hpa_page=p_hpa,
       gpa_space=gpa_space,
       attrs={"v": 1, "r": 1, "w": 1, "x": 1, "a": 1, "d": 1},
       vs_pagesize=RV.RiscvPageSizes.S4KB,
       gstage_mode=RV.RiscvPagingModes.SV39,
   )
   result = builder.build()

   _steps, gpa = result.space(va_space).walk(0x2000)     # VA -> GPA
   _steps, hpa = result.space(gpa_space).walk(gpa)       # GPA -> HPA (== GPA)

``add_two_stage_mapping`` registers ``p_hpa``; do not also call ``add_page`` on
it. It returns the new GPA page (``gpa_page`` above). Writing the same shape by
hand means the HPA page, a GPA page with ``AddrSpec(relation=SameAs(p_hpa))``,
the two leaf mappings, **and** ``PTGPage`` identities on every VS table frame
below the root. Use the helper unless you need fields it does not set.

Aliasing
--------

Aliasing maps two virtual addresses to the same physical page. Use
:class:`~riescue.riemap.request.SameAs` on a page's ``AddrSpec`` to pin one address
equal to another's; the shared address is reserved only once. Here two VAs share one
physical page with different permissions:

.. code-block:: python

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
   va_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))

   va_ro = builder.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
   va_rw = builder.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
   shared = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80050000)))
   shared_alias = builder.add_page(Page(space=builder.phys, addr=AddrSpec(relation=SameAs(shared))))

   builder.add_mapping(Mapping(src=va_ro, dst=shared,       pt_nodes={LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 0, "a": 1, "d": 1})}))
   builder.add_mapping(Mapping(src=va_rw, dst=shared_alias, pt_nodes={LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1})}))
   result = builder.build()

   _steps, pa_ro = result.space(va_space).walk(0x1000)
   _steps, pa_rw = result.space(va_space).walk(0x2000)
   assert pa_ro == pa_rw == 0x80050000    # same physical page, different permissions

Two related relations live on ``AddrSpec.relation``:

- :class:`~riescue.riemap.request.OffsetFrom` -- this address equals another's plus a
  fixed ``delta`` (a linked child, or a buddy page). The derived span is reserved.
- :class:`~riescue.riemap.request.DerivedFrom` -- this address is
  ``((source & and_mask) ^ not_mask) | or_mask`` of another's address, with an
  optional ``random_mask`` filling the unselected bits with a fresh draw.

Both take the target ``Page`` object directly, the same way ``SameAs`` does above --
for example ``AddrSpec(relation=OffsetFrom(parent, delta=0x1000))``.

Page attributes
---------------

A mapping declares its PTE bits per page-table node:
``pt_nodes[LEAF].attrs`` are the leaf PTE's attributes and each non-leaf node carries
its own. Pass base attribute names; the builder expands them to per-level PTE fields,
including levels not listed in ``pt_nodes``.

Standard PTE bits (0 or 1 unless noted):

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Attribute
     - Meaning
   * - ``v``
     - Valid.
   * - ``r`` / ``w`` / ``x``
     - Read / Write / Execute.
   * - ``u``
     - User-mode accessible.
   * - ``g``
     - Global.
   * - ``a`` / ``d``
     - Accessed / Dirty (default to 1 if unset).
   * - ``pbmt``
     - Page-based memory type (Svpbmt).
   * - ``n``
     - NAPOT (Svnapot); auto-set for 64 KiB pages.
   * - ``rsw`` / ``reserved``
     - Reserved-for-software / reserved PTE bits.

Page size is set on the ``Page``, not with a PTE attribute:

.. code-block:: python

   huge = builder.add_page(Page(space=va_space, pagesize=RV.RiscvPageSizes.S2MB))

Valid sizes are ``S4KB``, ``S64KB``, ``S4MB``, ``S2MB``, ``S1GB``, ``S512GB``,
``S256TB`` (``RV.RiscvPageSizes``), subject to the paging mode -- see the
:ref:`page-size table <riemap-page-sizes>`.

To target one page-table level you name that level: a ``pt_nodes`` key is an
architectural level int in the source's own stage, so level 1's PTE bits are
``pt_nodes={1: PTNode(attrs={...})}``.

Two surfaces take an attribute dict with the level spelled into the attribute
name -- :meth:`~riescue.riemap.builder.PageTableBuilder.add_two_stage_mapping`'s
``attrs`` and the :doc:`JSON frontend </reference/riemap/json_reference>` -- and expand it
into ``pt_nodes``:

- ``{attr}_level{n}`` overrides ``attr`` at VS/single-stage level ``n``. Which level is
  the leaf follows from the pagesize, and is not always 0: it is 0 for 4 KiB and 64 KiB,
  1 for 2 MiB (and Sv32's 4 MiB), 2 for 1 GiB, 3 for 512 GiB, 4 for 256 TiB.
  ``LEAF`` selects the leaf level for the pagesize so callers need not compute it.
- ``{attr}_level{vs}_glevel{g}`` overrides it at a specific VS level under a specific
  G-stage level (two-stage). The symbolic
  :ref:`g-stage forcing attributes <riemap-gstage-forcing-attrs>` (for example
  ``a_leaf_gnonleaf``) are accepted in the same dicts.

G-stage geometry
~~~~~~~~~~~~~~~~

A ``Mapping`` is one stage, so it carries no second-stage geometry of its own. In a
two-stage build that geometry lives on the objects it describes:

- the g-stage translation of a VS leaf's target is that target's own geometry,
  ``dst.pagesize`` -- ``dst`` is a ``Stage.G`` page whose own ``Mapping`` *is* the
  GPA-to-HPA leaf. For a bare-g-stage source (a space walked with the VS stage disabled,
  whose guest walks ``hgatp`` directly) the source page is that leaf, so it is
  ``src.pagesize``.
- the g-stage translation of a non-leaf node's frame is that node's own ``pagesize``:
  the :class:`~riescue.riemap.request.PTGPage`'s for an identity RieMap synthesizes, or
  the pinned frame ``Page``'s.

:meth:`~riescue.riemap.builder.PageTableBuilder.add_two_stage_mapping` sets both:
the leaf geometry from ``hpa_page.pagesize``, which the GPA page it creates
inherits, and the synthesized non-leaf identities' from its
``gstage_nonleaf_pagesize`` argument.

Finite policy choices (``Choice``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use :class:`~riescue.riemap.request.Choice` when several values are legal but one is
preferred. A scalar is always a hard requirement.

.. code-block:: python

   from riescue.riemap.request import Choice, PTGPage

   gstage_frame = PTGPage(
       pagesize=Choice(
           preferred=RV.RiscvPageSizes.S1GB,
           alternatives=(
               RV.RiscvPageSizes.S2MB,
               RV.RiscvPageSizes.S4KB,
           ),
       ),
       identity=True,
   )

RieMap tries the preferred value first and may use an alternative when required by
sharing, address pressure, or topology. It resolves the choice before computing the
frame's translated coverage; that resolved coverage is then a hard correctness
constraint. An empty ``alternatives`` tuple is legal but equivalent to a hard scalar.

Choices can also be used for choice-valued ``PTNode.attrs``. Use
``PTNode.choice=Choice(...)`` when attributes are correlated and must switch as one
whole-node variant rather than field by field. See
:ref:`Hard constraints and choices <riemap-choice-model>` for the model contract.

.. _riemap-pt-nodes:

Page-table nodes
----------------

By default the builder synthesizes every intermediate page-table node and may
share frames when attributes allow; you declare only the leaf. To control a node --
pin its frame, force its PTE bits, or alias and recurse tables -- name it in the same
``pt_nodes`` dict.

``pt_nodes`` maps a walk level to a :class:`~riescue.riemap.request.PTNode`. The key
is an architectural level int in the source's own stage, or the ``LEAF`` sentinel,
which resolves to the source pagesize's leaf level. A ``PTNode`` carries a ``page``
(the frame backing that node; ``None`` lets the builder allocate and share it) and
``attrs`` (that level's PTE bits, as plain base names). A missing level is auto.
``pt_nodes`` is the only place a mapping's PTE bits come from; where the ``LEAF`` node
leaves a base name unset, the builder falls back to that page's default base bit, then
``{base}_level0`` if present, so a partial leaf declaration still yields a complete PTE. See
:doc:`the model </reference/riemap/model>` for the frame-vs-node distinction and
attribute-based sharing.

Compatible node declarations may share one table. When a table's finite PTE-slot
capacity would be exceeded, the builder may instead distribute compatible mappings
across multiple feasible tables; callers must not rely on one table per mapping or
per attribute signature. A ``PTGPage``'s ``pagesize`` defines its synthesized
frame's geometry, while its ``pt_nodes`` declare PTE bits only. Use an explicit
``Page.reserve_size`` when a caller needs exclusive address coverage over a bounded
window. Use ``Page.reserve_granule`` to reserve
``align_down(address, granule) .. align_up(address + reserve_size, granule)`` in the
page's address domain without allocating that much DRAM -- for example when a walk
slot must stay exclusive because runtime code rewrites its tables. A matching
``reserve_size`` would allocate the full DRAM span.

Single-stage
~~~~~~~~~~~~

.. code-block:: python

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
   os_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
   leaf = {"v": 1, "r": 1, "w": 1, "a": 1, "d": 1}

   # (1) common case -- auto leaf (LEAF resolves to this mapping's leaf level)
   va = builder.add_page(Page(space=os_space, addr=AddrSpec(exact=0x1000)))
   pa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80010000)))
   builder.add_mapping(Mapping(src=va, dst=pa, pt_nodes={LEAF: PTNode(attrs=leaf)}))

   # (2) pin the level-1 frame and force its pointer PTE (D=0); no subtree reservation
   node1 = builder.add_page(Page(space=builder.phys))          # frame: holds 512 PTEs
   va2 = builder.add_page(Page(space=os_space, addr=AddrSpec(exact=0x2000)))
   pa2 = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80020000)))
   builder.add_mapping(Mapping(src=va2, dst=pa2, pt_nodes={
       LEAF: PTNode(attrs=leaf),
       1:    PTNode(page=node1, attrs={"d": 0})}))

   # (3) aliased tables -- two VAs whose level-1 node is one physical frame
   shared = builder.add_page(Page(space=builder.phys))
   va_a = builder.add_page(Page(space=os_space))
   va_b = builder.add_page(Page(space=os_space))
   pa_a = builder.add_page(Page(space=builder.phys))
   pa_b = builder.add_page(Page(space=builder.phys))
   builder.add_mapping(Mapping(src=va_a, dst=pa_a, pt_nodes={LEAF: PTNode(attrs=leaf), 1: PTNode(page=shared)}))
   builder.add_mapping(Mapping(src=va_b, dst=pa_b, pt_nodes={LEAF: PTNode(attrs=leaf), 1: PTNode(page=shared)}))

   # (4) recursive self-map -- the Sv39 root frame (level 2) is also the leaf target
   root = builder.add_page(Page(space=builder.phys))
   va_self = builder.add_page(Page(space=os_space))
   builder.add_mapping(Mapping(src=va_self, dst=root, pt_nodes={LEAF: PTNode(attrs=leaf), 2: PTNode(page=root)}))

Two-stage
~~~~~~~~~

In a two-stage build a pinned VS-stage node's frame is a ``Stage.G``-space page: its
GPA, HPA, and g-stage attributes all come from that frame page's *own* mapping (its
g-stage leaf). Declare the frame's g-stage leaf, then pin the frame into the VS
mapping's ``pt_nodes``.

.. code-block:: python

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)
   vs = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.VS))
   g = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39, stage=Stage.G))
   leaf = {"v": 1, "r": 1, "w": 1, "a": 1, "d": 1}

   def gpa_with_gstage_leaf(attrs):
       """A GPA page plus its identity g-stage leaf (GPA == HPA), so it is reachable."""
       gpa = builder.add_page(Page(space=g))
       hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(relation=SameAs(gpa))))
       builder.add_mapping(Mapping(src=gpa, dst=hpa, pt_nodes={LEAF: PTNode(attrs=attrs)}))
       return gpa

   # (1) auto two-stage identity via the convenience wrapper
   va = builder.add_page(Page(space=vs, addr=AddrSpec(exact=0x3000)))
   hpa = Page(space=builder.phys, addr=AddrSpec(exact=0x80030000))
   builder.add_two_stage_mapping(
       va_page=va, hpa_page=hpa, gpa_space=g, attrs=dict(leaf, x=1),
       vs_pagesize=RV.RiscvPageSizes.S4KB, gstage_mode=RV.RiscvPagingModes.SV39)

   # (2) pin the VS level-1 node into a specific G-stage frame; its GPA/HPA/g-stage
   #     attrs come from that frame's own g-stage leaf, declared here (X=1)
   gnode = builder.add_page(Page(space=g))
   gnode_hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(relation=SameAs(gnode))))
   builder.add_mapping(Mapping(src=gnode, dst=gnode_hpa, pt_nodes={LEAF: PTNode(attrs=dict(leaf, x=1))}))
   va2 = builder.add_page(Page(space=vs))
   # Level 0 still needs a G-stage identity; level 1 is the pinned frame.
   builder.add_mapping(Mapping(src=va2, dst=gpa_with_gstage_leaf(leaf),
       pt_nodes={LEAF: PTNode(attrs=leaf), 0: PTNode(page=PTGPage(identity=True)), 1: PTNode(page=gnode)}))

   # (3) force W=0 on the g-stage PTE of a VS level-1 node (address still auto):
   #     declare the frame with its own g-stage leaf forcing W=0, then pin it
   gnode2 = builder.add_page(Page(space=g))
   gnode2_hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(relation=SameAs(gnode2))))
   builder.add_mapping(Mapping(src=gnode2, dst=gnode2_hpa,
       pt_nodes={LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 0, "x": 1, "a": 1, "d": 1})}))
   va3 = builder.add_page(Page(space=vs))
   builder.add_mapping(Mapping(src=va3, dst=gpa_with_gstage_leaf(leaf),
       pt_nodes={LEAF: PTNode(attrs=leaf), 0: PTNode(page=PTGPage(identity=True)), 1: PTNode(page=gnode2)}))

   # (4) aliased VS node shared by two guests' walks (recursive-guest-table tests)
   shared_g = builder.add_page(Page(space=g))
   shared_g_hpa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(relation=SameAs(shared_g))))
   builder.add_mapping(Mapping(src=shared_g, dst=shared_g_hpa, pt_nodes={LEAF: PTNode(attrs=dict(leaf, x=1))}))
   va_x = builder.add_page(Page(space=vs))
   va_y = builder.add_page(Page(space=vs))
   shared_node = {LEAF: PTNode(attrs=leaf), 0: PTNode(page=PTGPage(identity=True)), 1: PTNode(page=shared_g)}
   builder.add_mapping(Mapping(src=va_x, dst=gpa_with_gstage_leaf(leaf), pt_nodes=dict(shared_node)))
   builder.add_mapping(Mapping(src=va_y, dst=gpa_with_gstage_leaf(leaf), pt_nodes=dict(shared_node)))

A VS space under a G space does not need an explicit ``Space.root_frame`` -- the
builder synthesizes a reachable root when none is declared. Pin the root only when
you need a specific frame (recursive tables, read-back windows).

Address constraints and regions
-------------------------------

An :class:`~riescue.riemap.request.AddrSpec` determines one address. Exactly one
determination applies, checked in order:

1. ``exact`` -- pin the value.
2. ``relation`` -- ``SameAs`` / ``OffsetFrom`` / ``DerivedFrom`` (above).
3. ``region`` -- draw inside a :class:`~riescue.riemap.request.MemoryRegion`.
4. otherwise a free draw subject to ``and_mask`` (alignment / clear bits),
   ``or_mask`` (set bits), ``bits`` (address width), and ``qualifiers`` (a set of
   ``RV.AddressQualifiers``).

A :class:`~riescue.riemap.request.MemoryRegion` is geometry the solver places
(fixed ``base``, or floating when ``base`` is ``None``); it has no associated
payload type. A caller that associates payload with a region (PMA attributes, a
named custom range, ...) keeps a ``MemoryRegion -> payload`` map keyed by object
identity. Register regions with
:meth:`~riescue.riemap.builder.PageTableBuilder.add_region` and read placements back
with :meth:`~riescue.riemap.result.AllocationResult.region_base`.

.. code-block:: python

   from riescue.riemap.request import MemoryRegion

   region = builder.add_region(MemoryRegion(size=0x100000))
   probe = builder.add_page(Page(space=builder.phys, addr=AddrSpec(region=region)))
   result = builder.build()

   base = result.region_base(region)
   addr = result.address(probe)
   assert base <= addr < base + region.size

Excluding windows from automatic allocation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pass ``excluded_regions`` to :class:`~riescue.riemap.builder.PageTableBuilder` to
keep every *automatically drawn* address (free pages, auto-allocated page-table
node frames, ...) out of a set of physical windows -- for example, a caller that
tracks decoy PMA regions the page tables must never overlap. Each entry is an
:class:`~riescue.riemap.addrgen.types.ExcludedRegion`, built with
``ExcludedRegion.from_interval(start, end)`` for a contiguous half-open span or
``ExcludedRegion.from_mask(mask, value)`` for a masked set. The list is held by
reference, so truncating or extending it later changes what subsequent draws
avoid.

Exclusions do not relocate exact addresses, relations, fixed ``MemoryRegion.base``,
or ``AddrSpec(region=...)`` placements. Region members are allocated inside the
region under their masks and pagesize alignment (no platform qualifiers). Free
draws skip excluded windows. ``AddrSpec.exclude`` overrides the builder exclusion
set for AddrGen draws (``()`` disables it); use
``AddrSpec(region=region, exclude=())`` when a region member must use an address
that is also in ``excluded_regions``.

.. code-block:: python

   from riescue.riemap.addrgen.types import ExcludedRegion

   decoy_window = ExcludedRegion.from_interval(0x9000_0000, 0x9010_0000)
   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory, excluded_regions=[decoy_window])

Reading back a build
--------------------

:meth:`~riescue.riemap.builder.PageTableBuilder.build` returns an
:class:`~riescue.riemap.result.AllocationResult`. Accessors take the same
``Space`` / ``Page`` / ``MemoryRegion`` instances used at declaration time:

- :meth:`~riescue.riemap.result.AllocationResult.space` returns a
  :class:`~riescue.riemap.result.SpaceResult` exposing
  :attr:`~riescue.riemap.result.SpaceResult.root_addr`,
  :attr:`~riescue.riemap.result.SpaceResult.paging_mode`,
  :attr:`~riescue.riemap.result.SpaceResult.is_gstage`,
  :meth:`~riescue.riemap.result.SpaceResult.walk`,
  :meth:`~riescue.riemap.result.SpaceResult.tables`, and
  :meth:`~riescue.riemap.result.SpaceResult.pte_entries`.
- :meth:`~riescue.riemap.result.AllocationResult.address_of` returns a mapped
  page's ``(src_addr, dst_addr)`` (VA and GPA for a VS leaf; GPA and HPA for a
  G leaf -- not a full two-stage walk to HPA);
  :meth:`~riescue.riemap.result.AllocationResult.address` returns a
  single resolved address for a bare (unmapped) page.
- :meth:`~riescue.riemap.result.AllocationResult.physical_intervals` /
  :meth:`~riescue.riemap.result.AllocationResult.linear_intervals` return the spans
  RieMap occupied.

.. code-block:: python

   space = result.space(va_space)
   # Where the bytes live: build a memory image (or a linker section) from this.
   for table in space.tables():
       for entry in table.entries:
           load(table.backing_addr + table.entry_size * entry.index, entry.value)
   # The walk domain: these keys are the ones walk() reports as WalkStep.pte_addr, so a
   # VS table under a non-identity g-stage appears at its GPA, not at its host frame.
   for pte_addr, pte_value in space.pte_entries():
       ...

``AllocationResult`` does not define symbol names for pages or spaces. If you need to
print or log something human-readable, keep a ``dict`` mapping each ``Page``/``Space``
to a label as you build it, and look names up by the same object you pass to
``AllocationResult``.
