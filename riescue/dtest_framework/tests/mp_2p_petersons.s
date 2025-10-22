;#test.name       sample_test
;#test.author     mbrothers@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp         on
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
;#test.summary    - intended to demonstrate using the MUTEX_ACQUIRE_PETERSONS and MUTEX_RELEASE_PETERSONS macros
;#test.summary    - as the names suggest these macros implement Peterson's algorithm for mutual exclusion, this implementation compatible with 2 harts.

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
    # Reset the test data
    la t0, h0_flag
    sd x0, 0(t0)

    la t0, h1_flag
    sd x0, 0(t0)

    la t0, turn
    sd x0, 0(t0)

    OS_SYNC_HARTS test01_initialized

    GET_MHART_ID # can call whenever desired

    bnez s1, h1_code
    j h0_code

    h0_flag:
        .dword 0x0
    h1_flag:
        .dword 0x0
    turn:
        .dword 0x0

    cs:
        j release_flag

    h0_code:
        #Set my flag
        la t0, h0_flag
        li t1, 1
        sd t1, 0(t0)

        #Set turn to 1
        la t0, turn
        li t1, 1
        sd t1, 0(t0)

        h0_wait:
            #Check h1's flag
            la t0, h1_flag
            ld t1, 0(t0)
            #Check turn
            la t2, turn
            ld t3, 0(t2)
            # wait if 1's flag and 1's turn
            and t4, t1, t3
            bnez t4, h0_wait
            j cs

    h1_code:
        #Set my flag
        la t0, h1_flag
        li t1, 1
        sd t1, 0(t0)

        #Set turn to 0
        la t0, turn
        li t1, 0
        sd t1, 0(t0)

        h1_wait:
            #Check h0's flag
            la t0, h0_flag
            ld t1, 0(t0)
            #Check my turn
            la t2, turn
            ld t3, 0(t2)
            #Wait if 0's flag and 0's turn
            li t4, 1
            and t4, t1, t4 # 0's flag is 1
            li t5, 1
            xor t5, t5, t3 # and turn is 0
            and t4, t5, t5
            beqz t4, h1_wait
            j cs

    release_flag:
        la t0, h0_flag
        add t0, t0, s1 #Offset to my flag
        sd x0, 0(t0) #Release my flag

    OS_SYNC_HARTS test01_done

    ;#test_passed()


#####################
# test02: sample test 2, one time randomized length test
#####################
;#discrete_test(test=test02)
test02:
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

