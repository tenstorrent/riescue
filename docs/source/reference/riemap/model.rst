The Model
=========

RieMap describes an allocation as **spaces**, **pages**, and **mappings**, related
by **constraints**. The builder solves the constraints together and emits the page
tables. Declarations are geometric (spaces, pages, mappings, address constraints).
Any declaration order yields a constraint-satisfying build; with a fixed seed,
exact addresses may still differ if declaration order differs.

Spaces
------

A :class:`~riescue.riemap.request.Space` is an address domain: an address pool, a
paging mode, and a :class:`~riescue.riemap.request.Stage`. A space that is the source
of at least one mapping has a page table and a root register. A space that is only
ever a mapping *target* is a **leaf** (the physical domain) and has no page table.

Additional ``Space`` fields:

- ``secure_pt_probability`` (0--100): chance each *auto-allocated* page-table node
  frame is drawn from secure memory. Allocator policy only; not PTE-bit policy.
  Pass ``0`` when not in secure mode.
- ``priv_mode``: privilege the space is entered at; selects the default leaf U
  bit. Does not randomize leaf PTE bits -- set those on ``Mapping.pt_nodes``.
- ``root_frame`` (optional ``Page``): pins this space's root register/table
  (satp / vsatp / hgatp). See :ref:`riemap-root-frames` below.

Undeclared PTE bits are left unset by RieMap; declared bits are written as given.
Free addresses and ``secure_pt_probability`` still depend on the builder RNG seed.
A caller that wants Svadu's A/D randomization rolls the bits itself and declares
the concrete ``a``/``d`` values on the mapping's nodes.

Declaration types are frozen dataclasses identified by object identity. Pass the
same ``Space`` (or ``Page``) instance wherever that object is referenced.

The builder creates the physical leaf domain as
:attr:`~riescue.riemap.builder.PageTableBuilder.phys`. Use it for physical (PA/HPA)
pages rather than adding a paging-disabled space.

Distinct spaces have independent pools, so the same virtual address in two spaces
can resolve to different physical addresses.

Pages
-----

A :class:`~riescue.riemap.request.Page` is a bare allocation in one space: an address
at a given pagesize, drawn from that space's pool. A page carries no translation and
no PTE attributes. Virtual, guest-physical, and physical pages are all ``Page``
objects in their respective spaces.

A page's address is described by an :class:`~riescue.riemap.request.AddrSpec`: an
exact value, alignment/OR masks with an address width, membership in a tagged
:class:`~riescue.riemap.request.MemoryRegion` (``region``), or a relation to another
page's address.

Address qualifiers belong to the page whose address is actually allocated. A page
related by ``SameAs`` or ``OffsetFrom`` derives its address class from the ultimate
free allocation root; qualifiers on a relational follower are not independent
metadata. If a consumer marks a linked page secure, apply that qualifier to the
family's allocation root before declaring the relation. The root's qualifier
selects the memory pool and sets the secure bit on every mapping that targets the
family.

Mappings
--------

A :class:`~riescue.riemap.request.Mapping` is one leaf PTE. In the source page's space
table, a leaf at the source address translates to the destination page's address, at the
source's pagesize and with the PTE bits declared on the mapping's ``LEAF`` node. The
source's space is the from-domain; the destination's space is the to-domain.

- A single-stage translation (VA to PA) is one mapping.
- A two-stage translation (VA to GPA to PA) is two mappings: VA to GPA, and GPA to
  PA.

Constraints
-----------

Page addresses are related by constraints instead of being computed by the caller. A
relation lives on a page's ``AddrSpec``:

- :class:`~riescue.riemap.request.SameAs` -- this address equals another request's
  resolved address (an alias; the shared address is not reserved twice).
- :class:`~riescue.riemap.request.OffsetFrom` -- this address equals another's plus a
  fixed delta (a linked child, or a buddy).
- :class:`~riescue.riemap.request.DerivedFrom` -- this address is a masked, optionally
  bit-flipped function of another's address.

``SameAs`` and ``OffsetFrom`` form affine allocation families: following their links
finds one allocation root plus a cumulative offset. Address qualifiers for the
family are taken from that root. ``DerivedFrom`` is not affine and therefore does
not inherit its target's address class.

A :class:`~riescue.riemap.request.MemoryRegion` is geometry plus placement
qualifiers, either fixed (``base`` pinned) or floating (``base is None``, solver
places it size-aligned). A page placed with ``AddrSpec(region=...)`` is drawn
inside it. A ``MemoryRegion`` stores only geometry and placement fields; it has
no associated payload type. A caller that associates payload with a region
(PMA attributes, a named custom range, ...) keeps a ``MemoryRegion -> payload``
map keyed by object identity, and reads the payload back next to
:meth:`~riescue.riemap.result.AllocationResult.region_base`.

.. _riemap-excluded-regions:

Excluding windows from automatic allocation
--------------------------------------------

:class:`~riescue.riemap.builder.PageTableBuilder` takes an optional
``excluded_regions`` list of
:class:`~riescue.riemap.addrgen.types.ExcludedRegion` values: physical windows
that no *automatically drawn* address (a free page, an auto-allocated page-table
node frame, ...) may be allocated in. An ``ExcludedRegion`` is either a contiguous
half-open interval (:meth:`~riescue.riemap.addrgen.types.ExcludedRegion.from_interval`)
or a masked set (:meth:`~riescue.riemap.addrgen.types.ExcludedRegion.from_mask`,
``address & mask == value``) for a window scattered across the address space. The
list is held by reference, so truncating or extending it later applies to
subsequent free draws.

Exclusions apply only to free draws. An ``AddrSpec(exact=...)``, a
``relation`` (``SameAs`` / ``OffsetFrom`` / ``DerivedFrom``), a fixed
``MemoryRegion.base``, or a page placed with ``AddrSpec(region=...)`` is not
moved to satisfy exclusions. A page with ``AddrSpec(region=...)`` is allocated
inside that region subject to its masks, address width, and pagesize alignment
(platform qualifiers are not applied), and is not re-checked against the
exclusion set. ``AddrSpec.exclude`` overrides the builder-level set for any draw
that goes through AddrGen (``None`` keeps the default, ``()`` disables
exclusions).

.. _riemap-choice-model:

Hard constraints and choices
----------------------------

A scalar declaration is a hard constraint. For the few policy values where several
answers are acceptable, a caller can instead pass a
:class:`~riescue.riemap.request.Choice`: ``preferred`` is the deterministic first
choice and ``alternatives`` is the complete ordered set of fallbacks. Every option is
legal; RieMap may select an alternative to preserve sharing or find a feasible global
topology, but never chooses a value outside the declared domain. If several
declarations describe one shared node, their domains must have a common value.

``Choice`` resolves to one concrete value first; allocation and coverage
constraints on that value remain hard. For example, a g-stage frame may request
a 1 GiB identity leaf but permit smaller coverage when that geometry conflicts:

.. code-block:: python

   from riescue.riemap.request import Choice, PTGPage

   frame = PTGPage(
       pagesize=Choice(
           preferred=RV.RiscvPageSizes.S1GB,
           alternatives=(
               RV.RiscvPageSizes.S2MB,
               RV.RiscvPageSizes.S4KB,
           ),
       ),
       identity=True,
   )

The corresponding scalar ``pagesize=RV.RiscvPageSizes.S1GB`` means exactly 1 GiB or
failure. ``Page.pagesize``, address relations, exact addresses, reservation sizes,
and reservation granules remain hard constraints; ``Choice`` is currently consumed for
``PTGPage.pagesize``, choice-valued ``PTNode.attrs``, and correlated whole-node
``PTNode.choice`` attribute dictionaries.

Identity translations
----------------------

Equal input/output addresses are expressed with ``SameAs``, not a ``Mapping`` flag.

- **dst ``SameAs`` src**: the builder allocates once in the physical pool and
  reserves that address in both the physical pool and the source space
  (typical single-stage VA==PA).
- **src ``SameAs`` dst**: the destination is drawn; the source aliases it
  (GPA ``SameAs`` HPA in ``add_two_stage_mapping``).
- **``PTGPage.identity``**: whether a *synthesized* VS page-table frame's
  g-stage translation is GPA==HPA (``True``) or independently allocated GPA and
  HPA (``False``). This is not a consumer leaf-mapping flag.

.. _riemap-root-frames:

Root frames
-----------

``Space.root_frame`` is optional.

- **Single-stage / G-stage** with ``root_frame is None``: the builder declares a
  physical root page. An hgatp root is placed 2 MiB-aligned (implementation;
  architectural minimum is 16 KiB).
- **VS-stage under a table-bearing G space** with ``root_frame is None`` and no
  root-level ``PTNode`` pin: the builder adds a GPA page in each target G space,
  pins it ``SameAs`` a physical HPA page, and adds an identity GPA-to-HPA leaf.
  Switch-hgatp (one VS space, several G targets) gets one root GPA per G space,
  all ``SameAs`` the same HPA.
- **Declared root**: set ``Space.root_frame``, or pin the root with a root-level
  ``PTNode(page=frame)``. Either disables builder synthesis for that space.
- A declared VS ``root_frame`` must be a GPA page in the target G space
  **and** the source of an explicit GPA-to-HPA ``Mapping``; RieMap does not
  synthesize that leaf for a declared root.

Single-stage vs two-stage
-------------------------

Every :class:`~riescue.riemap.request.Space` carries an explicit
:class:`~riescue.riemap.request.Stage`: :attr:`~riescue.riemap.request.Stage.SINGLE`
(satp), :attr:`~riescue.riemap.request.Stage.VS` (vsatp, the first stage of a
two-stage walk), or :attr:`~riescue.riemap.request.Stage.G` (hgatp, g-stage). A
``VS`` space mapping into a ``G`` space is a two-stage walk: the source's leaf
address is a GPA, and the ``G`` space translates it to a physical address.

The common virtualized case is an **identity G-stage** (GPA equals HPA): a physical
HPA page, a GPA page pinned ``SameAs`` it, a VA-to-GPA VS-stage mapping, and a
GPA-to-HPA G-stage leaf. Every VS-stage table frame below the root also needs an
explicit G-stage translation -- a ``PTGPage`` on that level, or a pinned
``Stage.G`` ``Page`` with its own GPA-to-HPA mapping -- so the VS tree is
reachable under the G space.
:meth:`~riescue.riemap.builder.PageTableBuilder.add_two_stage_mapping` declares
the identity G-stage case, including those ``PTGPage`` s.

.. mermaid::

   flowchart LR
       subgraph VA["VA space (vsatp, Sv39)"]
           va["VA page"]
       end
       subgraph GPA["GPA space (hgatp, Sv39)"]
           gpa["GPA page"]
       end
       subgraph PHYS["builder.phys (leaf, no table)"]
           hpa["HPA page"]
       end
       va -->|"VS-stage leaf PTE"| gpa
       gpa -->|"G-stage leaf PTE"| hpa
       gpa -. "SameAs: GPA == HPA (identity)" .-> hpa

A non-identity G-stage instead declares an explicit GPA-to-PA leaf, so the G-stage
table remaps the GPA to a different physical address. A single VS-stage table can
also be walked under several G-stages (the same VA-to-GPA declared once per G space,
the shared GPA resolving to a different PA in each).

A *bare* G-stage source -- a space walked with VS-stage disabled that walks its own
``hgatp`` directly -- is a :class:`~riescue.riemap.request.Space` with
``stage=Stage.G`` that is also a mapping source (not just a target). Every
``Space`` sets ``Stage`` explicitly; RieMap does not infer it from mappings.

Page-table nodes
----------------

By default you declare only leaves and the builder synthesizes every intermediate
node, allocating frames and sharing them when attributes allow. When a test needs
to control a node -- its frame, its PTE bits, or both -- it names that node in the
mapping's ``pt_nodes``.

Frame vs node
~~~~~~~~~~~~~

A :class:`~riescue.riemap.request.Page` is a **frame**: a physical page holding a table's
PTEs. A **node** is a *position* in the walk -- one table, indexed by one field of the
input address. How many slots that table has is derived from the paging mode and the
level, not fixed: the width of the level's index field sets it, so an Sv39/Sv48/Sv57
table has 512 eight-byte PTEs while an Sv32 table has 1024 four-byte ones. Either way it
is one 4 KiB frame.

Normally one frame backs one node, but **one frame can back several nodes**: different
tree positions packing their PTEs into the same physical page at their address-derived
slots. A node is identified by ``(frame base address, slot)``, not by the ``Page``
alone. The builder packs PTEs and errors on a real slot collision (same frame and
slot, different content) or slot exhaustion (more distinct slots than the level's
index field can address). That packing enables aliased and recursive tables.

The g-stage root frame is placed 2 MiB-aligned. RieMap currently supports a 9-bit
g-stage root index: 512 entries in one 4 KiB logical table, indexed like the root of
the corresponding VS-stage mode. This is a current implementation limit. It does not
implement the architectural x4 root's additional two index bits (2048 entries in a
16 KiB root), so GPAs that require those additional root-index bits are unsupported.

``pt_nodes``
~~~~~~~~~~~~

``Mapping.pt_nodes`` maps a walk level to a
:class:`~riescue.riemap.request.PTNode`. The key is an architectural level int in the
source's own stage, or the ``LEAF`` sentinel, which resolves to the source pagesize's
leaf level. A missing level is auto (builder-allocated, attribute-shared); an empty
``pt_nodes`` is fully auto.

A :class:`~riescue.riemap.request.PTNode` carries a ``page`` (the frame backing this
node) and ``attrs`` (that level's PTE bits, as plain base names --
``v``/``a``/``d``/``g``/``w``/``r``/``x``/``u``/``n``/``pbmt``; the level is implied
by the key). ``pt_nodes`` is the only place a mapping's PTE bits come from. A base name
the ``LEAF`` node leaves unset falls back to the page's default base bit, then
``{base}_level0`` if present, so a partial leaf declaration still yields a complete PTE.

Auto vs pinned frames
~~~~~~~~~~~~~~~~~~~~~~

A ``PTNode`` with ``page=None`` is **auto**: the builder allocates the frame and
shares it when attributes allow. A ``PTNode`` naming a ``Page`` is **pinned** to
that page's resolved address. For a single-stage or G-stage tree the frame is a
``builder.phys`` page. For a VS-stage tree a concrete frame must be a
``Stage.G`` ``Page`` that is already the source of an **explicit** GPA-to-HPA
``Mapping`` (missing that mapping is an error). Use
``PTNode(page=PTGPage(...))`` when RieMap should allocate the VS frame GPA and
emit its g-stage identity (``identity=True`` for GPA==HPA;
``identity=False`` for independent GPA/HPA). Frame placement
(secure memory, address masks) comes from a pinned frame page's ``AddrSpec``, or
from the builder for ``PTGPage`` / auto frames.

Aliasing and recursion
~~~~~~~~~~~~~~~~~~~~~~~~

Because identity is ``(frame, slot)`` and not the frame alone, several ``PTNode`` s
naming the same frame page pack their PTEs into it. Two mappings sharing a level's
frame **alias** that node -- one physical table serving both walks. A mapping whose
node frame is also its leaf target **recurses** -- a self-mapping page table. The
builder packs both and errors only on a same-slot content contradiction.

Attribute-based sharing
~~~~~~~~~~~~~~~~~~~~~~~

Two source addresses share a non-leaf pointer PTE when they reach the same node and
their forced non-default attributes at that level are identical; they receive
different index bits and are placed in different nodes only when those attributes
**conflict**. A pinned child frame is part of that signature too: two mappings whose
level-``L`` PTE would point at different concrete frames cannot share the level-``L``
slot, whether their sources are free or exact. A plain sibling with no child pin of its
own may still use the shared slot (and therefore the pinned child). Free mappings that
pin a conflicting child are assigned a different index; exact sources are never
moved, so two exact VAs that demand different children at one fixed index raise.
Compatible attributes and child pins share a non-leaf PTE by default. Conflicting
attributes or pinned children produce separate nodes. A page's reservation covers the
page, not the subtree beneath a forced attribute. There is no separate sharing API.

Reading back a build
--------------------

:meth:`~riescue.riemap.builder.PageTableBuilder.build` returns an
:class:`~riescue.riemap.result.AllocationResult`:

- :meth:`~riescue.riemap.result.AllocationResult.space` returns a space's
  :class:`~riescue.riemap.result.SpaceResult` -- its root address, paging mode, a
  :meth:`~riescue.riemap.result.SpaceResult.walk` of the tree, and the non-leaf
  :meth:`~riescue.riemap.result.SpaceResult.tables`.
- :meth:`~riescue.riemap.result.AllocationResult.address_of` returns a mapped page's
  ``(src_addr, dst_addr)`` -- VA and GPA for a VS leaf, GPA and HPA for a G leaf,
  not a full two-stage walk to HPA. :meth:`~riescue.riemap.result.AllocationResult.address`
  returns the page's allocated address alone.
- :meth:`~riescue.riemap.result.AllocationResult.region_base` returns a placed
  region's base, and
  :meth:`~riescue.riemap.result.AllocationResult.physical_intervals` /
  :meth:`~riescue.riemap.result.AllocationResult.linear_intervals` return the spans
  RieMap occupied.

Results expose structured data (addresses, levels, ``Page``/``Space`` objects), not
symbol names. Map objects to names in the consumer if needed.
