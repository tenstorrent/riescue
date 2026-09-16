;#test.name       private_maps_name_separator_test
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
;#test.features   private maps with map-separator characters in linear names
;#test.tags       private_maps name_map_encoding
;#test.summary
;#test.summary    Verifies --private_maps when linear names contain the
;#test.summary    map-name separator ('.'). Map names may not contain '.';
;#test.summary    combined names are a plain lin.map join, split on the last
;#test.summary    '.' against known private maps. Each hart switches to
;#test.summary    map_hart_N and reads a map-private buffer (shared.buf.a)
;#test.summary    initialized to a distinct value.
;#test.summary

;#page_map(name=map_hart_0, mode=sv39);
;#page_map(name=map_hart_1, mode=sv39);

;#random_addr(name=shared.buf.a, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=shared.buf.a, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map_hart_0'])
;#page_mapping(lin_name=shared.buf.a, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map_hart_1'])

;#random_addr(name=scratch.lin.0, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=scratch.lin.0, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map_hart_0'])
;#page_mapping(lin_name=scratch.lin.0, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'], page_maps=['map_hart_1'])

;#init_memory @shared.buf.a: map_hart_0
  .word 0x11111111

;#init_memory @shared.buf.a: map_hart_1
  .word 0x22222222

;#init_memory @scratch.lin.0: map_hart_0
  .word 0x0

;#init_memory @scratch.lin.0: map_hart_1
  .word 0x0

.align 2
.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    GET_MHART_ID
    li t0, 0
    beq x9, t0, set_map_hart_0
    li t0, 1
    beq x9, t0, set_map_hart_1
    ;#test_failed()

set_map_hart_0:
    csrr x1, satp
    li t3, 0x0ffff00000000000
    and x1, x1, t3
    li t3, (map_hart_0_sptbr>>12) | 0x8000000000000000
    or x1, x1, t3
    csrw satp, x1
    sfence.vma
    li t1, shared.buf.a
    lwu t2, 0(t1)
    li t3, 0x11111111
    bne t2, t3, fail_0
    ;#test_passed()
fail_0:
    ;#test_failed()

set_map_hart_1:
    csrr x1, satp
    li t3, 0x0ffff00000000000
    and x1, x1, t3
    li t3, (map_hart_1_sptbr>>12) | 0x8000000000000000
    or x1, x1, t3
    csrw satp, x1
    sfence.vma
    li t1, shared.buf.a
    lwu t2, 0(t1)
    li t3, 0x22222222
    bne t2, t3, fail_1
    ;#test_passed()
fail_1:
    ;#test_failed()

test_cleanup:
    ;#test_passed()
