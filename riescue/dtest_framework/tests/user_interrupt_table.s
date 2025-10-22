;#test.name       user_interrupt_table
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        virtualized bare_metal any
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      interrupt
;#test.features   ext_v.disable ext_fp.disable
;#test.tags       interrupt
;#test.summary
;#test.summary    Checks that user_interrupt_table is used instead of default interrupt / exception table. Needs to be ran with --user_interrupt_table


.section .code, "aw"


.align 2
USER_INTERRUPT_TABLE:
    li a0, 0xbeef
    li t0, ~(1<<1)      # Write a 0 to interrupt bit
    csrw mip, t0
    mret



#####################
# test_setup:
#####################
test_setup:
    ;#test_passed()

#####################
# test1: Check that --user_interrupt_table correctly jumps to USER_INTERRUPT_TABLE
#####################
;#discrete_test(test=test1)
test1:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS
    SET_DIRECT_INTERRUPTS


    # Trigger SSI
    li t0, (1<<1)
    csrs mie, t0
    csrs mip, t0

    li t0, 0xbeef
    bne t0, a0, failed
    ;#test_passed()

test_cleanup:
    ;#test_passed()

.section .data
