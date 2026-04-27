;#test.name       default_handler_override
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.paging     disable

# Demonstrates register_default_handler() via Conf.add_hooks().
#
# The conf file (default_handler_override_conf.py) registers "my_ssi_handler"
# for Supervisor Software Interrupt (vec 1).  This test fires SSIP and verifies
# that the custom handler ran by checking a marker written by the handler.
#
# Supports both vectored and direct mtvec modes via the USE_DIRECT_MODE define:
#
#   Vectored (default):
#     riescued.py -t ...default_handler_override.s \
#                 --conf ...default_handler_override_conf.py \
#                 --seed 1 --run_iss --deleg_excp_to=machine
#
#   Direct mode:
#     riescued.py -t ...default_handler_override.s \
#                 --conf ...default_handler_override_conf.py \
#                 --seed 1 --run_iss --deleg_excp_to=machine \
#                 -teq USE_DIRECT_MODE=1

;#random_addr(name=test_marker, type=physical, size=8, and_mask=0xfffffffffffffff8)

;#vector_delegation(1, machine)

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    # Zero the marker so we detect if the handler never ran
    li   t0, test_marker
    sd   x0, 0(t0)

    # Select interrupt dispatch mode based on compile-time define.
    # Default is vectored; pass -teq USE_DIRECT_MODE=1 for direct.
#ifdef USE_DIRECT_MODE
    SET_DIRECT_INTERRUPTS
#else
    SET_VECTORED_INTERRUPTS
#endif

    # Enable SSIE (mie bit 1) and global MIE
    li   t1, 2
    csrw mie, t1
    ENABLE_MIE

    # Fire supervisor software interrupt by setting SSIP (mip bit 1)
    csrsi mip, 2

    # Handler runs here; on return check the marker
    li   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xCAFE
    bne  t1, t2, test_fail_label

    # Disable interrupts and restore direct mode before test passes
    csrci mstatus, 8
    SET_DIRECT_INTERRUPTS
    ;#test_passed()

test_fail_label:
    ;#test_failed()

test_cleanup:
    csrci mstatus, 8
    SET_DIRECT_INTERRUPTS
    ;#test_passed()

;#init_memory @test_marker
    .dword 0x0
