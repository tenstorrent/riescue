;#test.name       install_excp_handler_s
;#test.author     njoaquin@tenstorrent.com
;#test.arch       rv64
;#test.priv       super
;#test.env        bare_metal
;#test.paging     sv39

# Paged S-mode variant of install_excp_handler, proving the M-mode dispatch's
# VA->PA relocation. The test runs in S-mode with sv39 paging, so
# OS_INSTALL_EXCP_HANDLER stores the handler's *virtual* address. With
# --deleg_excp_to=machine the ebreak lands in the M-mode trap handler, whose
# dispatch must relocate the stored VA to a physical address (.code bases)
# before jumping: M-mode instruction fetches are never translated, so without
# relocation the fetch would go to the wrong address. The test verifies:
#   1. armed + cause match (ebreak) -> relocated handler runs (s2 = 0xCAFE, mepc += 4)
#   2. uninstalled + ebreak         -> original path (OS_SETUP_CHECK_EXCP catches)
#
# The handler marks s2 (a register rather than memory, keeping the check free
# of VA/PA aliasing concerns).
#
# Usage:
#     riescued.py -t .../install_excp_handler_s.s --seed 1 --run_iss --deleg_excp_to=machine

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    # Case 1: armed for BREAKPOINT + ebreak -> my_bp_handler runs, resumes at next insn
    li   s2, 0
    OS_INSTALL_EXCP_HANDLER BREAKPOINT, my_bp_handler, CHECK_EXCP_MODE_MACHINE
    ebreak

    # The handler must have marked s2
    li   t0, 0xCAFE
    bne  s2, t0, test_fail_label

    # Case 2: uninstall + ebreak -> original path
    OS_UNINSTALL_EXCP_HANDLER
    OS_SETUP_CHECK_EXCP BREAKPOINT, case2_bp, case2_ret
case2_bp:
    ebreak
case2_ret:

    ;#test_passed()

test_fail_label:
    ;#test_failed()

# Runtime-installed BREAKPOINT handler running in M mode: marks s2 and skips
# the ebreak. Runs pre-save_context; may only clobber t0/t1 per the trap
# preamble contract (s2 is the test's own marker register).
my_bp_handler:
    li   s2, 0xCAFE
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

test_cleanup:
    ;#test_passed()
