
Developing
-------------------------------------

.. rubric:: Interested in making changes or contributing?

The main dependency needed for developing is a copy of the repo, singularity, and a basic python version installed.


Users can install the package in editable mode to make changes to the codebase while still in the package.
It's recommended to use a virtual environment to better manage dependencies while developing:

.. code-block:: bash

    git clone https://github.com/tenstorrent/riescue.git
    cd riescue
    ./infra/container-build
    ./infra/container-run
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e .

Note that python dependencies are found in the `pyproject.toml` file.

Users can add dependencies to pyproject.toml and test locally with pip install -e .. Rebuild the container to include new dependencies in the container image.

See the `Contributing page <https://github.com/tenstorrent/riescue/blob/main/.github/CONTRIBUTING.md>`_ for additional information.


Singularity container
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Riescue development uses Singularity to manage the development environment. See the `Apptainer docs <https://apptainer.org/docs/admin/main/installation.html>`_ for information on installing Apptainer.

ISS and python dependencies can be found listed in the ``infra/Container.def`` file. The RISC-V GNU toolchain is not included in the container by default.
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

    The container does **not** include the ``riscv-gnu-toolchain``. Users and developers should source their own toolchain and point RiESCUE to it by making it avilable in the ``PATH`` or setting the ``RV_GCC`` and ``RV_OBJDUMP`` environment variables. See :doc:`/tutorials/install` for more information.


Adding binds
=============
The container has some default binds that map ``/usr/lib64`` to ``/shared``, but to add additional binds, developers can add ``--singularity-binds=/bar,/foo:/foobar`` to the command line to add to the container binds.


Directories are separated by commas.
Mapping a directory to itself can be done by only providing the directory name.
Mapping a directory to a different directory can be done by providing the directory name and the destination directory name separated by a colon.


.. note::
    Keep all the arguments in a single argument without spaces.

    This will also remove all arguments that start with ``--singularity`` from the command line.


``riscv-coretp``
~~~~~~~~~~~~~~~~
.. rubric:: Configuring ``coretp``

``coretp`` is an external dependency used by RiescueC TestPlan mode. It's not on `pip` yet, but is included in the `pyproject.toml`. By default this is included when installing using ``pip install -e .`` or using the singularity container.

.. note::

    For active development on ``coretp``, users should clone the repo and install it using ``pip install -e .`` or using the singularity container. Instructions can be found in the `riscv-coretp README <https://github.com/tenstorrent/riscv-coretp?tab=readme-ov-file#installation>`_.
