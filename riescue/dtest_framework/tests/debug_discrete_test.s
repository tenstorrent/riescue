;#test.name       debug_discrete_test
;#test.author     dtest
;#test.arch       rv64
;#test.priv       any
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     any
;#test.category   arch
;#test.class      debug
;#test.features   not_hooked_up_yet
;#test.tags       debug
;#test.summary    Minimal test for ;#discrete_debug_test(): compile and content check only.

;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

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

test_setup:
    li x31, 0xf0001001
    ecall
    li x31, 0xf0001002
    ecall
    li x31, 0xf0001003
    ecall
    ;#test_passed()

;#discrete_test(test=main_test)
main_test:
    nop
    ;#test_passed()

test_cleanup:
    ;#test_passed()

;#discrete_debug_test()
    # For this mechanism to work, we expect DCSR.prv to set to machine mode using the backdoor write before reset.
    # The code is in this test is called when the hart enters debug mode. So, the initial entering of hart also
    # needs coordination with the testbench.

    # Riescue will add prologue code here which will set DCSR.prv to test_priv (DCSR only writable in debug mode)
    # This is handled automatically

    # DEBUG_ROM_BODY_MARKER, add your debug mode code here
    nop
    addi x0, x0, 0

    # Use following directive ;#dret to restore DPC and return from debug mode
    #Riescue will expand this directive to restore DPC and return from debug mode
;#dret
