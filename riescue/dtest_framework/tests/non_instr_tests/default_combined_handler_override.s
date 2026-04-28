;#test.name       default_combined_handler_override
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.paging     disable

# Demonstrates simultaneous use of register_default_handler() and
# register_default_exception_handler() in a single Conf.add_hooks().
#
# The conf file registers:
#   * combined_ssi_handler      for SSI          (interrupt, vec 1)
#   * combined_illegal_handler  for ILLEGAL_INSTR (exception, cause 2)
#
# This test exercises both: first an illegal instruction, then SSI.  Each
# handler writes a distinctive marker; the test asserts both markers hold
# the expected sentinels after handler execution, confirming the two
# override mechanisms coexist.
#
# Usage:
#     riescued.py -t .../default_combined_handler_override.s \
#                 --conf .../default_combined_handler_override_conf.py \
#                 --seed 1 --run_iss --deleg_excp_to=machine

;#random_addr(name=test_marker_intr, type=physical, size=8, and_mask=0xfffffffffffffff8)
;#random_addr(name=test_marker_excp, type=physical, size=8, and_mask=0xfffffffffffffff8)

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    # Zero both markers so we detect if either handler fails to run
    li   t0, test_marker_intr
    sd   x0, 0(t0)
    li   t0, test_marker_excp
    sd   x0, 0(t0)

    # ---- Exception first: illegal instruction (cause 2) ----
    # combined_illegal_handler writes 0xBEEF to test_marker_excp and advances
    # xepc by 4, so execution resumes at the next instruction.
    .word 0x00000000

    # ---- Then interrupt: SSI (vec 1) ----
    # Enable interrupts (direct mtvec mode is the default and fine for both
    # interrupt and exception dispatch)
    li   t1, 2
    csrw mie, t1
    ENABLE_MIE

    # Fire SSI by setting SSIP
    csrsi mip, 2

    # Both handlers have run by now; check both markers
    li   t0, test_marker_excp
    ld   t1, 0(t0)
    li   t2, 0xBEEF
    bne  t1, t2, test_fail_label

    li   t0, test_marker_intr
    ld   t1, 0(t0)
    li   t2, 0xCAFE
    bne  t1, t2, test_fail_label

    csrci mstatus, 8
    ;#test_passed()

test_fail_label:
    ;#test_failed()

test_cleanup:
    csrci mstatus, 8
    ;#test_passed()

;#init_memory @test_marker_intr
    .dword 0x0
;#init_memory @test_marker_excp
    .dword 0x0
