;#test.name       fail_test
;#test.author     nmatus@tenstorrent.com
;#test.arch       rv64
;#test.priv       any
;#test.env        any
;#test.cpus       2
;#test.paging     any
;#test.category   arch
;#test.class      none
;#test.features
;#test.tags       unit test
;#test.summary
;#test.summary      Test that fails in MP mode. Hart 0 passes, Hart 1 fails
;#test.summary



.section .code, "aw"

test_setup:
    ;#test_passed()


#####################
# test01: sample test 1
#####################
;#discrete_test(test=test01)
test01:
    GET_MHART_ID
    beq s1, x0, hart0_pass

    hart1_fail:
        ;#test_failed()

    hart0_pass:
        ;#test_passed()



;#discrete_test(test=test02)
test02:
    GET_MHART_ID
    beq s1, x0, 2f

1:
    ;#test_failed()

2:
    ;#test_passed()



test_cleanup:
    ;#test_passed()

.section .data
expected_tohost:
    .dword 0x00000f00
