"""
Assembly code routine helper functions that are used in multiple places, such as both OS and test macros.
"""

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


class Routines:
    def __init__(self):
        pass

    # Used in the place barrier routine
    @classmethod
    def place_acquire_lock(
        cls, name: str, lock_addr_reg: str, swap_val_reg: str, work_reg: str, end_test_label: str, max_tries: int = 500, time_wait: int = 500, disable_wfi_wait: bool = False
    ) -> str:
        return f"""
        li {lock_addr_reg}, barrier_lock
        li {swap_val_reg}, {max_tries}        # Initialize swap value.

        j {name}_retry_acquire_lock

        {name}_check_if_early_bail:
            li {lock_addr_reg}, num_harts_ended
            lw {work_reg}, 0({lock_addr_reg})
            bnez {work_reg}, {name}_early_bail
            # Looks like we are stuck and no other hart has apparently bailed, so end in a fail.
            li a0, failed_addr
            ld a1, 0(a0)
            jalr ra, 0(a1)

        # Another hart may have bailed for an acceptable reason, so end nominally.
        {name}_early_bail:
            li gp, 0x80000000 # Set GP[31]==1 to indicate failure
            li a0, {end_test_label}
            ld a1, 0(a0)
            jalr ra, 0(a1)

        {name}_retry_acquire_lock:
            # decrement swap value
            addi {swap_val_reg}, {swap_val_reg}, -1
            # jump to fail if {swap_val_reg} is zero or less
            bge zero, {swap_val_reg}, {name}_check_if_early_bail


            lw           {work_reg}, ({lock_addr_reg})     # Check if lock is held.
            bnez         {work_reg},  {f'{name}_skip_wfi_wait_jump_back' if disable_wfi_wait else f'{name}_wfi_wait'}    # Retry if held.
            amoswap.w.aq {work_reg}, {swap_val_reg}, ({lock_addr_reg})
            bnez         {work_reg},  {f'{name}_skip_wfi_wait_jump_back' if disable_wfi_wait else f'{name}_wfi_wait'}     # Retry if held.
            j {name}_acquired_lock
        {name}_wfi_wait:
            # do wait WFI here to prevent any sort of max instruction failure with arbitrary time wait
            rdtime {work_reg}
            addi {work_reg}, {work_reg}, {time_wait}
            csrrw x0, stimecmp, {work_reg}
            wfi
        {name}_skip_wfi_wait_jump_back:
            j {name}_retry_acquire_lock
        {name}_acquired_lock:
            fence

        """

    # Used in the place barrier routine
    @classmethod
    def place_release_lock(cls, name: str, lock_addr_reg: str) -> str:
        return f"""
        fence
        amoswap.w.rl x0, x0, ({lock_addr_reg}) # Release lock by storing 0.
        {name}_released_lock:

        """

    # Based on example from: http://15418.courses.cs.cmu.edu/spring2013/article/43 Implementing Barriers By Laine, nkindberg, xs33, and dyc
    @classmethod
    def place_barrier(
        cls,
        name: str,
        lock_addr_reg: str,
        arrive_counter_addr_reg: str,
        depart_counter_addr_reg: str,
        flag_addr_reg: str,
        swap_val_reg: str,
        work_reg_1: str,
        work_reg_2: str,
        num_cpus: int,
        end_test_label: str,
        max_tries: int,
        disable_wfi_wait: bool,
    ) -> str:
        return f"""
        li {lock_addr_reg}, barrier_lock
        li {arrive_counter_addr_reg}, barrier_arrive_counter
        li {depart_counter_addr_reg}, barrier_depart_counter
        li {flag_addr_reg}, barrier_flag

        {cls.place_acquire_lock(
            name = name + "_0",
            lock_addr_reg = lock_addr_reg,
            swap_val_reg = swap_val_reg,
            work_reg = work_reg_1,
            end_test_label=end_test_label,
            max_tries=max_tries,
            disable_wfi_wait=disable_wfi_wait
        )}
        # Branch if arrive_counter not equal to zero
        lw {work_reg_1}, 0({arrive_counter_addr_reg})
        bnez {work_reg_1}, {name}_arrive_count_not_zero
            # Branch if depart_counter not equal to num_harts
            lw {work_reg_1}, 0({depart_counter_addr_reg})
            li {work_reg_2}, {num_cpus}
            bne {work_reg_1}, {work_reg_2}, {name}_depart_count_not_num_harts
                # Set flag to zero
                amoswap.w x0, x0, ({flag_addr_reg})
                j {name}_arrive_count_not_zero
            {name}_depart_count_not_num_harts:
                {cls.place_release_lock(name = name + "_0", lock_addr_reg = lock_addr_reg)}
                {name}_wait_while_depart_count_not_num_harts:
                    lw {work_reg_1}, 0({depart_counter_addr_reg})
                    bne {work_reg_1}, {work_reg_2}, {name}_wait_while_depart_count_not_num_harts
                {cls.place_acquire_lock(
                    name = name + "_1",
                    lock_addr_reg = lock_addr_reg,
                    swap_val_reg = swap_val_reg,
                    work_reg = work_reg_1,
                    end_test_label=end_test_label,
                    max_tries=max_tries,
                    disable_wfi_wait=disable_wfi_wait
                )}
                # Set flag to zero
                amoswap.w x0, x0, ({flag_addr_reg})

        {name}_arrive_count_not_zero:
            li {work_reg_2}, 1
            amoadd.w {work_reg_1}, {work_reg_2}, ({arrive_counter_addr_reg})
            addi {work_reg_1}, {work_reg_1}, 1
            {cls.place_release_lock(name = name + "_1", lock_addr_reg = lock_addr_reg)}

            li {arrive_counter_addr_reg}, barrier_arrive_counter

            # Branch if arrive_count not equal to num_harts
            li {work_reg_2}, {num_cpus}
            bne {work_reg_1}, {work_reg_2}, {name}_arrive_count_not_num_harts # Last to arrive must reset variables
                # Set arrive_count to zero
                amoswap.w x0, x0, ({arrive_counter_addr_reg})
                # Set depart_counter to 1
                li {work_reg_1}, 1
                amoswap.w x0, {work_reg_1}, ({depart_counter_addr_reg})
                # Set flag to one
                amoswap.w x0, {work_reg_1}, ({flag_addr_reg})
                j {name}_barrier_complete
            {name}_arrive_count_not_num_harts:
                {name}_wait_while_flag_zero:
                    # Check again if num_harts_ended is non-zero
                    li {arrive_counter_addr_reg}, num_harts_ended
                    lw {work_reg_2}, 0({arrive_counter_addr_reg})
                    beqz {work_reg_2}, {name}_no_early_bail
                    {name}_yes_other_bailed:
                        li gp, 0x80000000
                        li a0, {end_test_label} # End test
                        ld a1, 0(a0)
                        jalr ra, 0(a1)
                    {name}_no_early_bail:
                    lw {work_reg_1}, 0({flag_addr_reg})
                    beqz {work_reg_1}, {name}_wait_while_flag_zero
                {cls.place_acquire_lock(
                    name = name + "_2",
                    lock_addr_reg = lock_addr_reg,
                    swap_val_reg = swap_val_reg,
                    work_reg = work_reg_1,
                    end_test_label=end_test_label,
                    max_tries=max_tries,
                    disable_wfi_wait=disable_wfi_wait
                )}
                li {work_reg_1}, 1
                amoadd.w {work_reg_2}, {work_reg_1}, ({depart_counter_addr_reg})
                {cls.place_release_lock(name = name + "_2", lock_addr_reg = lock_addr_reg)}
        {name}_barrier_complete:
            fence

        """

    # Set retry to true and this will loop until the lock is acquired.
    # If false this will mean that only one attempt is made to acquire the lock, if you want only one hart to succeed.
    @classmethod
    def place_acquire_lock_lr_sc(cls, name: str, lock_addr_reg: str, expected_val_reg: str, desired_val_reg: str, return_val_reg: str, work_reg: str, retry: bool) -> str:
        return f"""
            {name}_cas_acquire:
                lr.d {work_reg}, ({lock_addr_reg}) # Load original value.
                bne {work_reg}, {expected_val_reg}, {f'{name}_cas_acquire' if retry else f'{name}_cas_acquire_fail'}# Doesn't match, retry
                sc.d {work_reg}, {desired_val_reg}, ({lock_addr_reg}) # Try to update.
                bnez {work_reg}, {name}_cas_acquire # Retry if store-conditional failed.
                li {return_val_reg}, 0 # Set return to success.
                j {name}_cas_acquired_lock

            {name}_cas_acquire_fail:
                li {return_val_reg}, 1 # Set return to failure.
            {name}_cas_acquired_lock:
                fence
        """

    @classmethod
    def place_release_lock_lr_sc(cls, name: str, lock_addr_reg: str, expected_val_reg: str, desired_val_reg: str, return_val_reg: str, work_reg: str, retry: bool) -> str:
        return f"""
            fence
            {name}_cas_release:
                lr.d {work_reg}, ({lock_addr_reg}) # Load original value.
                bne {work_reg}, {expected_val_reg}, {f'{name}_cas_release' if retry else f'{name}_cas_release_fail'}# Doesn't match, retry
                sc.d {work_reg}, {desired_val_reg}, ({lock_addr_reg}) # Try to update.
                bnez {work_reg}, {name}_cas_release # Retry if store-conditional failed.
                li {return_val_reg}, 0 # Set return to success.
                j {name}_cas_released_lock

            {name}_cas_release_fail:
                li {return_val_reg}, 1
                j failed
            {name}_cas_released_lock:
        """

    @classmethod
    def place_semaphore_acquire_ticket(
        cls, name: str, semaphore_addr_reg: str, lock_addr_reg: str, swap_val_reg: str, return_val_reg: str, work_reg: str, retry: bool, end_test_label: str, disable_wfi_wait: bool
    ) -> str:
        return f"""
            {name}_acquire_ticket:
                {cls.place_acquire_lock(
                    name = name + "_acquire_semaphore",
                    lock_addr_reg = lock_addr_reg,
                    swap_val_reg = swap_val_reg,
                    work_reg = work_reg,
                    end_test_label=end_test_label,
                    disable_wfi_wait=disable_wfi_wait
                )}
                ld {work_reg}, ({semaphore_addr_reg})
                bge x0, {work_reg}, {f'{name}_acquire_ticket' if retry else f'{name}_acquire_ticket_fail'}

                # Decrement semaphore
                addi {work_reg}, {work_reg}, -1
                sd {work_reg}, ({semaphore_addr_reg})
                j {name}_acquired_ticket

            {name}_acquire_ticket_fail:
                li {return_val_reg}, 1
                j {name}_semaphore_release_lock

            {name}_acquired_ticket:
                li {return_val_reg}, 0

            {name}_semaphore_release_lock:
            {cls.place_release_lock(name = name + "_acquire_semaphore", lock_addr_reg = lock_addr_reg)}
            fence
        """

    @classmethod
    def place_semaphore_release_ticket(cls, name: str, semaphore_addr_reg: str, lock_addr_reg: str, swap_val_reg: str, return_val_reg: str, work_reg: str, disable_wfi_wait: bool) -> str:
        return f"""
            fence
            {name}_release_ticket:
                {cls.place_acquire_lock(
                    name = name + "_release_semaphore",
                    lock_addr_reg = lock_addr_reg,
                    swap_val_reg = swap_val_reg,
                    work_reg = work_reg,
                    end_test_label="end_test_addr",
                    disable_wfi_wait=disable_wfi_wait
                )}
                ld {work_reg}, ({semaphore_addr_reg})
                addi {work_reg}, {work_reg}, 1
                sd {work_reg}, ({semaphore_addr_reg})

            {name}_released_ticket:
                li {return_val_reg}, 0

            {cls.place_release_lock(name = name + "_release_semaphore", lock_addr_reg = lock_addr_reg)}
        """

    # Place routine required to be called before any access to arrays the offsets for are dependent on the ID number of the hart.
    @classmethod
    def place_get_unique_id(cls, name: str, hartid_counter: str, num_cpus: int) -> str:
        return f"""
            li a0, {hartid_counter}
            li t0, 1
            amoadd.w.aq t1, t0, (a0) # FIXME What happens when overflow occurs?
            li t2, {num_cpus}
            remu a0, t1, t2
        """

    # If this seed address is shared this is not a threadsafe routine, okay for non-sharing or for critical section.
    @classmethod
    def place_rng_unsafe_reg(cls, seed_addr_reg: str, modulus_reg: int, seed_offset_scale_reg: int, target_offset_scale_reg: int, num_ignore_reg: int, handler_priv_mode: str) -> str:
        return f"""
                # simple XORshift random number generator
                # https://www.javamex.com/tutorials/random_numbers/xorshift.shtml#.VlcaYzKwEV8
                {Routines.place_retrieve_hartid(dest_reg="t2", priv_mode=handler_priv_mode)}

                # Calculate seed addr offset
                mv t1, {seed_offset_scale_reg}
                mul t2, t2, t1

                # Load seed element for this hart
                mv t1, {seed_addr_reg}
                add t1, t1, t2
                ld t0, (t1)

                # Generate new seed
                slli t1, t0, 21
                xor t0, t0, t1
                srli t1, t0, 35
                xor t0, t0, t1
                slli t1, t0, 4
                xor t0, t0, t1

                # Store updated seed element for this hart
                mv t1, {seed_addr_reg}
                add t1, t1, t2
                sd t0, (t1)

                # Obtain random number
                mv t1, {modulus_reg}
                remu t0, t0, t1
                # Ignore * elements at the beginning of the array
                #mv t1, {num_ignore_reg}
                #add t0, t0, t1
                # Offset scale is the number of bytes per element for indexing into an array
                #mv t1, {target_offset_scale_reg}
                #mul t0, t0, t1

                # Store in return register
                mv a0, t0
        """

    @classmethod
    def place_offset_address_by_scaled_hartid(cls, address_reg: str, dest_reg: str, hartid_reg: str, work_reg: str, scale: int) -> str:
        return f"""
            li {work_reg}, {scale}
            mul {work_reg}, {hartid_reg}, {work_reg}
            add {dest_reg}, {address_reg}, {work_reg}
        """

    @classmethod
    def place_retrieve_hartid(cls, dest_reg: str, priv_mode: str) -> str:
        assert priv_mode in ["M", "S"], f"Invalid priv_mode: {priv_mode}"
        routine_string = ""
        if priv_mode == "M":
            routine_string += f"""
                csrr {dest_reg}, mhartid
            """
        elif priv_mode == "S":
            routine_string += f"""
                csrr {dest_reg}, sscratch
            """

        return routine_string

    @classmethod
    def place_store_hartid(cls, dest_csr: str, work_reg: str, priv_mode: str) -> str:
        assert priv_mode in ["M"], f"Invalid priv_mode: {priv_mode}"
        routine_string = f"""
            csrr {work_reg}, mhartid
            csrw {dest_csr}, {work_reg}
        """
        return routine_string

    @classmethod
    def read_tval(cls, dest_reg: str, priv_mode: str) -> str:
        if priv_mode == "M":
            return f"csrr {dest_reg}, mtval"
        elif priv_mode == "S":
            return f"csrr {dest_reg}, stval"
        else:
            return ""
