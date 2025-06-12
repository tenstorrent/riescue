.. Internal documentation main file

TT Documentation
===================================================

*Note for Riescue Devs*

These are the internal docs for RiESCUE for TT only. External documentaiton will be using `docs/source` as the source code tree. Voyager2 documentation will be using `docs/tt_source` as the source code tree.

The intention here is to keep the docs as close as possilbe but still have some place to have Voyager2 docs. General rules for editing stuff in here:

- Anything that is specific to Voyager2 should be in `docs/tt_source/internal/*`
- Keep the docs as close as possible to the source code, trying to keep symlinked directories and files the same.
- Update the public docs in `docs/source` when possible



Riescue reference
------------------

.. toctree::
   :maxdepth: 2
   :caption: Riescue Reference:


   api/riescue.lib
   api/riescue.dtest_framework
   voyager2/index
