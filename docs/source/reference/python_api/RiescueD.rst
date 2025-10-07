
RiescueD
--------
`RiescueD` is the Riescue Directed Test Framework used to generate directed tests. It consists of a Python library and a command-line interface.
The main workflow for writing directed tests is:

1. Write `.s` file using assembly with RiescueD Directives (see :doc:`../riescue_test_file/directives_reference`)
2. Provide system memory map and configuration in cpuconfig `.json` file (see :doc:`../config/configuration_schema`)
3. Run RiescueD to produce:

   * ELF binary
   * `.S` assembly file
   * `.ld` Linker Script



Command-line Interface
^^^^^^^^^^^^^^^^^^^^^^^
The main entry point for the framework is the `riescued.py` wraper script.
To see available run options, run:

.. code-block:: bash

   python3 -m riescued  -h


To run a directed test, use

.. code-block:: bash

   python3 -m riescued  --testname <path/to/test.s> --cpuconfig <path/to/cpuconfig.json>


You can also run a wrapper script, e.g.


.. code-block:: python

   from riescue import RiescueD

   RiescueD.run_cli("-t test.s".split())
   RiescueD.run_cli(["--t", "test.s"])





API
^^^
The `RiescueD` python library can also be used to generate directed tests.


.. autoclass:: riescue.RiescueD
   :members: configure, run, generate, build, simulate
   :undoc-members:




