;#test.name       test_on_demand_csr
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.category   arch
;#test.class      csr
;#test.features   not_hooked_up_yet ext_v.enable ext_fp.disable
;#test.tags       csr csr_rw
;#test.summary
;#test.summary    On-demand CSR read/write via ;#csr_rw API.
;#test.summary    test01_csr_selfcheck: mstatus MIE set_bit/clear_bit/write_subfield/read_subfield self-check
;#test.summary    test02_duplicate_csr_write: two writes to same CSR with different values
;#test.summary    test03_duplicate_csr_write_subfield: two write_subfield to same CSR with different fields
;#test.summary    test04_duplicate_set_clear_bit: set_bit/clear_bit with different bit positions
;#test.summary    test05_set_clear_values: set/clear with different mask values
;#test.summary

.section .code, "ax"

#####################
# test_setup
#####################
test_setup:
    ;#test_passed()

#####################
# test01_csr_selfcheck: mstatus MIE set_bit/clear_bit/write_subfield/read_subfield
# NOTE: Do NOT use mscratch/sscratch - they are used by the trap handler.
#####################
;#discrete_test(test=test01_csr_selfcheck)
test01_csr_selfcheck:
    ;#csr_rw(mstatus, set_bit, bit=3)
    ;#csr_rw(mstatus, read_subfield, field=MIE)
    li t3, 1
    bne t2, t3, failed

    ;#csr_rw(mstatus, clear_bit, bit=3)
    ;#csr_rw(mstatus, read_subfield, field=MIE)
    li t3, 0
    bne t2, t3, failed

    ;#csr_rw(mstatus, write_subfield, field=MIE, value=1)
    ;#csr_rw(mstatus, read_subfield, field=MIE)
    li t3, 1
    bne t2, t3, failed

    ;#test_passed()

#####################
# test02_duplicate_csr_write: two writes to same CSR with different values
#####################
;#discrete_test(test=test02_duplicate_csr_write)
test02_duplicate_csr_write:
    ;#csr_rw(stval, write, value=0xDEAD, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    li t3, 0xDEAD
    bne t2, t3, failed

    ;#csr_rw(stval, write, value=0xBEEF, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    li t3, 0xBEEF
    bne t2, t3, failed

    ;#test_passed()

#####################
# test03_duplicate_csr_write_subfield: two write_subfield to same CSR with different fields
#####################
;#discrete_test(test=test03_duplicate_csr_write_subfield)
test03_duplicate_csr_write_subfield:
    ;#csr_rw(mstatus, write_subfield, field=SIE, value=1)
    ;#csr_rw(mstatus, read, force_machine=true)
    andi t3, t2, 0x2
    beqz t3, failed

    ;#csr_rw(mstatus, write_subfield, field=SPIE, value=0)
    ;#csr_rw(mstatus, read, force_machine=true)
    andi t3, t2, 0x20
    bnez t3, failed

    ;#test_passed()

#####################
# test04_duplicate_set_clear_bit: set_bit/clear_bit with different bit positions
#####################
;#discrete_test(test=test04_duplicate_set_clear_bit)
test04_duplicate_set_clear_bit:
    # Clear both SIE (bit 1) and SPIE (bit 5) first
    ;#csr_rw(mstatus, clear_bit, bit=1)
    ;#csr_rw(mstatus, clear_bit, bit=5)

    # Set SIE (bit 1)
    ;#csr_rw(mstatus, set_bit, bit=1)
    ;#csr_rw(mstatus, read, force_machine=true)
    andi t3, t2, 0x2
    beqz t3, failed

    # Set SPIE (bit 5)
    ;#csr_rw(mstatus, set_bit, bit=5)
    ;#csr_rw(mstatus, read, force_machine=true)
    andi t3, t2, 0x20
    beqz t3, failed

    # Clear SIE (bit 1), verify it cleared while SPIE stays set
    ;#csr_rw(mstatus, clear_bit, bit=1)
    ;#csr_rw(mstatus, read, force_machine=true)
    andi t3, t2, 0x2
    bnez t3, failed
    andi t3, t2, 0x20
    beqz t3, failed

    ;#test_passed()

#####################
# test05_set_clear_values: set/clear with different mask values
#####################
;#discrete_test(test=test05_set_clear_values)
test05_set_clear_values:
    # Write 0 to stval to start clean
    ;#csr_rw(stval, write, value=0, force_machine=true)

    # Set bits 0xF0
    ;#csr_rw(stval, set, value=0xF0, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    andi t3, t2, 0xF0
    li t4, 0xF0
    bne t3, t4, failed

    # Set bits 0x0F (different value, same action)
    ;#csr_rw(stval, set, value=0x0F, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    andi t3, t2, 0xFF
    li t4, 0xFF
    bne t3, t4, failed

    # Clear bits 0xF0
    ;#csr_rw(stval, clear, value=0xF0, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    andi t3, t2, 0xFF
    li t4, 0x0F
    bne t3, t4, failed

    # Clear bits 0x0F (different value, same action)
    ;#csr_rw(stval, clear, value=0x0F, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    andi t3, t2, 0xFF
    bnez t3, failed

    ;#test_passed()

#####################
# test06_force_machine_no_force_machine: two writes to same CSR with different values, one from force_machine=true, one not
#####################
;#discrete_test(test=test06_force_machine_no_force_machine)
test06_force_machine_no_force_machine:
    ;#csr_rw(stval, write, value=0xDEAD, force_machine=true)
    ;#csr_rw(stval, read, force_machine=true)
    li t3, 0xDEAD
    bne t2, t3, failed

    ;#csr_rw(stval, write, value=0xBEEF)
    ;#csr_rw(stval, read)
    li t3, 0xBEEF
    bne t2, t3, failed

    ;#test_passed()

#####################
# test_cleanup
#####################
test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

.section .data
my_data:
    .dword 0xc001c0de
