;#test.name       test_aplic_intr.s
;#test.author     Himanshu Suri
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      custom
;#test.features   interrupt generation
;#test.tags       
;#test.summary
;#test.summary    APLIC and IMSIC programming

;#set_aplic_attr(max_aplic_irq=127)
;#enable_ext_intr_id(intr=1, isr=test_interrupt_handler, eiid=63, source_mode=edge1, hart=0, state=enabled)

.align 2

.data
intr_seen:
    .int 0

.section .code, "ax"

test_setup:
    ;#test_passed()

;#discrete_test(test=test01)
test01:
    la t0, intr_seen
    sw zero, 0(t0)

    la sp, __c__stack_addr    # Setup the stack prior to calling the c function
    ld sp, 0(sp)

    li t0, MAPLIC_MMR_BASE_ADDR
    li t1, 0x3000   # genmsi
    add t0, t0, t1
    li t1, 63   # generate msi 63 on HART 0
    sw t1, 0(t0)

    jal loop_a_bit
    ;#test_failed()

test_cleanup:
    ;#test_passed()

loop_a_bit:
    li a0, 1
    li t0, 0
    li t1, 0x40

_loop_here:
    add t0, t0, 1
    la t2, intr_seen
    li t3, 1
    lw t2, 0(t2)
    beq t2, t3, _passed
    bne t0, t1, _loop_here
    ret
_passed:
    ;#test_passed()
    ret

.globl test_interrupt_handler
test_interrupt_handler:
    la t0, trap_handler_m__trap_entry
    csrw mtvec, t0
    csrr t3, mcause
    li t0, 0x800
    csrc mip,t0
    csrr t0, mie
    la t0, intr_seen
    li t1, 1
    sw t1, 0(t0)
    j trap_handler_m__trap_exit
