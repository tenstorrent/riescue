# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# pyright: strict


class Variable:
    """
    Object for a variable used by the test runtime environment.
    Used to store data, registers, etc.


    Provides methods generating code to load and store variable.
    Assumes that hart context pointer is loaded into tp register.

    :param name: Name of the variable
    :param value: Value of the variable
    :param size: Size of the variable in bytes
    :param offset: Offset of the variable from the hart context.
    :param description: Description of the variable
    :param hart_variable: Whether the variable is a hart-local variable or shared variable. False means it's a shared variable.
    """

    def __init__(
        self,
        name: str,
        value: int,
        size: int,
        offset: int,
        description: str = "",
        amo_enabled: bool = False,
        hart_variable: bool = False,
    ):
        self.name = name
        self.value = value
        self.size = size
        self.offset = offset
        self.description = description
        self.amo_enabled = amo_enabled
        self.hart_variable = hart_variable

        self.base_pointer: str  #: Pointer to variable's base section. If hart-local, assumes tp is pointing to hart-local storage. If shared, assumes gp is pointing to shared storage.
        if self.hart_variable:
            self.base_pointer = "tp"
        else:
            self.base_pointer = "INVALID"  # Currently not using anything for shared variables, just the constant generated

    @property
    def data_type(self) -> str:
        """
        Returns the data type for the variable's size.
        """
        if self.size == 8:
            return "dword"
        elif self.size == 4:
            return "word"
        elif self.size == 2:
            return "half"
        elif self.size == 1:
            return "byte"
        else:
            raise ValueError(f"Unsupported size: {self.size}")

    def load_immediate(self, dest_reg: str) -> str:
        """
        Generates code to load the variable's address into a register.
        """
        if self.hart_variable:
            return f"addi {dest_reg}, {self.base_pointer}, {self.offset}"
        else:
            return f"li {dest_reg}, {self.name}"

    def load(self, dest_reg: str) -> str:
        """
        Generates code to load the variable's value into a register.
        """
        if self.hart_variable:
            return f"{self._load_instruction()} {dest_reg}, {self.offset}({self.base_pointer})"
        else:
            return "\n\t".join([self.load_immediate(dest_reg), f"{self._load_instruction()} {dest_reg}, ({dest_reg})"])

    def store(self, src_reg: str, temp_reg: str = "t7") -> str:
        """
        Store value in src_reg into variable. If shared variable, uses a temporary register.

        :param src_reg: Register to store the variable's value from.
        :param temp_reg: Temporary register to use for loading the variable's address.
        """
        if self.hart_variable:
            return f"{self._store_instruction()} {src_reg}, {self.offset}({self.base_pointer})"
        else:
            return "\n\t".join([self.load_immediate(temp_reg), f"{self._store_instruction()} {src_reg}, ({temp_reg})"])

    def increment(self, dest_reg: str, addr_reg: str = "t1") -> str:
        """
        Loads, increments value by 1, and stores.
        If amo enabled, uses amoadd.w/d

        Returns value+1 into dest_reg
        """
        if self.amo_enabled:
            li = self.load_immediate(addr_reg)
            op = "amoadd.d" if self.size == 8 else "amoadd.w"
            increment = f"li {dest_reg}, 1"
            swap = f"{op} {dest_reg}, {dest_reg}, ({addr_reg})"
            increment_local = f"addi {dest_reg}, {dest_reg}, 1"
            return "\n\t".join([li, increment, swap, increment_local])
        else:
            li = self.load_immediate(addr_reg)
            load_val = f"{self._load_instruction()} {dest_reg}, ({addr_reg})"
            increment = f"addi {dest_reg}, {dest_reg}, 1"
            store_val = f"{self._store_instruction()} {dest_reg}, ({addr_reg})"
            return "\n\t".join([li, load_val, increment, store_val])

    def load_and_clear(self, dest_reg: str) -> str:
        """
        Generates code to load the variable's value into a register and clear it.

        If amo is enabled, use an amoswap here.
        """

        if self.amo_enabled:
            if self.size == 8:
                op = "amoswap.d"
            else:
                op = "amoswap.w"

            return f"addi {dest_reg}, {self.base_pointer}, {self.offset}\n{op} {dest_reg}, x0, ({dest_reg})"
        else:
            return f"{self.load(dest_reg)}\n{self.store('x0')}"

    def _load_instruction(self) -> str:
        """
        Returns the load instruction for the variable's size.
        """
        if self.size == 8:
            return "ld"
        elif self.size == 4:
            return "lw"
        elif self.size == 2:
            return "lh"
        elif self.size == 1:
            return "lb"
        else:
            raise ValueError(f"Unsupported size: {self.size}")

    def _store_instruction(self) -> str:
        """
        Returns the store instruction for the variable's size.
        """
        if self.size == 8:
            return "sd"
        elif self.size == 4:
            return "sw"
        elif self.size == 2:
            return "sh"
        elif self.size == 1:
            return "sb"
        else:
            raise ValueError(f"Unsupported size: {self.size}")
