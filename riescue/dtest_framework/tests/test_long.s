;#test.name       sample_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
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

;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])



;#random_addr(name=my_code_page,  type=linear,   size=0x1000, and_mask=0xffffffffff000000)
;#random_addr(name=my_code_page_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=my_code_page, phys_name=my_code_page_phys, v=1, x=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

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

.section .code, "ax"

#####################
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################
test_setup:
    li a0, passed_addr
    ld a1, 0(a0)
    jalr ra, 0(a1)

test_cleanup:
    li a0, passed_addr
    ld a1, 0(a0)
    jalr ra, 0(a1)

#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    li a0, passed_addr
    ld a1, 0(a0)
    jalr ra, 0(a1)

.section .data

