Getting Started
===================================================

This page contains information for dependencies and installing RiESCUE.
If you are looking for more information on running RiESCUE, see :doc:`/user_guides/riescued_tutorial` page for information on getting started with RiESCUE.


Requirements
--------------
To get RiESCUE building tests, you'll need a few things:

- ``python3.9`` or greater
- The RISC-V Toolchain
- Whisper ISS
- Spike ISS

``python3.9``
~~~~~~~~~~~~~~
If you don't have ``python3.9`` you can source it by

- Installing it using your package manager
- Launching the container
- Building it with `pyenv <https://github.com/pyenv/pyenv>`_

``riscv-gnu-toolchain``
~~~~~~~~~~~~~~~~~~~~~~~

To install the RISC-V toolchain

- Download and unpack a packaged version from `riscv-gnu-toolchain releases <https://github.com/riscv-collab/riscv-gnu-toolchain/releases>`_`
- Build it from source from the `riscv-gnu-toolchain repo <https://github.com/riscv-collab/riscv-gnu-toolchain>`_

To point RiESCUE to the toolchain, see the :ref:`simulators-and-toolchains`.

ISS
~~~~~
To run your tests on ISS while generating them, you'll need an ISS. More infomration about installing and configuring the ISS is covered in :ref:`simulators-and-toolchains`.



Installing RiESCUE
------------------------
After you have all the dependencies installed, you can install the RiESCUE package using ``pip``:


.. rubric:: Installing RiESCUE as a package without cloning.

.. code-block:: bash

    pip install git+https://github.com/tenstorrent/riescue.git


Or you can clone the repo and install the package locally:

.. rubric:: Installing RiESCUE as a package after cloning the repo.

.. code-block:: bash

    git clone https://github.com/tenstorrent/riescue.git
    cd riescue
    pip install .


.. note::

    Installing with pip will not working if `python3.9` or greater is not installed


.. _simulators-and-toolchains:

Simulators and Toolchains
-------------------------------------

.. rubric:: Installing and Configuring Simulators

RiESCUE uses Whisper and Spike as optional Instruction Set Simulators.

Whisper
~~~~~~~~
`whisper <https://github.com/tenstorrent/whisper>`_ is RISC-V ISS used to verify tests have been generated correctly. It can be sourced by:

- using ``./infra/build_whisper.sh``
- cloning and installing from source repository - `whisper GitHub <https://github.com/tenstorrent/whisper>`_
- running the container flow

External whisper binaries can be passed in using the environment variable ``WHISPER_PATH`` or the command line switch ``--whisper_path``


Spike
~~~~~~
`spike <https://github.com/riscv-software-src/riscv-isa-sim>`_ is RISC-V ISS used to run tests. This repo uses the TT-fork, `TT-Spike Fork GitHub <https://github.com/tenstorrent/spike>`_
It can be sourced by:

- using ``./infra/build_spike.sh``
- cloning and installing from source repository
- running the container flow

External spike binaries can be passed in using the environment variable ``SPIKE_PATH`` or the command line switch ``--spike_path``


See the ``infra/Container.def`` container definition file for info on how Whisper and Spike are sourced.


.. _riscv-toolchain:

RISC-V Toolchain
~~~~~~~~~~~~~~~~
.. rubric:: Configuring Toolchains

RiESCUED uses the ``riscv-gnu-toolchain`` to assemble, compile, and disassemble ELF tests.
Like simulators, toolchains can be set with a command line switch, environment variable, or added to the ``PATH``.


- ``riscv64-unknown-elf-gcc`` is the default executable used for assembling and compiling

  - Tools will use the ``--compiler_path`` switch, followed by the environment variable ``RV_GCC``, then ``riscv64-unknown-elf-gcc`` in the ``PATH``

- ``riscv64-unknown-elf-objdump`` is the default executable used for disassembling

  - Tools will use the ``--disassembler_path`` switch, followed by the environment variable ``RV_OBJDUMP``, then ``riscv64-unknown-elf-objdump`` in the ``PATH``



With all dependencies sourced, users should be able to run the ``python3 -m riescued`` to build RiescueD tests.


**What next?** See the :doc:`/user_guides/riescued_tutorial` page for information on getting started with RiESCUE.


Developing
-------------------------------------

.. rubric:: Interested in making changes or contributing?

The main dependency needed for developing is a copy of the repo, singularity, and a basic python version installed.


Users can install the package in editable mode to make changes to the codebase while still in the package:

.. code-block:: bash

    git clone https://github.com/tenstorrent/riescue.git
    cd riescue
    ./infra/container-build
    ./infra/container-run
    pip install -e .

Note that python dependencies are found in the `pyproject.toml` file.

Users can add dependencies to pyproject.toml and test locally with pip install -e .. Rebuild the container to include new dependencies in the container image.

See the `Contributing page <https://github.com/tenstorrent/riescue/blob/main/.github/CONTRIBUTING.md>`_ for additional information.


Singularity container
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Riescue development uses Singularity to manage the development environment. See the `Apptainer docs <https://apptainer.org/docs/admin/main/installation.html>`_ for information on installing Apptainer.

All dependencies can be found listed in the ``infra/Container.def`` file.
Users looking to add to their own container or manage dependencies should refer to this file for all dependencies used.

To source dependencies with singularity, users need to build the container.
The `container-build` script will build the container and install all required dependencies.

.. code-block:: bash

    ./infra/container-build

Users can enter the container by running:

.. code-block:: bash

    ./infra/container-run

The container installs ``python3.9``, default python dependencies, and the default simulators (``whisper`` and ``spike``).

.. note::

    The container does **not** include the ``riscv-gnu-toolchain``. Users and developers should source their own toolchain and point RiESCUE to it using :ref:`riscv-toolchain`.





