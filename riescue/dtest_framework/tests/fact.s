;#test.name       sample_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     disable sv39 sv48 sv57 any
;#test.category   arch
;#test.class      vector
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       vectors vector_ld_st
;#test.summary
;#test.summary    This section is used for documenting of description of
;#test.summary    overall intention of the test and what each individual
;#test.summary    discrete_test(s) are supposed to verify
;#test.summary
;#test.summary    test01: sample test 1
;#test.summary
;#test.summary    test02: sample test 2
;#test.summary
;#test.summary



#####################
# Define random data here
#####################
;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
;#random_data(name=data2, type=bits20, and_mask=0xffffffff)
;#random_data(name=data3, type=bits22)


#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=lin1_io,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1_io, type=physical, io=1, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1_io, phys_name=phys1_io, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

# Another random_data and page_mapping entry
;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
;#random_addr(name=lin3,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys3, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin3, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['1gb'])

;#random_addr(name=lin4,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys4, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin4, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=lin5,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys5, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin5, phys_name=&random, v=1, r=1, w=1, a=1, d=1, g_nonleaf=1, pagesize=['256tb'])

;#random_addr(name=lin6,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys6, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6, phys_name=&random, v=1, r=1, w=1, a=1, d=1, modify_pt=1)

# Another random_data and page_mapping entry
;#reserve_memory(start_addr=0x5000000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x6000000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x5000000, addr_type=physical, size=0x1000)
#
;#page_mapping(lin_addr=0x5000000, phys_addr=0x5000000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_addr=0x6000000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_pt=1)


.section .code, "aw"

#####################
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################
test_setup:
    # Put your common initialization code here, e.g. initialize csr here if needed
    li x1, 0xc0010001
    j passed


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    nop
    li t0, data2
    li t1, 0xdeadbeef

    li t1, lin3 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin4 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin5 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, 0xc001c0de
    # bne t1, t2, failed

    # 'passed' and 'failed' are special RiESCUE defined labels, which each
    # discrete_test must use to indicate the end of the discrete_test

    # j failed  <-- 'j failed' should be used to indicate OS that discrete_test
    #               hit a fail condition and gracefully exit the test with errorcode

    j passed  # <-- 'j passed' should be used to indicate OS that discrete_test
    #               hit a pass condition and OS is free to schedule the next test


#####################
# test02: sample test 2
#####################
;#discrete_test(test=test02)
test02:
    # Initialize stack pointer
    li sp, os_stack+0x9000

    # Calculate the factorial of 5
    li a0, 500
    call factorial

    # Exit the program
    li a7, 93 # Exit system call number
    ecall

# Recursive factorial function
factorial:
    # Save the return address and stack frame pointer
    addi sp, sp, -16
    sd ra, 0(sp)
    sd s0, 8(sp)

    # Base case: if n <= 1, return 1
    li s0, 1
    bleu a0, s0, end_factorial
    # Recursive case: return n * factorial(n-1)
    addi a0, a0, -1
    call factorial
    ld s1, 0(sp)    # load result of factorial(n-1)
    mul s0, s1, a0  # calculate n * factorial(n-1)

    j passed

end_factorial:
    # Restore the return address and stack frame pointer
    ld ra, 0(sp)
    ld s0, 8(sp)
    addi sp, sp, 16

    # Return the factorial result
    ret


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
# -> we convert above syntax to this -> .section .lin1
my_data1:
    li x28, 0xc001c0de

    .dword 0xc001c0de


#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @lin2
    jr x3   # jump to
    .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @lin3
    .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @lin4
    .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @lin5
    jr x3   # jump to
    .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @0x6000000
    jr x3   # jump to
    .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @0x5000000
    jr x3   # jump to
    .dword 0xc001c0de
