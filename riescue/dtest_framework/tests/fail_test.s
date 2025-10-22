;#test.name       fail_test
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       any
;#test.env        any
;#test.cpus       1
;#test.paging     any
;#test.category   arch
;#test.class      none
;#test.features
;#test.tags       unit test
;#test.summary
;#test.summary      Test that fails; checks that expect_fail works
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
    ;#test_failed()





#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
test_cleanup:
    ;#test_passed()

#####################
# Default data section
#####################
.section .data
expected_tohost:
    .dword 0x00000f00
