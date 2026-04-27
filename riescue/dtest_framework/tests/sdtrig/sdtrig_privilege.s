;#test.name       sdtrig_privilege
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig trigger breakpoint privilege
;#test.summary
;#test.summary    Category 7: Privilege & Delegation
;#test.summary    7.1: Execute breakpoint from M-mode
;#test.summary    7.2: Execute breakpoint from S-mode (delegated)
;#test.summary    7.3: Execute breakpoint from U-mode (delegated)
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
    # RISC-V Debug Spec Sdtrig re-entrancy (option 1): triggers with action=0 do not
    # match while mstatus.MIE=0 (M), or while medeleg[3]=1 && sstatus.SIE=0 (S), or
    # while medeleg[3]=1 && hedeleg[3]=1 && vsstatus.SIE=0 (VS). Force SIE/MIE=1
    # so trigger configuration is deterministic under randomized delegation.
    ;#csr_rw(mstatus, set_bit, bit=3, force_machine=true)
    ;#csr_rw(mstatus, set_bit, bit=1, force_machine=true)
.if ENV_VIRTUALIZED
    ;#csr_rw(vsstatus, set_bit, bit=1, force_machine=true)
.endif
    ;#test_passed()

#####################
# 7.1/7.2/7.3: Execute breakpoint from current privilege mode
# Runs in M, S, or U depending on ;#test.priv instantiation
#####################
;#discrete_test(test=sdtrig_priv_exec_bp)
sdtrig_priv_exec_bp:
    OS_SETUP_CHECK_EXCP BREAKPOINT, priv_bp_here, priv_bp_after
    ;#trigger_config(index=0, type=execute, addr=priv_bp_here, action=breakpoint)
priv_bp_here:
    nop
priv_bp_after:
    ;#test_passed()

test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

.else
test_setup:
    ;#test_passed()

;#discrete_test(test=sdtrig_priv_exec_bp)
sdtrig_priv_exec_bp:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
.endif

.section .data
    .dword 0xc001c0de
