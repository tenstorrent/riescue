;#test.name       sample_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       super user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.paging_g   sv39 sv48 sv57
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

# ;#page_map(name=map1, mode=sv39)
# ;#page_map(name=map2, mode=sv48)
# ;#page_map(name=map3, mode=sv57)

#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'], gstage_vs_leaf_pagesize=['2mb'])

;#random_addr(name=lin1_io,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1_io, type=physical, io=0, size=0x1000, and_mask=0xfffffffffffff000)
# ;#page_mapping(lin_name=lin1_io, phys_name=phys1_io, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], gstage_vs_leaf_pagesize=['4kb'])
;#page_mapping(lin_name=lin1_io, phys_name=phys1_io, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
; #random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0x0000003ffffff000)
; #random_addr(name=phys2, type=physical, size=0x1000, and_mask=0x0000003ffffff000)
# ;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map1', 'map2', 'map3'])
; #page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
;#random_addr(name=lin7,  type=linear,   size=0x2000, and_mask=0xffffffffffffe000)
;#random_addr(name=phys7, type=physical, size=0x2000, and_mask=0xffffffffffffe000)
;#page_mapping(lin_name=lin7, phys_name=phys7, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], gstage_vs_leaf_pagesize=['4kb'])
;#page_mapping(lin_name=lin7+0x1000, phys_name=phys7+0x1000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], gstage_vs_leaf_pagesize=['4kb'])

# Another random_data and page_mapping entry
;#random_addr(name=lin3,  type=linear,   size=0x1000, and_mask=0xffffffffc0000000)
;#random_addr(name=phys3, type=physical, size=0x1000, and_mask=0xffffffffc0000000)
;#page_mapping(lin_name=lin3, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['1gb'], gstage_vs_nonleaf_pagesize=['1gb'])

;#random_addr(name=lin4,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys4, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin4, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], gstage_vs_leaf_pagesize=['1gb'])

# A bit of documentation on <attr>_* attributes
# <attr> = 1 : set leaf level attribute to 1
# <attr>_nonleaf = 1 : set non-leaf level attribute to 1
# <attr>_leaf_gnonleaf = 1 : set one of the g-stage nonleaf level (of the vs leaf level) attribute to 1
# <attr>_nonleaf_gnonleaf = 1 : set one of the g-stage nonleaf level (of one of the vs nonleaf level) attribute to 1
# <attr>_leaf_gleaf = 1 : set one of the g-stage leaf level (of the vs leaf level) attribute to 1
# <attr>_nonleaf_gleaf = 1 : set one of the g-stage leaf level (of one of the vs nonleaf level) attribute to 1
# gstage_vs_leaf_pagesize = ['4kb'] : set g-stage vs leaf level pagesize to 4kb
# gstage_vs_nonleaf_pagesize = ['1gb'] : set g-stage vs nonleaf level pagesize to 1gb

;#random_addr(name=lin5,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys5, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin5, phys_name=&random, v=1, r=1, w=1, a=1, d=1, g_nonleaf=0, pagesize=['1gb'])
;# ;#page_mapping(lin_name=lin5, phys_name=&random, v=1, r=1, w=1, a=1, d=1, g_nonleaf=0, pagesize=['256tb'])

;#random_addr(name=lin6,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys6, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6, phys_name=&random, v=1, r=1, w=1, d=1, a=0)

;#random_addr(name=lin6_a,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys6_a, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6_a, phys_name=phys6_a, v=1, r=1, w=1, a=1, d=1, a_nonleaf_gleaf=0)

;#random_addr(name=lin6_d,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys6_d, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6_d, phys_name=phys6_d, v=1, r=1, w=1, a=1, d=0)

;#random_addr(name=lin6_d_g,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys6_d_g, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6_d_g, phys_name=phys6_d_g, v=1, r=1, w=1, a=1, d=1, d_leaf_gleaf=0)

;#random_addr(name=lin8,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys8, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin8, phys_name=phys8, v=1, r=1, w=1, a=1, d=1, v_leaf_gnonleaf=0, pagesize=['4kb'])

;#random_addr(name=lin8_a,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys8_a, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin8_a, phys_name=phys8_a, v=1, r=1, w=1, a=1, d=1, v_nonleaf_gnonleaf=0, pagesize=['4kb'])

;#random_addr(name=lin9,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys9, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin9, phys_name=phys9, v=1, r=1, w=1, a=1, d=1, g_nonleaf_gnonleaf=1)

;#random_addr(name=lin9_a,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys9_a, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin9_a, phys_name=phys9_a, v=1, r=1, w=1, a=1, d=1, a_leaf_gleaf=0)

;#random_addr(name=lin9_b,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys9_b, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin9_b, phys_name=phys9_b, v=1, r=1, w=1, a=1, d=1, x_nonleaf=1)

# ;#random_addr(name=lin6,  type=linear,   size=0x205000, and_mask=0xffffffffffc00000)
# ;#random_addr(name=phys6, type=physical, size=0x205000, and_mask=0xffffffffffc00000)
# ;#page_mapping(lin_name=lin6, phys_name=phys6, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])
# ;#page_mapping(lin_name=lin6+0x201000, phys_name=phys6+0x201000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
# ;#page_mapping(lin_name=lin6+0x202000, phys_name=phys6+0x201000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
# ;#page_mapping(lin_name=lin6+0x203000, phys_name=phys6+0x201000, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
# ;#page_mapping(lin_name=lin6+0x204000, phys_name=phys6+0x201000, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
# ;#page_mapping(lin_name=lin6+0x205000, phys_name=phys6+0x201000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
;#reserve_memory(start_addr=0x5000000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x6000000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x5000000, addr_type=physical, size=0x1000)
#
# ;#page_ mapping(lin_addr=0x500000000, phys_addr=0x500000000, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])
# ;#page_ mapping(lin_addr=0x600000000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], modify_pt=1)

;#random_addr(name=my_code_page,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=my_code_page_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=my_code_page, phys_name=&random, v=1, u=1, x=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])


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

# Following code would be executed before and after launching the guest
# Execute before switching from Hypervisor to guest i.e. HS -> VS/VU
# This code is executed as hypervisor in HS mode
vmm_handler_pre:
    # This code is executed before launching the guest HS -> VS/VU
    li t0, 0xc0010003

    # Must add this return instruction at the end of the handler
    ret

# The following code will be executed when going back to hypervisor from guest i.e. VS/VU -> HS
# This generally happens when there's a trap that's taken to hypervisor
vmm_handler_post:
    # This code is executed before launching the guest HS -> VS/VU
    li t0, 0xc0010004
    # Must add this return instruction at the end of the handler
    ret

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
# test01: Test out attribute v_leaf_gnonleaf
#####################
;#discrete_test(test=test01)
test01:
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

    # li t1, lin6 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    # li t1, lin6 + 0x201000 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin7 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, lin7 + 0x1000 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    li t1, 0xc001c0de
    # bne t1, t2, failed

    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP LOAD_GUEST_PAGE_FAULT, excp_test01, ret_test01
    li t1, lin8
excp_test01:
    lwu t2, 0(t1)
    j failed

ret_test01:
    j passed


#####################
# test02: Test out attribute v_nonleaf_gnonleaf
#####################
;#discrete_test(test=test02)
test02:
.ifne PAGING_MODE_DISABLE
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP LOAD_GUEST_PAGE_FAULT, excp_test02, ret_test02
    li t1, lin8_a
excp_test02:
    lwu t2, 0(t1)
    j failed

ret_test02:
.endif
    j passed

#####################
# test03: Test out attribute v_leaf_gleaf
#####################
;#discrete_test(test=test03)
test03:
.ifeq PAGING_MODE_DISABLE
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP LOAD_GUEST_PAGE_FAULT, excp_test03, ret_test03
    li t1, lin9_a
excp_test03:
    lwu t2, 0(t1)
    j failed
.endif

ret_test03:
    j passed

#####################
# test04: Test out attribute v_nonleaf_gleaf
#####################
;#discrete_test(test=test04)
test04:
.ifne PAGING_MODE_DISABLE
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP LOAD_GUEST_PAGE_FAULT, excp_test04, ret_test04
    li t1, lin9
excp_test04:
    lwu t2, 0(t1)
    j failed

ret_test04:
.endif
    j passed


#    # Access my_code_page in user mode with u=1
#    li x1, my_code_page
#    jalr x1, x1, 0
#
#    # Switch to supervisor mode
#    li x31, 0xf0001002
#    ecall
#
#    # We are in supervisor mode now, access my_code_page in supervisor mode again
#    # But since that's going to cause an exception since u=1 for that page and supervisor
#    # can never execute user pages
#    # So, first setup the exception handler checks
#     OS_SETUP_CHECK_EXCP INSTRUCTION_PAGE_FAULT, excp_test01, ret_test01
#
#     li x1, my_code_page
# excp_test01:
#     jalr x1, x1, 0
#     j failed
#
# ret_test01:
#     # Switch back to testmode
#     li x31, 0xf0001004
#     ecall


    # 'passed' and 'failed' are special RiESCUE defined labels, which each
    # discrete_test must use to indicate the end of the discrete_test

    # j failed  <-- 'j failed' should be used to indicate OS that discrete_test
    #               hit a fail condition and gracefully exit the test with errorcode

    j passed  # <-- 'j passed' should be used to indicate OS that discrete_test
    #               hit a pass condition and OS is free to schedule the next test


#####################
# test05: Test out attribute a=0
#####################
;#discrete_test(test=test05)
test05:
.ifeq PAGING_MODE_DISABLE
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, excp_test05, ret_test05
    li t1, lin6
excp_test05:
    lwu t2, 0(t1)
    j failed

ret_test05:
.endif
    j passed

#####################
# test05_d: Test out attribute d=0
#####################
;#discrete_test(test=test05_d)
test05_d:
.ifeq PAGING_MODE_DISABLE
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, excp_test05_d, ret_test05_d
    li t1, lin6_d
excp_test05_d:
    sw t2, 0(t1)
    j failed

ret_test05_d:
.endif
    j passed

#####################
# test06: Test out attribute a_nonleaf_gleaf=0
#####################
;#discrete_test(test=test06)
test06:
.ifeq (PAGING_MODE_DISABLE || PAGING_G_MODE_DISABLE)
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP LOAD_GUEST_PAGE_FAULT, excp_test06, ret_test06
    li t1, lin6_a
excp_test06:
    lwu t2, 0(t1)
    j failed

ret_test06:
.endif
    j passed

#####################
# test07: Test out attribute d_nonleaf_gleaf=0
#####################
;#discrete_test(test=test07)
test07:
.ifeq PAGING_MODE_DISABLE
    # Access lin8, 8_a, 9, 9_a and expect a guest page fault
    OS_SETUP_CHECK_EXCP STORE_GUEST_PAGE_FAULT, excp_test07, ret_test07
    li t1, lin6_d_g
excp_test07:
    sw t2, 0(t1)
    j failed

ret_test07:
.endif
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
# ;#init_memory @lin2
#     .dword 0xc001c0de
#     jr x3   # jump to

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
# ;#init_memory @lin6
#     .dword 0xc001c0de
# .org 0x1000
#     .dword 0xc001daad

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
# ;#init_memory @0x600000000
#     jr x3   # jump to
#     .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
# ;#init_memory @0x500000000
#     jr x3   # jump to
#     .dword 0xc001c0de

#####################
# Another user defined data section located at address lin2 that has code
#####################
;#init_memory @my_code_page
    ret
    jr x3   # jump to
    .dword 0xc001c0de
