;#test.name       interrupt_enabled_test
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      interrupts
;#test.features   interrupts
;#test.tags       interrupts
;#test.summary
;#test.summary    Tests that interrupts are disabled by default unless --interrupts_enabled is passed through



.section .code, "ax"

.align 2
USER_INTERRUPT_TABLE:
    li a0, 0xbeef
    li t0, ~(1<<1)      # Write a 0 to interrupt bit
    csrw mip, t0
    mret


test_setup:
    ;#test_passed()


#####################
# test01: Check that interrupts are enabled
#####################
;#discrete_test(test=test01)
test01:
    # Trigger SSI
    li t0, (1<<1)
    csrs mie, t0
    csrs mip, t0

    li t0, 0xbeef
    bne t0, a0, failed
    ;#test_passed()


test_cleanup:
    ;#test_passed()


#####################
# Default data section
#####################
.section .data
my_data:
    .dword 0xc001c0de
    .dword 0xdeadbeee
