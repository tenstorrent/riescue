Test Plan Tutorial
==================

Test plans provide structured, scenario-based testing for complex RISC-V validation requirements.

Test Plan Overview
------------------

Test plans in RiESCUE-C enable sophisticated test generation beyond basic compliance testing through scenario-based testing with structured test scenarios, memory management testing for paging and SVADU/SINVAL instruction validation, context-aware generation that understands system state and dependencies, and advanced assertions with complex validation logic.

The framework supports memory hierarchy testing (virtual memory, caches, TLBs), system-level instruction validation, multi-step test scenarios with state dependencies, and configurable test environments and parameters.

Test Plan Architecture
----------------------

Test plans use the ``coretp`` (Core Test Plan) framework with these components:

**TestPlan**: Top-level test specification defining scenarios and objectives

**TestEnvCfg**: Environment configuration specifying:

* Memory layout and page sizes
* System register states
* Privilege levels and permissions

**TestScenario**: Individual test cases within a plan containing a sequence of test steps with metadata and environment configuration.

**TestStep**: Base class for test operations including ``Load``, ``Store``, ``Arithmetic``, ``Memory``, ``AssertEqual``, and ``CsrRead``/``CsrWrite`` operations that automatically track dependencies through object references.

Example Test Plan
---------------------

Test plans are defined using the ``coretp`` framework. Example memory paging test:

.. code-block:: python

   from coretp import TestPlan, TestEnvCfg, TestScenario
   from coretp.step import Load, Store, Memory, AssertEqual
   from coretp.rv_enums import PageSize, PrivilegeMode

   # Define test environment
   env_cfg = TestEnvCfg(
       page_sizes=[PageSize.SIZE_4K],
       priv_modes=[PrivilegeMode.S]
   )

   # Create memory and operations
   mem = Memory(size=0x10000)
   store_op = Store(memory=mem, offset=0x1000, value=0xDEADBEEF)
   load_op = Load(memory=mem, offset=0x1000)

   # Create test scenario
   scenario = TestScenario([
       mem,
       store_op,
       load_op,
       AssertEqual(src1=load_op, src2=0xDEADBEEF)
   ])

   # Combine into test plan
   test_plan = TestPlan(
       name="paging_validation",
       env_cfg=env_cfg,
       scenarios=[scenario]
   )

**Built-in Test Plans**

RiESCUE-C includes pre-defined test plans: ``paging_test_plan`` for virtual memory and page table validation, ``svadu_test_plan`` for SVADU (Sv32/39/48 Address Update) testing, and ``sinval_test_plan`` for supervisor invalidation instruction testing.

Test Plan Structure
-------------------

**Hierarchical Organization**

.. code-block:: text

   TestPlan
   ├── TestEnvCfg (environment configuration)
   ├── TestScenario 1
   │   └── TestStep sequence (Load, Store, Arithmetic, etc.)
   ├── TestScenario 2
   │   └── TestStep sequence
   └── TestScenario N

**Available TestStep Types**

The framework provides these concrete TestStep implementations: ``Load`` and ``Store`` for memory operations, ``Arithmetic`` for computational operations, ``Memory`` for memory allocation, ``AssertEqual`` and ``AssertNotEqual`` for validation, and ``CsrRead`` and ``CsrWrite`` for control and status register operations.

Test Execution Planning
-----------------------

**Running Test Plans**

Execute test plan mode using:

.. code-block:: bash

   riescuec --mode test_plan --seed 12345

**Test Generation Process**

The test generation follows this workflow: Test Plan Input uses ``coretp.TestPlan`` containing scenarios and environment configurations, Discrete Test Building creates ``DiscreteTest`` objects from scenarios using ``TestPlanFactory``, Environment Solving resolves ``TestEnv`` constraints using ``TestEnvSolver``, Elaboration fills in instruction details and resolves dependencies via ``Elaborator``, Register Allocation assigns registers using ``RegisterAllocator``, and Assembly Generation produces the final ``.s`` assembly file.


