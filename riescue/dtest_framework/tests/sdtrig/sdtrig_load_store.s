;#test.name       sdtrig_load_store
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig trigger watchpoint load store
;#test.summary
;#test.summary    Categories 3, 4, 5: Load, Store, Load/Store triggers
;#test.summary    3.1: Load from watched addr triggers breakpoint
;#test.summary    3.2: Load doubleword (8B) at watched addr
;#test.summary    3.3: Load byte at watched addr
;#test.summary    3.4: Load from different addr; no fire
;#test.summary    4.1: Store to watched addr triggers breakpoint
;#test.summary    4.2: Store doubleword at watched addr
;#test.summary    4.3: Store byte at watched addr
;#test.summary    4.4: Store to different addr; no fire
;#test.summary    5.1: Load/store trigger fires on load
;#test.summary    5.2: Load/store trigger fires on store
;#test.summary

;#random_addr(name=ldst_lin, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=ldst_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=ldst_lin, phys_name=ldst_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

;#init_memory @ldst_lin
    .dword 0
    .dword 0

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
# 3.1 sdtrig_load_bp_basic: Load from watched address triggers breakpoint
#####################
;#discrete_test(test=sdtrig_load_bp_basic)
sdtrig_load_bp_basic:
    OS_SETUP_CHECK_EXCP BREAKPOINT, load_bp_here, load_bp_after
    ;#trigger_config(index=0, type=load, addr=ldst_lin, action=breakpoint)
    li t1, ldst_lin
load_bp_here:
    ld t0, 0(t1)
load_bp_after:
    ;#test_passed()

#####################
# 3.2 sdtrig_load_bp_size_8: Load doubleword (8B) at watched addr
#####################
;#discrete_test(test=sdtrig_load_bp_size_8)
sdtrig_load_bp_size_8:
    OS_SETUP_CHECK_EXCP BREAKPOINT, load_size8_here, load_size8_after
    ;#trigger_config(index=0, type=load, addr=ldst_lin, action=breakpoint, size=8)
    li t1, ldst_lin
load_size8_here:
    ld t0, 0(t1)
load_size8_after:
    ;#test_passed()

#####################
# 3.3 sdtrig_load_bp_size_1: Load byte at watched addr
#####################
;#discrete_test(test=sdtrig_load_bp_size_1)
sdtrig_load_bp_size_1:
    OS_SETUP_CHECK_EXCP BREAKPOINT, load_size1_here, load_size1_after
    ;#trigger_config(index=0, type=load, addr=ldst_lin, action=breakpoint, size=1)
    li t1, ldst_lin
load_size1_here:
    lb t0, 0(t1)
load_size1_after:
    ;#test_passed()

#####################
# 3.4 sdtrig_load_bp_no_fire: Load from different addr; no fire
#####################
;#discrete_test(test=sdtrig_load_bp_no_fire)
sdtrig_load_bp_no_fire:
    ;#trigger_config(index=0, type=load, addr=ldst_lin+0x100, action=breakpoint)
    li t1, ldst_lin
load_no_fire_here:
    ld t0, 0(t1)
    ;#test_passed()

#####################
# 4.1 sdtrig_store_bp_basic: Store to watched address triggers breakpoint
#####################
;#discrete_test(test=sdtrig_store_bp_basic)
sdtrig_store_bp_basic:
    OS_SETUP_CHECK_EXCP BREAKPOINT, store_bp_here, store_bp_after
    ;#trigger_config(index=0, type=store, addr=ldst_lin, action=breakpoint)
    li t1, ldst_lin
store_bp_here:
    sd x0, 0(t1)
store_bp_after:
    ;#test_passed()

#####################
# 4.2 sdtrig_store_bp_size_8: Store doubleword at watched addr
#####################
;#discrete_test(test=sdtrig_store_bp_size_8)
sdtrig_store_bp_size_8:
    OS_SETUP_CHECK_EXCP BREAKPOINT, store_size8_here, store_size8_after
    ;#trigger_config(index=0, type=store, addr=ldst_lin, action=breakpoint, size=8)
    li t1, ldst_lin
store_size8_here:
    sd x0, 0(t1)
store_size8_after:
    ;#test_passed()

#####################
# 4.3 sdtrig_store_bp_size_1: Store byte at watched addr
#####################
;#discrete_test(test=sdtrig_store_bp_size_1)
sdtrig_store_bp_size_1:
    OS_SETUP_CHECK_EXCP BREAKPOINT, store_size1_here, store_size1_after
    ;#trigger_config(index=0, type=store, addr=ldst_lin, action=breakpoint, size=1)
    li t1, ldst_lin
store_size1_here:
    sb x0, 0(t1)
store_size1_after:
    ;#test_passed()

#####################
# 4.4 sdtrig_store_bp_no_fire: Store to different addr; no fire
#####################
;#discrete_test(test=sdtrig_store_bp_no_fire)
sdtrig_store_bp_no_fire:
    ;#trigger_config(index=0, type=store, addr=ldst_lin+0x100, action=breakpoint)
    li t1, ldst_lin
store_no_fire_here:
    sd x0, 0(t1)
    ;#test_passed()

#####################
# 5.1 sdtrig_ldst_bp_load: Load/store trigger fires on load
#####################
;#discrete_test(test=sdtrig_ldst_bp_load)
sdtrig_ldst_bp_load:
    OS_SETUP_CHECK_EXCP BREAKPOINT, ldst_load_here, ldst_load_after
    ;#trigger_config(index=0, type=load_store, addr=ldst_lin, action=breakpoint)
    li t1, ldst_lin
ldst_load_here:
    ld t0, 0(t1)
ldst_load_after:
    ;#test_passed()

#####################
# 5.2 sdtrig_ldst_bp_store: Load/store trigger fires on store
#####################
;#discrete_test(test=sdtrig_ldst_bp_store)
sdtrig_ldst_bp_store:
    OS_SETUP_CHECK_EXCP BREAKPOINT, ldst_store_here, ldst_store_after
    ;#trigger_config(index=0, type=load_store, addr=ldst_lin, action=breakpoint)
    li t1, ldst_lin
ldst_store_here:
    sd x0, 0(t1)
ldst_store_after:
    ;#test_passed()

test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

.else
test_setup:
    ;#test_passed()

;#discrete_test(test=sdtrig_load_bp_basic)
sdtrig_load_bp_basic:
;#discrete_test(test=sdtrig_load_bp_size_8)
sdtrig_load_bp_size_8:
;#discrete_test(test=sdtrig_load_bp_size_1)
sdtrig_load_bp_size_1:
;#discrete_test(test=sdtrig_load_bp_no_fire)
sdtrig_load_bp_no_fire:
;#discrete_test(test=sdtrig_store_bp_basic)
sdtrig_store_bp_basic:
;#discrete_test(test=sdtrig_store_bp_size_8)
sdtrig_store_bp_size_8:
;#discrete_test(test=sdtrig_store_bp_size_1)
sdtrig_store_bp_size_1:
;#discrete_test(test=sdtrig_store_bp_no_fire)
sdtrig_store_bp_no_fire:
;#discrete_test(test=sdtrig_ldst_bp_load)
sdtrig_ldst_bp_load:
;#discrete_test(test=sdtrig_ldst_bp_store)
sdtrig_ldst_bp_store:
    ;#test_passed()

test_cleanup:
    ;#test_passed()
.endif

.section .data
    .dword 0xc001c0de
