;#test.name       sample_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       super
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57
;#test.category   arch
;#test.class      vector
;#test.features   not_hooked_up_yet ext_v.enable ext_fp.disable
;#test.tags       exceptions
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
;#page_mapping(lin_name=lin1, phys_name=phys1, v=0, v_nonleaf=0, r=1, w=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'], modify_pt=1)

;#random_addr(name=lin1a,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1a, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1a, phys_name=phys1a, v=0, r=1, w=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'], modify_pt=1)

;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin2, phys_name=phys2, v=1, r=0, w=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=lin3,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys3, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin3, phys_name=phys3, v=1, r=1, w=0, g_nonleaf=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=lin4,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys4, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin4, phys_name=&random, v=1, r=1, w=1, pagesize=['1gb'])

;#random_addr(name=lin5,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys5, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin5, phys_name=&random, v=1, r=1, w=1, pagesize=['512gb'])

;#random_addr(name=lin6,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys6, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin6, phys_name=&random, v=1, r=1, w=1, a=1, d=1, modify_pt=1, pagesize=['4kb', '2mb'])

;#random_addr(name=lin7,  type=linear,   size=0x206000, and_mask=0xffffffffffe00000)
;#random_addr(name=phys7, type=physical, size=0x206000, and_mask=0xffffffffffe00000)
;#page_mapping(lin_name=lin7, phys_name=phys7, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])
;#page_mapping(lin_name=lin7+0x201000, phys_name=phys7+0x201000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin7+0x202000, phys_name=phys7+0x202000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin7+0x203000, phys_name=phys7+0x203000, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin7+0x204000, phys_name=phys7+0x204000, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin7+0x205000, phys_name=phys7+0x205000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Another random_data and page_mapping entry
# ;#reserve_memory(start_addr=0x500000, addr_type=linear, size=0x1000)
# ;#reserve_memory(start_addr=0x600000, addr_type=linear, size=0x1000)
# ;#reserve_memory(start_addr=0x500000, addr_type=physical, size=0x1000)
#
# ;#page_mapping(lin_addr=0x500000, phys_addr=0x500000, v=1, r=1, w=1, pagesize=['4kb', '2mb'])
# ;#page_mapping(lin_addr=0x600000, phys_name=&random, v=1, r=1, w=1, pagesize=['4kb', '2mb'])


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
    li t1, lin1 # <-- use lin1 as an address for ld/st
    # lwu t2, 0(t1)

    # Setup exception checks
    OS_SETUP_CHECK_EXCP ECALL, excp, excp_ret

    # # Setup exception check
    # li x1, check_excp_expected_cause
    # li t2, ECALL
    # sw t2, 0(x1)

    # # Expected PC
    # la t2, excp
    # li x1, check_excp_expected_pc
    # sd t2, 0(x1)

    # # Return pc
    # la t2, excp_ret
    # li x1, check_excp_return_pc
    # sd t2, 0(x1)

# Following instruction is going to cause an exception
excp:
    ecall
    # We should never reach here
    j failed

# We return here after exception is handled
excp_ret:

    li t1, 0xc001c0de
    # bne t1, t2, failed

    # 'passed' and 'failed' are special RiESCUE defined labels, which each
    # discrete_test must use to indicate the end of the discrete_test

    # j failed  <-- 'j failed' should be used to indicate OS that discrete_test
    #               hit a fail condition and gracefully exit the test with errorcode

    j passed  # <-- 'j passed' should be used to indicate OS that discrete_test
    #               hit a pass condition and OS is free to schedule the next test


#####################
# test02a: Generate a load page fault with v=0
#####################
;#discrete_test(test=test02a)
test02a:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

.ifeq PAGING_MODE_DISABLE
    # Setup exception checks
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, excp2a, excp2a_ret, lin1a

    li x1, lin1a
excp2a:
    lwu t2, 0(x1)

    j failed

    # Setup exception checks
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, excp2a_1, excp2a_ret, lin7 + 0x203000

    li x1, lin7 + 0x203000
excp2a_1:
    lwu t2, 0(x1)

    j failed

.endif

excp2a_ret:
    j passed

#####################
# test02: Generate a load page fault with v_nonleaf=0
#####################
;#discrete_test(test=test02)
test02:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

.ifeq PAGING_MODE_DISABLE
    # Setup exception checks
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, excp2, excp2_ret

    li x1, lin1
excp2:
    lwu t2, 0(x1)

    j failed

.endif

excp2_ret:
    j passed

#####################
# test03: Generate a store page fault with v=0
#####################
;#discrete_test(test=test03)
test03:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

.ifeq PAGING_MODE_DISABLE
    # Setup exception checks
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, excp3, excp3_ret

    li x1, lin1
    li t2, 0
excp3:
    sw t2, 0(x1)

    j failed

.endif

excp3_ret:
    j passed

#####################
# test04: Generate a load page fault with r=0
#####################
;#discrete_test(test=test04)
test04:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

.ifeq PAGING_MODE_DISABLE
    # Setup exception checks
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, excp4, excp4_ret

    li x1, lin2
    li t2, 0
excp4:
    lwu t2, 0(x1)

    j failed

.endif

excp4_ret:
    j passed

#####################
# test05: Generate a store page fault with w=0
#####################
;#discrete_test(test=test05)
test05:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

.ifeq PAGING_MODE_DISABLE
    # Setup exception checks
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, excp5, excp5_ret

    li x1, lin3
    li t2, 0
excp5:
    sw t2, 0(x1)

    j failed

.endif

excp5_ret:
    j passed

#####################
# test06: Modify pagetable and mark v=0 and generate the pagefault
#####################
;#discrete_test(test=test06)
test06:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    li t1, data  # t1 = my_data
    lwu t2, 0(t1)

.ifeq PAGING_MODE_DISABLE
    # We used lin6 for this
    li x1, lin6
    lw t2, 0(x1)

    # Now let's modify the level2 v=0
    li x1, lin6__pt_level2
    ld t2, 0(x1)
    li x3, 0xfffffffffffffffe
    and t2, x3, t2
    sd t2, 0(x1)

    # Before accessing the page again, let's setup the exception due to clearing v=0
    # Setup exception checks
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, excp6, excp6_ret

    SFENCE.VMA
    li x1, lin6
    li t2, 0
excp6:
    sw t2, 0(x1)

    j failed

.endif

excp6_ret:
    # Now let's again set v=1 for level2, so we won't take a pagefault above in next run
    li x1, lin6__pt_level2
    ld t2, 0(x1)
    li x3, 0x1
    or t2, x3, t2
    sd t2, 0(x1)
    SFENCE.VMA

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

vmm_handler_pre:
    nop
    ret

vmm_handler_post:
    nop
    ret

excp_handler_pre:
    # Discrete test can setup any register to pick custom code here per discrete_test
    nop
    li t0, 0xc001daad
    ret

excp_handler_post:
    # Discrete test can setup any register to pick custom code here per discrete_test
    nop
    li t0, 0xc001dead
    ret

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
    .dword 0xc001c0de

#####################
# User defined data section located at address lin7
#####################
;#init_memory @lin7
    .dword 0xc001c0de

#####################
# User defined data section located at address lin1a
#####################
;#init_memory @lin1a
# -> we convert above syntax to this -> .section .lin1a
my_data1a:
    .dword 0xc001c0de


#####################
# Another user defined data section located at address lin2
#####################
;#init_memory @lin2
    .dword 0xc001c0de
