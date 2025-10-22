;#test.name       sample_test
;#test.author     mbrothers@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp_mode    simultaneous
;#test.cpus       2
;#test.paging     sv57
;#test.category   arch
;#test.class      vector
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       vectors vector_ld_st
;#test.summary
;#test.summary    This section is used for documenting of description of
;#test.summary    overall intention of the test and what each individual
;#test.summary    discrete_test(s) are supposed to verify
;#test.summary
;#test.summary    test01:
;#test.summary    - shows how to use the macros GET_HART_ID and GET_MHART_ID as well as the CRITICAL_SECTION_AMO macro.
;#test.summary    -- GET_HART_ID provides a unique number per hart, so long as all harts have executed the macro, however this number is not the same as mhartid usually and will change if the routine is rerun.
;#test.summary    -- GET_MHART_ID provides the mhartid, which is unique per hart, and will not change if the routine is rerun, however it depends on being able to delegate exceptions to machine mode, which is not always possible.
;#test.summary    -- CRITICAL_SECTION_AMO is a macro to wrap a call to a subroutine you provide, guarded by a user lock, which is implemented with AMOs. The routines that make up this macro can be called individually as well
;#test.summary       if you don't want the critical section to be a single subroutine call. Refer to dtest_framework/runtime/macros.py for your options.
;#test.summary    - shows how to use the OS_SYNC_HARTS macro to synchronize harts with a barrier based on AMOs.
;#test.summary
;#test.summary    test02 and test03:
;#test.summary    - tests of random length, meant to complement test01, stress and synchronization code and exercise the scheduler.


# page to put test data in
;#random_addr(name=my_data_page,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=my_data_page_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=my_data_page, phys_name=my_data_page_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=test01_user_lock,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=test01_user_lock_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=test01_user_lock, phys_name=test01_user_lock_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=test01_another_user_lock,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=test01_another_user_lock_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=test01_another_user_lock, phys_name=test01_another_user_lock_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_data(name=data1, type=bits12, and_mask=0xfff)
;#random_data(name=data2, type=bits12, and_mask=0xfff)
;#random_data(name=data3, type=bits12, and_mask=0xfff)


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
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################
test_setup:
    ;#test_passed()


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    GET_MHART_ID # can call whenever desired

    j cs_test
    # Routine counts the number of harts that have executed the critical section
    critical_subroutine:
        li t0, my_data_page
        lw t1, 0(t0)
        addi t1, t1, 1
        sw t1, 0(t0)
        ret

    cs_test:
    li a0, test01_user_lock
    mv t0, x0
    mv t1, x0
    la a1, critical_subroutine
    CRITICAL_SECTION_AMO test01

    # Make first hart wait for last hart to finish
    OS_SYNC_HARTS test01_another_0
    li t0, my_data_page
    lw t1, 0(t0)
    li t2, num_harts
    lw t0, 0(t2)
    bne t1, t0, failed
    OS_SYNC_HARTS test01_another_1
    GET_MHART_ID # can call whenever desired
    bne s1, x0, test01_dont_write
    li t0, my_data_page
    sw x0, 0(t0)
    test01_dont_write:
    OS_SYNC_HARTS test01_another_2
    li t0, my_data_page
    lw t1, 0(t0)
    bne t1, x0, failed

    li t1, data1
    blt t1, x0, negative_01
    positive_01:
        addi t1, t1, -1
        blt t1, x0, done_01
    negative_01:
        addi t1, t1, 1
        bge t1, x0, done_01
    done_01:

    ;#test_passed() # Needs support from the scheduler to correctly indicate when no more tests are left.


#####################
# test02: sample test 2, one time randomized length test
#####################
;#discrete_test(test=test02)
test02:
    #Id the hart running this test
    #get hart id
    GET_MHART_ID # can call whenever desired
    li t1, data2
    blt t1, x0, negative_02
    positive_02:
        addi t1, t1, -1
        bge t1, x0, positive_02
        j done_02
    negative_02:
        addi t1, t1, 1
        blt t1, x0, negative_02
    done_02:
    ;#test_passed()


#####################
# test03: sample test 3, one time randomized length test
#####################
;#discrete_test(test=test03)
test03:
    GET_MHART_ID # can call whenever desired
    li t1, data3
    blt t1, x0, negative_03
    positive_03:
        addi t1, t1, -1
        bge t1, x0, positive_03
        j done_03
    negative_03:
        addi t1, t1, 1
        blt t1, x0, negative_03
    done_03:
    ;#test_passed()


#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
#FIXME: this may have been removed as a feature.
test_cleanup:
    ;#test_passed()


#####################
# Default data section
#####################
.section .data

;#init_memory @my_data_page
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0
    .dword 0x0

;#init_memory @test01_user_lock
    .dword 0x0

;#init_memory @test01_another_user_lock
    .dword 0x0
