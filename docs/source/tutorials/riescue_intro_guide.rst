

Intro to RiESCUE, RiescueD, and RiescueC
===========================================

What's RiESCUE?
------------------
RiESCUE is an open-source Python library for generating RISC-V assembly tests. It compiles assembly tests into ELF binaries and uses instruction set simulators during generation to validate test correctness.

RiESCUE provides two main tools: **RiescueD** for writing directed tests with special assembly directives, and **RiescueC** for generating self-checking compliance tests.


RiESCUE Directed Testing Framework - **RiescueD**
-------------------------------------------------

Writing directed RISC-V assembly tests by hand can be time consuming, hard to maintain, and leads to static tests.
**RiescueD** speeds up the test development process by providing a framework for writing directed tests while controlling the environment, offering an extensible test harness, and providing constrained randomization of memory addresses and data values.


Users provide a directed test case written in assembly with Riescue Directives and a CPU configuration file detailing the memory map and ISA of the target system. **RiescueD** uses this configuration to setup the environment and the test harness. It compiles and runs the test on an Instruction Set Simulator to verify the test is valid.
This enables quick development of directed tests without having to write the test harness from scratch, manage the environment, generate the correct memory map, or randomize memory addresses and data values.

.. image:: /common/images/riescued_diagram.png


**RiescueD** provides a set of tools and directives to control the test scenario. This includes:

- randomized memory address and data values
- automatic page table generation
- multiple privilege modes (machine, supervisor, user)
- paging mode support (sv39, sv48, sv57)
- OS simulation with exception handling and scheduling
- multiple processor testing (MP)


**RiescueD** use cases:
~~~~~~~~~~~~~~~~~~~~~~~~~~

**RiescueD** excels as a directed test framework when used to bringup and verify new RISC-V features.
The utilities provided in **RiescueD** let test writers focus on writing test logic and not worry about the environment and test harness.
This allows for both quick bringup for new features and randomized test generation for regression testing.

**RiescueD** also excels as a framework to build complex test generators on top of.
With **RiescueD** handling the environment and test harness, test generators using the framework can focus on generating interesting stimulus instead of a complex environment and managing virtual memory.
Combined with support for multiprocessor testing (MP), this creates an extensible framework for generating complex tests for a range of RISC-V platforms.

For examples on how to write directed tests, see the :doc:`RiescueD User Guide <../user_guides/riescued_user_guide>`.



RiESCUE Compliance Testing - **RiescueC**
------------------------------------------------------------

**RiescueC** is a specialized test generator that generates self-checking compliance tests for RISC-V extensions. It provides **bringup**, **test plan**, and **compliance** modes for generating tests.

- **bringup** mode uses a CPU configuration file and a set of extensions and/or instructions to generate a self-checking test targeting instruction based compliance
- **test plan** mode uses a CPU configuration file and a  ``TestPlan`` object to generate architectural compliance tests for non-instruction based compliance
- **compliance** mode uses a CPU configuration file and an ``ISA`` string to generate a suite of self-checking tests.

More information and user guides for **RiescueC** will be added with the release of **RiescueC**.

**RiescueC** Use Cases:
~~~~~~~~~~~~~~~~~~~~~~~~~~

**RiescueC** excels for generating self-checking compliance tests targeting instruction and architectural compliance.
**bringup** mode allows for bringing up new extensions, while **test plan** mode allows for generating architectural compliance tests for non-instruction based compliance.
Both are useful for bringing up and verifying ISA compliance while developing RISC-V platforms.
**compliance** mode generates suites of tests useful for regression testing for mature platforms.


.. rubric:: Next Steps

See the :doc:`riescued/first_test` tutorial to get started on a basic directed test.