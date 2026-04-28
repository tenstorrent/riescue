;#test.name       default_excp_handler_override_s
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       supervisor
;#test.env        bare_metal
;#test.paging     disable

# S-mode delegation variant of default_excp_handler_override.s.
#
# Same conf file (default_excp_handler_override_conf.py) registers
# "my_illegal_handler" for ILLEGAL_INSTRUCTION (cause 2), but here we run
# with --deleg_excp_to=super so medeleg bit 2 is set and the handler is
# emitted in the S-mode TrapHandler.  The single handler callable works
# unchanged because it uses ctx.xepc / ctx.xret (resolved to sepc/sret here).
#
# Usage:
#     riescued.py -t .../default_excp_handler_override_s.s \
#                 --conf .../default_excp_handler_override_conf.py \
#                 --seed 1 --run_iss --deleg_excp_to=super

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

    # Trigger an illegal instruction (same as M-mode variant).
    # S-mode handler writes 0xCAFE and advances sepc; execution resumes here.
    .word 0x00000000

    li   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xCAFE
    bne  t1, t2, test_fail_label

    ;#test_passed()

test_fail_label:
    ;#test_failed()

test_cleanup:
    ;#test_passed()

;#init_memory @test_marker
    .dword 0x0
