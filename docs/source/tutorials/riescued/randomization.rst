Randomizing Data & Addresses
===============================

This tutorial walks you through adding randomization to your RiescueD tests, building from simple random values and random addresses.

Randomization helps find bugs that fixed test data might miss. Instead of always testing with the same values, you can generate thousands of different test cases automatically while keeping them reproducible for debugging.

Adding Some Random Data and Addresses
-------------------------------------

Let's start by adding a random value to a simple test. Create a file called ``random_tutorial.s``:

.. literalinclude:: ../../../../riescue/dtest_framework/tests/tutorials/randomization_test.s

We can run the test using:

.. code-block:: bash

   python3 -m riescued --testname random_tutorial.s --run_iss

Each time you run it, ``my_8_bit_value`` gets a different random value between 0-255. You can check the disassembly file to see what value was generated when no seed is specified.


``;#discrete_test`` - Multiple Discrete Tests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This test includes multiple discrete tests by repeating the ``;#discrete_test`` directive with the test label.
The order of the discrete tests is determined by the order of the ``;#discrete_test`` directives.

Additionally, the number of times the discrete test is executed is determined by the ``repeat_times`` parameter. More info on that can be found in the Configuration docs.


Test Configuration Headers
---------------------------

This example has introduced a new header:

``;#test.paging`` Header
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Here we are using the ``;#test.paging`` header to specify the paging mode for the test. ``disable`` means no paging is enabled for the test or addresses generated.


Random Data Generation
-----------------------

This uses the ``;#random_data`` directive to generate random data like the first example, but with additional constraints.


``;#random_data`` -  Generating an 8-bit Random Value
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#random_data(name=my_8_bit_value, type=bits8, and_mask=0xFF)

This is creating a random 8-bit value and storing it in the constant ``my_8_bit_value``. Looking at the additional parameters:
- The ``type=bits8`` is requesting an 8-bit random value. Any arbitrary bit width can be requested.
- ``and_mask=0xFF`` is a mask that will be applied to the random value to ensure it is in the range of 0-255. Without it generates a random value between 0-255.

.. note::
   The ``and_mask`` parameter is optional but ensures that random values are generated within the range of the mask.

``;#random_data`` -  Generating a 16-bit Random Value
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#random_data(name=my_16_bit_value, type=bits16, and_mask=0xFFFF)

This is creating a random 16-bit value and storing it in the constant ``my_16_bit_value``.
The ``0xFFFF`` is a mask that will be applied to the random value to ensure it generates a 16-bit value.


Random Memory Initialization
-----------------------------

The ``load_from_data_regions`` test uses the ``;#random_data`` directive to request an address in memory. The first one used is

``;#random_addr`` - Generating Random Addresses
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: asm

   ;#random_addr(name=aligned_addr, type=physical, size=0x1000, and_mask=0xFFFFFF00)

This generates a 4-KiB section of memory that is aligned to 256-byte boundaries.

Looking at the parameters:

- ``name=aligned_addr`` creates a symbol that holds the random address
- ``type=physical`` specifies this is a physical address (required parameter)
- ``size=0x1000`` controls the size of the memory region (4-KiB in this case)
- ``and_mask=0xFFFFFF00`` controls the alignment by masking the lower 8 bits to zero, creating 256-byte alignment

.. note::

   Currently the ``type`` parameter is required for ``;#random_addr``. ``physical`` should be used for physical addresses and platforms that don't support virtual memory.


Loading Data from a Data Section
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This tests loads ``data_region1`` and ``data_region2`` with the random values generated.
By loading the symbol matching the address of these sections, the values can be loaded and stored.


``;#init_memory`` - Initializing and Populating Memory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
To populate the memory with data, we can use the ``;#init_memory`` directive and add code or data to the section.

For example, to place a random 8-bit value in ``data_region1``, we can use the following code:
.. code-block:: asm

   ;#init_memory @data_region1
     .byte my_8_bit_number


This stores the data at the address of ``data_region1``.

Randomization with Seeds
-------------------------

Let's see how seeds work for reproducible testing. Run the same test multiple times:

.. code-block:: bash

   # Run 1 - note the seed in the output
   python3 -m riescued --testname random_tutorial.s

   # Run 2 - different random values
   python3 -m riescued --testname random_tutorial.s

   # Run 3 - reproduce Run 1 exactly (use seed from Run 1 output)
   python3 -m riescued --testname random_tutorial.s --seed 1234567890

The third run will generate identical random values to the first run, making debugging much easier. Without specifying a seed, a 2^32 bit seed is selected autoamtically.


For complete documentation of all available directives, see the :doc:`RiESCUE Directives Reference <../../reference/riescue_test_file/directives_reference>` and :doc:`Test Headers Reference <../../reference/riescue_test_file/test_headers_reference>`.


