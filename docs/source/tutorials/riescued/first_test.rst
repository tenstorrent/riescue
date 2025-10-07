First RiescueD Test
========================

This tutorial walks you through creating and running a simple directed test, along with some of the basic RiescueD concepts.

Prerequisites
-------------
- RiESCUE installed (:doc:`../install`)
- Basic RISC-V assembly knowledge

Running RiescueD
--------------------
RiescueD can be ran from the command line or as a Python library.

CLI Usage
~~~~~~~~~
The command line offers allows test writers to iteratively develop tests and run them on an ISS.

To see available options, run:

.. code-block:: bash

   python3 -m riescued --help

To generate and compile a test, pass in the test file using ```--testname``

.. code-block:: bash

   python3 -m riescued --testname tutorial_example_test.s


To run the test on an ISS, add the ``--run_iss`` flag. By default this runs on Whisper, but using ``--iss spike`` you can run the test on Spike.

.. code-block:: bash

   python3 -m riescued --testname tutorial_example_test.s --run_iss

Python Library Usage
~~~~~~~~~~~~~~~~~~~~
The python library can also be used to build and run tests.
Using it in python code allows for more complex test configuration and generation.

With a test ``tutorial_example_test.s`` , the following code will generate and compile the test.
.. code-block:: python

    from riescue import RiescueD
    from pathlib import Path

    rd = RiescueD(testfile=Path("tutorial_example_test.s"))
    featmgr = rd.configure(args=None)
    generator = rd.generate(featmgr)
    rd.build(featmgr, generator)


A Simple Test
--------------------------
We can start by creating a simple discrete test that does some basic arithmetic and a branch instruction.
Here we will be building a test case with a single discrete test, ``test01``.

Copy the example test file ``tutorial_example_test.s`` into your working directory:

.. literalinclude:: ../../../../riescue/dtest_framework/tests/tutorials/example_test.s

RiescueD Test Headers
~~~~~~~~~~~~~~~~~~~~~
At the top of the file we have some test headers that begin with ``;#test.``.
These headers are key value pairs that can be used to configure the test.


``;#test.arch``: Selects the target architecture as ``rv64`` configuring it for the RV64I ISA.

``;#test.priv``: Selects the privilege level as ``machine`` configuring it for the Machine mode. This is the privilege that the discrete tests will run in by default.

``;#test.env``: Selects the test environment as ``bare_metal`` configuring it for the bare metal environment, not using the hypervisor.



RiescueD Test Structure
~~~~~~~~~~~~~~~~~~~~~~~~~

The example test contains a few required labels to test code correctly:

.. code-block:: asm

  test_setup:

Executed before each test, exactly once. This is required

.. code-block:: asm

  test_cleanup:

This is a required lables  executed after all tests are run, exactly once.

.. code-block:: asm

  j failed

This indicates a test failed and starts the end of test sequence.

.. code-block:: asm

  j passed

This indicates a test passed or some test setup code has finished. It resumes control to the test harness.


RiescueD Directives
~~~~~~~~~~~~~~~~~~~
The test file uses several RiescueD Directives. These are comments in the code RiescueD parses and uses to configure the test.
These start with ``;#`` and handle randomizing variables, registering test cases, and managing the test environment.

The directives in this test are:


.. code-block:: asm

  ;#random_data(name=test_data, type=bits32, and_mask=0xfffffff0)

This directive generates random test data as a symbol in the test. The compiler will replace the symbol with a random value.



.. code-block:: asm

  ;#discrete_test(test=test01)

This marks individual test cases. The argument ``test=test01`` is the label of the test case to run.


Run RiescueD and build the test
-----------------------------------------

.. code-block:: bash

  python3 -m riescued --testname tutorial_example_test.s


This will generate the test harness, linker script, and compile the test into an ELF binary.

Adding ``--run_iss`` will also run the test on an Instruction Set Simulator to verify the test is valid.


Examining the Output
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In your working directory, you should see your compiled ELF ``tutorial_example_test`` along with some test harness ``.inc``, a linker script, and a disassembly file.

In the disassembly file  (``tutorial_example_test.dis``) we can see the discrete test was compiled with the test harness in the subroutine ``test01``:

.. code-block::

    Disassembly of section .code:

    0000000080010122 <test_setup>:
        80010122:	0300006f          	j	80010152 <passed>

    0000000080010126 <test01>:
        80010126:	008722b7          	lui	x5,0x872
        8001012a:	8602829b          	addw	x5,x5,-1952 # 871860 <test_data>
        8001012e:	00038337          	lui	x6,0x38
        80010132:	ab73031b          	addw	x6,x6,-1353 # 37ab7 <XLEN+0x37a77>
        80010136:	00e31313          	sll	x6,x6,0xe
        8001013a:	eef30313          	add	x6,x6,-273
        8001013e:	006283b3          	add	x7,x5,x6
        80010142:	00039463          	bnez	x7,8001014a <test01_pass>
        80010146:	0240006f          	j	8001016a <failed>



Next Steps
----------
Now that you've seen a basic test, you can learn about managing memory and test configurations:

- How to randomize data and memory in :doc:`randomization`
- How to configure page mapping in :doc:`virtual_memory`
- Learn about :doc:`../cpu_configuration`
- Explore :doc:`randomization`



For complete documentation of all available directives, see the :doc:`RiESCUE Directives Reference <../../reference/riescue_test_file/directives_reference>` and :doc:`Test Headers Reference <../../reference/riescue_test_file/test_headers_reference>`.
