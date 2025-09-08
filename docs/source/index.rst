.. Public documentation main file

Riescue documentation
===================================================


.. toctree::
   :maxdepth: 2
   :caption: Contents:
   :hidden:

   /tutorials/index
   /user_guides/index
   /api/index



**RiESCUE** (pronounced *"rescue"*) is an open-source Python library for generating RISC-V tests.

RISC-V processor verification requires comprehensive testing of architectural features with complex scenarios involving virtual memory, privilege modes, and a variety of extensions.
**RiESCUE** provides the tools needed for quick bringup using directed testing and arhcitectural compliance verification.


RiESCUE consists of two main tools:

- :doc:`RiescueD <api/public/RiescueD>` **- Directed Test Framework**
- **RiescueC - Compliance Test Generator**


Getting Started
---------------

New to RiESCUE? Start with:

- :doc:`Getting Started Guide </tutorials/index>` - Set up RiESCUE, learn about RiESCUE's main features, and see some tutorials.
- :doc:`Riescue User Guides </user_guides/index>` - Learn more in-depth information on how to write directed tests and setup the test environment.

API Documentation
-----------------

RiESCUE provides both **public APIs** (stable, supported) and **internal APIs** (subject to change).
Use the :doc:`public APIs </api/public/index>` for production code to ensure compatibility across versions.
