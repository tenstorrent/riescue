;#test.name       all_interrupts_S_delegate_S
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       supervisor
;#test.env        virtualized bare_metal any
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      interrupt
;#test.features   ext_v.disable ext_fp.disable
;#test.tags       interrupt
;#test.opts       deleg_excp_to=super
;#test.summary
;#test.summary    Tests interrupts triggerd by writing to sip CSR while in supervisor mode, and checking that interrupts stay in supervisor mode
;#test.summary
;#test.summary     NOTE: requires switch --deleg_excp_to=super



#####################
# Define random data here
#####################
;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
;#random_data(name=data2, type=bits1, and_mask=0xffffffff)


#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])


;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin2, phys_name=phys2, a=1, d=1, v=1, r=1, w=1, x=1, pagesize=['4kb'], g=0, g_nonleaf=0, modify_pt=1)
;#random_data(name=SMSInum , type=bits64, and_mask=0xff)
;#random_data(name=MMSInum , type=bits64, and_mask=0xff)
;#random_data(name=MISA_H , type=bits64, and_mask=0x1)


.section .code, "ax"


#####################
# CSR macros
#####################


.macro enable_and_trigger_sip interrupt_value
    li t0, \interrupt_value
    csrs sie, t0
    csrs sip, t0
.endm

.macro trigger_sip interrupt_value
    li t0, \interrupt_value
    csrs sip, t0
.endm




#####################
# test_setup: Set sie in mstatus
#####################
test_setup:
    nop
    csrr t0, sstatus






end_setup:
    nop

#####################
# Test macros:
#####################

# Assumes xlen is 64
.macro test_sip_traps_direct interrupt_val
    SET_DIRECT_INTERRUPTS_S
    enable_and_trigger_sip \interrupt_val   # Enable and Trigger interrupt
    csrr t0, scause                         # Check that bit 1 of scause set
    li t1, (1<<(XLEN-1))
    and t0, t0, t1
    bne t0, t1, failed
    csrw scause, x0                         # Clear scause so read-only bits can be checked
.endm

.macro test_sip_traps_vector interrupt_val
    enable_and_trigger_sip \interrupt_val   # Enable and Trigger interrupt
    csrr t0, scause                         # Check that bit 1 of scause set
    li t1, (1<<(XLEN-1))
    and t0, t0, t1
    bne t0, t1, failed
    csrw scause, x0                         # Clear scause so read-only bits can be checked
.endm

# Assumes xlen is 64
.macro test_sip_no_interrupt interrupt_val
    enable_and_trigger_sip \interrupt_val
    csrr t0, scause                         # Assert scause doesn't contain interrupt bits
    li t1, \interrupt_val
    and t0, t0, t1
    bne t0, x0, failed
.endm

.macro test_sip_readonly interrupt_val
    enable_and_trigger_sip \interrupt_val
    csrr t0, sip                            # Check that interrupt wasn't written
    bne x0, t0, failed
.endm

.macro assert_no_trap
    csrr t0, scause
    bne x0, t0, failed
.endm

# Checks if interrupt is implented, stores 1 to t0 if true
.macro check_interrupt_implemented interrupt_val
    li t0, \interrupt_val
    csrs sie, t0    # Set bit
    csrr t1, sie    # Read to check if implemented
    csrc sie, t0    # Clear bit
    slt t0, x0, t1  # return t0=1 if implemneted
.endm


#####################
# test01: Tests directed interrupt for S mode for SSI
#####################
;#discrete_test(test=test01)
test01:
    ENABLE_SIE
    check_interrupt_implemented (0x1<<1)
    beqz t0, test_cleanup
    test_sip_traps_direct (0x1<<1)

    li t0, ~0x0
    csrc scause, t0
    # clear sie, trigger sip
    csrc sie, t0
    trigger_sip (0x1<<1)
    csrr t0, scause
    bnez t0, failed                         # Shouldn't have had an interrupt


    # enable ssie, but disable S-mode interrupts
    DISABLE_SIE
    li t0, (0x1<<1)
    csrs sie, t0
    trigger_sip (0x1<<1)
    csrr t0, sip                            # Check that interrupt wasn't written
    beq x0, t0, failed                      # Interrupt would have cleared the sip bit
    j test_cleanup

#####################
# test02: Tests directed interrupt for S mode for STI
#####################
;#discrete_test(test=test02)
test02:
    ENABLE_SIE
    check_interrupt_implemented (0x1<<5)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<5)
    j test_cleanup

#####################
# test03: Tests directed interrupt for S mode for SEI
#####################
;#discrete_test(test=test03)
test03:
    ENABLE_SIE
    check_interrupt_implemented (0x1<<9)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<9)
    j test_cleanup

#####################
# test04: Test MSI interrupts
#####################
;#discrete_test(test=test04)
test04:
    ENABLE_SIE
    check_interrupt_implemented (0x1<<3)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<3)
    j test_cleanup

#####################
# test05: Test MTI interrupt
#####################
;#discrete_test(test=test05)
test05:
    ENABLE_SIE
    check_interrupt_implemented (0x1<<7)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<7)
    j test_cleanup

#####################
# test06: Test MEI interrupt
#####################
;#discrete_test(test=test06)
test06:
    ENABLE_SIE
    check_interrupt_implemented (0x1<<11)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<11)
    j test_cleanup


#####################
# test07: Tests vectored interrupt for S mode for SSI
#####################
;#discrete_test(test=test07)
test07:
    ENABLE_SIE
    SET_VECTORED_INTERRUPTS_S
    check_interrupt_implemented (0x1<<1)
    beqz t0, test_cleanup
    test_sip_traps_vector (0x1<<1)
    csrr t1, sip                            # Assert that mip was cleared by handler (trap was taken)
    bnez t1, failed

    j test_cleanup

#####################
# test08: Tests vectored interrupt for S mode for STI
#####################
;#discrete_test(test=test08)
test08:
    ENABLE_SIE
    SET_VECTORED_INTERRUPTS_S
    check_interrupt_implemented (0x1<<5)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<5)
    j test_cleanup

#####################
# test09: Tests vectored interrupt for S mode for SEI
#####################
;#discrete_test(test=test09)
test09:
    ENABLE_SIE
    SET_VECTORED_INTERRUPTS_S
    check_interrupt_implemented (0x1<<9)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<9)
    j test_cleanup

#####################
# test10: Test vectored MSI interrupts
#####################
;#discrete_test(test=test10)
test10:
    ENABLE_SIE
    SET_VECTORED_INTERRUPTS_S
    check_interrupt_implemented (0x1<<3)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<3)
    j test_cleanup

#####################
# test11: Test vectored MTI interrupt
#####################
;#discrete_test(test=test11)
test11:
    ENABLE_SIE
    SET_VECTORED_INTERRUPTS_S
    check_interrupt_implemented (0x1<<7)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<7)
    j test_cleanup

#####################
# test12: Test vectored MEI interrupt
#####################
;#discrete_test(test=test06)
test12:
    ENABLE_SIE
    SET_VECTORED_INTERRUPTS_S
    check_interrupt_implemented (0x1<<11)
    beqz t0, test_cleanup
    test_sip_no_interrupt (0x1<<11)
    j test_cleanup




# Reset scause and a0 (vectored interrupt register)
test_cleanup:
    csrw scause, 0
    li t0, (~0)
    csrc sie, t0
    csrw sip, x0
    li a0, 0
    j passed




#####################
# Default data section
#####################
.section .data
my_data:
    .dword 0xdeadbeef


#####################
# User defined data section located at address lin1
#####################
;#init_memory @lin1
# -> we convert above syntax to this -> .section .lin1
my_data1:
    .dword 0xc001c0de
