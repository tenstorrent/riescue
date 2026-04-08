;#test.name       default_handler_override_s
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       supervisor
;#test.env        bare_metal
;#test.paging     disable

# Demonstrates register_default_handler() with S-mode delegation via Conf.add_hooks().
#
# The same conf file (default_handler_override_conf.py) registers "my_ssi_handler"
# for Supervisor Software Interrupt (vec 1).  Here vec 1 is delegated to S-mode
# (;#vector_delegation(1, supervisor)), so the framework calls the handler with
# SUPERVISOR_CTX: ctx.xip="sip", ctx.xret="sret".
#
# The handler clears SSIP via sip and writes 0xCAFE to test_marker.  The test
# fires SSIP via csrsi sip, 2 and verifies the marker after sret returns.
#
# Run with:
#   riescued.py -t riescue/dtest_framework/tests/non_instr_tests/default_handler_override_s.s \
#               --conf riescue/dtest_framework/tests/non_instr_tests/default_handler_override_conf.py \
#               --seed 1 --run_iss --deleg_excp_to=super

;#random_addr(name=test_marker, type=physical, size=8, and_mask=0xfffffffffffffff8)

;#vector_delegation(1, supervisor)

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    # Zero the marker so we detect if the handler never ran
    li   t0, test_marker
    sd   x0, 0(t0)

    # Enable vectored interrupt dispatch (S-mode stvec), SSIE (sie bit 1), global SIE
    SET_VECTORED_INTERRUPTS_S
    li   t1, 2
    csrw sie, t1
    ENABLE_SIE

    # Fire supervisor software interrupt by setting SSIP (sip bit 1)
    csrsi sip, 2

    # Handler runs here (sret returns to next instruction); check the marker
    li   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xCAFE
    bne  t1, t2, test_fail_label

    # Disable interrupts and restore direct mode before test passes
    csrci sstatus, 2
    SET_DIRECT_INTERRUPTS_S
    ;#test_passed()

test_fail_label:
    ;#test_failed()

test_cleanup:
    csrci sstatus, 2
    SET_DIRECT_INTERRUPTS_S
    ;#test_passed()

;#init_memory @test_marker
    .dword 0x0
