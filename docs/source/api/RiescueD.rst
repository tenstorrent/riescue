
RiescueD
--------
`RiescueD` is the Riescue Directed Test Framework used to generate directed tests. It consists of a Python library and a command-line interface.
The main workflow for writing directed tests is:
1. Write `.s` file using assembly with RiescueD Directives
2. Provide system memory map and configuration in cpuconfig `.json`` file
3. Run RiescueD to produce:

   * ELF binary
   * `.S` assembly file
   * `.ld` Linker Script



Command-line Interface
^^^^^^^^^^^^^^^^^^^^^^^
The main entry point for the framework is the `riescued.py` wraper script.
To see available run options, run:

.. code-block:: bash

   ./riescued.py -h


To run a directed test, use

.. code-block:: bash

   ./riescued.py run --testname <path/to/test.s> --cpuconfig <path/to/cpuconfig.json>



API
^^^
The `RiescueD` python library can also be used to generate directed tests.

*NOTE: This will likely change before the v.1.0.0 release, it's recommneded to start with the command-line until there's a stable python library*

.. autoclass:: riescue.RiescueD
   :members:
   :undoc-members:


