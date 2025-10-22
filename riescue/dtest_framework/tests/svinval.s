;#test.name       rva23_svinval
;#test.author     pkennedy@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.category   arch
;#test.summary    Tests compliance with the Svinval extension

;#random_data(name=data1, type=bits32, and_mask=0xffffffff)
;#random_data(name=data2, type=bits32, and_mask=0xffffffff)
;#random_data(name=data3, type=bits32, and_mask=0xffffffff)
;#random_data(name=random_word, type=bits32, and_mask=0xffffffff)

;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=0, x=0, a=1, d=1, pagesize=['4kb'], modify_pt=1)

# Code section for the switch to machine mode
.section .code_machine_0, "ax"

    # Initialize the hstatus.VTVM and mstatus.TVM as follows:
    # hstatus.VTVM = a0[0]
    # mstatus.TVM = a0[1]

    li t0, 0x100000 # hstatus.VTVM mask
    andi t1, a0, 1
    bnez t1, 1f
    csrc hstatus, t0 # VTVM=0
    j 2f
1:  csrs hstatus, t0 # VTVM=1
2:

    li t0, 0x100000 # mstatus.TVM mask
    andi t1, a0, 2
    bnez t1, 1f
    csrc mstatus, t0 # TVM=0
    j 2f
1:  csrs mstatus, t0 # TVM=1
2:

    # Return to testmode
    li x31, 0xf0001004
    ecall

.section .code, "ax"

test_setup:

    ;#test_passed()

;#discrete_test(test=test01)
test01:

    # Randomize the hstatus.VTVM and mstatus.TVM bist
    li a0, random_word
    li x31, 0xf0001001 # Switch to machine mode
    ecall

.if PRIV_MODE_USER

    # When in U-mode, all of the Svinval instructions raise an illegal instruction exception
    # When in VU-mode, all of the Svinval instructions raise a virtual instruction exception

    .if ENV_VIRTUALIZED
        .equ EXPECTED_EXCP_TYPE , VIRTUAL_INSTRUCTION
    .else
        .equ EXPECTED_EXCP_TYPE , ILLEGAL_INSTRUCTION
    .endif

    OS_SETUP_CHECK_EXCP EXPECTED_EXCP_TYPE, pre_excp1, post_excp1
    pre_excp1:
        sfence.w.inval
        .dword 0x0
    post_excp1:
    OS_SETUP_CHECK_EXCP EXPECTED_EXCP_TYPE, pre_excp2, post_excp2
    pre_excp2:
        sfence.inval.ir
        .dword 0x0
    post_excp2:
    OS_SETUP_CHECK_EXCP EXPECTED_EXCP_TYPE, pre_excp3, post_excp3
    pre_excp3:
        sinval.vma x0, x0
        .dword 0x0
    post_excp3:
    OS_SETUP_CHECK_EXCP EXPECTED_EXCP_TYPE, pre_excp4, post_excp4
    pre_excp4:
        hinval.gvma x0, x0
        .dword 0x0
    post_excp4:
    OS_SETUP_CHECK_EXCP EXPECTED_EXCP_TYPE, pre_excp5, post_excp5
    pre_excp5:
        hinval.vvma x0, x0
        .dword 0x0
    post_excp5:

.endif

.if PRIV_MODE_SUPER

    .if ENV_VIRTUALIZED
        # When in VS-mode with hstatus.VTVM=0:
        # - sfence.w.inval      valid
        # - sfence.inval.ir     valid
        # - sinval.vma          valid
        # - hinval.gvma         virtual instruction exception
        # - hinval.vvma         virtual instruction exception
        # When in VS-mode with hstatus.VTVM=1, the same is true except:
        # - sinval.vma          virtual instruction exception

        sfence.w.inval
        sfence.inval.ir

        li t0, random_word
        andi t0, t0, 1
        bnez t0, vtvm_is_set
        sinval.vma x0, x0
        j 1f
        vtvm_is_set:
            OS_SETUP_CHECK_EXCP VIRTUAL_INSTRUCTION, pre_excp3, post_excp3
            pre_excp3:
                sinval.vma x0, x0
                .dword 0x0
            post_excp3:
        1:
        OS_SETUP_CHECK_EXCP VIRTUAL_INSTRUCTION, pre_excp4, post_excp4
        pre_excp4:
            hinval.gvma x0, x0
            .dword 0x0
        post_excp4:
        OS_SETUP_CHECK_EXCP VIRTUAL_INSTRUCTION, pre_excp5, post_excp5
        pre_excp5:
            hinval.vvma x0, x0
            .dword 0x0
        post_excp5:
    .else
        # When in HS-mode with mstatus.TVM=0, all Svinval instructions are valid
        # When in HS-mode with mstatus.TVM=1:
        # - sfence.w.inval      valid
        # - sfence.inval.ir     valid
        # - sinval.vma          illegal instruction exception
        # - hinval.gvma         illegal instruction exception
        # - hinval.vvma         valid

        sfence.w.inval
        sfence.inval.ir

        li t0, random_word
        andi t0, t0, 2
        bnez t0, tvm_is_set
        sinval.vma x0, x0
        hinval.gvma x0, x0
        j 1f
        tvm_is_set:
            OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, pre_excp3, post_excp3
            pre_excp3:
                sinval.vma x0, x0
                .dword 0x0
            post_excp3:
            OS_SETUP_CHECK_EXCP ILLEGAL_INSTRUCTION, pre_excp4, post_excp4
            pre_excp4:
                hinval.gvma x0, x0
                .dword 0x0
            post_excp4:
        1:

        hinval.vvma x0, x0

    .endif

.endif

.if PRIV_MODE_MACHINE

    # When in M-mode, all Svinval instructions all are valid

    sfence.w.inval
    sfence.inval.ir
    sinval.vma x0, x0
    hinval.gvma x0, x0
    hinval.vvma x0, x0

.endif

    li a0, 0 # clear mstatus.TVM and hstatus.VTVM
    li x31, 0xf0001001 # Switch to machine mode
    ecall

.if PRIV_MODE_SUPER && !PAGING_MODE_DISABLE

    # read from the page (cause the translation to be cached in the TLB)
    li t2, lin1
    ld t0, 0(t2)

    # change the PTE to be read-write
    li t0, lin1__pt_level0
    ld t1, 0(t0)    # load the PTE
    ori t1, t1, 4   # mark as writable
    sd t1, 0(t0)    # update the PTE

    # perform sinval sequence
    sfence.w.inval
    sinval.vma x0, x0
    sfence.inval.ir

    # write to the page
    # iff a store page fault occurs, fail the test
    sd x0, 0(t2)

.endif

    ;#test_passed()

.section .data

;#init_memory @lin1
    .dword 1
