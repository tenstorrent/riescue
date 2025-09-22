# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Transformer logic for test plan.
The idea here is to have ``TestSteps`` act as a high-level IR that contains just enough information to describe a plan,
and have ``Instruction`` act as a low-level IR that contains the information to emit the assembly code.

This ``Transformer`` module will generate a list of ``Action`` medium-level IR objects from a list of ``TestStep`` high-level IR objects.
``Action`` objects can be expanded in place and emit ``Instruction`` objects. The general flow is:

- Lowering 1: ``TestStep`` -> ``Action``
- Expand: Complex ``Action`` objects are expanded into a flat list of ``Action`` objects
- Lowering 2: ``Action`` -> ``Instruction``
- Legalize: ``Instruction`` objects are legalized into a flat list of ``Instruction`` objects with all ``li`` and cast instructions
- Allocation: registers, symbols, and memory are allocated


Classes in this module should be focus on logic for transforming IR objects rather than capturing state of generated objects.
Should be stateless except for other 'stateless' classes that are used for generating all ``DiscreteTest`` objects.
E.g. ``RandNum``, ``InstructionCatalog``, ``MemoryRegistry``.

:class:`Transformer` Orchestrator, main interface
:class:`Elaborator` picks instructions, source of randomization
:class:`RegisterAllocator` Allocates registers, symbols
:class:`LabelFactory` creates labels for the test plan
"""

from .transformer import Transformer


__all__ = ["Transformer"]
