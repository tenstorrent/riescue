;#test.name       xtinst
;#test.author     ysohail@tenstorrent.com
;#test.arch       rv64
;#test.priv       super
;#test.env        bare_metal virtualized
;#test.cpus       1
;#test.paging     sv39 sv48
;#test.category   arch
;#test.summary    Drives every mtinst/htinst check path in the trap handler

# Exercises the trap handler's mtinst/htinst check (TrapHandler.xtinst_check) by
# taking one trap per shape the spec allows the trap instruction register to
# hold. The check runs underneath these traps and fails the test on its own if
# the DUT writes a value the cause does not permit, or a transformed value that
# does not describe the instruction that trapped -- so a passing run means every
# path below was accepted.
#
# Covered:
#   test01  transformed basic load          (LOAD kind, funct3/rd/opcode kept)
#   test02  transformed basic store         (STORE kind, rs2/funct3/opcode kept)
#   test03  transformed atomic              (AMO kind, every field but rs1 kept)
#   test04  compressed load                 (bits[1:0]=0b01, no field compare)
#   test05  breakpoint                      (zero or a custom value only)
#   test06  illegal instruction             (zero only)
#   test07  page-straddling misaligned load (nonzero Addr. Offset)
#   test08  HLV from HS                     (HLV kind, every field but rs1 kept)
#
# The check is opt-in, so --check_xtinst is required or this test passes
# vacuously -- the traps still fire, but nothing inspects the trap value.
#
# Usage:
#     riescued.py -t .../xtinst.s --seed 1 --run_iss --check_xtinst
#     riescued.py -t .../xtinst.s --seed 1 --run_iss --check_xtinst \
#         --test_env virtualized --medeleg=0xffffffff --hedeleg=0x0 \
#         --test_paging_g_mode sv39

# W without R is a reserved leaf encoding, so any access faults with the fault
# type of the access -- a load here.
;#random_addr(name=lin_ld, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_ld, phys_name=&random, v=1, r=0, w=1, a=1, d=1, pagesize=['4kb'])

# A valid page immediately followed by an invalid one, so a misaligned load across
# the boundary faults on its second half.
;#random_addr(name=lin_split, type=linear, size=0x2000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_split, phys_name=&random, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#page_mapping(lin_name=lin_split+0x1000, phys_name=&random, v=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])

# Readable but not writable: loads succeed, stores and AMOs fault.
;#random_addr(name=lin_st, type=linear, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin_st, phys_name=&random, v=1, r=1, w=0, a=1, d=1, pagesize=['4kb'])

# HS-mode (V=0) continuation for the switch-to-super syscall, which is where
# test08's HLV access has to run. Ends by switching back to the test's own
# privilege, which resumes right after test08's ecall.
.section .code_super_0, "ax"
.if ENV_VIRTUALIZED
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, fault_hlv, ret_hlv, lin_ld
    li x1, lin_ld
fault_hlv:
    hlv.d t2, 0(x1)
    j hs_fail_label
ret_hlv:
.endif
    li x31, 0xf0001004              # back to the test's own privilege
    ecall
hs_fail_label:
    ;#test_failed()

.section .code, "ax"

test_setup:
    nop
    ;#test_passed()

#####################
# test01: a basic load faults. The trap instruction register may hold the
# transformed load: funct3, rd and opcode from the ld, immediate zeroed, rs1
# replaced by Addr. Offset.
#####################
;#discrete_test(test=test01)
test01:
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, fault_ld, ret_ld, lin_ld
    li x1, lin_ld
fault_ld:
    ld t2, 0(x1)
    ;#test_failed()
ret_ld:
    ;#test_passed()

#####################
# test02: a basic store faults. The transformed store keeps rs2, funct3 and
# opcode, and zeroes both the immediate and rd.
#####################
;#discrete_test(test=test02)
test02:
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, fault_sd, ret_sd, lin_st
    li x1, lin_st
    li t3, 0x1234
fault_sd:
    sd t3, 0(x1)
    ;#test_failed()
ret_sd:
    ;#test_passed()

#####################
# test03: an AMO faults. The transformed atomic keeps every field except rs1.
#####################
;#discrete_test(test=test03)
test03:
    OS_SETUP_CHECK_EXCP STORE_PAGE_FAULT, fault_amo, ret_amo, lin_st
    li x1, lin_st
    li t3, 0x1234
fault_amo:
    amoadd.d t2, t3, (x1)
    ;#test_failed()
ret_amo:
    ;#test_passed()

#####################
# test04: a compressed load faults. A transformed value must then have
# bits[1:0]=0b01; the handler checks that agreement and stops short of
# expanding the instruction to compare fields.
#####################
;#discrete_test(test=test04)
test04:
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, fault_cld, ret_cld, lin_ld
    li s1, lin_ld
fault_cld:
    # The loader emits a file-wide .option norvc, so re-enable compression for
    # just this instruction -- it has to stay 16 bits for the check to see the
    # compressed case.
    .option push
    .option rvc
    c.ld a0, 0(s1)
    .option pop
    ;#test_failed()
ret_cld:
    ;#test_passed()

#####################
# test05: a breakpoint. Only zero or a custom value is permitted, and no
# transformation is defined, so a standard encoding here must be rejected.
#####################
;#discrete_test(test=test05)
test05:
    OS_SETUP_CHECK_EXCP BREAKPOINT, fault_bp, ret_bp
fault_bp:
    ebreak
ret_bp:
    ;#test_passed()

#####################
# test06: an illegal instruction. Zero is the only permitted value.
#####################
;#discrete_test(test=test06)
test06:
    OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, fault_ill, ret_ill
fault_ill:
    .word 0x00000000
ret_ill:
    ;#test_passed()

#####################
# test07: a misaligned load straddling into an invalid page. The faulting virtual
# address is the page boundary, past the address the instruction computed, so
# Addr. Offset in the transformed load is nonzero.
#####################
;#discrete_test(test=test07)
test07:
    OS_SETUP_CHECK_EXCP LOAD_PAGE_FAULT, fault_split, ret_split, (lin_split + 0x1000)
    li x1, lin_split
    li t0, 0xffc
    add x1, x1, t0
fault_split:
    ld t2, 0(x1)
    ;#test_failed()
ret_split:
    ;#test_passed()

#####################
# test08: an HLV fault, exercising the transformed virtual-machine load, which
# keeps every field except rs1.
#
# HLV/HLVX/HSV are only executable with V=0, so the faulting access has to run
# outside the guest. Syscall 0xf0001002 clears mstatus.MPV and lands in HS -- not
# back at the ecall, but at .code_super_0, where the body of this case lives.
#####################
;#discrete_test(test=test08)
test08:
.if ENV_VIRTUALIZED
    li x31, 0xf0001002              # to HS (V=0); continues in .code_super_0
    ecall
.endif
    ;#test_passed()

test_fail_label:
    ;#test_failed()

test_cleanup:
    ;#test_passed()
