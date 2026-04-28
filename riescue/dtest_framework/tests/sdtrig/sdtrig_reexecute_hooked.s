;#test.name       sdtrig_reexecute_hooked
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable any
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig trigger breakpoint reexecute hooks
;#test.summary
;#test.summary    mcontrol6 + re_execute=1 variant of RVTOOLS-5039. This test
;#test.summary    must be run with --excp_hooks because mcontrol6 execute
;#test.summary    triggers do NOT self-disable (unlike icount). Without an
;#test.summary    in-handler disable the trap re-fires on the re-fetch and
;#test.summary    the hart loops forever.
;#test.summary
;#test.summary    Flow:
;#test.summary      1. OS_SETUP_CHECK_EXCP arms a BREAKPOINT expectation with
;#test.summary         return_pc=rx_bp_after (strictly past the faulting addi)
;#test.summary         and re_execute=1.
;#test.summary      2. ;#trigger_config arms mcontrol6 execute on rx_bp_here.
;#test.summary      3. addi x8, x8, 1 at rx_bp_here fires the trigger.
;#test.summary      4. excp_handler_pre disables trigger 0 (only on cause=3) so
;#test.summary         the second fetch is clean.
;#test.summary      5. Handler returns with re_execute=1 → xepc keeps the
;#test.summary         faulting PC → addi re-executes → x8 == 1 → pass.
;#test.summary
;#test.summary    With re_execute=0 the handler would overwrite mepc with
;#test.summary    rx_bp_after, the addi would be skipped, x8 == 0, and the
;#test.summary    bne would flag the test as failed. That is the regression
;#test.summary    guard.
;#test.summary

.section .code, "ax"

.ifne SDTRIG_SUPPORTED
test_setup:
    li x1, 0xc0010001
    ;#test_passed()

#####################
# mcontrol6 execute breakpoint + re_execute=1. Uses an excp_handler_pre hook
# (BREAKPOINT cause only) to disable the trigger before the handler returns,
# so the re-fetch at rx_bp_here is not re-trapped.
#####################
;#discrete_test(test=re_execute_exec_bp_hooked)
re_execute_exec_bp_hooked:
    # Callee-saved register for the counter (trap handler may clobber t0-t2).
    li x8, 0

    # Positional args: expected_cause, expected_pc, return_pc,
    #                  expected_tval, expected_htval, skip_pc_check,
    #                  far_expected_pc, far_return_pc, gva_check,
    #                  expected_mode, re_execute
    # NOTE: return_pc intentionally points PAST the faulting instruction so
    # that with re_execute=0 the addi would be skipped and the test would
    # fail. This keeps the test a real regression guard.
    OS_SETUP_CHECK_EXCP BREAKPOINT, rx_bp_here, rx_bp_after, 0, 0, 0, 0, 0, 0, 0, 1
    ;#trigger_config(index=0, type=execute, addr=rx_bp_here, action=breakpoint)
rx_bp_here:
    addi x8, x8, 1
rx_bp_after:
    li x9, 1
    bne x8, x9, re_execute_exec_bp_hooked_fail
    ;#test_passed()

re_execute_exec_bp_hooked_fail:
    ;#test_failed()

test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

#####################
# Pre-hook: runs BEFORE the default check_excp logic. Only touches tdata1
# when the cause is BREAKPOINT so unrelated ecalls (e.g. the CSR syscalls
# emitted by ;#trigger_config) are unaffected.
#
# Safe to clobber t0/t1 here — the outer trap handler has already saved
# GPRs. ra is used by _call_excp_hook (jalr ra, hook); a plain `ret`
# returns without extra bookkeeping.
#####################
excp_handler_pre:
    csrr t0, mcause
    li   t1, 3                       # RISC-V BREAKPOINT cause
    bne  t0, t1, excp_handler_pre_done
    csrw tselect, x0
    csrw tdata1, x0
excp_handler_pre_done:
    ret

excp_handler_post:
    ret

.else
test_setup:
    ;#test_passed()

;#discrete_test(test=re_execute_exec_bp_hooked)
re_execute_exec_bp_hooked:
    ;#test_passed()

test_cleanup:
    ;#test_passed()

excp_handler_pre:
    ret

excp_handler_post:
    ret
.endif

.section .data
    .dword 0xc001c0de
