;#test.name       install_excp_handler_mp
;#test.author     njoaquin@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.paging     disable
;#test.mp         on
;#test.mp_mode    simultaneous
;#test.cpus       2

# MP variant of install_excp_handler: the arming state (excp_handler_cause /
# addr / mode) is hart-local, so each hart gets its own handler view.
#
#   - hart 0: arms a BREAKPOINT handler -> its ebreak takes the custom handler
#     (marker[0] = 0xCAFE), then an illegal instruction (cause mismatch) falls
#     back to the original path.
#   - hart 1: arms nothing -> its ebreak takes the original check_excp path and
#     marker[1] must stay zero (proves the handler ran only on hart 0's behalf).
#
# Usage:
#     riescued.py -t .../install_excp_handler_mp.s --seed 1 --run_iss --deleg_excp_to=machine

;#random_addr(name=mp_marker, type=physical, size=16, and_mask=0xfffffffffffffff8)

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    GET_MHART_ID                       # s1 = mhartid (clobbers a0, tp, x31)
    li   t3, 0
    beq  s1, t3, test01_hart0
    j    test01_hart1

    #####################
    # hart 0: handler armed -> ebreak hits the custom handler
    #####################
    test01_hart0:
        OS_INSTALL_EXCP_HANDLER BREAKPOINT, mp_bp_handler, CHECK_EXCP_MODE_MACHINE
        ebreak

        # Handler must have written marker[0]
        li   t0, mp_marker
        ld   t1, 0(t0)
        li   t2, 0xCAFE
        bne  t1, t2, test_fail_label

        # Cause mismatch (illegal) with handler still armed -> original path
        OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, h0_bad, h0_ret
    h0_bad:
        .word 0x00000000
    h0_ret:
        j    test01_done

    #####################
    # hart 1: nothing armed -> ebreak takes the original path
    #####################
    test01_hart1:
        OS_SETUP_CHECK_EXCP BREAKPOINT, h1_bp, h1_ret
    h1_bp:
        ebreak
    h1_ret:
        # marker[1] is only written by a handler running on hart 1; must be zero
        li   t0, mp_marker
        ld   t1, 8(t0)
        bnez t1, test_fail_label

    test01_done:
        ;#test_passed()
        li x31, 0xf0000001  # Test Passed; Schedule test
        ecall

test_fail_label:
    ;#test_failed()

# Runtime-installed BREAKPOINT handler: writes 0xCAFE to marker[mhartid], skips
# the ebreak. Runs pre-save_context; may only clobber t0/t1 per the trap
# preamble contract.
mp_bp_handler:
    li   t0, mp_marker
    csrr t1, mhartid
    slli t1, t1, 3
    add  t0, t0, t1
    li   t1, 0xCAFE
    sd   t1, 0(t0)
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

test_cleanup:
    ;#test_passed()

;#init_memory @mp_marker
    .dword 0x0
    .dword 0x0
