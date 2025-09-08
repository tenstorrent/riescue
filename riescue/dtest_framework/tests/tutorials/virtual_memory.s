
;#test.arch       rv64
;#test.priv       user super
;#test.env        bare_metal
;#test.paging     sv39 sv48 sv57

.section .code, "ax"

test_setup:
    j passed

;#random_data(name=my_word, type=bits32, and_mask=0xFFFFFFFF)

;#random_addr(name=physical_address, type=physical)
;#random_addr(name=virtual_address,  type=linear)
;#page_mapping(lin_name=virtual_address, phys_name=physical_address, v=1, r=1, w=1, a=1, d=1, pagesize=['any'])

;#discrete_test(test=test_paging)
test_paging:
    li t0, virtual_address
    lw t1, 0(t0)
    li t2, my_word
    beq t1, t2, paging_passed # Assert the loaded virtual address is reading data correctly
    j failed
paging_passed:
    j passed

test_cleanup:
    j passed

.section .data
;#init_memory @virtual_address
    .word my_word
