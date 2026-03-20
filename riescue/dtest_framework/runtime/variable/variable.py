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
    :param size: Size of the variable in bytes (per element for arrays)
    :param offset: Offset of the variable from the hart context.
    :param description: Description of the variable
    :param hart_variable: Whether the variable is a hart-local variable or shared variable. False means it's a shared variable.
    :param element_count: Number of elements in the array (1 for scalar variables)
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
        element_count: int = 1,
    ):
        self.name = name
        self.value = value
        self.size = size  # size per element
        self.offset = offset
        self.description = description
        self.amo_enabled = amo_enabled
        self.hart_variable = hart_variable
        self.element_count = element_count

        self.base_pointer: str  #: Pointer to variable's base section. If hart-local, assumes tp is pointing to hart-local storage. If shared, assumes gp is pointing to shared storage.
        if self.hart_variable:
            self.base_pointer = "tp"
        else:
            self.base_pointer = "INVALID"  # Currently not using anything for shared variables, just the constant generated

    @property
    def total_size(self) -> int:
        """Return total size in bytes."""
        return self.element_count * self.size

    def _element_offset(self, index: int) -> int:
        """Return byte offset for element at index."""
        if index < 0 or index >= self.element_count:
            raise IndexError(f"Index {index} out of bounds [0, {self.element_count})")
        return self.offset + (index * self.size)

    def _offset_fits_12bit(self, offset: int) -> bool:
        """Return True if offset fits in RISC-V 12-bit signed immediate (-2048 to 2047)."""
        return -2048 <= offset <= 2047

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

    def _resolve_name(self, bare: bool, index: int = 0) -> str:
        """
        Resolve the equate name for shared variables.

        :param bare: If True, use the PA equate (name_pa) for M-mode bare access.
        :param index: Index of the element.
        """
        name = f"{self.name}_pa" if bare else self.name
        if index == 0:
            return name
        return f"{name} + {index * self.size}"

    def load_immediate(self, dest_reg: str, index: int = 0, bare: bool = True) -> str:
        """
        Generates code to load the variable's address into a register.

        :param dest_reg: Register to load the address into.
        :param index: Index of the element to load (for array variables).
        :param bare: If True, use PA equate for M-mode bare access (shared variables only).
        """
        offset = self._element_offset(index)
        if self.hart_variable:
            if self._offset_fits_12bit(offset):
                return f"addi {dest_reg}, {self.base_pointer}, {offset}"
            return f"li {dest_reg}, {offset}\n\tadd {dest_reg}, {dest_reg}, {self.base_pointer}"
        else:
            return f"li {dest_reg}, {self._resolve_name(bare, index)}"

    def load(self, dest_reg: str, index: int = 0, bare: bool = True) -> str:
        """
        Generates code to load the variable's value into a register.

        :param dest_reg: Register to load the value into.
        :param index: Index of the element to load (for array variables).
        :param bare: If True, use PA equate for M-mode bare access (shared variables only).
        """
        comment = f"# {self.name}" if self.element_count == 1 else f"# {self.name}[{index}]"
        offset = self._element_offset(index)
        if self.hart_variable:
            if self._offset_fits_12bit(offset):
                return f"{self._load_instruction()} {dest_reg}, {offset}({self.base_pointer}) {comment:>30}"
            return "\n\t".join([self.load_immediate(dest_reg, index), f"{self._load_instruction()} {dest_reg}, ({dest_reg}) {comment:>30}"])
        else:
            return "\n\t".join([self.load_immediate(dest_reg, index, bare), f"{self._load_instruction()} {dest_reg}, ({dest_reg}) {comment:>30}"])

    def store(self, src_reg: str, temp_reg: str = "t6", index: int = 0, bare: bool = True) -> str:
        """
        Store value in src_reg into variable. If shared variable, uses a temporary register.

        :param src_reg: Register to store the variable's value from.
        :param temp_reg: Temporary register to use for loading the variable's address.
        :param index: Index of the element to store (for array variables).
        :param bare: If True, use PA equate for M-mode bare access (shared variables only).
        """
        comment = f"# {self.name}" if self.element_count == 1 else f"# {self.name}[{index}]"
        offset = self._element_offset(index)
        if self.hart_variable:
            if self._offset_fits_12bit(offset):
                return f"{self._store_instruction()} {src_reg}, {offset}({self.base_pointer}) {comment:>30}"
            return "\n\t".join([self.load_immediate(temp_reg, index), f"{self._store_instruction()} {src_reg}, ({temp_reg}) {comment:>30}"])
        else:
            return "\n\t".join([self.load_immediate(temp_reg, index, bare), f"{self._store_instruction()} {src_reg}, ({temp_reg}) {comment:>30}"])

    def increment(self, dest_reg: str, addr_reg: str = "t1", index: int = 0, bare: bool = True) -> str:
        """
        Loads, increments value by 1, and stores.
        If amo enabled, uses amoadd.w/d

        Returns value+1 into dest_reg

        :param dest_reg: Register to store the incremented value.
        :param addr_reg: Temporary register for address.
        :param index: Index of the element to increment (for array variables).
        :param bare: If True, use PA equate for M-mode bare access (shared variables only).
        """
        if self.amo_enabled:
            li = self.load_immediate(addr_reg, index, bare)
            op = "amoadd.d" if self.size == 8 else "amoadd.w"
            increment = f"li {dest_reg}, 1"
            swap = f"{op} {dest_reg}, {dest_reg}, ({addr_reg})"
            increment_local = f"addi {dest_reg}, {dest_reg}, 1"
            return "\n\t".join([li, increment, swap, increment_local])
        else:
            li = self.load_immediate(addr_reg, index, bare)
            load_val = f"{self._load_instruction()} {dest_reg}, ({addr_reg})"
            increment = f"addi {dest_reg}, {dest_reg}, 1"
            store_val = f"{self._store_instruction()} {dest_reg}, ({addr_reg})"
            return "\n\t".join([li, load_val, increment, store_val])

    def load_and_clear(self, dest_reg: str, index: int = 0, bare: bool = True) -> str:
        """
        Generates code to load the variable's value into a register and clear it.

        If amo is enabled, use an amoswap here.

        :param dest_reg: Register to load the value into.
        :param index: Index of the element to load and clear (for array variables).
        :param bare: If True, use PA equate for M-mode bare access (shared variables only).
        """
        if self.amo_enabled:
            if self.size == 8:
                op = "amoswap.d"
            else:
                op = "amoswap.w"
            addr_code = self.load_immediate(dest_reg, index)
            return f"{addr_code}\n\t{op} {dest_reg}, x0, ({dest_reg})"
        else:
            return f"{self.load(dest_reg, index, bare)}\n{self.store('x0', index=index, bare=bare)}"

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
