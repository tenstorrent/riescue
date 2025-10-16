.. Public documentation main file

Riescue documentation
===================================================


.. toctree::
   :maxdepth: 2
   :caption: Contents:
   :hidden:

   /tutorials/index
   /user_guides/index
   /reference/index



**RiESCUE** (pronounced *"rescue"*) is an open-source Python library for generating RISC-V tests.

RISC-V processor verification requires comprehensive testing of architectural features with complex scenarios involving virtual memory, privilege modes, and a variety of extensions.
**RiESCUE** provides the tools needed for quick bringup using directed testing and arhcitectural compliance verification.


RiESCUE consists of three main tools:

- :doc:`RiescueD <reference/python_api/RiescueD>` **- Directed Test Framework**
- :doc:`RiescueC <reference/python_api/RiescueC>` **- Compliance Test Generator**
- :doc:`CTK <reference/python_api/CTK>` **- Compliance Test Kit**


Getting Started
---------------

New to RiESCUE? Start with:

- :doc:`Getting Started Guide </tutorials/index>` - Set up RiESCUE, learn about RiESCUE's main features, and see some tutorials.
- :doc:`User Guides </user_guides/index>` - Learn how to accomplish tasks with RiESCUE through step-by-step workflows and best practices.

Want to get started generating tests? See  :doc:`/tutorials/riescuec/bringup_mode_tutorial` to start with the RiescueC Bringup Mode tutorial

Reference Documentation
------------------------

Complete reference material for all RiESCUE tools and configuration options.
The :doc:`reference documentation </reference/index>` includes Python APIs, test language syntax, and configuration schemas.
