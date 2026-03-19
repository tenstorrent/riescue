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
    ;#test_passed()

test_cleanup:
    ;#test_passed()
.endif

.section .data
    .dword 0xc001c0de

;#init_memory @watch_lin
    nop
    jr x0
