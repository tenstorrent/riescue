- [Open Source Roadmap](#open-source-roadmap)
- [RiESCUE](#riescue)
- [Installation and Usage](#installation-and-usage)
- [Contributing](#developing-and-contributing)

---

**[Read the documentation on docs.tenstorrent.com](https://docs.tenstorrent.com/riescue/)**

---

# Open Source Roadmap
We are excited to announce that we will be open-sourcing a suite of tools under the RiESCUE umbrella. Below is a list of tools that we plan to release in the coming months.


## RiescueD - Directed Test Framework
A powerful framework for writing directed tests in RISC-V assembly. It provides a library for test generator development, with features such as:
 - OS code simulation
 - Random address generation
 - Memory management
 - Page table generation
 - Exception handling and self-checking
 - Hypervisor
 - Multi-processer support
 - Support for various privilege modes, paging modes, and virtualization modes

## RiescueC - Compliance Test Generator
A specialized test generator for RISC-V compliance testing that supports:
- Multiple RISC-V extensions (I, M, A, F, C, D, V, etc.)
- Self-checking test generation
- Configurable test constraints
- Comprehensive RISC-V RVA23 extensions support

## CTK - Compliance Test Kit
Tool for generating a suite of architectural compliance tests using the RiescueC framework (with wrapper around RiescueC)
- Allows configuration of test environments: privilege modes, paging, virtualization
- Supports a variety of RISC-V base ISAs, privilege specifications, and extensions
- Supports numerous memory maps and system configurations for additional flexibility
- Used for generating the [tenstorrent/tt_riscv_arch_tests](https://github.com/tenstorrent/riscv_arch_tests) self-checking architectural test suite

## CoreArchCoverage
Framework for auto-generating and collecting architectural (ISA) coverage from reference models (ISS)
- Can generate SystemVerilog coverage packages, while allowing user-defined, case-specific coverages
- Supports ISS-only sampling for timely feedback from regression tests
- Provides support for coverage collection in co-simulation scenarios
- Core archcoverage white paper [link](https://github.com/tenstorrent/riescue/releases/download/v0.2.5/tenstorrent-Architectural-Coverage-Framework.pdf) (PDF Warning!)

## Core Test Plan
Extensible framework for defining, managing, and consuming RISC-V architectural compliance test plans
* Generates both human-readable documentation and machine-parseable input for compliance test generation
* Provides a common format and APIs for:
  * Writing RISC-V architectural test plans and test scenarios
  * Parsing and transforming scenarios into structured data for downstream tools
  * Rendering test plans as documentation


# RiESCUE
RISC-V Directed Test Framework and Compliance Suite, RiESCUE

RiESCUE provides a suite of Python scripts and libraries for generating RISC-V tests:
* `RiescueD` - RiESCUE Directed Test Framework
* `RiescueC` - RiESCUE Compliance Test Generator 'RiescueC'

Other Riescue projects include:
* `CTK` - Compliance Test Kit
* `CoreArchCoverage` - RISC-V ISA Coverage from ISS
* `coretp` - RISC-V Core Test Plan


### 1. RiescueD - Directed Test Framework
A powerful Python library for writing directed tests in assembly with features such as:
- OS code simulation
- Random address generation
- Memory management
- Page table generation
- Support for various privilege modes, paging modes, and virtualization modes

[Learn more about RiescueD](riescue/dtest_framework/README.md)

[Detailed RiescueD User Guide](https://docs.tenstorrent.com/riescue/user_guides/riescued_tutorial.html)


### 2. RiescueC - Compliance Test Generator
A specialized test generator for RISC-V compliance testing that supports:
- Multiple RISC-V extensions (I, M, A, F, C, D, V, etc.)
- Self-checking test generation
- Configurable test constraints
- Comprehensive extension support

[Learn more about RiescueC](riescue/compliance/README.md) (Link works when RiescueC is open-sourced)


# Installation and Usage
## From git
To install directly
```
python3 -m pip install git+https://github.com/tenstorrent/riescue.git
```

This installs the command line scripts `riescued`, along with making the `riescue` Python package available for importing.

## Requirements
### Singularity / Apptainer
This repo currently uses a `singularity` container flow to manage the environment. All dependencies are listed in the [Container.def](infra/Container.def) file.

In the future, the Python `setuptools` script will source Python dependencies.

### Toolchains
RiescueD uses the riscv-gnu-toolchain to assemble, compile, and disassemble ELF tests. Toolchain paths can be passed as a command line flag, set as an environment variable, or added to the `PATH`.
- `riscv64-unknown-elf-gcc` is the default executable used for assembling and compiling
  - Searches for `--compiler_path`, followed by the environment variable `RV_GCC`, then `riscv64-unknown-elf-gcc` in the `PATH`
- `riscv64-unknown-elf-objdump` is the default executable used for disassembling
  - Searches for `--disassembler_path`, followed by the environment variable `RV_OBJDUMP`, then `riscv64-unknown-elf-objdump` in the `PATH`

### Simulators
Riescue invokes the following Instruction Set Simulators:
- `whisper` [whisper GitHub](https://github.com/tenstorrent/whisper)
  - `whisper` is a git submodule and can be built in the container using `./infra/build_whisper.sh`
  - External whisper binaries can be passed in using the environment variable `WHISPER_PATH` or the command line switch `--whisper_path`
- `spike` [riscv-isa-sim GitHub](https://github.com/riscv-software-src/riscv-isa-sim)
  - `spike` is installed normally in the container flow.
  - External spike binaries can be passed in using the environment variable `SPIKE_PATH` or the command line switch `--spike_path`

Like toolchains, simulators can be set with a command line switch, environment variable, or added to the `PATH`.

### `riscv-coretp`
RiescueC uses the [`riscv-coretp`](https://github.com/tenstorrent/riscv-coretp) project, which contains python data structures, test plans, and a basic RISC-V instruction API for generating instructions. It's an external dependncy that isn't on `pip` yet, but is included in the `pyproject.toml`. For more information see the [Install and Setup guide](https://docs.tenstorrent.com/riescue/tutorials/install.html) on the docs.


## Getting Started
See the [RiescueD Getting Started Guide](https://docs.tenstorrent.com/riescue/tutorials/index.html) for more information on setting up RiescueD and running tests.

# Developing and Contributing
To develop or contribute, you need [Apptainer (formerly Singularity)](https://apptainer.org/).

See the [Contributing page](.github/CONTRIBUTING.md) for information on setting up a developer environment.
