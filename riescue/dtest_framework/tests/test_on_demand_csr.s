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
;#test.summary

# Code section for the switch to super mode
.section .code_super_0, "ax"
    li x31, 0xf0001004
    ecall

# Code section for the switch to user mode
.section .code_user_0, "ax"
    li x31, 0xf0001004
    ecall

# Code section for the switch to machine mode
.section .code_machine_0, "ax"
    li x31, 0xf0001004
    ecall

.section .code, "ax"

#####################
# test_setup
#####################
test_setup:
    li x1, 0xc0010001
.if OS_DELEG_EXCP_TO_MACHINE
    li x31, 0xf0001001
    ecall
    li x31, 0xf0001002
    ecall
    li x1, 0x00000000
    li x31, 0xf0001003
    ecall
    li x1, 0x00000000
.endif
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
# test_cleanup
#####################
test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

.section .data
my_data:
    .dword 0xc001c0de
