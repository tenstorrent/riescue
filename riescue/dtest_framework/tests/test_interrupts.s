;#test.name       sample_test
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        virtualized bare_metal any
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      interrupt
;#test.tags       interrupt
;#test.summary
;#test.summary    Checks that vectored_interrupt directive works. Triggers an SSI interrupt


.section .code, "aw"


;#vectored_interrupt(1, ssi_handler)
ssi_handler:
    li a0, 0xbeef
    li t0, ~(1<<1)      # Write a 0 to interrupt bit
    csrw mip, t0
    mret

;#vectored_interrupt(SEI, sei_handler)
sei_handler:
    li a0, 0xbeef
    li t0, ~(1<<1)      # Write a 0 to interrupt bit
    csrw mip, t0
    mret

test_setup:
    j passed

#####################
# test1: Triggers an SSI interrupt and checks that custom handler was used
#####################
;#discrete_test(test=test1)
test1:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

    # Trigger SSI
    li t0, (1<<1)
    csrs mie, t0
    csrs mip, t0

    li t0, 0xbeef
    bne t0, a0, failed
    j passed

test_cleanup:
    j passed

.section .data
