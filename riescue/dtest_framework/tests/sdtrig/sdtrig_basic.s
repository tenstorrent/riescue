;#test.name       sdtrig_basic
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig trigger breakpoint
;#test.summary
;#test.summary    Categories 1, 2, 6: Enumeration, Execute triggers, Enable/Disable
;#test.summary    1.1: Discover triggers via tselect/tinfo
;#test.summary    1.2: Read tinfo for trigger 0
;#test.summary    1.3: tdata1 WARL (write 0, read back)
;#test.summary    1.4/2.1: Execute breakpoint at fixed label
;#test.summary    2.2: Execute breakpoint at random address
;#test.summary    2.4: Execute at different addr; no fire
;#test.summary    2.5: Two execute triggers; fire each
;#test.summary    6.1: Disable trigger, no fire
;#test.summary    6.2: Disable then re-enable, fire
;#test.summary    6.3: Disable one trigger; other still fires
;#test.summary    6.4: Re-enable restores the preceding same-index config,
;#test.summary         not a later config that reuses the index with another type
;#test.summary

;#random_addr(name=watch_lin, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=watch_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=watch_lin, phys_name=watch_phys, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

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
# 1.1 sdtrig_enum_count: Discover triggers via tselect/tinfo
#####################
;#discrete_test(test=sdtrig_enum_count)
sdtrig_enum_count:
    ;#csr_rw(tselect, write, value=0, force_machine=true)
    ;#csr_rw(tinfo, read, force_machine=true)
    ;#test_passed()

#####################
# 1.2 sdtrig_tinfo_types: Read tinfo for trigger 0
#####################
;#discrete_test(test=sdtrig_tinfo_types)
sdtrig_tinfo_types:
    ;#csr_rw(tselect, write, value=0, force_machine=true)
    ;#csr_rw(tinfo, read, force_machine=true)
    ;#test_passed()

#####################
# 1.3 sdtrig_tdata1_warl: Write 0 to tdata1, read back
#####################
;#discrete_test(test=sdtrig_tdata1_warl)
sdtrig_tdata1_warl:
    ;#csr_rw(tselect, write, value=0, force_machine=true)
    ;#csr_rw(tdata1, write, value=0, force_machine=true)
    ;#csr_rw(tdata1, read, force_machine=true)
    ;#test_passed()

#####################
# 1.4/2.1 sdtrig_exec_bp_basic: Execute breakpoint at fixed label
#####################
;#discrete_test(test=sdtrig_exec_bp_basic)
sdtrig_exec_bp_basic:
    OS_SETUP_CHECK_EXCP BREAKPOINT, exec_bp_here, exec_bp_after
    ;#trigger_config(index=0, type=execute, addr=exec_bp_here, action=breakpoint)
exec_bp_here:
    nop
exec_bp_after:
    ;#test_passed()

#####################
# 2.2 sdtrig_exec_bp_random_addr: Execute breakpoint at random address
#####################
;#discrete_test(test=sdtrig_exec_bp_random_addr)
sdtrig_exec_bp_random_addr:
    OS_SETUP_CHECK_EXCP BREAKPOINT, watch_lin, exec_random_after, 0, 0, 0, 1, 0
    ;#trigger_config(index=0, type=execute, addr=watch_lin, action=breakpoint)
    li t0, watch_lin
    jalr x0, 0(t0)
exec_random_after:
    ;#test_passed()


#####################
# 2.4 sdtrig_exec_bp_no_fire: Trigger at A, execute at B; no fire
#####################
;#discrete_test(test=sdtrig_exec_bp_no_fire)
sdtrig_exec_bp_no_fire:
    ;#trigger_config(index=0, type=execute, addr=no_fire_trigger_addr, action=breakpoint)
    j no_fire_exec_here
no_fire_trigger_addr:
    nop
no_fire_exec_here:
    nop
    ;#test_passed()

#####################
# 2.5 sdtrig_exec_bp_multiple: Two execute triggers; fire each
#####################
;#discrete_test(test=sdtrig_exec_bp_multiple)
sdtrig_exec_bp_multiple:
    ;#trigger_config(index=0, type=execute, addr=multi_bp_first, action=breakpoint)
    ;#trigger_config(index=1, type=execute, addr=multi_bp_second, action=breakpoint)
    OS_SETUP_CHECK_EXCP BREAKPOINT, multi_bp_first, multi_after_first
multi_bp_first:
    nop
multi_after_first:
    OS_SETUP_CHECK_EXCP BREAKPOINT, multi_bp_second, multi_after_second
multi_bp_second:
    nop
multi_after_second:
    ;#test_passed()

#####################
# 6.1 sdtrig_disable_no_fire: Configure, disable, execute at addr; no fire: Configure, disable, execute at addr; no fire
#####################
;#discrete_test(test=sdtrig_disable_no_fire)
sdtrig_disable_no_fire:
    ;#trigger_config(index=0, type=execute, addr=disable_nop_here, action=breakpoint)
    ;#trigger_disable(index=0)
disable_nop_here:
    nop
    ;#test_passed()

#####################
# 6.2 sdtrig_enable_after_disable: Disable, re-enable, execute; fire
#####################
;#discrete_test(test=sdtrig_enable_after_disable)
sdtrig_enable_after_disable:
    ;#trigger_config(index=0, type=execute, addr=enable_bp_here, action=breakpoint)
    ;#trigger_disable(index=0)
    ;#trigger_enable(index=0)
    OS_SETUP_CHECK_EXCP BREAKPOINT, enable_bp_here, enable_bp_after
enable_bp_here:
    nop
enable_bp_after:
    ;#test_passed()

#####################
# 6.3 sdtrig_disable_other_active: Two triggers; disable one; other fires
#####################
;#discrete_test(test=sdtrig_disable_other_active)
sdtrig_disable_other_active:
    ;#trigger_config(index=0, type=execute, addr=disable_other_addr0, action=breakpoint)
    ;#trigger_config(index=1, type=execute, addr=disable_other_addr1, action=breakpoint)
    ;#trigger_disable(index=0)
    OS_SETUP_CHECK_EXCP BREAKPOINT, disable_other_addr1, disable_other_after
disable_other_addr0:
    nop
disable_other_addr1:
    nop
disable_other_after:
    ;#test_passed()

#####################
# 6.4 sdtrig_enable_reuse_index: Re-enable must restore the execute config that
# textually PRECEDES it, even when a later discrete test reuses the same trigger
# index with a different type (see sdtrig_reuse_index_itrigger below). Regression
# for trigger_enable selecting the last same-index config in the whole file: that
# produced an itrigger tdata1, which the ISS WARL-masks to disabled, so the
# breakpoint never fired.
#####################
;#discrete_test(test=sdtrig_enable_reuse_index)
sdtrig_enable_reuse_index:
    ;#trigger_config(index=2, type=execute, addr=reuse_bp_here, action=breakpoint)
    ;#trigger_disable(index=2)
    ;#trigger_enable(index=2)
    OS_SETUP_CHECK_EXCP BREAKPOINT, reuse_bp_here, reuse_bp_after
reuse_bp_here:
    nop
reuse_bp_after:
    ;#test_passed()

#####################
# 6.5 sdtrig_reuse_index_itrigger: A later config reusing index 2 with a
# different (itrigger) type. Its presence textually AFTER the enable in 6.4 is
# what used to corrupt that enable's restored tdata1. Configure then disable so
# it does not fire on its own.
#####################
;#discrete_test(test=sdtrig_reuse_index_itrigger)
sdtrig_reuse_index_itrigger:
    ;#trigger_config(index=2, type=itrigger, addr=0x800, action=breakpoint, priv_mode=[m])
    ;#trigger_disable(index=2)
    ;#test_passed()

#####################
# 6.6 sdtrig_enable_branch_join: ;#trigger_enable is a shared join point reached
# from two ;#trigger_config sites for the SAME index via a branch. Which config to
# restore is a runtime property of the path taken and cannot be resolved at
# generation time. The runtime path here arms an EXECUTE trigger, while the
# textually-last config before the join (not executed) is an itrigger. The enable
# must restore the EXECUTE config so the breakpoint fires; restoring the itrigger
# (the static "textually preceding" pick) WARL-masks to disabled and never fires,
# falling through to ;#test_failed(). Regression for the runtime-shadow re-arm.
#####################
;#discrete_test(test=sdtrig_enable_branch_join)
sdtrig_enable_branch_join:
    li t0, 0
    bnez t0, branch_join_itrig_path     # t0 == 0: fall through to the execute path (taken)
    ;#trigger_config(index=2, type=execute, addr=branch_join_fire, action=breakpoint)
    j branch_join_point
branch_join_itrig_path:                 # NOT executed at runtime; textually-last index-2 config
    ;#trigger_config(index=2, type=itrigger, addr=0x800, action=breakpoint, priv_mode=[m])
    j branch_join_point
branch_join_point:
    ;#trigger_disable(index=2)
    ;#trigger_enable(index=2)
    OS_SETUP_CHECK_EXCP BREAKPOINT, branch_join_fire, branch_join_after
branch_join_fire:
    nop
    ;#test_failed()                     # reached only if the breakpoint did NOT fire
branch_join_after:
    ;#trigger_disable(index=2)
    ;#test_passed()

test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

.else
test_setup:
    ;#test_passed()

;#discrete_test(test=sdtrig_enum_count)
sdtrig_enum_count:
;#discrete_test(test=sdtrig_tinfo_types)
sdtrig_tinfo_types:
;#discrete_test(test=sdtrig_tdata1_warl)
sdtrig_tdata1_warl:
;#discrete_test(test=sdtrig_exec_bp_basic)
sdtrig_exec_bp_basic:
;#discrete_test(test=sdtrig_exec_bp_random_addr)
sdtrig_exec_bp_random_addr:
;#discrete_test(test=sdtrig_exec_bp_no_fire)
sdtrig_exec_bp_no_fire:
;#discrete_test(test=sdtrig_exec_bp_multiple)
sdtrig_exec_bp_multiple:
;#discrete_test(test=sdtrig_disable_no_fire)
sdtrig_disable_no_fire:
;#discrete_test(test=sdtrig_enable_after_disable)
sdtrig_enable_after_disable:
;#discrete_test(test=sdtrig_disable_other_active)
sdtrig_disable_other_active:
;#discrete_test(test=sdtrig_enable_reuse_index)
sdtrig_enable_reuse_index:
;#discrete_test(test=sdtrig_reuse_index_itrigger)
sdtrig_reuse_index_itrigger:
;#discrete_test(test=sdtrig_enable_branch_join)
sdtrig_enable_branch_join:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
.endif

.section .data
    .dword 0xc001c0de

;#init_memory @watch_lin
    nop
    jr x0
