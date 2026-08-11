API Reference
=============

The builder API
---------------

.. automodule:: riescue.riemap.builder
   :members:

Requests and constraints
-------------------------

The declaration types include :class:`~riescue.riemap.request.PTNode` and
``Mapping.pt_nodes`` for controlling individual page-table nodes (see
:doc:`the model </reference/riemap/model>`); ``LEAF`` is the ``pt_nodes`` sentinel key
meaning "this mapping's leaf level".

:class:`~riescue.riemap.request.Choice` declares a finite set of legal policy values
with deterministic preference ordering. Scalars remain hard constraints; see
:ref:`Hard constraints and choices <riemap-choice-model>` for supported fields and
coverage semantics.

``Space.root_frame`` is optional. When a VS space maps into a table-bearing G
space and neither ``Space.root_frame`` nor a root-level ``PTNode`` is set, the
builder synthesizes a root (a GPA page plus an identity GPA-to-HPA leaf). Setting
either form of pin disables that synthesis; a declared VS root still needs an
explicit GPA-to-HPA mapping. See :ref:`riemap-root-frames`.

.. automodule:: riescue.riemap.request
   :members:

.. autodata:: riescue.riemap.request.LEAF
   :annotation:

   Sentinel ``pt_nodes`` key meaning "this mapping's leaf level": it resolves to the
   source pagesize's leaf level where consumed, so a mapping need not name the level
   explicitly. A distinct object, never an int.

Excluding windows from automatic allocation
--------------------------------------------

See :ref:`Excluding windows from automatic allocation <riemap-excluded-regions>` in
the model doc for how ``excluded_regions`` and ``AddrSpec.exclude`` interact.

.. autoclass:: riescue.riemap.addrgen.types.ExcludedRegion
   :members:

The result surface
-------------------

.. automodule:: riescue.riemap.result
   :members:

JSON frontend
-------------

.. automodule:: riescue.riemap.json_frontend
   :members:
   :exclude-members: MemoryRegion

The frontend defines its own ``MemoryRegion``: one entry of the input config's ``mmap``,
a span of physical memory. That is a different type from
:class:`riescue.riemap.request.MemoryRegion`, the *allocation* region a page can be
drawn inside. In API signatures, unqualified ``MemoryRegion`` means
:class:`riescue.riemap.request.MemoryRegion` unless stated otherwise.

.. autoclass:: riescue.riemap.json_frontend.MemoryRegion
   :members:
   :no-index:
