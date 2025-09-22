# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Optional, Any

from coretp import StepIR
from coretp.isa import Instruction

from riescue.compliance.test_plan.context import LoweringContext


class Action(ABC):
    """
    Base Action class. Intermediate Representation (IR) of test code. Defines how instructions are selected.
    Optionally into more `class:Action` objects.
    Can be built from a ``StepIR`` object or directly.

    `method:pick_instruction()` called to generate ``Instruction`` IR.
    `method:expand()` called to expand into a list of other Action objects.

    .. note::

        ``register_fields`` is required for canonicalization and :meth:`rename_ids()` to correctly rename
        src registers. Without this, it causes new actions to not canonicalize temp register names. Rather
        than having users debug why the registers aren't being updated to a canonical form, this will raise
        an error.

    :param step_id: ID of the TestStep object to be executed.

    :raises NotImplementedError: If `register_fields` is not defined.

    """

    register_fields: list[str] = []

    def __init_subclass__(cls, **kwargs) -> None:
        """
        `NotImplementedError` if `register_fields` is not defined.

        ``register_fields`` is required for canonicalization and ``rename_ids()`` to correctly rename
        src registers. Without this, it causes new actions to not canonicalize temp register names. Rather
        than having users debug why the registers aren't being updated to a canonical form, this will raise
        an error.
        """
        super().__init_subclass__(**kwargs)
        if "register_fields" not in cls.__dict__:
            raise NotImplementedError(f"{cls.__name__} must define a 'register_fields' attribute of type list")
        if not isinstance(cls.register_fields, list):
            raise TypeError(f"{cls.__name__} must define a 'register_fields' attribute of type list (got {type(cls.register_fields)})")

    def __init__(
        self,
        step_id: str,
    ):
        self.constraints: dict[str, Any] = {}  # constraints for selecting instruction. Should be defined in _build()
        self.step_id = step_id

    def __repr__(self) -> str:
        # Should probably include a some other function here that forces extended classes to print a better repr,
        return f"{self.__class__.__name__}({self.step_id}, {self.repr_info()})"

    @classmethod
    def from_step(cls, step_id: str, step: StepIR, **kwargs) -> "Action":
        """
        Create an Action from a TestStep.

        FIXME: TestSteps need to be fixed to use inputs and positional arguments if possible
        """
        return cls(step_id=step_id, **kwargs)

    def rename_ids(self, id_map: dict[str, str]):
        """
        Renames internal step ID references using the provided mapping.
        This implementation iterates over `register_fields` and updates them.

        This allows for canonicalization of all the temporary register names, using

        :param id_map: A dictionary mapping old step IDs to new step IDs.
        """
        self.step_id = id_map[self.step_id]
        for field in self.register_fields:
            value = getattr(self, field, None)
            if isinstance(value, str) and value in id_map:
                setattr(self, field, id_map[value])

    def expand(self, ctx: LoweringContext) -> Optional[list["Action"]]:
        """
        If an action can be expanded into a series of other actions, this method should be implemented.
        The elaborator will replace this action with the returned list of actions.


        :param rng: Random number generator
        :param env: Test environment
        :return: A list of actions
        """

        return None

    @abstractmethod
    def pick_instruction(self, ctx: LoweringContext) -> Instruction:
        """
        Pick an instruction from the instruction catalog. Returns selected instruction.

        :param instruction_catalog: InstructionCatalog to be used for instruction selection.
        :param rng: RandNum to be used for instruction selection.
        :param env: TestEnv to be used for instruction selection.
        :return: Selected instruction
        """
        pass

    @abstractmethod
    def repr_info(self) -> str:
        """
        Debug information about the inputs and outputs of the action. Extended classes should override this
        instead of the __repr__ method.

        .. code-block:: python

            def repr_info(self) -> str:
                return f"'{self.rs1}', '{self.rs2}', 0x{self.imm:x}"
        """
        pass


class CodeMixin(ABC):
    """

    Mixin class for Actions to define a code attribute


    .. note::

        Previously used a ``defines_code`` flag that indicates if the action has a code list of Actions. This provides a more
        pythonic way to indicate that the action contains code
    """

    @property
    @abstractmethod
    def code(self) -> list[Action]:
        """
        Code for the action.
        """
        pass

    @abstractmethod
    def update_code(self, code: list[Action]):
        """
        Set the code for the action.

        Workaround for

        .. code-block:: python

            @code.setter
            @abstractmethod

        Not working correctly (Python doesn't check if the setter is implemented unless setter is called)
        """
        pass
