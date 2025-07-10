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
;#random_addr(name=adj_matrix,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=adj_matrix, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

;#random_addr(name=dist,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=dist, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

;#random_addr(name=visited,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=visited, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

;#random_addr(name=heap,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=heap, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

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
# test02: Fibonacci sequence
#####################
;#discrete_test(test=test02)
test02:
    # Initialize stack pointer
    # la sp, _stack_top
    li sp, os_stack+(0x1000*31)

# Define constants
MAX_NODES = 10
MAX_EDGES = 100
INF = 1000

# Define main function
.globl main
main:
    # Initialize adjacency matrix
    # jal init

    # Set the source node
    li a0, 0

    # Call Prim's algorithm
    jal prim

exit:
    # End of program
    j passed

# Function to initialize adjacency matrix
# init:
#     li a0, 0
#     li a1, MAX_NODES
#     li a2, MAX_EDGES
#     la t0, adj_matrix
#     loop1:
#         la t1, (adj_matrix)(a0)
#         li t2, 0
#         loop2:
#             sw t2, (t1)
#             addi t1, t1, 4
#             blt t2, a1, loop2
#         addi t0, t0, a1, slli a1, a1, 2
#         blt a0, a2, loop1
#     ret

# Function to find the minimum spanning tree using Prim's algorithm
prim:
    # Initialize distance array with INF and visited array with 0
    li t0, dist
    li t1, INF
    li a1, MAX_NODES
    li a2, dist
    li a3, heap
    li t2, 0
 #   loop1:
 #       sw t1, (t0)
 #       addi t0, t0, 4
 #       sw t2, (t0)
 #       addi t0, t0, 4
 #       blt t2, a1, loop1

    # Set the distance of the source node to 0
    slli t1, a0, 2
    li t0, dist
    add t0, t0, t1
    li t2, 0
    sw t2, (t0)

    # Create a priority queue
    li t0, heap
    li t1, 1
    sw t1, (t0)
    li t1, 0
    sw t1, 4(t0)

    # Add the source node to the priority queue
    slli t1, a0, 2
    # add t0, t0, a3
    add t0, t0, 8
    sw t1, (t0)
    addi t0, t0, 4
    sw t2, (t0)
    addi t0, t0, 4

    # Initialize heap size and MST weight
    li t1, 1
    li t2, 0

    # Main loop
    loop2:
        # Get the vertex with the minimum distance from the priority queue
        li t0, heap
        lw t1, 4(t0)
        slli t1, t1, 2
        li t0, dist
        add t0, t0, t1
        lw t1, (t0)

        # Remove the vertex from the priority queue
        lw t0, 8(t0)
        sw t0, 8(a3)
        addi t1, t1, 1
        sw t1, 4(a3)

        # Update MST weight
        add t2, t2, t1

        # Update distances of adjacent vertices
        slli t1, t0, 2
        li t3, adj_matrix
    add t3, t3, t1
    addi t1, t0, 1
    slli t1, t1, 2
    add t3, t3, t1
    li t1, INF
    li t4, dist
    lw t5, (t4)
    loop3:
        beq t5, t0, skip
        li t6, visited
        slli s0, t5, 2
        add t6, t6, s0
        lw s0, (t6)
        beq s0, zero, update
        j next
        update:
        lw t6, (t3)
        bge t6, t1, next
        slli t1, t5, 2
        add t4, a2, t1
        lw t1, (t3)
        sw t1, (t4)
        slli t1, t5, 2
        add t4, a3, 8
        sw t1, (t4)
        addi t4, t4, 4
        sw t3, (t4)
        addi t4, t4, 4
        lw t3, (t3)
        sw t3, (t4)
        addi t1, t1, 1
        li t6, visited
        add t6, t6, s0
        sw t1, (t6)
        next:
        addi t3, t3, 4
        addi t5, t5, 1
        blt t5, a1, loop3
    skip:
    li t6, visited
    slli t1, t0, 2
    add t6, t6, t1
    li s1, 1
    sw s1, (t6)

    # Check if all vertices are visited
    li t0, visited
    lw t1, (t0)
    li t2, 1
    loop4:
        beq t1, zero, exit
        addi t0, t0, 8
        lw t1, (t0)
        blt t2, a1, loop4

# End of function
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
# .section .data
# my_data:
#     .dword 0xc001c0de
#     .dword 0xdeadbeee

.section .data
_adjacency_matrix:
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0
    .byte 0,1,1,0,0,1,0,0,1,0,1,0,0,1,0,0,0,1,0,1,1,0,1,0


#####################
# User defined data section located at address lin1
#####################
;#init_memory @adj_matrix
.space MAX_NODES*4, 0

;#init_memory @dist
.space MAX_NODES*8, 0

;#init_memory @visited
.space MAX_NODES*4, 0

;#init_memory @heap
.space MAX_NODES*4, 0

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
