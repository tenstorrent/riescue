;#test.name       htif_rvcp_fail_test
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      none
;#test.features
;#test.tags       htif rvcp unit_test
;#test.summary
;#test.summary    Minimal test for verifying HTIF RVCP injection.
;#test.summary
;#test.summary    test_htif_rvcp_fail: unconditionally calls ;#test_failed() so that
;#test.summary    --eot_print_htif_console injects an RVCP FAIL message.  Also
;#test.summary    exercises --print_rvcp_passes on setup/cleanup which call
;#test.summary    ;#test_passed().
;#test.summary


.section .code

test_setup:
    ;#test_passed()


;#discrete_test(test=htif_rvcp_fail)
htif_rvcp_fail:
    # Unconditional failure: used to verify HTIF RVCP FAIL injection.
    ;#test_failed()


test_cleanup:
    ;#test_passed()
