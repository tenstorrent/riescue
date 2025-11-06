;#test.name       mp_with_c.s
;#test.author     Himanshu Suri
;#test.arch       rv64
;#test.priv       super
;#test.env        bare_metal
;#test.mp         on
;#test.mp_mode    simultaneous
;#test.cpus       2
;#test.paging     sv39
;#test.category   arch
;#test.class      custom
;#test.features   private page mappings with different physical address generation
;#test.tags       c-execution
;#test.summary
;#test.summary    This test creates a random, named random, and a constant physical
;#test.summary    address and maps them to linear addresses 0x10000, 0x11000, and 0x12000
;#test.summary    respectively as private maps. The first linear address is passed as
;#test.summary    argument to two C functions and offset 0x2000 from it (address 0x12000)
;#test.summary    is used as a shared buffer (same constant physical address) for IPC.
;#test.summary    Stack grows down from linear address 0x16000 down to 0x15000.
;#test.summary

;#page_map(name=map0, mode=sv39);
;#page_map(name=map1, mode=sv39);

;#page_mapping(lin_addr=0x10000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map0'])

;#random_addr(name=phys_addr_1, type=physical, size=0x2000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_addr=0x11000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map0'])


;#page_mapping(lin_addr=0x10000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map1'])

;#random_addr(name=phys_addr_2, type=physical, size=0x2000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_addr=0x11000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map1'])

;#page_mapping(lin_addr=0x12000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

;#page_mapping(lin_addr=0x15000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map0'])

;#page_mapping(lin_addr=0x15000, phys_name=&random, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'], page_maps=['map1'])

;#init_memory @0x10000: map0
  .rept 128
    .byte 0
  .endr

;#init_memory @0x11000: map0
  .rept 128
    .byte 0
  .endr

;#init_memory @0x10000: map1
  .rept 128
    .byte 0
  .endr

;#init_memory @0x11000: map1
  .rept 128
    .byte 0
  .endr

;#init_memory @0x12000
  .rept 128
    .byte 0
  .endr

.align 2

.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    GET_MHART_ID
    li t0, 0
    beq x9, t0, set_map_0
    li t0, 1
    beq x9, t0, set_map_1
set_map_0:
    csrr x1, satp
    li t3, 0x0ffff00000000000
    and x1, x1, t3
    li t3, (map0_sptbr>>12) | 0x8000000000000000
    or x1, x1, t3
    csrw satp, x1
    sfence.vma
    li a0, 0x10000
    li sp, 0x16000
    jal c_func_0
    beq a0, x0, passed_0
    ;#test_failed()
passed_0:
    ;#test_passed()

set_map_1:
    csrr x1, satp
    li t3, 0x0ffff00000000000
    and x1, x1, t3
    li t3, (map1_sptbr>>12) | 0x8000000000000000
    or x1, x1, t3
    csrw satp, x1
    sfence.vma
    li a0, 0x10000
    li sp, 0x16000
    jal c_func_1
    beq a0, x0, passed_1
    ;#test_failed()
passed_1:
    ;#test_passed()

test_cleanup:
    ;#test_passed()

.section .data
my_data:
    .long 0
