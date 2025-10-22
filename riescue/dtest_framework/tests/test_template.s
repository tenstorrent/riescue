;#test.name       sample_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv32
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     all
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
;#random_data(name=data1, type=bits32,                  and_mask=0xfffff000)


#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear32,   size=0x1000, and_mask=0xfffff000)
;#random_addr(name=phys1, type=physical32, size=0x1000, and_mask=0xfffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1)

# Another random_data and page_mapping entry
;#random_addr(name=lin2,  type=linear32,   size=0x1000, and_mask=0xfffff000)
;#random_addr(name=phys2, type=physical32, size=0x1000, and_mask=0xfffff000)
;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1)


.section .text

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
;#discrete_test(test=test02)
test01:
    nop
    # la t1, my_data  # t1 = my_data
    li t1, 0xdeadbeef
    li t1, lin1 # <-- use lin1 as an address for ld/st
    lwu t2, 0(t1)

    # 'passed' and 'failed' are special RiESCUE defined labels, which each
    # discrete_test must use to indicate the end of the discrete_test

    # ;#test_failed()  <-- ';#test_failed()' should be used to indicate OS that discrete_test
    #               hit a fail condition and gracefully exit the test with errorcode

    ;#test_failed()  # <-- ';#test_passed()' should be used to indicate OS that discrete_test
    #               hit a pass condition and OS is free to schedule the next test


#####################
# test02: sample test 2
#####################
;#discrete_test(test=test02)
test02:
    addi x0, x0, 0
    li t1, 0xfeedbeef
    la t1, my_data  # t1 = my_data
    lwu t2, 0(t1)

    ;#test_passed()


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
# Another user defined data section located at address lin2
#####################
;#init_memory @lin2
    .dword 0xc001c0de
