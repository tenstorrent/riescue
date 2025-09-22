# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from typing import Generator

from riescue.compliance.test_plan.actions import Action, MemoryAction, CodePageAction, CodeMixin
from riescue.compliance.test_plan.types import DiscreteTest
from riescue.compliance.test_plan.context import LoweringContext


class Canonicalizer:
    """
    Canonicalize instructions by ensuring all IDs are unique [i.e. Ids are sequential]. Modifies ``DiscreteTest`` objects in place.

    Variable IDs should be unique across tests. While ``Action`` IRs are unique per test, canonicalization
    ensures that variable IDs are unique across tests and memory IDs can be shared across tests.

    Memory IDs should be unqiue across tests until shared memory is required, then need a mechanism
    to ensure memory IDs are shared across ``DiscreteTest`` objects.
    """

    def __init__(self):
        pass

    def canonicalize(self, tests: list[DiscreteTest], ctx: LoweringContext) -> tuple[list[DiscreteTest], list[CodePageAction], list[MemoryAction]]:
        """
        Canonicalize instructions by ensuring all IDs are unique. Modifies ``DiscreteTest`` objects in place.
        Allocates global memory objects
        Pulls out global code

        :param tests: List of ``DiscreteTest`` objects to canonicalize.
        """
        canonicalized_tests = []
        all_functions = []
        all_memory = []
        for test in tests:
            canonicalized_test, global_functions, memory = self._canonicalize_test(test, ctx)
            canonicalized_tests.append(canonicalized_test)
            all_functions.extend(global_functions)
            all_memory.extend(memory)
        return canonicalized_tests, all_functions, all_memory

    def _canonicalize_test(self, test: DiscreteTest, ctx: LoweringContext) -> tuple[DiscreteTest, list[CodePageAction], list[MemoryAction]]:
        """
        Canonicalizes ``DiscreteTest`` objects by assinging unique labels.
        Currently modifies ``DiscreteTest`` object in place.

        :return: Tuple of ``DiscreteTest`` object, list of global functions, list of memory actions

        This assumes that all values are local to a DiscreteTest object. Global values are not currently supported.
        They can be supported later by adding a separate global_step_ids set.


        :param test: ``DiscreteTest`` object to canonicalize.
        """
        local_step_id_to_canonical: dict[str, str] = {}  # Used to map local step IDs to canonical steps
        global_functions: dict[str, CodePageAction] = {}
        memory_actions: dict[str, MemoryAction] = {}

        # 1 gather all the step IDs from DT, gather global functions
        for action in self._action_generator(test.actions):

            # for action in test.actions:
            if action.step_id in local_step_id_to_canonical:
                raise ValueError(f"Step ID {action.step_id} was already defined in this DiscreteTest object")
            if isinstance(action, CodePageAction):
                global_function_id = ctx.new_code_memory_id()
                local_step_id_to_canonical[action.step_id] = global_function_id
                global_functions[global_function_id] = action
            elif isinstance(action, MemoryAction):
                new_memory_id = ctx.new_memory_id()
                local_step_id_to_canonical[action.step_id] = new_memory_id
                memory_actions[new_memory_id] = action
            else:
                local_step_id_to_canonical[action.step_id] = ctx.new_value_id()

        # 2 replace all the stepIDs with the canonical IDs, replace all source registers with canonical IDs. Allocate memory into MemoryRegistry
        for action in self._action_generator(test.actions):
            if action.step_id not in local_step_id_to_canonical:
                raise ValueError(f"Step ID {action.step_id} was not defined in this DiscreteTest object?")
            action.rename_ids(local_step_id_to_canonical)

        # 3 if global code, pull out and allocate new ID
        for global_function_id, action in global_functions.items():
            ctx.mem_reg.allocate_code(global_function_id, action)

        # 4 add memory actions to MemoryRegistry
        for memory_action in memory_actions.values():
            ctx.mem_reg.allocate_data(memory_action.step_id, memory_action)

        # 5 remove memory and code actions from actions
        test.actions = self._pop_memory_actions(test.actions)
        test.actions = self._pop_global_functions(test.actions)

        return test, list(global_functions.values()), list(memory_actions.values())

    def _action_generator(self, actions: list[Action]) -> Generator[Action, None, None]:
        """
        Yields all actions in the list, including nested actions
        """

        for action in actions:
            code = getattr(action, "code", None)
            if code is not None:
                yield from self._action_generator(code)
            yield action

    def _pop_memory_actions(self, actions: list[Action]) -> list[Action]:
        """
        Recursively pop MemoryActions from actions
        """
        ret_actions: list[Action] = []
        for action in actions:
            if isinstance(action, CodeMixin):
                action.update_code(self._pop_memory_actions(action.code))
            elif isinstance(action, MemoryAction):
                continue
            ret_actions.append(action)
        return ret_actions

    def _pop_global_functions(self, actions: list[Action]) -> list[Action]:
        """
        Recursively pop CodePageActions and MemoryActions from actions

        CodePageActions and MemoryActions need to be handled first
        """
        ret_actions: list[Action] = []
        for action in actions:
            if isinstance(action, CodePageAction):
                action.update_code(self._pop_global_functions(action.code))  # remove code page from actions
            else:
                ret_actions.append(action)
        return ret_actions
