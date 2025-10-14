
CTK
--------
`CTK` is the Compliance Test Kit used to generate a directory of tests given an ISA string (e.g. ``"rv64imfv"``).

- For a first time tutorial of how to use CTK, see the tutorial at :doc:`../../tutorials/ctk/index` for a walkthrough of how to use CTK.
- If you want to get started quickly, see the :doc:`../../user_guides/ctk/vector_test_kit` User Guide for a quick start guide on how to generate a test kit for a single extension.


Command-line Interface
^^^^^^^^^^^^^^^^^^^^^^^
The main entry point for the framework is the `ctk.py` orchestrator script.
To see available run options, run:

.. code-block:: bash

   ctk  -h

To generate a test kit, use

.. code-block:: bash

   ctk  --isa <isa> --run_dir path/to/output_directory


The `ctk` python library can also be used to generate a test kit.

.. code-block:: python

   from riescue import Ctk

   Ctk.run_cli("--isa <isa> --run_dir path/to/output_directory")



API
^^^
The `Ctk` python library can also be used to generate a test kit.


.. autoclass:: riescue.Ctk
   :members: configure, run, generate
   :undoc-members:




