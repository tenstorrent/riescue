;#test.name       check_excp
;#test.author     pkennedy@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.summary    Tests os_check_exception

.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:

.if PRIV_MODE_USER && !ENV_VIRTUALIZED
    OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, excp, excp_ret
.endif
.if PRIV_MODE_USER && ENV_VIRTUALIZED
    OS_SETUP_CHECK_EXCP VIRTUAL_INSTRUCTION, excp, excp_ret
.endif

excp:
    sfence.vma

excp_ret:

    ;#test_passed()


.section .data
