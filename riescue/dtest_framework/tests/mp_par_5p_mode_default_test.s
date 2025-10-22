;#test.name       sample_test
;#test.author     mbrothers@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp         on
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
;#test.summary    This test is intended to check that the MP_PARALLEL mode is the default mode.
;#test.summary    It is intended to fail if more than one hart is running the same discrete_test.
;#test.summary
;#test.summary    test01, test02 and test03:
;#test.summary    - These tests are intended to have generation-time randomized duration and to detect if they are being falsely run simultaneously.
;#test.summary    - This provides a test of the functionality of MP_PARALLEL mode, which is meant to run multiple strictly different discrete tests on the harts concurrently.


# page to put test data in
;#random_addr(name=my_data_page,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=my_data_page_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=my_data_page, phys_name=my_data_page_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=test01_parity,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=test01_parity_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=test01_parity, phys_name=test01_parity_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=test02_parity,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=test02_parity_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=test02_parity, phys_name=test02_parity_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=test03_parity,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=test03_parity_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=test03_parity, phys_name=test03_parity_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

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
# test01: sample test 2
#####################
;#discrete_test(test=test01)
test01:
    li t1, data1
    mv t4, t1

    # Precheck parity
    li t2, test01_parity
    ld t3, 0(t2)
    bnez t3, failed
    li t3, 1

    # Counting up or down?
    blt t4, x0, negative_01

    positive_01:
        addi t4, t4, -1
        amoadd.d x0, t3, (t2)
        bge t4, x0, positive_01
        j done_01

    negative_01:
        addi t4, t4, 1
        amoadd.d x0, t3, (t2)
        blt t4, x0, negative_01

    done_01:

    # Postcheck parity
    ld t3, 0(t2)
    addi t3, t3, -1
    mv t1, t1
    bne t3, t1, failed
    sd x0, 0(t2)

    ;#test_passed()


# This test is to have another distinct discrete test
#####################
# test02: sample test 2
#####################
;#discrete_test(test=test02)
test02:
    li t1, data1
    mv t4, t1

    # Precheck parity
    li t2, test02_parity
    ld t3, 0(t2)
    bnez t3, failed
    li t3, 1

    # Counting up or down?
    blt t4, x0, negative_02

    positive_02:
        addi t4, t4, -1
        amoadd.d x0, t3, (t2)
        bge t4, x0, positive_02
        j done_02

    negative_02:
        addi t4, t4, 1
        amoadd.d x0, t3, (t2)
        blt t4, x0, negative_02

    done_02:

    # Postcheck parity
    ld t3, 0(t2)
    addi t3, t3, -1
    mv t1, t1
    bne t3, t1, failed
    sd x0, 0(t2)

    ;#test_passed()


# This test is also just to have another distinct discrete test
#####################
# test03: sample test 3
#####################
;#discrete_test(test=test03)
test03:
    li t1, data1
    mv t4, t1

    # Precheck parity
    li t2, test03_parity
    ld t3, 0(t2)
    bnez t3, failed
    li t3, 1

    # Counting up or down?
    blt t4, x0, negative_03

    positive_03:
        addi t4, t4, -1
        amoadd.d x0, t3, (t2)
        bge t4, x0, positive_03
        j done_03

    negative_03:
        addi t4, t4, 1
        amoadd.d x0, t3, (t2)
        blt t4, x0, negative_03

    done_03:

    # Postcheck parity
    ld t3, 0(t2)
    addi t3, t3, -1
    mv t1, t1
    bne t3, t1, failed
    sd x0, 0(t2)

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

;#init_memory @test01_parity
    .dword 0x0

;#init_memory @test02_parity
    .dword 0x0

;#init_memory @test03_parity
    .dword 0x0

