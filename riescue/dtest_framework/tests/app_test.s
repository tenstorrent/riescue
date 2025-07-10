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
;#page_mapping(lin_name=lin1_io, phys_name=phys1_io, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb'])

# Another random_data and page_mapping entry
;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
;#random_addr(name=lin7,  type=linear,   size=0x2000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys7, type=physical, size=0x2000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin7, phys_name=phys7, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin7+0x1000, phys_name=phys7+0x1000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

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

;#random_addr(name=lin6,  type=linear,   size=0x205000, and_mask=0xffffffffffe00000)
;#random_addr(name=phys6, type=physical, size=0x205000, and_mask=0xffffffffffe00000)
;#page_mapping(lin_name=lin6, phys_name=phys6, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])
;#page_mapping(lin_name=lin6+0x201000, phys_name=phys6+0x201000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin6+0x202000, phys_name=phys6+0x202000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin6+0x203000, phys_name=phys6+0x203000, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin6+0x204000, phys_name=phys6+0x204000, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin6+0x205000, phys_name=phys6+0x205000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
;#reserve_memory(start_addr=0x5000000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x6000000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x5000000, addr_type=physical, size=0x1000)
#
;#page_mapping(lin_addr=0x5000000, phys_addr=0x5000000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_addr=0x6000000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_pt=1)


.section .code, "ax"

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

    li t1, lin1 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin3 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin4 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin5 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin6 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin6 + 0x201000 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin7 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin7 + 0x1000 # <-- use lin1 as an address for ld/st
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
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

    li t1, 0xc001c0de
    bne t1, t2, failed

    # Call and return from a user defined section
    li x1, 0x5000000
    jalr x3, x1, 0


    j passed


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
;#init_memory @lin6
    .dword 0xc001c0de
.org 0x1000
    .dword 0xc001daad

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @lin7
    .dword 0xc001c0de
.org 0x1000
    .dword 0xc001daad

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
