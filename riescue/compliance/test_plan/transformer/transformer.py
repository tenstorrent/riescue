# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import logging
from dataclasses import dataclass
from typing import Optional, Union
from coretp import Instruction, TestEnv, InstructionCatalog
from coretp.isa import Label, Register, RISCV_REGISTERS

from riescue.compliance.test_plan.types import DiscreteTest, TextSegment, DataSegment, GlobalFunction, DataPage, TestCase, MachineCodeSegment, SupervisorCodeSegment, TextBlock
from riescue.lib.rand import RandNum
from riescue.lib import enums as RV
from .expander import Expander
from .elaborator import Elaborator
from .canonicalizer import Canonicalizer
from .legalizer import Legalizer
from .allocator import Allocator
from .test_harness import TestHarness
from riescue.compliance.test_plan.context import LoweringContext
from riescue.compliance.test_plan.memory import MemoryRegistry
from riescue.compliance.test_plan.actions import Action, StackPageAction
from riescue.compliance.test_plan.actions.privilege_mode import MachineCodeAction, SupervisorCodeAction, UserCodeAction, PrivilegeBlockMarkerInstruction
from riescue.compliance.test_plan.actions.csr import CsrApiInstruction, CsrDirectAccessAction
from riescue.compliance.test_plan.actions.assertions.assert_exception import AssertExceptionMarkerInstruction
from riescue.dtest_framework.config import FeatMgr

log = logging.getLogger(__name__)


@dataclass
class CsrAccessContext:
    """Tracks whether a CSR is accessed in regular, privileged, and/or assert_exception contexts."""

    in_regular: bool = False
    in_privileged: bool = False
    in_assert_exception: bool = False


class Transformer:
    """
    Orchestrator for transformation process; transforms ``TestStep`` IR into ``Action`` IR, then ``Instruction`` Final IR

    :param rng: :class:`RandNum` object to use for randomization.
    """

    def __init__(self, rng: RandNum, mem_reg: MemoryRegistry, featmgr: FeatMgr, isa: str = "rv64i_zicsr"):
        self.rng = rng
        self.mem_reg = mem_reg  # Memory Registry exists for life of Transformer
        self.featmgr = featmgr  # FeatMgr needed for feature discovery
        self.expander = Expander()  # Expands Actions into a flat list of Actions
        self.elaborator = Elaborator()  # Elaborates Actions into Instructions
        self.canonicalizer = Canonicalizer()  # Canonicalizes Actions
        self.legalizer = Legalizer()  # Legalizes Instructions
        self.allocator = Allocator()  # Allocates Instructions into Subroutines
        self.test_harness = TestHarness()  # Adds test harness code
        self.catalog = InstructionCatalog(isa)

    def transform_tests(self, tests: list[DiscreteTest], env: TestEnv) -> tuple[TextSegment, DataSegment]:
        """
        Transform a list of ``DiscreteTest`` IR objects into a list of lists of ``Instruction`` IR objects.
        """
        ctx = LoweringContext(
            rng=self.rng,
            mem_reg=self.mem_reg,
            env=env,
            instruction_catalog=self.catalog,
            featmgr=self.featmgr,
        )
        canonicalized_tests, code_page_actions, memory, machine_code_actions, supervisor_code_actions, user_code_actions = self.canonicalizer.canonicalize(tests, ctx)
        global_functions = []
        # create global functions
        for code_page_action in code_page_actions:
            code_page_id = code_page_action.step_id
            function_instructions = self._transform([code_page_action], ctx)
            code_page = ctx.mem_reg.get_code_page(code_page_id)
            global_functions.append(GlobalFunction.from_instructions(code_page, function_instructions))

            ctx.global_function_clobbers[code_page_id] = self._clobbered_registers(function_instructions)
        data_segment = DataSegment(blocks=global_functions)  # Global functions and data

        csr_storage_name = "tp_csr_storage"
        csr_storage_page = StackPageAction(name=csr_storage_name)
        ctx.mem_reg.allocate_data(csr_storage_name, csr_storage_page)

        test_blocks = []
        for test in canonicalized_tests:
            # add stack pages for each test before transforming
            stack_name = f"{test.name}_stack"
            stack_page = StackPageAction(name=stack_name)
            ctx.mem_reg.allocate_data(stack_name, stack_page)

            # transform DiscreteTest into TestCase
            log.debug(f"Transforming test: {test.name}")
            test_instructions = self._transform(test.actions, ctx)

            # Extract privilege block instructions
            test_instructions = self._extract_privilege_block_instructions(test_instructions, ctx)

            csrs_save_instructions, allocated_csrs = self._save_csrs(test_instructions, ctx, csr_storage_name)
            restored_csrs = self._restore_csrs(allocated_csrs, ctx, csr_storage_name)
            test_cast_instructions = [Label(test.name)] + csrs_save_instructions + self.initialize_stack(test.name, ctx) + test_instructions + restored_csrs
            test_block = TestCase.from_instructions(test_cast_instructions, header=f";#discrete_test(test={test.name})")
            test_block.blocks.append(self.test_harness.test_passed(test_name=test.name))
            test_blocks.append(test_block)

        # Generate privilege mode jump tables
        machine_code_segment = self._generate_privilege_code_jump_table(machine_code_actions, ctx, "machine", 0xF0001001)
        supervisor_code_segment = self._generate_privilege_code_jump_table(supervisor_code_actions, ctx, "supervisor", 0xF0001002)
        user_code_segment = self._generate_privilege_code_jump_table(user_code_actions, ctx, "user", 0xF0001003)

        text_segment = TextSegment(blocks=test_blocks)  # Test code

        # add test harness code
        text_segment = self.test_harness.add_test_harness(text_segment)

        # Append privilege mode sections to text segment if they have blocks
        if machine_code_segment is not None:
            text_segment.blocks.append(machine_code_segment)
        if supervisor_code_segment is not None:
            text_segment.blocks.append(supervisor_code_segment)
        if user_code_segment is not None:
            text_segment.blocks.append(user_code_segment)

        # allocate all memory (global functions / test code may have allocated more)
        for mem in ctx.mem_reg.data:
            data_segment.blocks.append(mem)

        return text_segment, data_segment

    def _generate_privilege_code_jump_table(
        self,
        code_actions: Union[dict[int, MachineCodeAction], dict[int, SupervisorCodeAction], dict[int, UserCodeAction]],
        ctx: LoweringContext,
        mode_name: str,
        syscall_num: int,
    ) -> Optional[TestCase]:
        """
        Generate a jump table for privilege mode code blocks.

        The jump table structure is:
        ```asm
        .section .code_{mode}_0, "ax"
        # Jump table - block index is in t0
        li x31, 0
        beq t0, x31, {mode}_block_0
        li x31, 1
        beq t0, x31, {mode}_block_1
        ...
        j end_{mode}_code

        {mode}_block_0:
            <code from block 0>
            j end_{mode}_code

        {mode}_block_1:
            <code from block 1>
            j end_{mode}_code

        end_{mode}_code:
            li x31, 0xf0001004
            ecall
        ```

        :param code_actions: Dictionary mapping block_index to code action
        :param ctx: LoweringContext for transformation
        :param mode_name: "machine" or "supervisor"
        :param syscall_num: The syscall number used to invoke this mode
        :return: TestCase containing the jump table, or None if no code actions
        """
        if len(code_actions) == 0:
            return None

        # Generate unique labels using rng
        uuid_suffix = self.rng.get_uuid()
        end_label = f"end_{mode_name}_code_{uuid_suffix}"

        # Build assembly text for the jump table
        lines: list[str] = []

        # Section header
        if mode_name == "machine":
            section_name = "code_machine_0"
        elif mode_name == "supervisor":
            section_name = "code_super_0"
        else:  # user
            section_name = "code_user_0"
        lines.append(f'.section .{section_name}, "ax"')
        lines.append(f"# {mode_name.capitalize()} code jump table - block index in s11")

        # Generate dispatch comparisons
        block_labels: dict[int, str] = {}
        for block_idx in sorted(code_actions.keys()):
            label = f"{mode_name}_block_{block_idx}_{uuid_suffix}"
            block_labels[block_idx] = label
            lines.append(f"li x31, {block_idx}")
            lines.append(f"beq s11, x31, {label}")

        # Jump to end if no match
        lines.append(f"j {end_label}")
        lines.append("")

        # Generate code blocks
        for block_idx in sorted(code_actions.keys()):
            label = block_labels[block_idx]
            lines.append(f"{label}:")

            # Use stored instructions
            block_instructions = ctx.privilege_block_instructions[mode_name].get(block_idx, [])
            for instr in block_instructions:
                lines.append(f"    {instr.format()}")

            # Jump to end after block execution
            lines.append(f"    j {end_label}")
            lines.append("")

        # End label - return to test mode via syscall
        lines.append(f"{end_label}:")
        lines.append("    li x31, 0xf0001004")
        lines.append("    ecall")

        # Create TestCase from text
        return TestCase.from_text(lines)

    def _extract_csr_write_name(self, instr: Instruction) -> Optional[str]:
        """Extract CSR name if instruction is a CSR write, None otherwise."""
        if isinstance(instr, CsrApiInstruction) and instr.api_call != "read":
            return instr.csr_name
        if instr.name in ["csrrw", "csrrwi", "csrrs", "csrrsi", "csrrc", "csrrci"]:
            csr_operand = instr.csr_operand()
            if csr_operand is not None:
                # Check if this is a read-only access
                if instr.name.endswith("i"):
                    check_operand = instr.immediate_operand()
                    if check_operand is None or check_operand.val == 0:
                        return None  # read-only CSR
                else:
                    main_rs1 = instr.rs1()
                    if main_rs1 is None or (isinstance(main_rs1.val, Register) and main_rs1.val.num == 0) or main_rs1.val in ("x0", "zero"):
                        return None  # read-only CSR
                return str(csr_operand.val)
        return None

    def _create_sd_instruction(self, ctx: LoweringContext, sp, t2, space_index: int) -> Instruction:
        """Create a configured SD instruction for CSR storage."""
        sd = ctx.instruction_catalog.get_instruction("sd")
        sd.instruction_id = ctx.new_value_id()
        sd_rs1 = sd.rs1()
        sd_rs2 = sd.rs2()
        sd_imm = sd.immediate_operand()
        if sd_rs1 is None or sd_rs2 is None or sd_imm is None:
            raise Exception(f"sd instruction {sd.name} has no source operand /source operand /immediate operand")
        sd_rs1.val = sp
        sd_rs2.val = t2
        sd_imm.val = space_index
        return sd

    def _save_csrs(self, test_instructions: list[Instruction], ctx: LoweringContext, space_name: str):
        """
        Analyze CSR accesses and generate save instructions.

        Tracks CSR writes in both regular code and privilege blocks (MachineCode/SupervisorCode).
        CSRs written in privilege blocks need force_machine_rw=True for save/restore.
        CSRs written in both contexts need to be saved/restored twice (with and without force_machine_rw).

        CSRs that are ONLY accessed within AssertException blocks are skipped, since those
        accesses are expected to cause exceptions and don't need state preservation.

        Returns:
            tuple: (save_instructions, space_index_to_csr_info)
                space_index_to_csr_info maps space offset to (csr_name, force_machine_rw)
        """
        # Track CSRs and their access contexts
        csr_access_contexts: dict[str, CsrAccessContext] = {}

        # Only need to distinguish privileged context when virtualized
        is_virtualized = ctx.featmgr.env == RV.RiscvTestEnv.TEST_ENV_VIRTUALIZED

        # First pass: analyze all CSR accesses in main instruction stream
        # Track whether we're inside an AssertException block
        in_assert_exception = False
        for instr in test_instructions:
            # Check for AssertException markers
            if isinstance(instr, AssertExceptionMarkerInstruction):
                if instr.marker_type == "start":
                    in_assert_exception = True
                elif instr.marker_type == "end":
                    in_assert_exception = False
                continue

            csr_name = self._extract_csr_write_name(instr)
            if csr_name:
                if csr_name not in csr_access_contexts:
                    csr_access_contexts[csr_name] = CsrAccessContext()
                if in_assert_exception:
                    csr_access_contexts[csr_name].in_assert_exception = True
                else:
                    csr_access_contexts[csr_name].in_regular = True

        # Second pass: analyze CSR accesses in privilege blocks
        for mode in ["machine", "supervisor", "user"]:
            if mode in ctx.privilege_block_instructions:
                for _, block_instrs in ctx.privilege_block_instructions[mode].items():
                    for instr in block_instrs:
                        csr_name = self._extract_csr_write_name(instr)
                        if csr_name:
                            if csr_name not in csr_access_contexts:
                                csr_access_contexts[csr_name] = CsrAccessContext()
                            # Only mark as privileged if virtualized; otherwise treat as regular
                            if is_virtualized:
                                csr_access_contexts[csr_name].in_privileged = True
                            else:
                                csr_access_contexts[csr_name].in_regular = True

        # Filter out CSRs that are ONLY accessed within AssertException blocks
        # (they don't need save/restore since the access is expected to fault)
        csr_access_contexts = {csr_name: ctx_info for csr_name, ctx_info in csr_access_contexts.items() if ctx_info.in_regular or ctx_info.in_privileged}

        # No CSRs to save
        if not csr_access_contexts:
            return [], {}

        # Generate save instructions
        instructions = []
        space_index_to_csr_info = {}  # maps offset to (csr_name, force_machine_rw)
        space_index = 0

        # use sp (which will be overwritten) and t2 which is going to be used as CSR RW
        sp = RISCV_REGISTERS[2]
        t2 = RISCV_REGISTERS[7]

        # sp = shared space for CSR swap
        li_space = ctx.instruction_catalog.get_instruction("li")
        li_space.instruction_id = ctx.new_value_id()
        li_space_imm = li_space.immediate_operand()
        li_space_rd = li_space.destination
        if li_space_imm is None or li_space_rd is None:
            raise Exception(f"li instruction {li_space.name} has no immediate operand /destination operand")
        li_space_imm.val = space_name
        li_space_rd.val = sp
        instructions.append(li_space)

        def emit_csr_save(csr_name: str, force_machine_rw: bool):
            """Helper to emit CSR read + store instructions."""
            nonlocal space_index
            instruction_id = ctx.new_value_id()
            csr_read = CsrApiInstruction(csr_name=csr_name, name="csrr", api_call="read", force_machine_rw=force_machine_rw, instruction_id=instruction_id)
            instructions.append(csr_read)
            instructions.append(self._create_sd_instruction(ctx, sp, t2, space_index))
            space_index_to_csr_info[space_index] = (csr_name, force_machine_rw)
            space_index += 8

        # Save each CSR with appropriate force_machine_rw settings
        for csr_name, ctx_info in sorted(csr_access_contexts.items()):
            if is_virtualized:
                # In virtualized mode, distinguish between regular and privileged contexts
                if ctx_info.in_regular:
                    emit_csr_save(csr_name, force_machine_rw=False)
                if ctx_info.in_privileged:
                    emit_csr_save(csr_name, force_machine_rw=True)
            else:
                # In non-virtualized mode, always use force_machine_rw=True
                emit_csr_save(csr_name, force_machine_rw=True)

        return instructions, space_index_to_csr_info

    def _restore_csrs(self, allocated_csrs: dict[int, tuple[str, bool]], ctx: LoweringContext, space_name: str):
        """
        Generate restore instructions for saved CSRs.

        Args:
            allocated_csrs: maps space offset to (csr_name, force_machine_rw)
            ctx: LoweringContext
            space_name: name of the CSR storage space

        Returns:
            list of restore instructions
        """
        if len(allocated_csrs) == 0:
            return []

        instructions = []

        # use sp (which will be overwritten) and t2 which is going to be used as CSR RW
        sp = RISCV_REGISTERS[2]
        t2 = RISCV_REGISTERS[7]

        # load space name into sp
        li_space = ctx.instruction_catalog.get_instruction("li")
        li_space.instruction_id = ctx.new_value_id()
        li_space_imm = li_space.immediate_operand()
        li_space_rd = li_space.destination
        if li_space_imm is None or li_space_rd is None:
            raise Exception(f"li instruction {li_space.name} has no immediate operand /destination operand")
        li_space_imm.val = space_name
        li_space_rd.val = sp
        instructions.append(li_space)

        for space_index, (csr_name, force_machine_rw) in allocated_csrs.items():
            # load CSR from space to t2
            ld = ctx.instruction_catalog.get_instruction("ld")
            ld.instruction_id = ctx.new_value_id()
            ld_rs1 = ld.rs1()
            ld_rd = ld.destination
            ld_imm = ld.immediate_operand()
            if ld_rs1 is None or ld_rd is None or ld_imm is None:
                raise Exception(f"ld instruction {ld.name} has no source operand /source operand /immediate operand")
            ld_rs1.val = sp
            ld_rd.val = t2
            ld_imm.val = space_index
            instructions.append(ld)

            # write t2 to CSR with appropriate force_machine_rw setting
            instruction_id = ctx.new_value_id()
            csr_write = CsrApiInstruction(csr_name=csr_name, name="csrw", api_call="write", force_machine_rw=force_machine_rw, instruction_id=instruction_id)
            instructions.append(csr_write)
        return instructions

    # Specific to CsrDirectAccessAction
    def _randomize_csr(self, ctx: LoweringContext, op: str, src: Optional[str], src_value: Optional[int]) -> tuple[str, bool]:
        """Select a random CSR valid for current privilege mode and operation."""
        priv = ctx.env.priv.name.lower()

        FILTERED_CSRS = ["mip", "mie", "sip", "sie", "satp"]  # Do not create new interrupts

        # Map privilege to Accessibility filter
        accessibility_map = {
            "m": "Machine",
            "s": "Supervisor",
            "u": "User",
        }
        accessibility = accessibility_map.get(priv, "Machine")

        is_read_only_op = op in ["csrrs", "csrrc", "csrrsi", "csrrci"] and (src is None or src == "zero") and (src_value == 0 or src_value is None)

        names_to_csrs = []

        if accessibility == "User":
            csr_configs_exclude_machine = ctx.get_csr_manager().lookup_csrs(
                match={"software-write": "W", "ISS_Support": "Yes"},
                exclude={"Accessibility": "Machine"},
            )

            non_machine_csrs = list(csr_configs_exclude_machine.keys())

            csr_configs_exclude_super = ctx.get_csr_manager().lookup_csrs(
                match={"software-write": "W", "ISS_Support": "Yes"},
                exclude={"Accessibility": "Supervisor"},
            )

            non_super_csrs = list(csr_configs_exclude_super.keys())

            names_to_csrs += [csr for csr in non_machine_csrs if csr in non_super_csrs]

        else:
            csr_configs = ctx.get_csr_manager().lookup_csrs(
                match={"Accessibility": accessibility, "software-write": "W", "ISS_Support": "Yes"},
            )
            if not csr_configs:
                # Fallback: try without ISS_Support filter
                csr_configs = ctx.get_csr_manager().lookup_csrs(
                    match={"Accessibility": accessibility, "software-write": "W"},
                )
            names_to_csrs += list(csr_configs.keys())

        if is_read_only_op:
            if accessibility == "User":
                csr_configs_exclude_machine = ctx.get_csr_manager().lookup_csrs(
                    match={"software-read": "R", "ISS_Support": "Yes"},
                    exclude={"Accessibility": "Machine"},
                )

                non_machine_csrs = list(csr_configs_exclude_machine.keys())

                csr_configs_exclude_super = ctx.get_csr_manager().lookup_csrs(
                    match={"software-read": "R", "ISS_Support": "Yes"},
                    exclude={"Accessibility": "Supervisor"},
                )

                non_super_csrs = list(csr_configs_exclude_super.keys())

                names_to_csrs += [csr for csr in non_machine_csrs if csr in non_super_csrs]

            else:
                csr_configs = ctx.get_csr_manager().lookup_csrs(
                    match={"Accessibility": accessibility, "software-read": "R", "ISS_Support": "Yes"},
                )
                if not csr_configs:
                    # Fallback: try without ISS_Support filter
                    csr_configs = ctx.get_csr_manager().lookup_csrs(
                        match={"Accessibility": accessibility, "software-read": "R"},
                    )
                names_to_csrs += list(csr_configs.keys())
        names_to_csrs = [name for name in names_to_csrs if name not in FILTERED_CSRS]
        csr_name = ctx.rng.choice(names_to_csrs)
        return csr_name, is_read_only_op

    def _transform(self, actions: list[Action], ctx: LoweringContext) -> list[Instruction]:
        """
        Transform a ``ActionBlock`` of ``Action`` IR objects into a list of ``Instruction`` IR objects.

        Private to indicate that transform_tests should be used instead. This is becasue the canonicalizer needs to be called first.
        Otherwise actions that expand and source operands might need new IDs and canonicalizer needs to be setup correctly
        """

        expanded_actions = self.expander.expand(actions, ctx)
        elaborated_instructions = self.elaborator.elaborate(expanded_actions, ctx)
        legalized_instructions = self.legalizer.legalize(elaborated_instructions, ctx)
        allocated_subroutines = self.allocator.allocate(legalized_instructions, ctx)

        log.debug(f"Transformed Actions: {actions}")
        log.debug(f"Expanded actions: {expanded_actions}")
        log.debug(f"Elaborated to Instructions: {elaborated_instructions}")
        log.debug(f"Legalized instructions: {legalized_instructions}")
        log.debug(f"Allocated subroutines: {allocated_subroutines}")
        return allocated_subroutines

    def _extract_privilege_block_instructions(
        self,
        instructions: list[Instruction],
        ctx: LoweringContext,
    ) -> list[Instruction]:
        """
        Extract privilege block instructions from the instruction stream.

        Scans for PrivilegeBlockMarkerInstructions, extracts instructions between
        start/end markers, stores them in ctx.privilege_block_instructions,
        and returns the filtered instruction stream (without markers or block code).
        """
        result: list[Instruction] = []
        current_block: Optional[tuple[str, int]] = None
        current_block_instructions: list[Instruction] = []

        for instr in instructions:
            if isinstance(instr, PrivilegeBlockMarkerInstruction):
                if instr.marker_type == "start":
                    current_block = (instr.mode, instr.block_index)
                    current_block_instructions = []
                elif instr.marker_type == "end":
                    if current_block is not None:
                        mode, block_index = current_block
                        ctx.privilege_block_instructions[mode][block_index] = current_block_instructions
                        current_block = None
                        current_block_instructions = []
                # Don't add marker to result
            elif current_block is not None:
                # Inside a privilege block - collect instruction
                current_block_instructions.append(instr)
            else:
                # Outside privilege block - keep in main stream
                result.append(instr)

        return result

    def _split_instructions(self, instructions: list[Instruction]) -> list[list[Instruction]]:
        """
        Split instructions into label separated instructions
        """
        current_block: list[Instruction] = []
        all_instructions: list[list[Instruction]] = []
        for instruction in instructions:
            if isinstance(instruction, Label):
                all_instructions.append(current_block)
                current_block = [instruction]
            else:
                current_block.append(instruction)
        all_instructions.append(current_block)
        return all_instructions

    def initialize_stack(self, test_name: str, ctx: LoweringContext) -> list[Instruction]:
        """
        Initialize stack for a test

        Note: Tests assume stack pointer is initialized. Might need a better way to determine
        if stack is needed at all, and a better way to link the stack memory besides the test name
        """

        stack_page_symbol = f"{test_name}_stack"
        stack_page = ctx.mem_reg.get_data_page(stack_page_symbol)

        sp = RISCV_REGISTERS[2]
        t0 = RISCV_REGISTERS[5]
        if sp.name != "sp" or t0.name != "t0":
            raise Exception(f"Incorrect sp and/or t0 in sp={sp} t0={t0}. Need better way to get registers from RISCV_REGISTERS")

        # li sp, stack_page_symbol
        li = ctx.instruction_catalog.get_instruction("li")
        li.instruction_id = ctx.new_value_id()
        li_imm = li.immediate_operand()
        li_rd = li.destination
        if li_imm is None or li_rd is None:
            raise Exception(f"li instruction {li.name} has no immediate operand /destination operand")
        li_imm.val = stack_page_symbol
        li_rd.val = sp

        # li t0, stack_page_size
        li_t0 = ctx.instruction_catalog.get_instruction("li")
        li_t0.instruction_id = ctx.new_value_id()
        li_t0_imm = li_t0.immediate_operand()
        li_t0_rd = li_t0.destination
        if li_t0_imm is None or li_t0_rd is None:
            raise Exception(f"li instruction {li_t0.name} has no immediate operand /destination operand")
        li_t0_imm.val = stack_page.size
        li_t0_rd.val = t0

        # add sp, sp, t0
        add = ctx.instruction_catalog.get_instruction("add")
        add.instruction_id = ctx.new_value_id()
        add_rs1 = add.rs1()
        add_rs2 = add.rs2()
        add_rd = add.destination
        if add_rs1 is None or add_rs2 is None or add_rd is None:
            raise Exception(f"add instruction {add.name} has no source operand /source operand /destination operand")
        add_rs1.val = sp
        add_rs2.val = t0
        add_rd.val = sp

        # andi sp, sp, -16
        andi = ctx.instruction_catalog.get_instruction("andi")
        andi.instruction_id = ctx.new_value_id()
        andi_imm = andi.immediate_operand()
        andi_rs1 = andi.rs1()
        andi_rd = andi.destination
        if andi_imm is None or andi_rs1 is None or andi_rd is None:
            raise Exception(f"andi instruction {andi.name} has no immediate operand /source operand /destination operand")

        andi_imm.val = -16
        andi_rs1.val = sp
        andi_rd.val = sp

        # Add stack size to stack page
        return [li, li_t0, add, andi]

    def _clobbered_registers(self, instructions: list[Instruction]) -> list[Register]:
        """
        Get the list of registers that are clobbered by the instructions
        """
        return [instr.destination.val for instr in instructions if instr.destination is not None and isinstance(instr.destination.val, Register)]
