;#test.name       sample_test
;#test.author     mbrothers@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp         on
;#test.mp_mode    simultaneous
;#test.cpus       5
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
;#test.summary    - intended to demonstrate using the MUTEX_ACQUIRE_LR_SC and MUTEX_RELEASE_LR_SC macros.
;#test.summary    - as the names suggest these macros implement mutex using the LR/SC instructions forming the "compare and swap" idiom.

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

;#random_addr(name=semaphore,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=semaphore_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=semaphore, phys_name=semaphore_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])


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
    j passed


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    GET_MHART_ID # can call whenever desired
    li a0, test01_user_lock
    mv a1, x0 # We want to get the lock in its ready state
    addi a2, s1, 1 # Desired value is hartid plus 1, unique to this hart
    addi a3, x0, 1
    mv t0, x0
    MUTEX_ACQUIRE_LR_SC test01_1

    # Do some reads and write with some checks which will mess up under sufficient contamination.
    li t2, test01_another_user_lock
    ld t3, 0(t2)
    bnez t3, failed
    addi t3, s1, 1
    sd t3, 0(t2)
    ld t4, 0(t2)
    bne t3, t4, failed
    sd x0, 0(t2)

    li a0, test01_user_lock
    addi a1, s1, 1 # Expected value is hartid plus 1, because we dont expect any other hart to have modified it.
    mv a2, x0 # We want to reset the lock.
    addi a3, x0, 1
    mv t0, x0
    MUTEX_RELEASE_LR_SC test01_2

    j passed

#####################
# test02: sample test 2
#####################
;#discrete_test(test=test02)
test02:
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
    j passed


#####################
# test03: sample test 3
#####################
;#discrete_test(test=test03)
test03:
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
    j passed


#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
#FIXME: this may have been removed as a feature.
test_cleanup:
    j passed


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

;#init_memory @semaphore
    .dword 0x3
