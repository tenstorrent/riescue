# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass
from collections import defaultdict

from coretp import TestEnv, InstructionCatalog
from coretp.isa import Register
from riescue.lib.rand import RandNum
from riescue.compliance.test_plan.memory import MemoryRegistry


class _IDTracker:
    """
    Helper to manage new canonical IDs for a DiscreteTest object. Tracks different ID types
    """

    def __init__(self):
        self._value_count = 0
        self._memory_count = 0
        self._label_count = 0
        self._counters = defaultdict(int)

    def get_unique_name(self, prefix: str) -> str:
        """Get a unique name with a given prefix."""
        count = self._counters[prefix]
        self._counters[prefix] += 1
        return f"{prefix}_{count}"

    def new_label(self) -> str:
        """Get a new label unnamed label"""
        new_label = f"L{self._label_count}"
        self._label_count += 1
        return new_label

    def new_value_id(self) -> str:
        value_id = f"r{self._value_count}"
        self._value_count += 1
        return value_id

    def new_memory_id(self) -> str:
        memory_id = f"mem{self._memory_count}"
        self._memory_count += 1
        return memory_id

    def new_code_memory_id(self) -> str:
        memory_id = f"code_mem{self._memory_count}"
        self._memory_count += 1
        return memory_id


@dataclass
class LoweringContext:
    """
    Context for lowering actions into Instructions. Used for `class:Expander` / `class:Elaborator`
    to pass shared state (rng, environment)

    Should be local to a single DiscreteTest
    """

    rng: RandNum
    mem_reg: MemoryRegistry
    env: TestEnv
    instruction_catalog: InstructionCatalog

    def __post_init__(self):
        self.id_tracker = _IDTracker()
        self.global_function_clobbers: dict[str, list[Register]] = {}  # maps function name to list of clobbered registers
        self._built = False

    def new_label(self) -> str:
        return self.id_tracker.new_label()

    def unique_label(self, name: str) -> str:
        return self.id_tracker.get_unique_name(name)

    def new_value_id(self) -> str:
        "Returns the next available ID for an ``Action``"
        return self.id_tracker.new_value_id()

    def new_memory_id(self) -> str:
        return self.id_tracker.new_memory_id()

    def new_code_memory_id(self) -> str:
        return self.id_tracker.new_code_memory_id()

    def and_mask(self) -> str:
        return f"0x{((1 << self.env.get_max_va_bits()) - 1) & 0xFFFF_FFFF_FFFF_F000:x}"  # 4KiB aligned mask

    def random_n_width_number(self, n: int = 32, min_val: int = 2) -> int:
        """Generate a random number with a random bit width.

        min_num is 1<<min_val - 1
        max_num is (1 << n) - 1
        Random number is chosen between min_num and max_num

        :param n: Maximum bit width (inclusive). Must be > 2.
        :type n: int
        :return: A random integer.
        :rtype: int
        :raises ValueError: If n is not greater than 2.
        """
        if n <= 2:
            raise ValueError("n must be greater than 2")
        if min_val < 2:
            raise ValueError("min_val must be greater than 2")
        min_num = (1 << min_val) - 1
        max_num = (1 << n) - 1
        return self.rng.randint(min_num, max_num)
