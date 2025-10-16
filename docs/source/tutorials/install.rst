Install and Setup
===================================================

This page contains information for dependencies and installing RiESCUE.
If you are looking for more information on running RiESCUE, see :doc:`/user_guides/riescued_user_guide` page for information on getting started with RiESCUE.


Requirements
--------------
To get RiESCUE building tests, you'll need to have ``python3.9`` or greater installed and some non-python dependencies installed.
These are not included in the python package and need to be compiled and installed before running.
These include:

- `The RISC-V Toolchain <https://github.com/riscv-collab/riscv-gnu-toolchain>`_
- `Whisper ISS <https://github.com/tenstorrent/whisper>`_
- `Spike ISS <https://github.com/tenstorrent/spike>`_

.. _sourcing-dependencies:

Sourcing Dependencies
------------------------
All dependncies that aren't included in the python package can be sourced to RiESCUE by:

- Making the dependency available in the ``PATH``
- Setting an environment variable
- Passing the path on the command line


``riscv-gnu-toolchain``
~~~~~~~~~~~~~~~~~~~~~~~


To install the RISC-V toolchain you can:

- Download and unpack a packaged version from `riscv-gnu-toolchain releases <https://github.com/riscv-collab/riscv-gnu-toolchain/releases>`_
- Build it from source from the `riscv-gnu-toolchain repo <https://github.com/riscv-collab/riscv-gnu-toolchain>`_

Sourcing
____________

RiESCUED uses the ``riscv-gnu-toolchain`` to assemble, compile, and disassemble ELF tests.
By default RiESCUE uses the ``riscv64-unknown-elf-gcc`` and ``riscv64-unknown-elf-objdump`` executables for compiling and disassembling ELF tests.

To source the toolchain, you  can source it to RiESCUE through any of the following methods:

1. Make ``riscv64-unknown-elf-gcc`` and ``riscv64-unknown-elf-objdump`` available in the ``PATH``.
2. Set the environment variables ``RV_GCC`` and ``RV_OBJDUMP`` to the paths of ``riscv64-unknown-elf-gcc`` and ``riscv64-unknown-elf-objdump``.
3. Pass the paths on the command line using the ``--compiler_path`` and ``--disassembler_path`` switches (if using the CLI).


Whisper ISS
~~~~~~~~~~~~
`whisper <https://github.com/tenstorrent/whisper>`_ is a RISC-V ISS used to verify tests have been generated correctly.
It can be sourced by cloning and installing from source repository - `whisper GitHub <https://github.com/tenstorrent/whisper>`_.


Sourcing
____________

Once you have a whisper excecutable installed, you can source it to RiESCUE through any of the following methods:

1. Making the ``whisper`` binary available in the ``PATH``
2. Setting the environment variable ``WHISPER_PATH`` to the path of the ``whisper`` binary
3. Passing the path on the command line using the ``--whisper_path`` switch


Spike ISS
~~~~~~~~~~~~

Install
___________
`Spike <https://github.com/tenstorrent/spike>`_ is a RISC-V reference model used to run tests.
This repo currently uses the TT-fork, `TT-Spike Fork GitHub <https://github.com/tenstorrent/spike>`_, but the original version can be found on the `riscv-isa-sim GitHub <https://github.com/riscv-software-src/riscv-isa-sim>`_.

Sourcing
____________

Once you have a Spike excecutable installed, you can source it to RiESCUE through any of the following methods:

1. Making the ``spike`` binary available in the ``PATH``
2. Setting the environment variable ``SPIKE_PATH`` to the path of the ``spike`` binary
3. Passing the path on the command line using the ``--spike_path`` switch




Installing RiESCUE
------------------------
After you have all the dependencies installed, you can install the RiESCUE package using ``pip``:


.. rubric:: Installing RiESCUE as a package without cloning.

.. code-block:: bash

    pip install git+https://github.com/tenstorrent/riescue.git

.. note::

    RiESCUE will fail at runtime, not installation, if dependencies are missing. See the :ref:`troubleshooting` section for more information.


**What next?** See the :doc:`/user_guides/riescued_user_guide` page for information on getting started with RiESCUE.


.. _troubleshooting:

Troubleshooting
------------------------

A common error after installing RiESCUE from `pip` is not finding depedencies. It looks like a python traceback, like:

.. code-block:: bash

    FileNotFoundError: Could not find whisper in PATH. Add it to your PATH or set WHISPER_PATH environment variable


This can be fixed by ensuring the depencies are properly sourced. See the :ref:`sourcing-dependencies` section for more information.


Developing
--------------

Interested in modifying or developing RiESCUE? See the :doc:`develop` page for more information.

.. toctree::
   :hidden:
   :maxdepth: 1

   develop
