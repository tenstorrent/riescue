RiescueC Framework Overview
===========================

RiescueC is a comprehensive RISC-V compliance test generation framework designed to create self-checking tests for various RISC-V instruction set extensions. The framework operates through multiple sophisticated modules to generate, execute, and validate RISC-V assembly tests.

Core Architecture
=================

**Main Entry Point**: ``RiescueC`` class in ``riescuec.py`` serves as the top-level orchestrator with three primary execution modes:

- **Compliance Mode**: Full test generation and validation pipeline
- **Test Plan Mode**: Specialized test generation using test plans
- **Microprobe Mode**: (Not yet implemented)

**Resource Management**: Central ``Resource`` class manages shared state, configuration, and instruction tracking across all modules.

Beginner Features
=================

Basic Test Generation
---------------------

The framework accepts JSON configuration files specifying:

- Instruction extensions to include/exclude (``I``, ``M``, ``F``, ``D``, ``V``, etc.)
- Specific instruction groups or individual instructions
- Test parameters and constraints

Simple Workflow
---------------

1. **Configuration**: Load test specification from JSON file
2. **Generation**: Create randomized instruction sequences
3. **Execution**: Run tests on instruction set simulators
4. **Validation**: Compare results between different simulators

Command Line Interface
----------------------

Extensive command-line options via ``cmdline.json`` including:

- Test file specification (``--json``)
- Output formatting (``--output_file``)
- Configuration overrides (``--user_config``, ``--default_config``)
- Execution modes (``--mode``) (bringup, test_plan, compliance)

Advanced Features
=================

Multi-Pass Test Generation
---------------------------

**Two-Pass Architecture**:

- **Pass 1**: Initial test generation and execution to gather runtime information
- **Pass 2**: Enhanced test generation using Pass 1 results for improved coverage

Sophisticated Instruction Management
------------------------------------

**InstrGenerator**: Handles complex instruction generation with:

- Configuration-driven randomization
- Repeat count management
- Line count tracking for test size control
- Shuffled instruction ordering to avoid bias

**InstrBuilder**: Creates instruction class templates from instruction records

**InstrOrganizer**: Performs intelligent shuffling of instruction sequences

Advanced Configuration System
------------------------------

**Multi-Level Configuration**:

- Default configurations for standard setups
- User-defined overrides for customization
- Floating-point specific configurations
- Extension-specific parameter files

Simulator Integration
---------------------

**Dual ISS Support**:

- **Whisper**: Primary instruction set simulator
- **Spike**: Alternative simulator for cross-validation
- **Comparator**: Automated result comparison between simulators

Test Plan Integration
---------------------

**TestPlanRunner**: Advanced test generation using structured test plans:

- Memory management tests (paging, SVADU, SINVAL)
- Configurable test environments
- Randomized test scenario generation

Resource Optimization
----------------------

**Smart Resource Management**:

- Instruction line counting to prevent oversized tests
- Early bailout mechanisms for efficiency
- Configurable maximum instructions per file
- Memory-conscious instruction tracking

Extensibility Framework
-----------------------

**Modular Design**:

- Plugin-based extension support
- Configurable parser extensions
- Flexible toolchain integration (Compiler, Disassembler, Spike, Whisper)

Technical Implementation Details
================================

Core Modules
------------

- **TestGenerator**: Manages multi-pass test creation with header generation and state tracking
- **Runner**: Wraps RiescueD framework for test execution with experimental configuration support
- **Comparator**: Performs sophisticated log comparison between different ISS runs

Configuration Management
-------------------------

The framework uses a hierarchical configuration system with JSON files for:

- Architecture-specific defaults (``rv64_IMFV.json``)
- Extension-specific parameters (``rv_d_f_zfh.json``)
- User customizations and overrides
- Floating-point instruction configurations

Output Formats
--------------

Supports multiple output formats including assembly (``.s``), disassembly (``.dis``), logs (``.log``), and preprocessed assembly (``.S``).

The framework provides both beginner-friendly JSON-based configuration and advanced programmatic control for sophisticated test generation scenarios, making it suitable for both basic compliance testing and complex verification workflows.