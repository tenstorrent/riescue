;#test.name       sdtrig_reexecute
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable any
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig trigger breakpoint reexecute
;#test.summary
;#test.summary    Exercises OS_SETUP_CHECK_EXCP with __re_execute=1 (RVTOOLS-5039).
;#test.summary
;#test.summary    Both discrete tests use an icount trigger — icount naturally
;#test.summary    latches count=0 after firing, so the OS trap handler can
;#test.summary    re-execute the faulting PC without any handler-side trigger
;#test.summary    cleanup. This lets the test run cleanly without --excp_hooks.
;#test.summary
;#test.summary    re_execute_icount:
;#test.summary      Smoke test: icount fires somewhere inside a sled of addi
;#test.summary      instructions, the handler re-executes the faulting PC,
;#test.summary      and control proceeds to the post-sled label.
;#test.summary
;#test.summary    re_execute_icount_counter:
;#test.summary      Stronger check: a callee-saved counter register is bumped
;#test.summary      by the faulting instruction. return_pc is set PAST the
;#test.summary      counter bump on purpose — when re_execute=1 the handler
;#test.summary      must leave xepc alone so the addi retires; with the flag
;#test.summary      at 0 the handler writes xepc := return_pc, the addi is
;#test.summary      skipped and the bne marks the test as failed.
;#test.summary
;#test.summary    NOTE: The strict re_execute=0 vs re_execute=1 regression
;#test.summary    guard for RVTOOLS-5039 lives in sdtrig_reexecute_hooked.s
;#test.summary    (mcontrol6 + --excp_hooks). icount firing behaviour varies
;#test.summary    across ISS configurations, so these tests also pass when
;#test.summary    the trigger does not fire (smoke only in that case).
;#test.summary

.section .code, "ax"

.ifne SDTRIG_SUPPORTED
test_setup:
    li x1, 0xc0010001
    ;#test_passed()

#####################
# icount trigger + re_execute=1. Uses skip_pc_check=1 because icount fires at
# a non-deterministic retirement boundary (CSR plumbing for ;#trigger_config
# also retires). After the fault the handler must leave xepc pointing at the
# faulting PC so mret resumes there; icount is already latched to 0 so the
# second fetch is clean.
#####################
;#discrete_test(test=re_execute_icount)
re_execute_icount:
    # Positional args: expected_cause, expected_pc, return_pc,
    #                  expected_tval, expected_htval, skip_pc_check,
    #                  far_expected_pc, far_return_pc, gva_check,
    #                  expected_mode, re_execute
    OS_SETUP_CHECK_EXCP BREAKPOINT, rx_ic_after, rx_ic_after, 0, 0, 1, 0, 0, 0, 0, 1
    ;#trigger_config(index=0, type=icount, count=3, action=breakpoint)

    # Enough instructions to guarantee the icount fires within this region.
    addi x10, x0, 0
    addi x10, x10, 1
    addi x10, x10, 1
    addi x10, x10, 1
    addi x10, x10, 1
    addi x10, x10, 1
    addi x10, x10, 1
    addi x10, x10, 1
rx_ic_after:
    ;#trigger_disable(index=0)
    ;#test_passed()

#####################
# icount trigger + re_execute=1 with explicit single-execution proof. The
# faulting instruction is `addi x8, x8, 1` — a callee-saved register, so the
# trap handler (which may clobber t0-t2) cannot disturb it. After the fault
# the handler returns to the same PC, the addi retires exactly once (icount
# latched to 0 → no re-trap), and x8 must equal 1.
#
# Note the asymmetric return_pc: expected_pc=rx_ic_bump (where the trap is
# taken) but return_pc=rx_ic_bump_done (PAST the addi). This makes the test
# strictly distinguishing:
#   re_execute=1 → handler leaves xepc at rx_ic_bump, addi runs, x8 == 1 (pass)
#   re_execute=0 → handler writes mepc := rx_ic_bump_done, addi is SKIPPED,
#                  x8 == 0, bne fires, test_failed (regression guard for
#                  RVTOOLS-5039).
#####################
;#discrete_test(test=re_execute_icount_counter)
re_execute_icount_counter:
    # Use callee-saved s0/s1 (x8/x9) — the default trap handler freely
    # clobbers t0-t2 (= x5-x7), so the counter must not be a t-reg.
    li x8, 0

    # Positional args: expected_cause, expected_pc, return_pc,
    #                  expected_tval, expected_htval, skip_pc_check,
    #                  far_expected_pc, far_return_pc, gva_check,
    #                  expected_mode, re_execute
    OS_SETUP_CHECK_EXCP BREAKPOINT, rx_ic_bump, rx_ic_bump_done, 0, 0, 1, 0, 0, 0, 0, 1
    # Count=1 → the icount fires on the very next retiring instruction
    # (the addi below). icount latches to 0 on fire, so the addi runs cleanly
    # on the re-fetch and no further triggers are pending.
    ;#trigger_config(index=0, type=icount, count=1, action=breakpoint)
rx_ic_bump:
    addi x8, x8, 1
rx_ic_bump_done:
    ;#trigger_disable(index=0)
    li x9, 1
    bne x8, x9, re_execute_icount_counter_fail
    ;#test_passed()

re_execute_icount_counter_fail:
    ;#test_failed()

test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

#####################
# Unused in this config (tests run without --excp_hooks), but both labels are
# required by the loader so provide trivial stubs.
#####################
excp_handler_pre:
    ret

excp_handler_post:
    ret

.else
test_setup:
    ;#test_passed()

;#discrete_test(test=re_execute_icount)
re_execute_icount:
    ;#test_passed()

;#discrete_test(test=re_execute_icount_counter)
re_execute_icount_counter:
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
