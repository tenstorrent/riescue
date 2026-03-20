;#test.name       custom_handler_dispatch
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal any
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      interrupt
;#test.tags       interrupt custom_handler
;#test.summary
;#test.summary    Verifies that the PROLOGUE/EPILOGUE pointer-swap mechanism dispatches to the
;#test.summary    correct per-discrete_test custom handler when multiple tests register handlers
;#test.summary    for the same vector.
;#test.summary
;#test.summary    Two discrete_tests both register a custom handler for SSI (vector 1):
;#test.summary      test01: handler_a  — writes 0xA to test_marker
;#test.summary      test02: handler_b  — writes 0xB to test_marker
;#test.summary
;#test.summary    Association mechanism:
;#test.summary      PROLOGUE stores the current test's handler address into the shared
;#test.summary      interrupt_handler_pointer for vector 1.  The vectored jump table loads
;#test.summary      from that pointer at trap-take time, so whichever handler was last
;#test.summary      written by PROLOGUE is what executes.  EPILOGUE restores the default.
;#test.summary      Because discrete_tests run sequentially, the pointer always reflects the
;#test.summary      currently-executing test — there is no collision between tests.

;#vector_delegation(1, machine)

.section .code, "ax"

#####################
# test_setup
#####################
test_setup:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

end_setup:
    nop

#####################
# test01
# Installs handler_a for SSI (vec 1), fires SSI, verifies marker == 0xA.
#####################
;#custom_handler(1, handler_a)
;#discrete_test(test=test01)
test01:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

    # Zero out the marker so we can confirm the handler wrote to it.
    la   t0, test_marker
    sd   x0, 0(t0)

    # PROLOGUE: from this point SSI dispatches to handler_a.
    CUSTOM_HANDLER_PROLOGUE_1 handler_a

    # Fire SSI — handler_a runs via the vectored jump table.
    li   t0, (1 << 1)
    csrs mie, t0
    csrs mip, t0             # interrupt taken here; handler_a runs

    # EPILOGUE: restore the default handler before leaving this segment.
    CUSTOM_HANDLER_EPILOGUE_1

    # Verify handler_a ran (marker must be 0xA).
    la   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xA
    bne  t1, t2, failed

    j    test_cleanup

# handler_a is at the end of test01's code block.
# Unreachable by fall-through (j test_cleanup above skips over it).
# Only entered via the vectored interrupt mechanism.
handler_a:
    li   t0, (1 << 1)
    csrc mip, t0             # clear SSI before returning
    la   t1, test_marker
    li   t0, 0xA
    sd   t0, 0(t1)
    mret


#####################
# test02
# Installs handler_b for SSI (vec 1), fires SSI, verifies marker == 0xB.
#####################
;#custom_handler(1, handler_b)
;#discrete_test(test=test02)
test02:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

    # Reset marker to 0 to distinguish a fresh write from a leftover 0xA.
    la   t0, test_marker
    sd   x0, 0(t0)

    # PROLOGUE: from this point SSI dispatches to handler_b.
    CUSTOM_HANDLER_PROLOGUE_1 handler_b

    # Fire SSI — dispatches to handler_b, not handler_a.
    li   t0, (1 << 1)
    csrs mie, t0
    csrs mip, t0             # interrupt taken here; handler_b runs

    # EPILOGUE: restore the default handler.
    CUSTOM_HANDLER_EPILOGUE_1

    # Can add optional handler verification here
    la   t0, test_marker
    ld   t1, 0(t0)
    li   t2, 0xB
    bne  t1, t2, failed

    j    test_cleanup

# handler_b is at the end of test02's code block.
handler_b:
    li   t0, (1 << 1)
    csrc mip, t0             # clear SSI before returning
    la   t1, test_marker
    li   t0, 0xB
    sd   t0, 0(t1)
    mret


test_cleanup:
    csrw mip, x0
    li   t0, (~0)
    csrc mie, t0
    csrw mcause, x0
    ;#test_passed()


#####################
# Shared data — stays in .code (no new .section; avoids duplicate-registration).
# la (PC-relative) reaches it from the test body, and bare-metal M-mode
# allows writes to .code memory.
#####################
    # Marker written by whichever custom handler fires.
    # test01 expects 0xA; test02 expects 0xB.
test_marker:
    .dword 0x0
