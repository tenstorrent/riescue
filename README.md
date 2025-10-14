- [RiESCUE](#riescue)
- [Open Source Roadmap](#open-source-roadmap)
- [Installation and Usage](#installation-and-usage)
- [Contributing](#developing-and-contributing)

---

**[Read the documentation on docs.tenstorrent.com](https://docs.tenstorrent.com/riescue/)**


# RiESCUE
RiESCUE provides a Python library and command-line tools for generating, building, and compiling RISC-V test ELF files.
* `RiescueD` - RiESCUE Directed Test Framework
* `RiescueC` - RiESCUE Compliance Test Generator
* `CTK` - RiESCUE Compliance Test Kit

Other Riescue projects include:
* `CoreArchCoverage` - RISC-V ISA Coverage from ISS (Separate Repository Available in the Future)
* `coretp` - RISC-V Core Test Plan - see [riscv-coretp GitHub](https://github.com/tenstorrent/riscv-coretp)


### 1. RiescueD - Directed Test Framework
RiescueD is a Python library and command-line tool (`riescued`) for creating directed test cases. It compiles assembly tests with pre-processor directives into ELF executables, provides a runtime environment, and simulates tests on an instruction set simulator (ISS).

Features include:
- OS code simulation
- Random address generation
- Memory management
- Page table generation
- Support for various privilege modes, paging modes, and virtualization modes

To get started writing RiescueD direct tests, check out the [RiescueD User Guide](https://docs.tenstorrent.com/riescue/user_guides/riescued_user_guide.html). Source code is found in the `riescue/dtest_framework` directory.


### 2. RiescueC - Compliance Test Generator
RiescueC is a Python library and command-line tool (`riescuec`) that consists of a couple command line generation tools:

#### Datapath Compliance Testing - `bringup` mode
`BringupMode` or `riescuec --mode bringup` is the default RiescueC mode targeting datapath instruction compliance. It generates directed tests that:
- Multiple RISC-V extensions
  * Standard extensions (M, F, D, V, A, C)
  * Bit manipulation extensions (Zba, Zbb, Zbc, Zbs, Zb)
  * Half-precision floating point (Zfh, Zfbfmin)
- Self-checking test generation
- Configurable test constraints
- Comprehensive extension support

[See the RiescueC documentation on docs.tenstorrent.com](https://docs.tenstorrent.com/riescue/tutorials/riescuec/riescue_c_brief.html) to get started with `bringup` mode. The source code can be found in the `riescue/compliance` directory.


#### Privileged Testing - `tp` mode
Test Plan or `riescuec --mode tp` mode generates directed tests using `TestPlan`s described in [riscv-coretp](https://github.com/tenstorrent/riscv-coretp).

`TestScenarios` provide a set of python data structures describing sequences of instructions and environmental configuration to test architectural and privilege scenarios. RiescueC consumes these scenarios and generates test ELFs using the underlying `RiescueD` framework.

More documentation and information to come in future updates



### 3. CTK - Compliance Test Kit
High-level tool used to generates a directory of tests given an ISA string (e.g. "rv64imfv"). Tests are ran using RiescueC to generate self-checking tests for a range of extensions.

- See the [CTK Guide on docs.tenstorrent.com](https://docs.tenstorrent.com/riescue/tutorials/ctk/ctk_tutorial.html) for a tutorial on running `ctk`.
- Just looking to generate a single extension of tests? See the User Guide on [Generating a Vector Test Kit on docs.tenstorrent.com](https://docs.tenstorrent.com/riescue/user_guides/ctk/vector_test_kit.html) to see how a test kit can be generated for just vector tests.


## Installation and Usage
For info on installing dependencies, see the [Installation Guide on docs.tenstorrent.com](https://docs.tenstorrent.com/riescue/tutorials/install.html)

### Quick Install


```
python3 -m pip install git+https://github.com/tenstorrent/riescue.git
```

This installs the command line scripts `riescued`, along with making the `riescue` Python package available for importing. This doesn't source some of the non-python requirements for Riescue.

### Requirements

- RISC-V GNU Toolchain - [riscv-gnu-toolchain GitHub](https://github.com/riscv-collab/riscv-gnu-toolchain)
- `whisper` - [whisper GitHub](https://github.com/tenstorrent/whisper)
- `spike` - [riscv-isa-sim GitHub](https://github.com/riscv-software-src/riscv-isa-sim)
- `coretp` Python library - [riscv-coretp GitHub](https://github.com/tenstorrent/riscv-coretp)


## Getting Started
See the [RiescueD Getting Started Guide](https://docs.tenstorrent.com/riescue/tutorials/index.html) for more information on setting up RiescueD and running tests.


---
# Open Source Roadmap
We are excited to announce that we will be open-sourcing a suite of tools under the RiESCUE umbrella.
<details>
Click here to see a list of tools that we plan to release in the coming months.
<summary>
</summary>

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
</details>



# Developing and Contributing
To develop or contribute, you need [Apptainer (formerly Singularity)](https://apptainer.org/).

See the [Contributing page](.github/CONTRIBUTING.md) for information on setting up a developer environment.
