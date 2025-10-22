;#test.name       check_excp
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       any
;#test.env        any
;#test.cpus       1
;#test.paging     any
;#test.category   arch
;#test.summary    Tests --test_equates switch to pass defines. Can pass multiple defines
;#test.summary      e.g. the following would make the test pass:
;#test.summary          --test_equates DEFINE_A=1 --test_equates DEFINE_B=1
;#test.summary          --test_equates CUSTOM_DEFINE_EN=1

.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:


.if DEFINE_A==1 && DEFINE_B==1
    ;#test_passed()
.endif
.if CUSTOM_DEFINE_EN==1
    ;#test_passed()
.endif
    ;#test_failed()

test_cleanup:
    ;#test_passed()

.section .data
