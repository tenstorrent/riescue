
.global _start
_start:
    li t0, 0x1
    li t1, 0x2
    add t2, t0, t1


end_test:
    .extern tohost
    la t1, tohost

    # To test max instruction
.if INFINITE_LOOP==1
    j infinite_loop
.endif

    # To test fail tohost
.if TEST_FAIL==1
    j fail
.endif

    li t0, 0x1
pass:
    sw t0, 0(t1)
    j pass

fail:
    li t0, 0x3
    sw t0, 0(t1)
    j fail


infinite_loop:
    j infinite_loop

