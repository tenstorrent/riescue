Getting Started with RieMap
===========================

RieMap generates RISC-V page tables from a description of address spaces and the
mappings between them. It has two frontends: a ``riemap`` command-line tool driven
by JSON, and the :class:`~riescue.riemap.builder.PageTableBuilder` Python API. This
page walks through both with a minimal example.

For the Python API in depth see the
:doc:`RieMap user guide </user_guides/riemap_user_guide>`; for every JSON config
field see the :doc:`JSON configuration reference </reference/riemap/json_reference>`;
for the underlying concepts see the :doc:`model </reference/riemap/model>`.

JSON quickstart
---------------

Write a config describing physical memory (``mmap``) and one or more address spaces
(``spaces``). This one maps two data pages in an Sv39 space:

.. code-block:: json

   {
     "mmap": [["0x80000000", "0x100000000"]],
     "spaces": {
       "os": {
         "paging_mode": "sv39",
         "pages": [
           {"id": "data", "num_pages": 2, "attributes": {"v": 1, "r": 1, "w": 1}}
         ]
       }
     }
   }

Run the generator:

.. code-block:: bash

   riemap config.json pagetables.json --seed 1

The output JSON has an ``entries`` map (PTE address to PTE value, ready to load into
memory) and a ``spaces`` map giving each space's root address, paging mode, and
per-page VA-to-PA walks. The
:doc:`JSON configuration reference </reference/riemap/json_reference>` documents the
full input and output formats.

Python quickstart
-----------------

The same idea from Python: create a :class:`~riescue.riemap.memory.Memory`, add a
space, a source page and a physical page, and one mapping between them.
``add_space`` and ``add_page`` return ``Space``/``Page`` instances; pass those to
``Mapping`` and to ``AllocationResult`` accessors.

.. code-block:: python

   import riescue.lib.enums as RV
   from riescue.lib.rand import RandNum
   from riescue.riemap.memory import Memory
   from riescue.riemap.builder import PageTableBuilder
   from riescue.riemap.request import LEAF, AddrSpec, Mapping, Page, PTNode, Space

   memory = Memory.from_dict(
       {"dram": {"dram0": {"address": "0x80000000", "size": "0x80000000000000", "cacheable": True, "configurable": True}}}
   )

   builder = PageTableBuilder(rng=RandNum(seed=1), memory=memory)

   # One Sv39 VA space; builder.phys (the physical leaf domain) is auto-created.
   va_space = builder.add_space(Space(paging_mode=RV.RiscvPagingModes.SV39))
   code = builder.add_page(Page(space=va_space, addr=AddrSpec(exact=0x1000)))
   code_pa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80010000)))
   builder.add_mapping(Mapping(src=code, dst=code_pa, pt_nodes={
       LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 0, "x": 1, "a": 1, "d": 1})}))

   result = builder.build()
   steps, pa = result.space(va_space).walk(0x1000)
   print(hex(pa))                    # 0x80010000
   print(result.address_of(code))    # (4096, 2147549184) -- decimal ints

PTE bits are declared per page-table node in ``pt_nodes``. ``LEAF`` resolves to the
leaf level for the source pagesize (level 0 for a 4 KiB Sv39 page).

Declaring page-table nodes
--------------------------

Intermediate nodes were not declared: the builder allocated Sv39 level-2 and
level-1 nodes and may share those frames when attributes allow. To control a
node -- pin its frame, force its PTE bits, or alias and recurse tables -- name it
in the same ``pt_nodes`` dict. Declared before the ``build()`` above
(``build()`` may be called only once per builder), a second page pinning its
level-1 node to a frame and forcing that pointer PTE's D bit:

.. code-block:: python

   data = builder.add_page(Page(space=va_space, addr=AddrSpec(exact=0x2000)))
   data_pa = builder.add_page(Page(space=builder.phys, addr=AddrSpec(exact=0x80020000)))
   node1 = builder.add_page(Page(space=builder.phys))   # the frame holding the level-1 table

   builder.add_mapping(Mapping(src=data, dst=data_pa, pt_nodes={
       LEAF: PTNode(attrs={"v": 1, "r": 1, "w": 1, "a": 1, "d": 1}),
       1:    PTNode(page=node1, attrs={"d": 0})}))

``pt_nodes`` keys are architectural level ints in the source's own stage (or ``LEAF``
for the leaf level); each :class:`~riescue.riemap.request.PTNode` gives a frame
``page`` (``None`` = auto) and that level's ``attrs``. Aliasing, recursion, and the
two-stage variants are in the
:doc:`user guide </user_guides/riemap_user_guide>`.

Next steps
----------

- :doc:`/user_guides/riemap_user_guide` -- two-stage translation, aliasing,
  attributes, page-table nodes, and address constraints in the Python API.
- :doc:`/reference/riemap/model` -- the constraint model.
- :doc:`/reference/riemap/json_reference` -- the complete JSON schema.
