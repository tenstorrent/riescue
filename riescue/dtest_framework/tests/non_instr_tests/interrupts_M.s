;#test.name       all_interrupts_m
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        virtualized bare_metal any
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      interrupt
;#test.features   ext_v.disable ext_fp.disable
;#test.tags       interrupt
;#test.summary
;#test.summary    Tests interrupts triggerd by writing to mip CSR while in machine mode.

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


.macro enable_and_trigger_mip interrupt_value
    li t0, \interrupt_value
    csrs mie, t0
    csrs mip, t0
.endm

.macro trigger_mip interrupt_value
    li t0, \interrupt_value
    csrs mip, t0
.endm




#####################
# test_setup: Set MIE in mstatus
#####################
test_setup:
    # Enable Global interrupts
    ENABLE_MIE




end_setup:
    nop

#####################
# Test macros:
#####################

# Assumes xlen is 64
.macro test_mip_traps_direct interrupt_val
    SET_DIRECT_INTERRUPTS
    enable_and_trigger_mip \interrupt_val   # Enable and Trigger interrupt
    csrr t0, mcause                         # Check that bit 1 of mcause set
    li t1, (1<<(XLEN-1))
    and t0, t0, t1
    bne t0, t1, failed
    csrw mcause, x0                         # Clear mcause so read-only bits can be checked
.endm

# Assumes xlen is 64
.macro test_mip_no_interrupt interrupt_val
    enable_and_trigger_mip \interrupt_val
    csrr t0, mcause                         # Assert mcause doesn't contain interrupt bits
    li t1, \interrupt_val
    and t0, t0, t1
    bne t0, x0, failed
.endm

.macro test_mip_readonly interrupt_val
    enable_and_trigger_mip \interrupt_val
    csrr t0, mip                            # Check that interrupt wasn't written
    bne x0, t0, failed
.endm

.macro assert_no_trap
    csrr t0, mcause
    bne x0, t0, failed
.endm



#####################
# test01: Tests directed interrupt for M mode for SSI
#####################
;#discrete_test(test=test01)
test01:
    ENABLE_MIE
    SET_DIRECT_INTERRUPTS
    test_mip_traps_direct (0x1<<1)
    j test_cleanup

#####################
# test02: Tests directed interrupt for M mode for STI
#####################
;#discrete_test(test=test02)
test02:
    ENABLE_MIE
    SET_DIRECT_INTERRUPTS
    test_mip_traps_direct (0x1<<5)
    j test_cleanup

#####################
# test03: Tests directed interrupt for M mode for SEI
#####################
;#discrete_test(test=test03)
test03:
    ENABLE_MIE
    SET_DIRECT_INTERRUPTS
    test_mip_traps_direct (0x1<<9)
    j test_cleanup


#####################
# test04: Tests directed interrupt for M mode for MSI
# MSI should be read-only, so writes shouldn't trigger interrupt
#####################
;#discrete_test(test=test04)
test04:
    ENABLE_MIE
    SET_DIRECT_INTERRUPTS
    test_mip_no_interrupt (0x1 << 3)
    test_mip_readonly (0x1 << 3)
    j test_cleanup


#####################
# test05: Tests directed interrupt for M mode for MTI
# MTI should be read-only, so writes shouldn't trigger interrupt
#####################
;#discrete_test(test=test05)
test05:
    ENABLE_MIE
    SET_DIRECT_INTERRUPTS
    test_mip_no_interrupt (0x1 << 7)
    test_mip_readonly(0x1 << 7)
    j test_cleanup


#####################
# test06: Tests directed interrupt for M mode for MEI
# MEI should be read-only, so writes shouldn't trigger interrupt
#####################
;#discrete_test(test=test06)
test06:
    ENABLE_MIE
    SET_DIRECT_INTERRUPTS
    test_mip_no_interrupt (0x1 << 11)
    test_mip_readonly (0x1 << 11)
    j test_cleanup



#####################
# test07: Tests that disabled global MIE bit and mie CSR doesn't trigger interrupts - software interrupts
#####################
;#discrete_test(test=test07)
test07:
    DISABLE_MIE
    test_mip_no_interrupt (0x1 << 1)
    assert_no_trap
    test_mip_no_interrupt (0x1 << 3)
    assert_no_trap

    # Clear interrupts, enable MIE and check pending interrupts get cleared
    csrw mip, x0
    ENABLE_MIE
    assert_no_trap

    # Only write to the interrupt bit, don't Enable
    csrw mie, x0
    trigger_mip (0x1 << 1)
    assert_no_trap
    trigger_mip (0x1 << 3)
    assert_no_trap

    j test_cleanup

#####################
# test08: Tests that disabled global MIE bit and mie CSR doesn't trigger interrupts - timer interrupts
#####################
;#discrete_test(test=test08)
test08:
    DISABLE_MIE
    test_mip_no_interrupt (0x1 << 5)
    assert_no_trap
    test_mip_no_interrupt (0x1 << 7)
    assert_no_trap

    # Clear interrupts, enable MIE and check pending interrupts get cleared
    csrw mip, x0
    ENABLE_MIE
    assert_no_trap

    # Only write to the interrupt bit, don't Enable
    csrw mie, x0
    trigger_mip (0x1 << 5)
    assert_no_trap
    trigger_mip (0x1 << 7)
    assert_no_trap

    j test_cleanup


#####################
# test09: Tests that disabled global MIE bit and mie CSR doesn't trigger interrupts - timer interrupts
#####################
;#discrete_test(test=test09)
test09:
    DISABLE_MIE
    test_mip_no_interrupt (0x1 << 9)
    assert_no_trap
    test_mip_no_interrupt (0x1 << 11)
    assert_no_trap

    # Clear interrupts, enable MIE and check pending interrupts get cleared
    csrw mip, x0
    csrw mie, x0
    ENABLE_MIE
    assert_no_trap

    # Only write to the interrupt bit, don't Enable
    csrw mie, x0
    trigger_mip (0x1 << 9)
    assert_no_trap
    trigger_mip (0x1 << 11)
    assert_no_trap

    j test_cleanup


#####################
# test10: Test vector interrupts SSI / MSI
#####################
;#discrete_test(test=test10)
test10:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

    enable_and_trigger_mip (0x1<<1)         # Enable and Trigger SSI
    csrr t0, mcause                         # Check that bit 1 of mcause set
    csrr t1, mip                            # Assert that mip was cleared by handler (trap was taken)
    bnez t1, failed

    li t1, (1<<(XLEN-1))
    and t0, t0, t1
    bne t0, t1, failed
    csrw mcause, x0                         # Clear mcause so read-only bits can be checked

    trigger_mip (0x1 << 3)
    assert_no_trap

    j test_cleanup



#####################
# test11: Test vector interrupts STI / MTI
#####################
;#discrete_test(test=test11)
test11:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

    li a0, EXPECT_STI
    enable_and_trigger_mip (0x1<<5)         # Enable and Trigger SSI
    csrr t0, mcause                         # Check that bit 1 of mcause set
    csrr t1, mip                            # Assert that mip was cleared by handler (trap was taken)
    bnez t1, failed

    li t1, (1<<(XLEN-1))
    and t0, t0, t1
    bne t0, t1, failed
    csrw mcause, x0                         # Clear mcause so read-only bits can be checked

    trigger_mip (0x1 << 7)
    assert_no_trap

    j test_cleanup


#####################
# test12: Test vector interrupts SEI / MEI
#####################
;#discrete_test(test=test12)
test12:
    ENABLE_MIE
    SET_VECTORED_INTERRUPTS

    li a0, EXPECT_SEI
    enable_and_trigger_mip (0x1<<9)         # Enable and Trigger SSI
    csrr t0, mcause                         # Check that bit 1 of mcause set
    csrr t1, mip                            # Assert that mip was cleared by handler (trap was taken)
    bnez t1, failed

    li t1, (1<<(XLEN-1))
    and t0, t0, t1
    bne t0, t1, failed
    csrw mcause, x0                         # Clear mcause so read-only bits can be checked

    trigger_mip (0x1 << 11)
    assert_no_trap

    j test_cleanup




# Reset mcause and a0 (vectored interrupt register)
test_cleanup:
    csrw mcause, 0
    li t0, (~0)
    csrc mie, t0
    csrw mip, x0
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
