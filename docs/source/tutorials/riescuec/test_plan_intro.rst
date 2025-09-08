Test Plan Tutorial
==================

Test plans provide structured, scenario-based testing for complex RISC-V validation requirements.

Test Plan Overview
------------------

Test plans in RiESCUE-C enable sophisticated test generation beyond basic compliance testing. They support:

* **Scenario-Based Testing**: Structured test scenarios with specific objectives
* **Memory Management Testing**: Paging, SVADU, SINVAL instruction validation
* **Context-Aware Generation**: Tests that understand system state and dependencies
* **Advanced Assertions**: Complex validation logic beyond simple result checking

Key capabilities:

* Memory hierarchy testing (virtual memory, caches, TLBs)
* System-level instruction validation
* Multi-step test scenarios with state dependencies
* Configurable test environments and parameters

Test Plan Architecture
----------------------

Test plans use the ``coretp`` (Core Test Plan) framework with these components:

**TestPlan**: Top-level test specification defining scenarios and objectives

**TestEnvCfg**: Environment configuration specifying:

* Memory layout and page sizes
* System register states
* Privilege levels and permissions

**TestScenario**: Individual test cases within a plan containing:

* Setup actions (memory initialization, register setup)
* Test actions (instruction execution, memory operations)
* Validation assertions (expected results, state checks)

**Actions**: Atomic test operations including:

* ``Load``/``Store``: Memory operations with address generation
* ``Arithmetic``: Computational operations with result validation
* ``Memory``: Memory management operations (paging, TLB)
* ``CSR``: Control and status register operations

Creating a Test Plan
---------------------

Test plans are defined using the ``coretp`` framework. Example memory paging test:

.. code-block:: python

   from coretp import TestPlan, TestEnvCfg, TestScenario
   from coretp.step import Load, Store, Memory
   from coretp.rv_enums import PageSize

   # Define test environment
   env_cfg = TestEnvCfg(
       page_size=PageSize.KB_4,
       virtual_memory=True,
       privilege_level="S"
   )

   # Create test scenario
   scenario = TestScenario([
       Memory.setup_page_table(),
       Store.to_virtual_address(0x1000, 0xDEADBEEF),
       Load.from_virtual_address(0x1000),
       Assert.equal(loaded_value, 0xDEADBEEF)
   ])

   # Combine into test plan
   test_plan = TestPlan(
       name="paging_validation",
       env_cfg=env_cfg,
       scenarios=[scenario]
   )

**Built-in Test Plans**

RiESCUE-C includes pre-defined test plans:

* ``paging_test_plan``: Virtual memory and page table validation
* ``svadu_test_plan``: SVADU (Sv32/39/48 Address Update) testing
* ``sinval_test_plan``: Supervisor invalidation instruction testing

Test Plan Structure
-------------------

**Hierarchical Organization**

.. code-block:: text

   TestPlan
   ├── TestEnvCfg (environment configuration)
   ├── TestScenario 1
   │   ├── Setup Actions
   │   ├── Test Actions
   │   └── Validation Assertions
   ├── TestScenario 2
   │   └── ...
   └── TestScenario N

**Action Types**

1. **Setup Actions**: Initialize test environment

   * Memory allocation and initialization
   * Register value setup
   * System state configuration

2. **Test Actions**: Execute test operations

   * Instruction execution with specific operands
   * Memory operations with address patterns
   * System calls and privilege transitions

3. **Validation Actions**: Verify expected behavior

   * Result value assertions
   * Memory state verification
   * System register validation

Test Execution Planning
-----------------------

**Running Test Plans**

Execute test plan mode using:

.. code-block:: bash

   riescue_c.py --mode test_plan --seed 12345

**Test Generation Process**

1. **Plan Selection**: Choose appropriate test plan (currently hardcoded to paging)
2. **Context Creation**: Initialize test environment and memory state
3. **Action Expansion**: Convert high-level actions to RISC-V instructions
4. **Code Generation**: Generate assembly test with self-checking assertions
5. **Execution**: Run generated test on instruction set simulator


