;#test.name       end_test_test
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.env        bare_metal
;#test.cpus       2
;#test.category   arch
;#test.class      vector
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       vectors vector_ld_st
;#test.summary
;#test.summary    Checks that end_test works. Does a load and calls end_test.


.section .code, "ax"

test_setup:
    ;#test_passed()

#####################
# test01: do a load and call end_test
#####################
;#discrete_test(test=test01)
test01:
    li t0, my_data_page
    lw t1, 0(t0)
    j end_test
    ;#test_passed()

# don't end test
;#discrete_test(test=test02)
test02:
    li t0, my_data_page
    lw t1, 0(t0)
    ;#test_passed()

test_cleanup:
    ;#test_passed()



.section .data


# page to put test data in
;#random_addr(name=my_data_page,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=my_data_page_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=my_data_page, phys_name=my_data_page_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

;#init_memory @my_data_page
    .dword 0x0
