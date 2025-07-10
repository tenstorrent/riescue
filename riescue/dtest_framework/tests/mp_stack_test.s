;#test.name       mp_stack_test
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp_mode    simultaneous
;#test.cpus       2
;#test.paging     sv57
;#test.category   arch
;#test.class      NA
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       vectors vector_ld_st
;#test.summary
;#test.summary      Demonstrates that mp stack works and doesn't have any conflicts
;#test.summary


# page to put test data in
;#random_addr(name=my_data_page,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=my_data_page_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=my_data_page, phys_name=my_data_page_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

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
    j passed


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:

    GET_MHART_ID # returns hartid to s1



    li t0, 100
    addi sp, sp, -4                 # allocate word on stack
    loop:
        sw s1, 0(sp)                # Write HART ID to allocated word, check that it's still this hart's word
        addi t0, t0, -1
        lw t1, 0(sp)
        bne s1, t1, failed

        beq s1, x0, eval            # if hart1, add some delay to ensure eventual overlap
        nop
        nop
        nop
        lw t1, 0(sp)
        bne s1, t1, failed



    eval:
        bnez t0, loop

    addi sp, sp, 4                 # deallocate word on stack


    j passed





#####################
# test_cleanup:
#####################
test_cleanup:
    j passed


#####################
# Default data section
#####################
.section .data

;#init_memory @my_data_page
    .dword 0x0
    .dword 0x1
    .dword 0x2
    .dword 0x3
    .dword 0x4

