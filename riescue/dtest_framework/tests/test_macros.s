;#test.name       test_macros
;#test.author     nmatus
;#test.arch       rv64
;#test.priv       machine super user any
;#test.cpus       2
;#test.paging     sv39 sv48 sv57 disable any
;#test.class      macros
;#test.mp         simultaneous
;#test.summary
;#test.summary    Test cases to check macros
;#test.summary


.section .code, "ax"

#####################
# test_setup: Common initialization
#####################
test_setup:
    li x1, 0xc0010001
    ;#test_passed()

#####################
# test01: OS_SETUP_CHECK_EXCP macro
#####################
;#discrete_test(test=test01)
test01:
    # setup an ilegal instruction check by jumping to word 0
    OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, test01_trigger, test01_ret, 0x0, 0
    j test01_trigger

# illegal isntruction, shouldn't load the data
test01_trigger:
    .word 0x00000000
    # Should not reach here - exception handler intercepts
    li t0, 0xAAAAAAAA
    ;#test_failed()

test01_ret:
    # Exception handler restored us here
    # If macro setup correctly, we pass
    ;#test_passed()

#####################
# test_cleanup: Common cleanup
#####################
test_cleanup:
    li x1, 0xc0010002
    ;#test_passed()

#####################
# Data section
#####################
.section .data
    .align 3
    .dword 0x0

;#random_addr(name=mutex_lock,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mutex_lock_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mutex_lock, phys_name=mutex_lock_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#init_memory @mutex_lock
    .dword 0x0

;#random_addr(name=another_user_lock,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=another_user_lock_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=another_user_lock, phys_name=another_user_lock_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#init_memory @another_user_lock
    .dword 0x0

;#random_addr(name=semaphore,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=semaphore_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=semaphore, phys_name=semaphore_phys, v=1, u=1, x=0, r=1, w=1, a=1, d=1, pagesize=['4kb'])
;#init_memory @semaphore
    .dword 0x3
