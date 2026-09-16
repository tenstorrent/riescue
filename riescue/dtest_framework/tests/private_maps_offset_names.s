;#test.name       private_maps_offset_names
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
;#test.features   private maps with named base+offset page mappings and init_memory
;#test.tags       private_maps offset_mapping init_memory
;#test.summary
;#test.summary    Verifies --private_maps with lin_name/phys_name offset forms
;#test.summary    (base+0x1000) against named random_addr windows. Each private
;#test.summary    map gets its own contiguous phys window so ;#init_memory at the
;#test.summary    base (with .org for the offset page) can initialize both pages.
;#test.summary    Each hart switches to mapN and checks base and +0x1000.
;#test.summary

;#page_map(name=map0, mode=sv39);
;#page_map(name=map1, mode=sv39);

;#random_addr(name=buf_base_lin, type=linear, size=0x2000, and_mask=0xfffffffffffff000)
;#random_addr(name=buf_phys_0, type=physical, size=0x2000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=buf_base_lin, phys_name=buf_phys_0, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map0'])
;#page_mapping(lin_name=buf_base_lin+0x1000, phys_name=buf_phys_0+0x1000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map0'])

;#random_addr(name=buf_phys_1, type=physical, size=0x2000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=buf_base_lin, phys_name=buf_phys_1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map1'])
;#page_mapping(lin_name=buf_base_lin+0x1000, phys_name=buf_phys_1+0x1000, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map1'])

;#init_memory @buf_base_lin: map0
  .word 0x11111111
.org 0x1000
  .word 0xaaaaaaaa

;#init_memory @buf_base_lin: map1
  .word 0x22222222
.org 0x1000
  .word 0xbbbbbbbb

.align 2
.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    GET_MHART_ID
    li t0, 0
    beq x9, t0, set_map0
    li t0, 1
    beq x9, t0, set_map1
    ;#test_failed()

set_map0:
    csrr x1, satp
    li t3, 0x0ffff00000000000
    and x1, x1, t3
    li t3, (map0_sptbr>>12) | 0x8000000000000000
    or x1, x1, t3
    csrw satp, x1
    sfence.vma
    li t1, buf_base_lin
    lwu t2, 0(t1)
    li t3, 0x11111111
    bne t2, t3, fail_0
    li t1, buf_base_lin + 0x1000
    lwu t2, 0(t1)
    li t3, 0xaaaaaaaa
    bne t2, t3, fail_0
    ;#test_passed()
fail_0:
    ;#test_failed()

set_map1:
    csrr x1, satp
    li t3, 0x0ffff00000000000
    and x1, x1, t3
    li t3, (map1_sptbr>>12) | 0x8000000000000000
    or x1, x1, t3
    csrw satp, x1
    sfence.vma
    li t1, buf_base_lin
    lwu t2, 0(t1)
    li t3, 0x22222222
    bne t2, t3, fail_1
    li t1, buf_base_lin + 0x1000
    lwu t2, 0(t1)
    li t3, 0xbbbbbbbb
    bne t2, t3, fail_1
    ;#test_passed()
fail_1:
    ;#test_failed()

test_cleanup:
    ;#test_passed()
