;#test.name       init_csr_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      rv64i
;#test.features   ext_i.enable wysiwyg
;#test.tags       csr mscratch
;#test.summary    This test verifies that mscratch CSR is properly initialized with the value provided through command line
;#test.summary    test01: Verify mscratch CSR value matches the expected value from command line

.section .text

#####################
# test01: Verify mscratch CSR value from command line
#####################
;#discrete_test(test=test01)
test01:
    # Read mscratch value
    csrr t0, mscratch

    # Compare with expected value (will be set through command line)
    # The value should be passed as a parameter to the test
    li t1, 0x12345678  # This value should be overridden by command line parameter
    bne t0, t1, failed

    j passed



passed:
    li x1, 0
    li x31, 0xc001c0de
    j end

failed:
    li x1, 1
    j end
end:
    la x2, tohost
    sw x1, 0(x2)
    j end

.align 6
.global tohost
tohost:
    .dword 0