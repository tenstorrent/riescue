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

The main features of RiescueD are OS code simulation, random address generation, memory management, and page table generation, providing a randomized environment for privilege modes, paging modes, and virtualization modes (bare metal and virtualized).

### Features
At its core, `RiescueD` provides the following key features:

1. **Random Data Generation
2. **Random Address Generation
3. **Random Pagetable Generation
4. **OS Code**: Pseudo OS for scheduling, exception handling, and pass/fail conditions.
5. **Page Tables**: Generates page tables for memory management testing.
6. **Randomized Privilege Mode**: Randomized Privilege Mode: Tests varying privilege levels (supports running tests in machine|supervisor|user modes)
7. **Randomized Paging Mode**: Supports running tests in sv39/sv48/sv57/bare.
8. **Randomized Virtualized Mode**: Run tests on bare metal or virtualized guest
9. **RISC-V Feature management**: RVA23 extension and feature management
10. **RISC-V CSR Manager**: Provides definition of CSRs through APIs


### Workflow
The test generation flow compiles an ELF binary from a user-written assembly .s file that uses Riescue Directives for memory and environment randomization. The workflow for testing writing is:

1. Write `.s` file using assembly with Riescue Directives
2. Provide system memory map and configuration in cpuconfig json file
3. Run RiescueD to produce `.S`, `.ld` Linker Script, and ELF binary. RiescueD runs the test on compiler (gcc/llvm or of your choice)

#### How to Write a RiescueD Test
Coming Soon!
This needs to include a `test.s`, `config.json`, and a command-line example.

### Use Cases
#### Quicker and Comprehensive Assembly Test Case Writing
`RiescueD` provides a framework to start writing tests faster and cover the scenario in different environments automatically. It allows users to quickly come up with scenarios for test cases where RiescueD can provide:

1. Random addresses (for load, store etc instructions)
2. Random data (with `RiescueD Directives`)
3. Random addresses (with `RiescueD Directives`), and
4. Page table generation

This allows for a quick bring-up for different instructions and features while constraining different test environments.

#### Enable Faster Random Test Generator Development
The library part of the RiescueD provides functionality of random address generation, pagetable generation, exception handling, environment constraints which handles a lot of heavy lifting of random test generation. RiescueC is a test generator that makes use RiescueD library for its test generation. Similarly, other test generators could be written using RiescueD APIs to speed up the development of a new test generator.



### Example `test.s`
Below is the minimum code needed to generate a RiescueD test case. A full sample RiescueD test could found here which showcases various features of RiescueD.
```s
;#test.name       sample_test
;#test.author     name@email.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.category   arch
;#test.class      vector
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       vectors vector_ld_st
;#test.summary
;#test.summary    This section is used for documenting of description of
;#test.summary    overall intention of the test and what each individual
;#test.summary    discrete_test(s) are supposed to verify
;#test.summary
;#test.summary    test01: sample test 1
;#test.summary
;#test.summary    test02: sample test 2
;#test.summary
;#test.summary


.section .code, "ax"
test_setup:
#####################
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################

j passed


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    nop
    beq x0, t0, failed

    j passed
```

### Usage

#### Basic Command Line Interface
```bash
riescue_d.py [options] <test_file.s>
```

#### Required Arguments
- `<test_file.s>`: The assembly test file containing RiescueD directives

#### Common Options
1. **Test Environment Configuration**
   ```bash
   --paging_mode <mode>      # Set paging mode (sv39/sv48/sv57/disable/any)
   --privilege_mode <mode>   # Set privilege mode (machine/supervisor/user/any)
   --test_env <env>         # Set test environment (bare_metal/virtualized)
   --cpuconfig <file>       # Path to CPU feature configuration file
   ```

2. **Output Control**
   ```bash
   --output_file <name>     # Specify output filename
   --output_format <fmt>    # Set output format (all/s/ld/dis/log)
   --run_dir <dir>         # Set run directory (default: ./)
   ```

3. **Test Generation Options**
   ```bash
   --seed <value>          # Set random seed for reproducibility
   --repeat_runtime <n>    # Run each discrete test n times
   --single_assembly_file  # Write all assembly to a single file
   --force_alignment      # Force byte alignment for data and code
   ```

#### Example Usage

1. **Basic Test Generation**
   ```bash
   riescue_d.py test.s --cpuconfig cpu_config.json
   ```

2. **Test with Specific Environment**
   ```bash
   riescue_d.py test.s \
     --paging_mode sv39 \
     --privilege_mode machine \
     --test_env bare_metal \
     --cpuconfig cpu_config.json
   ```

3. **Reproducible Test Generation**
   ```bash
   riescue_d.py test.s \
     --seed 12345 \
     --repeat_runtime 3 \
     --cpuconfig cpu_config.json
   ```

4. **Advanced Configuration**
   ```bash
   riescue_d.py test.s \
     --output_file custom_test \
     --output_format all \
     --run_dir ./test_output \
     --single_assembly_file \
     --force_alignment \
     --cpuconfig cpu_config.json
   ```

#### CPU Configuration File
The CPU configuration file (`cpu_config.json`) specifies the system's memory map and feature support. Example structure:
```json
{
    "memory_map": {
        "ram": {
            "start": "0x80000000",
            "size": "0x10000000"
        }
    },
    "features": {
        "ext_v": true,
        "ext_fp": false
    }
}
```

#### Output Files
RiescueD generates several output files:
- `.s`: Final assembly file
- `.ld`: Linker script
- `.dis`: Disassembly file
- `.log`: Execution log
- ELF binary

#### Best Practices
1. Always provide a CPU configuration file for proper memory mapping
2. Use `--seed` for reproducible test generation
3. Use `--single_assembly_file` for simpler test management
4. Enable appropriate feature flags for your target architecture
5. Use `--repeat_runtime` for stress testing

## RiescueC
COMING SOON!

`RiescueC` is a test generator specifically designed to generate test suitable for RISC-V compliance. These tests are not sufficient for verification, but could provide basic tests that can be used for proving the RISC-V specification compliance. We currently support these extensions: rv64imafcdhv_zfh_zvfh_zba_zbb_zbs_zfbfmin_zvbb_zbc_zvfbfmin

### Key Features

1. **Extension Support**
   - Comprehensive support for RISC-V base extensions (I, M, A, F, C, D)
   - Advanced support for vector extensions (V, Zv*)
   - Bit manipulation extensions (Zba, Zbb, Zbs)
   - Floating-point extensions (Zfh, Zvfh, Zfbfmin)
   - Customizable extension configuration through JSON files

2. **Test Generation Capabilities**
   - Self-checking test generation
   - Instruction-level test customization
   - Group-based test organization
   - Support for different privilege modes
   - Configurable test constraints

3. **Configuration System**
   - JSON-based configuration for extension support
   - Fine-grained control over included/excluded instructions
   - Group-based instruction organization
   - Architecture-specific settings (rv32/rv64)

### Usage

1. **Basic Configuration**
   ```json
   {
       "arch": "rv64",
       "include_extensions": ["i_ext"],
       "include_groups": [],
       "include_instrs": [],
       "exclude_groups": [],
       "exclude_instrs": ["wfi", "ebreak", "mret", "sret", "ecall", "fence", "fence.i", "c.ebreak"]
   }
   ```

2. **Running Tests**
   ```bash
   ./infra/container-run "./riescue_c.py --json <config_file>"
   ```

### Extension System

RiescueC organizes instructions into a hierarchical structure:
- **Extensions**: Correspond to RISC-V extensions (e.g., V, M)
- **Groups**: Logical groupings of related instructions within an extension
- **Instructions**: Individual RISC-V instructions

### Customization

1. **Instruction Selection**
   - Include/exclude specific instructions
   - Group-based filtering
   - Extension-level control

2. **Test Constraints**
   - Per-instruction constraints
   - Group-level constraints
   - Extension-specific settings

### Extensibility

RiescueC is designed to be easily extended:
1. **New Extensions**
   - Add new extension definitions
   - Define instruction groups
   - Specify instruction constraints

2. **Custom Instructions**
   - Support for custom instruction sets
   - Integration with existing extensions
   - Custom test generation rules

### Integration

The tool can be integrated into existing verification environments:
- Command-line interface for automation
- JSON-based configuration for CI/CD integration
- Containerized execution for consistent environments


# Open Source Roadmap
This repository is in the process of being open sourced. The work can be summarized below:

![Riescue Open Source Roadmap](docs/images/Roadmap.png "Roadmap")

The main milestones for completely open sourcing this repository are to:
- Provide API Documentation for RiescueD
- Open Source the Compliance Suite tools - RiescueC
- Open source the Compliance Test Generation tools - CTK
- Installable python library

# Installation
## From git
To install directly
```
pip install git+git@github.com:tenstorrent/riescue.git#egg=riescue
```

This will install the command line scripts `riescued`, along with making the `riescue` python package available for importing.

## From `PyPi`
Coming Soon!

## Requirements
### Singularity / Apptainer
This repo currently uses a `singularity` container flow to manage the environment. All dependencies can be found listed in the [Container.def](infra/Container.def) file.

This will be changed in the future to use the python `setuptools` to source python dependencies.

### Toolchains
RiescueD uses the riscv-gnu-toolchain to assemble, compile, and disassemble ELF tests. Toolchain paths can be passed as a command line flag, set as an environment variable, or added to the `PATH`.
- `riscv64-unknown-elf-gcc` is the default executable used for assembling and compiling
  - Searches for a `--compiler_path`, followed by the environment variable `RV_GCC`, then `riscv64-unknown-elf-gcc` in the `PATH`
- `riscv64-unknown-elf-objdump` is the default executable used for disassembling
  - Searches for a `--disassembler_path`, followed by the environment variable `RV_OBJDUMP`, then `riscv64-unknown-elf-objdump` in the `PATH`



### Simulators
Riescue invokes the following Instruction Set Simulators:
- `whisper` [whisper GitHub](https://github.com/tenstorrent/whisper)
  - `whisper` is a git submodule and can be built in the container using `./infra/build_whisper.sh`
  - External whisper binaries can be passed in using the environment variable `WHISPER_PATH` or the command line switch `--whisper_path`
- `spike` [riscv-isa-sim GitHub](https://github.com/riscv-software-src/riscv-isa-sim)
  - `spike` is installed normally in the container flow.
  - External spike binaries can be passed in using the environment variable `SPIKE_PATH` or the command line switch `--spike_path`
Like toolchains, simulators can be set with a command line switch, environment variable, or added to the `PATH`.


# Developing and Contributing
The main dependency needed for developing is singularity or apptainer. See the [Contributing page](.github/CONTRIBUTING.md) for information on setting up a developer environment.
