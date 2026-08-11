;#test.name       install_excp_handler
;#test.author     njoaquin@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.paging     disable

# Demonstrates the runtime-installed exception handler macros
# (OS_INSTALL_EXCP_HANDLER / OS_UNINSTALL_EXCP_HANDLER). The install macro
# stores {cause, handler address, expected mode} in hart-local variables; the
# trap dispatch jumps to the handler on cause (+mode) match, else falls through
# to the original path. The test verifies:
#   1. armed + cause match (ebreak)        -> custom handler runs (marker = 0xCAFE, mepc += 4)
#   2. armed + cause mismatch (illegal)    -> original path (OS_SETUP_CHECK_EXCP catches)
#   3. armed for a different cause + ebreak -> slot miss -> original path (marker stays 0)
#   4. uninstalled + ebreak                -> original path
#   5. armed with wrong expected mode (HS) -> mode gate blocks the handler -> original path
#
# Usage:
#     riescued.py -t .../install_excp_handler.s --seed 1 --run_iss --deleg_excp_to=machine

;#random_addr(name=test_marker, type=physical, size=8, and_mask=0xfffffffffffffff8)

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    # Zero the marker so we detect if the handler never ran
    li   t0, test_marker
    sd   x0, 0(t0)

    # Case 1: armed for BREAKPOINT + ebreak -> my_bp_handler runs, resumes at next insn
    OS_INSTALL_EXCP_HANDLER BREAKPOINT, my_bp_handler, CHECK_EXCP_MODE_MACHINE
    ebreak

    # The handler must have written the marker
    li   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xCAFE
    bne  t1, t2, test_fail_label

    # Case 2: handler still armed but cause 2 (illegal) -> falls back to original path
    OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, case2_bad, case2_ret
case2_bad:
    .word 0x00000000
case2_ret:

    # Case 3: re-arm for a different cause; ebreak (cause 3) misses the slot
    # -> original path. my_il_handler writes 0xBEEF if it ever runs; it must not.
    li   t0, test_marker
    sd   x0, 0(t0)
    OS_INSTALL_EXCP_HANDLER ILLEGAL_INSTRUCTION, my_il_handler, CHECK_EXCP_MODE_MACHINE
    OS_SETUP_CHECK_EXCP BREAKPOINT, case3_bp, case3_ret
case3_bp:
    ebreak
case3_ret:
    li   t0, test_marker
    ld   t1, 0(t0)
    bnez t1, test_fail_label

    # Case 4: uninstall + ebreak -> original path
    OS_UNINSTALL_EXCP_HANDLER
    OS_SETUP_CHECK_EXCP BREAKPOINT, case4_bp, case4_ret
case4_bp:
    ebreak
case4_ret:

    # Case 5: armed with the wrong expected mode (CHECK_EXCP_MODE_HS) while the
    # trap lands in the M-mode handler -> the mode gate must block the handler
    # -> original path (marker stays 0).
    li   t0, test_marker
    sd   x0, 0(t0)
    OS_INSTALL_EXCP_HANDLER BREAKPOINT, my_bp_handler, CHECK_EXCP_MODE_HS
    OS_SETUP_CHECK_EXCP BREAKPOINT, case5_bp, case5_ret
case5_bp:
    ebreak
case5_ret:
    li   t0, test_marker
    ld   t1, 0(t0)
    bnez t1, test_fail_label

    ;#test_passed()

test_fail_label:
    ;#test_failed()

# Runtime-installed BREAKPOINT handler: writes 0xCAFE to test_marker, skips the ebreak.
# Runs pre-save_context; may only clobber t0/t1 per the trap preamble contract.
my_bp_handler:
    li   t0, test_marker
    li   t1, 0xCAFE
    sd   t1, 0(t0)
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

# Runtime-installed ILLEGAL handler for case 3: writes 0xBEEF to test_marker and
# skips the faulting instruction. The test never expects it to run.
my_il_handler:
    li   t0, test_marker
    li   t1, 0xBEEF
    sd   t1, 0(t0)
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

test_cleanup:
    ;#test_passed()

;#init_memory @test_marker
    .dword 0x0
