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
    ;#test_passed()


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

    # ;#test_failed()  <-- ';#test_failed()' should be used to indicate OS that discrete_test
    #               hit a fail condition and gracefully exit the test with errorcode

    ;#test_passed()  # <-- ';#test_passed()' should be used to indicate OS that discrete_test
    #               hit a pass condition and OS is free to schedule the next test


#####################
# test02: Fibonacci sequence
#####################
;#discrete_test(test=test02)
test02:
    # Initialize stack pointer
    # la sp, _stack_top
    li sp, os_stack+(0x1000*31)

    # Initialize adjacency matrix
    li a0, data
    li t0, 0
    li t1, 0
    li t2, 3
    li t3, 2
    li t4, 5
    li t5, 7
    sb t0, 0(a0)
    sb t3, 1(a0)
    sb t1, 2(a0)
    sb t4, 3(a0)
    sb t2, 4(a0)
    sb t5, 5(a0)
    sb t1, 6(a0)
    sb t2, 7(a0)
    sb t0, 8(a0)
    sb t2, 9(a0)
    sb t1, 10(a0)
    sb t3, 11(a0)
    sb t4, 12(a0)
    sb t2, 13(a0)
    sb t0, 14(a0)
    sb t5, 15(a0)
    sb t3, 16(a0)
    sb t4, 17(a0)
    sb t5, 18(a0)
    sb t0, 19(a0)
    sb t2, 20(a0)
    sb t1, 21(a0)
    sb t5, 22(a0)
    sb t2, 23(a0)

    # Initialize distances matrix
    li a0, data+0x200
    li t0, 0
    li t1, 1
    li t2, 2
    li t3, 3
    li t4, 4
    li t5, 5
    sw t0, 0(a0)
    sw t3, 4(a0)
    sw t1, 8(a0)
    sw t2, 12(a0)
    sw t4, 16(a0)
    sw t0, 20(a0)
    sw t5, 24(a0)
    sw t3, 28(a0)
    sw t4, 32(a0)
    sw t5, 36(a0)
    sw t0, 40(a0)
    sw t1, 44(a0)
    sw t4, 48(a0)
    sw t5, 52(a0)
    sw t2, 56(a0)
    sw t0, 60(a0)
    sw t3, 64(a0)
    sw t5, 68(a0)
    sw t2, 72(a0)
    sw t1, 76(a0)
    sw t0, 80(a0)
    sw t4, 84(a0)

    # Call Floyd-Warshall function
    li a0, data+0x200
    call floyd_warshall

#     # Print distances matrix
#     la a0, _distances_matrix
#     li t6, 0
#     li s4, 0
#     li s5, 0
# print_loop:
#     beq t6, 5, print_exit
#     beq s4, 5, print_next_row
#     lw s6, s5(a0)
#     li s7, 10
#     call print_integer
#     li s8, 32
#     call print_character
#
#     addi s5, s5, 4
#     addi s4, s4, 1
#     j print_loop
#
# print_next_row:
#     li s8, 10
#     call print_character
#     li s4, 0
#     addi t6, t6, 1
#     j print_loop
#
# print_exit:
#     li s8, 10
#     call print_character
#     li s8, 10
#     call print_character

    # Exit program
    # li a7, 10
    # ecall
    ;#test_passed()

# Function to perform Floyd-Warshall algorithm
floyd_warshall:
    addi sp, sp, -20
    sw ra, 0(sp)
    sw s0, 4(sp)
    sw s1, 8(sp)
    sw s2, 12(sp)
    sw s3, 16(sp)

    li s0, 6 # Number of vertices
    li s1, 4 # Number of bytes per entry in distances matrix
    li s2, 1 # Number of bytes per entry in adjacency matrix

    li t0, 0
    li t1, 1
    li t2, 2

    # Perform algorithm
    li t3, 0
floyd_outer_loop:
    beq t3, s0, floyd_exit
    li t4, 0
floyd_middle_loop:
    beq t4, s0, floyd_inner_loop_end
    li t5, 0
floyd_inner_loop:
    beq t5, s0, floyd_middle_loop_end
    li a0, data+0x20
    mv a3, t4
    mul a3, a3, s1
    add a3, a3, a0
    # lw t6, t4*s1(a0)
    lw t6, 0(a3)
    li a0, data+0x20
    mv a3, t3
    mul a3, a3, s1
    add a3, a3, a0
    # lw s4, t3*s1(a0)
    lw s4, 0(a3)
    li a0, data
    mv a3, t4
    mul a3, a3, s2
    add a3, a3, a0
    # lbu s5, t4*s2(a0)
    lbu s5, 0(a3)
    beq s5, x0, floyd_skip_update
    li a0, data
    mv a3, t3
    mul a3, a3, s2
    add a3, a3, a0
    # lbu s6, t3*s2(a0)
    lbu s6, 0(a3)
    beq s6, x0, floyd_skip_update
    add s7, s4, t6
    li a0, data+0x200
    mv a3, t5
    mul a3, a3, s1
    add a3, a3, a0
    # lw s8, t5*s1(a0)
    lw s8, 0(a3)
    blt s7, s8, floyd_update
floyd_skip_update:
    addi t5, t5, 1
    j floyd_inner_loop
floyd_update:
    li a0, data+0x200
    mv a3, t5
    mul a3, a3, s1
    add a3, a3, a0
    # sw s7, t5*s1(a0)
    sw s7, 0(a3)
floyd_continue_inner_loop:
    addi t5, t5, 1
    j floyd_inner_loop
floyd_middle_loop_end:
    addi t4, t4, 1
    j floyd_middle_loop
floyd_inner_loop_end:
    addi t3, t3, 1
    j floyd_outer_loop
floyd_exit:
    lwu ra, 0(sp)
    lwu s0, 4(sp)
    lwu s1, 8(sp)
    lwu s2, 12(sp)
    lwu s3, 16(sp)
    addi sp, sp, 20
    jr ra

#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
test_cleanup:
    # Put your common initialization code here, e.g. initialize csr here if needed
    li x1, 0xc0010002
    ;#test_passed()


#####################
# Default data section
#####################
# .section .data
# my_data:
#     .dword 0xc001c0de
#     .dword 0xdeadbeee

.section .data
_adjacency_matrix:
    .byte 0, 3, 2, 5, 7, 0, 2, 1, 3, 4, 5, 0, 2, 1, 5, 2

.org 0x200
_distances_matrix:
    .word 0, 8, 9, 5, 7, 100000, 2, 1, 3, 4, 5, 100000, 2, 1, 0, 2

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
