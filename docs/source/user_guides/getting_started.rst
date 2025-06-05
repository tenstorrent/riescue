Getting Started
===================================================

This page contains information for sourcing dependencies and getting started with Riescue.

Installing Dependencies
------------------------

.. rubric:: Dependencies

This repo currently uses a ``singularity`` container flow to manage the environment. See the `Apptainer docs <https://apptainer.org/docs/admin/main/installation.html>`_ for information on installing Apptainer.

All dependencies can be found listed in the ``infra/Container.def`` file.
Users looking to add to their own container or manage dependencies should refer to this file for all dependencies used.

.. rubric:: Installing Riescue as a package after cloning the repo

Users can install the package

.. code-block:: bash

    git clone https://github.com/tenstorrent/riescue.git
    cd riescue
    pip install .

.. rubric:: Installing Riescue as a package without cloning.

.. code-block:: bash

    pip install git+https://github.com/tenstorrent/riescue.git


.. rubric:: Sourcing dependencies with singularity:

To source dependencies with singularity, users need to build the container.
The `container-build` script will build the container and install all required dependencies.

.. code-block:: bash

    ./infra/container-build

Users can enter the container by running:

.. code-block:: bash

    ./infra/container-run

The container installs ``python3.9``, default python dependencies, and the default simulators (``whisper`` and ``spike``).
It doesn't currently include ``riscv-gnu-toolchain``.



Simulators and Toolchains
-------------------------------------

.. rubric:: Installing and Configuring Simulators

Riescue invokes the following Instruction Set Simulators.
Like simulators can be set with a command line switch, environment variable, or added to the ``PATH``.

- ``whisper`` - `whisper GitHub <https://github.com/tenstorrent/whisper>`_

  - ``whisper`` is a git submodule and can be built in the container using ``./infra/build_whisper.sh``

  - External whisper binaries can be passed in using the environment variable ``WHISPER_PATH`` or the command line switch ``--whisper_path``

- ``spike`` - `spike GitHub <https://github.com/riscv-software-src/riscv-isa-sim>`_, `TT-Spike Fork GitHub <https://github.com/tenstorrent/spike>`_

  - ``spike`` is installed normally in the container flow.

  - External spike binaries can be passed in using the environment variable ``SPIKE_PATH`` or the command line switch ``--spike_path``

  - Please see the ``infra/Container.def`` container definition file for more info on building Spike from the TT-source


After installing the dependencies, users can configure riescue to point to the correct toolchains and simulators.


.. rubric:: Installing and Configuring Toolchains

RiescueD uses the ``riscv-gnu-toolchain`` to assemble, compile, and disassemble ELF tests.
Like simulators, toolchains can be set with a command line switch, environment variable, or added to the ``PATH``.


- ``riscv64-unknown-elf-gcc`` is the default executable used for assembling and compiling

  - Tools will use the ``--compiler_path`` switch, followed by the environment variable ``RV_GCC``, then ``riscv64-unknown-elf-gcc`` in the ``PATH``

- ``riscv64-unknown-elf-objdump`` is the default executable used for disassembling

  - Tools will use the ``--disassembler_path`` switch, followed by the environment variable ``RV_OBJDUMP``, then ``riscv64-unknown-elf-objdump`` in the ``PATH``



With all dependencies sourced, users should be able to run the ``./riescued.py`` script to run the RiescueD tests.


**What next?** See the :doc:`/user_guides/riescued_tutorial` page for information on getting started with RiESCUE.


Developing
-------------------------------------

.. rubric:: Interested in making changes or contributing?

The main dependency needed for developing is a copy of the repo, singularity, and a basic python version installed.


Users can install the package in editable mode to make changes to the codebase while still in the package:

.. code-block:: bash

    git clone https://github.com/tenstorrent/riescue.git
    cd riescue
    pip install -e .


See the `Contributing page <https://github.com/tenstorrent/riescue/blob/main/.github/CONTRIBUTING.md>`_ for additional information.

