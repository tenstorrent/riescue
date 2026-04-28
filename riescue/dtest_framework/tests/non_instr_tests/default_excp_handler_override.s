;#test.name       default_excp_handler_override
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.paging     disable

# Demonstrates register_default_exception_handler() via Conf.add_hooks().
#
# The conf file (default_excp_handler_override_conf.py) registers "my_illegal_handler"
# for ILLEGAL_INSTRUCTION (cause 2).  This test triggers an illegal instruction
# and verifies the custom handler ran by checking the marker the handler wrote,
# then confirms execution resumed past the faulting instruction.
#
# Works in both direct and vectored mtvec modes — exceptions always dispatch
# to mtvec BASE regardless of MODE, so the override dispatch at exception_path
# runs identically in both cases.  Pass -teq USE_VECTORED_MODE=1 to exercise
# the vectored-mtvec path.
#
# Usage (direct mode, default):
#     riescued.py -t .../default_excp_handler_override.s \
#                 --conf .../default_excp_handler_override_conf.py \
#                 --seed 1 --run_iss --deleg_excp_to=machine
#
# Usage (vectored mode):
#     riescued.py -t .../default_excp_handler_override.s \
#                 --conf .../default_excp_handler_override_conf.py \
#                 --seed 1 --run_iss --deleg_excp_to=machine \
#                 -teq USE_VECTORED_MODE=1

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

    # Select mtvec mode: default is direct; -teq USE_VECTORED_MODE=1 exercises vectored.
#ifdef USE_VECTORED_MODE
    SET_VECTORED_INTERRUPTS
#else
    SET_DIRECT_INTERRUPTS
#endif

    # Trigger an illegal instruction: a 32-bit all-zeros word is not a valid
    # RV32/RV64 encoding and faults with ILLEGAL_INSTRUCTION (cause 2).
    # The conf-registered handler writes 0xCAFE to test_marker, advances
    # xepc by 4, and returns — so execution resumes right here (next insn).
    .word 0x00000000

    # On return from the handler, verify it wrote the marker
    li   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xCAFE
    bne  t1, t2, test_fail_label

    # Restore direct mode before test passes (keeps cleanup deterministic)
    SET_DIRECT_INTERRUPTS
    ;#test_passed()

test_fail_label:
    ;#test_failed()

test_cleanup:
    SET_DIRECT_INTERRUPTS
    ;#test_passed()

;#init_memory @test_marker
    .dword 0x0
