;#test.name       os_sync_some_harts_directed
;#test.author     njoaquin@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp         on
;#test.mp_mode    simultaneous
;#test.cpus       8
;#test.paging     sv57
;#test.category   arch
;#test.class      custom
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       mp synchronization os_sync_some_harts
;#test.summary
;#test.summary    Directed test for the OS_SYNC_SOME_HARTS macro (subset-of-harts barrier).
;#test.summary    8 harts are programmed; only the two with mhartid 0 and 1 participate in the
;#test.summary    barrier (num_harts=2 < num_cpus=8, so the macro takes its subset path).
;#test.summary
;#test.summary    test01 (all 8 harts run it in simultaneous mode, then branch on mhartid):
;#test.summary    - hart 0     : jumps straight into OS_SYNC_SOME_HARTS
;#test.summary    - hart 1     : runs a short (~250 dynamic-instruction) wait loop, then OS_SYNC_SOME_HARTS
;#test.summary    - harts 2..7 : run a long (~1500 dynamic-instruction) wait loop and DO NOT sync
;#test.summary    - afterwards
;#test.summary    - hart 3     : jumps straight into OS_SYNC_SOME_HARTS
;#test.summary    - hart 4,5   : runs a short (~250 dynamic-instruction) wait loop, then OS_SYNC_SOME_HARTS
;#test.summary    - rest       : run a long (~1500 dynamic-instruction) wait loop and DO NOT sync
;#test.summary
;#test.summary    The subset barrier must rendezvous harts 0 and 1 regardless of what the other
;#test.summary    6 harts do. num_harts_ended is subset-local, so the 6 non-participants finishing
;#test.summary    the test cannot trip the barrier's early-bail; a broken subset barrier would
;#test.summary    instead time out -> os_failed (the test would FAIL). sync_region is zero-initialized
;#test.summary    via ;#init_memory below so the macro's lazy init lock (init_lock @+40, init_done
;#test.summary    @+48) and subset-local num_harts_ended @+32 bootstrap correctly.


# Shared region backing the subset barrier. Must be >= OS_SYNC_SOME_HARTS_REGION_SIZE (56)
# bytes, 8-byte aligned (a 4kb page is), and zero-initialized (see .data section below).
;#random_addr(name=sync_region,      type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=sync_region_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=sync_region, phys_name=sync_region_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Second shared region backing the subset barrier. Must be >= OS_SYNC_SOME_HARTS_REGION_SIZE (56)
# bytes, 8-byte aligned (a 4kb page is), and zero-initialized (see .data section below).
;#random_addr(name=sync_region_2,      type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=sync_region_2_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=sync_region_2, phys_name=sync_region_2_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Code section for the switch to super mode
.section .code_super_0, "ax"
    # Return to testmode
    li x31, 0xf0001004
    ecall

# Code section for the switch to user mode
.section .code_user_0, "ax"
    # Return to testmode
    li x31, 0xf0001004
    ecall

# Code section for the switch to machine mode
.section .code_machine_0, "ax"
    #excecute the code at address in a0 in machine mode.
    jalr ra, a0
    # Return to testmode
    li x31, 0xf0001004
    ecall


.section .code, "ax"

#####################
# test_setup: executed once before any discrete_test
#####################
test_setup:
    ;#test_passed()
	li x31, 0xf0000001  # Test Passed; Schedule test
	ecall


#####################
# test01: subset barrier across mhartid 0 and 1 only
#####################
;#discrete_test(test=test01)
test01:
    GET_MHART_ID                       # s1 = mhartid (clobbers a0, tp, x31)

    # Route harts: 0 -> sync now; 1 -> short wait then sync; 2..7 -> long wait, never sync.
    li t3, 0
    beq s1, t3, test01_sync_now
    li t3, 1
    beq s1, t3, test01_hart1_wait
    j test01_other_wait

    #####################
    # hart 1: short delay loop (~250 dynamic instructions) then join the barrier
    #####################
    test01_hart1_wait:
        li t3, 125
    test01_hart1_wait_loop:
        addi t3, t3, -1
        bnez t3, test01_hart1_wait_loop
        j test01_sync_now

    #####################
    # harts 2..7: long delay loop (~1500 dynamic instructions); these harts never sync
    #####################
    test01_other_wait:
        li t3, 750
    test01_other_wait_loop:
        addi t3, t3, -1
        bnez t3, test01_other_wait_loop
        j test01_done

    #####################
    # harts 0 and 1 rendezvous here on the 2-hart subset barrier
    #####################
    test01_sync_now:
        OS_SYNC_SOME_HARTS test01_subset, sync_region, 2
        j test01_done

    test01_done:

    GET_MHART_ID                       # s1 = mhartid (clobbers a0, tp, x31)

    # Route harts: 0 -> sync now; 1 -> short wait then sync; 2..7 -> long wait, never sync.
    li t3, 3
    beq s1, t3, test01_two_part_sync_now
    li t3, 4
    beq s1, t3, test01_two_part_1_wait
    li t3, 5
    beq s1, t3, test01_two_part_1_wait
    j test01_two_part_other_wait

    #####################
    # hart 4/5: short delay loop (~250 dynamic instructions) then join the barrier
    #####################
    test01_two_part_1_wait:
        li t3, 125
    test01_two_part_hart1_wait_loop:
        addi t3, t3, -1
        bnez t3, test01_two_part_hart1_wait_loop
        j test01_two_part_sync_now

    #####################
    # harts 2..7: long delay loop (~1500 dynamic instructions); these harts never sync
    #####################
    test01_two_part_other_wait:
        li t3, 750
    test01_two_part_other_wait_loop:
        addi t3, t3, -1
        bnez t3, test01_two_part_other_wait_loop
        j test_01_two_part_done

    #####################
    # harts 3/4/5 rendezvous here on the 3-hart subset barrier
    #####################
    test01_two_part_sync_now:
        OS_SYNC_SOME_HARTS test01_subset_two_part, sync_region_2, 3
        j test_01_two_part_done
    
    test_01_two_part_done:

    ;#test_passed()
	li x31, 0xf0000001  # Test Passed; Schedule test
	ecall


#####################
# test_cleanup: executed once after all discrete_test(s)
#####################
test_cleanup:
    ;#test_passed()
	li x31, 0xf0000001  # Test Passed; Schedule test
	ecall


#####################
# Default data section
#####################
.section .data

# Zero-initialize the subset-barrier region (7 dwords = 56 bytes). Field layout (stride 8):
#   +0  barrier_lock             +8  barrier_arrive_counter   +16 barrier_depart_counter
#   +24 barrier_flag             +32 num_harts_ended (subset) +40 init_lock   +48 init_done
;#init_memory @sync_region
.section .sync_region, "aw"

    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0

# Zero-initialize the subset-barrier region (7 dwords = 56 bytes). Field layout (stride 8):
#   +0  barrier_lock             +8  barrier_arrive_counter   +16 barrier_depart_counter
#   +24 barrier_flag             +32 num_harts_ended (subset) +40 init_lock   +48 init_done
;#init_memory @sync_region_2
.section .sync_region_2, "aw"

    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
