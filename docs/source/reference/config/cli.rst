Command Line Interface Reference
=================================

This page documents all command-line flags for RiESCUE tools. Each tool inherits flags from shared components (FeatMgr, Toolchain, Logger), so many flags are available across multiple tools.

RiescueD
---------

RiescueD CLI. Includes flags from FeatMgr, Logger, and Toolchain.

.. argparse:: riescue.RiescueD

RiescueC
---------

RiescueC CLI. Includes flags from Bringup mode, Test Plan mode, FeatMgr, Logger, and Toolchain.

.. argparse:: riescue.riescuec.RiescueC

CTK
----

CTK CLI. Includes flags from Bringup mode, Test Plan mode, FeatMgr, Logger, and individual tool paths.

.. argparse:: riescue.ctk.Ctk
