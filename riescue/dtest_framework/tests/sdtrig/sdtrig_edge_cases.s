;#test.name       sdtrig_edge_cases
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig trigger breakpoint edge
;#test.summary
;#test.summary    Category 8: Edge Cases & Corner Conditions
;#test.summary    8.1: Execute + load triggers at same addr (order unspecified)
;#test.summary    8.2: Invalid tselect (out-of-range) - expect illegal
;#test.summary



.section .code_super_0, "ax"
    li x31, 0xf0001004
    ecall

.section .code_user_0, "ax"
    li x31, 0xf0001004
    ecall

.section .code_machine_0, "ax"
    li x31, 0xf0001004
    ecall

.section .code, "ax"

.ifne SDTRIG_SUPPORTED
test_setup:
    li x1, 0xc0010001
    ;#test_passed()

#####################
# 8.1 sdtrig_multiple_same_prio: Execute + load at same addr; both fire (order unspecified)
#####################
;#discrete_test(test=sdtrig_multiple_same_prio)
sdtrig_multiple_same_prio:
    ;#trigger_config(index=0, type=execute, addr=edge_same_addr, action=breakpoint)
    ;#trigger_config(index=1, type=load, addr=edge_same_addr, action=breakpoint)
    OS_SETUP_CHECK_EXCP BREAKPOINT, edge_same_addr, edge_same_after
    la t1, edge_same_addr
edge_same_addr:
    ld t0, 0(t1)
edge_same_after:
    ;#test_passed()

#####################
# 8.2 sdtrig_invalid_tselect: tselect out of range; expect illegal instruction
#####################
;#discrete_test(test=sdtrig_invalid_tselect)
sdtrig_invalid_tselect:
    OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, invalid_tselect_here, invalid_tselect_after
invalid_tselect_here:
    ;#csr_rw(tselect, write, value=0x1000, force_machine=true)
invalid_tselect_after:
    ;#test_passed()

test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

.else
test_setup:
    ;#test_passed()

;#discrete_test(test=sdtrig_multiple_same_prio)
sdtrig_multiple_same_prio:
;#discrete_test(test=sdtrig_invalid_tselect)
sdtrig_invalid_tselect:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
.endif

.section .data
    .dword 0
    .dword 0xc001c0de
