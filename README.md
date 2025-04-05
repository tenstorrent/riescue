- [RiESCUE](#riescue)
  * [RiescueD](#riescued)
    + [Features](#features)
    + [Workflow](#workflow)
      - [How to Write a RiescueD Test](#how-to-write-a-riescued-test)
    + [Use Cases](#use-cases)
      - [Quicker and Comprehensive Assembly Test Case Writing](#quicker-and-comprehensive-assembly-test-case-writing)
      - [Enable Faster Random Test Generator Development](#enable-faster-random-test-generator-development)
    + [Example `test.s`](#example-tests)
- [Open Source Roadmap](#open-source-roadmap)
- [Installation and Usage](#installation-and-usage)
- [Contributing](#contributing)



# RiESCUE
RISC-V Directed Test Framework and Compliance Suite, RiESCUE

RiESCUE provides a suite of python scripts and libraries for generating RISC-V tests:
* `RiescueD` - RiESCUE Directed Test Framework
* `RiescueC` - RiESCUE Compliance Test Generator 'RiescueC'


## RiescueD
`RiescueD` is a Python library serving as a Directed Test Framework, offering

1. A framework for writing directed tests in assembly
2. A comprehensive set of APIs for test generation

The main features of RiescueD are OS code simulation, random address generation, memory management and page table generation and provide randomized environment for privilege modes, paging modes, virtualization modes (bare metal and virualized)

### Features
At its core, `RiescueD` provides the following key features:

1. **OS Code**: Pseudo OS for scheduling, exception handling, and pass/fail conditions.
2. **Page Tables**: Generates page tables for memory management testing.
3. **Randomized Privilege Mode**: Randomized Privilege Mode: Tests varying privilege levels (supports running tests in machine|supervisor|user modes)
4. **Randomized Paging Mode**: Supports running tests in sv39/sv48/sv57/bare.
5. **Randomized Virtualized Mode**: Run tests on bare metal or virtualized guest
6. **RISC-V Feature management**: RVA23 extension and feature management
7. **RISC-V CSR Manager**: Provides definition of CSRs through APIs


### Workflow
The test generation flow compiles an ELF binary from a user-written assembly .s file that uses Riescue Directives for memory and environment randomization. The workflow for testing writing is:

1. Write `.s` file using assembly with Riescue Directives
2. Provide system memory map and configuration in cpuconfig json file
3. Run RiescueD to produce `.S`, `.ld` Linker Script, and ELF binary. RiescueD runs the test on compiler (gcc/llvm or of your choice)

#### How to Write a RiescueD Test
Coming Soon!
This needds to include a `test.s`, `config.json`, and a command line example.

### Use Cases
#### Quicker and Comprehensive Assembly Test Case Writing
`RiescueD`  provides a framework to start writing tests faster and cover the scenario in different environment automatically. It allow users to quickly come up with scenarios for test cases where RiescueD can provide:

1. Random addresses (for load, store etc instructions)
2. Random data (with `RiescueD Directives`)
3. Random addresses (with `RiescueD Directives`), and
4. Page table generation

This allow for a quick bring up for different instructions and features while constraining different test environments.

#### Enable Faster Random Test Generator Development
The library part of the RiescueD provides functionality of random address generation, pagetable generation, exception handling, environment constraints which handles a lot of heavy lifting of random test generation. RiescueC is a test generator that makes use RiescueD library for its test generation. Similarly, other test generators could be written using RiescueD APIs to speed up the development of a new test generator.



### Example `test.s`
Below is the minimum code needed to generate a RiescueD test case. A full sample RiescueD test could found here which showcases various features of RiescueD.
```s
.section .code, "ax"
test_setup:
    csrr t0, mstatus
;#discrete_test(test=test01)
test01:
    nop
    beq x0, t0, failed
    j passed
```


# Open Source Roadmap
This repository is in the process of being open sourced. The work can be summarized below:

![Riescue Open Source Roadmap](docs/images/Roadmap.png "Roadmap")

The main milestones for completely open sourcing this repository are to:
- Provide API Documentation for RiescueD
- Open Source the Compliance Suite tools - RiescueC
- Open source the Compliance Test Generation tools - CTK
- Installable python library


# Installation
## Installing from `PyPi`
Coming Soon!

## Installing Riescue as a python package
To install directly
```
pip install git+git@github.com:tenstorrent/riescue.git#egg=riescue
```

This will install the command line scripts `riescued`, along with making the `riescue` python package available for importing.

# Usage
## Running from container
Running interactively in the container ensures dependencies are sourced correctly.

## Running outside the container
ISS and python dependencies can be found in the [container file](infra/Container.def). Ensure all Python dependencies have been installed.

Non-Python dependencies can be passed through using the command line or by making the executables available in the PATH.
More details coming soon.


# Contributing
Please see the [Contributing](.github/CONTRIBUTING.md) guide for more info on contributing to this repository.