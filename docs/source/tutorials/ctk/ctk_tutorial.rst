CTK Tutorial
============

CTK is a tool for generating a directory of self-checking tests given an ISA string (e.g. "rv64imfv").
Under the hood, it uses `RiescueC` to generate the tests. More information about `RiescueC` can be found in the :doc:`/tutorials/riescuec/index` page.

Currently it's only generating datapath related instructions, but future versions will support generating architectural tests using the testplan flow.



Basic Usage
-----------
Before starting here, make sure that all depedencies are installed. See the :doc:`/tutorials/install` page for more information.

To generate a test kit, use:

.. code-block:: bash

   ctk  --run_dir path/to/output_directory --isa <isa> --seed <seed>


Here the ``run_dir`` is the directory where tests will be generated.
The ``isa`` is the ISA string to generate tests for.
The ``seed`` is the seed to use for reproducibility.

E.g.

.. code-block:: bash

   ctk --run_dir my_test_kit --seed 12345

This command generates 20 tests (default) in the ``my_test_kit`` directory using seed ``12345`` for reproducibility.

.. note::

   The run directory must be empty. If it contains files, CTK will exit with an error. Remove the directory first with ``rm -rf my_test_kit``


Test Kit Structure
-------------------

The test kit will be generated in the ``run_dir`` directory. Currently the tests are all generated in a flat directory.
At the end of the run, the directory will contain a set of binary files for the tests. Tests are of the format:

.. code-block:: bash

   <extension>_<privilege_mode>_<paging_mode>_<test_number>

ISA Configuration
~~~~~~~~~~~~~~~~~

Specify the target ISA string (currently configured internally, future versions will allow command-line specification):

.. code-block:: bash

   # ISA is currently set to rv64i internally
   # Future: --isa rv64imfv



Toolchain Configuration
~~~~~~~~~~~~~~~~~~~~~~~

CTK requires the RISC-V toolchain and ISS tools. Configure paths using command-line arguments or environment variables:

.. code-block:: bash

   ctk --run_dir my_tests \
       --compiler_path /path/to/riscv64-unknown-elf-gcc \
       --disassembler_path /path/to/riscv64-unknown-elf-objdump \
       --whisper_path /path/to/whisper \
       --spike_path /path/to/spike

Alternatively, set environment variables:

.. code-block:: bash

   export RV_GCC=/path/to/riscv64-unknown-elf-gcc
   export RV_OBJDUMP=/path/to/riscv64-unknown-elf-objdump
   export WHISPER_PATH=/path/to/whisper
   export SPIKE_PATH=/path/to/spike
   ctk --run_dir my_tests

See :doc:`/tutorials/install` for more information on sourcing dependencies.

Advanced Usage
--------------

CPU Configuration
~~~~~~~~~~~~~~~~~

Riescue allows for flexible configuration of the test environment by supporting a cpu configuration file.
This file is used to configure the memory map, supported extensions, and other test environment settings. It's passed through on the command line with the ``--cpuconfig`` flag.

Refer to :doc:`../cpu_configuration` for complete details on CPU configuration options.


Troubleshooting
---------------

**Error: Run directory is not empty**

CTK requires an empty directory to prevent accidental overwrites:

.. code-block:: bash

   rm -rf my_test_kit
   python -m riescue.ctk --run_dir my_test_kit --seed 12345

**Missing toolchain or ISS**

Ensure all dependencies are installed. See :doc:`/tutorials/install` for installation instructions.

**Test generation failure**

Check the ``ctk.log`` file in the run directory for detailed error messages and reproduction commands.


Bug Reporting
--------------

`ctk` is still in development, so please report any bugs you find.
Currently if a test generation fails, it will exit with an error not clean up the test directory.

You can file a bug here `file a bug here`_. Plase provide the exact command line you used, and the error message you recieved. Thanks!

.. _file a bug here: https://github.com/tenstorrent/riescue/issues

Next Steps
----------

- Learn about CPU configuration in :doc:`../cpu_configuration`
- Understand BringupMode in :doc:`../riescuec/bringup_mode_tutorial`
- Explore the Python API in :doc:`/reference/python_api/CTK`
- Learn how to generate a test kit for a specific ISA in the :doc:`/user_guides/ctk/vector_test_kit` User Guide