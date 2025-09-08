;#test.name       sample_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57
;#test.category   arch
;#test.class      priv
;#test.features
;#test.summary
;#test.summary    Test to check that M + paging works


;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
;#random_data(name=data2, type=bits20, and_mask=0xffffffff)
;#random_data(name=data3, type=bits22)


;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0x0000003ffffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0x0000003ffffff000)
;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

# Code section for the switch to user mode
.section .code_user_0, "ax"
    # Return to testmode
    li x31, 0xf0001004
    ecall


.section .code, "ax"

#####################
test_setup:
    j passed


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    nop
    li t0, data2
    csrr t0, mstatus

    li t1, lin1
    lw t2, 0(t1)
    li t1, 0xC001
    bne t1, t2, failed

    li t1, lin2
    lw t2, 0(t1)
    li t1, 0xBEEF
    bne t1, t2, failed

    li t1, 0xc001c0de
    j passed



# #####################
# # test03: sample test 3
# #####################
;#discrete_test(test=test03)
test03:
    li x31, 0xf0001003 # Switch to user mode
    ecall

    csrr t0, mstatus

#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
test_cleanup:
    # Put your common initialization code here, e.g. initialize csr here if needed
    li x1, 0xc0010002
    j passed


#####################
# Default data section
#####################
.section .data
my_data:
    .dword 0xc001c0de
    .dword 0xdeadbeee


#####################
# User defined data section located at address lin1
#####################
;#init_memory @lin1
    .word 0xC001

;#init_memory @lin2
    .word 0xBEEF

