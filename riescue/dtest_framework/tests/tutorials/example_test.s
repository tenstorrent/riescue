;#test.author     developer@company.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal

.section .code, "ax"

test_setup:
    # Executed before each test, exactly once
    ;#test_passed()

;#random_data(name=test_data, type=bits32, and_mask=0xfffffff0)

;#discrete_test(test=test01)
test01:
    li t0, test_data
    li t1, 0xdeadbeef
    add t2, t0, t1

    bne t2, x0, test01_pass
    ;#test_failed()

test01_pass:
    ;#test_passed()

test_cleanup:
    # Executed after all tests are run, exactly once
    ;#test_passed()

.section .data