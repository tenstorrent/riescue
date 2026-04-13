;#test.name       htif_rvcp_fail_test
;#test.author     riescue@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.category   arch
;#test.class      rvcp
;#test.summary
;#test.summary    Test file that intentionally fails to verify RVCP FAIL messages
;#test.summary    are printed to the HTIF console when --print_rvcp_failed is enabled.
;#test.summary
;#test.summary    test_setup: Pass
;#test.summary    test01: Intentionally fails with ;#test_failed()
;#test.summary

.section .code, "ax"

#####################
# test_setup: Initialize test
#####################
test_setup:
    li x1, 0x12345678
    ;#test_passed()

#####################
# test01: This test intentionally fails
#####################
;#discrete_test(test=test01)
test01:
    li x1, 0xDEADBEEF
    # Intentionally fail to test RVCP FAIL message output
    ;#test_failed()

#####################
# test_cleanup: Final cleanup
#####################
test_cleanup:
    li x1, 0x0
    ;#test_passed()
