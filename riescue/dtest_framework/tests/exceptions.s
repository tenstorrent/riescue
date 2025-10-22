;#test.name       Exceptions test suite 1
;#test.author     travi@tenstorrent.com
;#test.arch       rv64
#;#test.priv       super user machine any
#TODO : Shravil/Micheal Can we override the test.priv tag using a cmdline ? Follow up
;#test.priv       super
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39
;#test.category   arch
;#test.class      rv64i
;#test.features   ext_i.enable
;#test.tags       int rv64i exceptions
;#test.summary
;#test.summary
;#test.summary    test01: Instruction Address Misaligned
;#test.summary    test02: Illegal Instruction Test
;#test.summary    test03: System Call (M/S)
;#test.summary    test04:
;#test.summary    test05:
;#test.summary    test06: Load Access Fault      #Bug 612
;#test.summary    test07: Load Page Fault        #Bug 611
;#test.summary    test08: Store AMO Page Fault   #Bug 611
;#test.summary    test09: Instruction Page Fault #Bug 611
;#test.summary    test10: Store AMO Access Fault #Bug 612
;#test.summary
;#test.summary          Progress - 9/13
;#test.summary
;#test.summary    #TODO - INSTRUCTION_ACCESS_FAULT - LOAD_ADDRESS_MISALIGNED(WIP) - STORE_ADDRESS_MISALIGNED
;#test.summary    #TODO Post Delegation - ECALL various mode access's -
;#test.summary

#TODO : Nested - Delegation - Access fault via PMA/PMP configuration

#Exception Cause Table is part of the Riescue-D framework

#Paging offsets
#Lin1
.equ misaligned_instruction_addr, 0x20
.equ illegal_instruction_addr, 0x30
#Lin2
.equ my_data2, 0x10
.equ load_page_fault_addr, 0x1000

#Misc
.equ invalid_opcode, 0x0

#TODO Macro calling Macro or Macro as Arg ? for more control over the Instruction granularity
.macro EXCEPTION cause, instruction_addr, return_pc=excp_ret
    #Might have to add few NOPs in RTL implementation

    li x28, \cause
    sd x28, 0(x10)

    la x29, \return_pc
    sd x29, 0(x11)

    la x30, \instruction_addr

    jalr x30
    ;#test_failed() # Should not reach here

.endm

.macro UPDATE_EPC
    auipc x28, 0x0
    addi x28,x28,0xc
    sd x28, 0(x12) #Update expected PC value

.endm

#####################
# Define random data here
#####################
;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
;#random_data(name=data2, type=bits20, and_mask=0xffffffff)
;#random_data(name=data3, type=bits22)


#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0xfffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0xfffff000)
;#page_mapping(lin_name=lin2, phys_name=phys2, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['any'])




.section .code, "aw"

#####################
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################
test_setup:
    # Put your common initialization code here, e.g. initialize csr here if needed

    #TODO : Request Darshak to incorporate some hook to do some setup as machine
    li x1, 0xc0010001
    li a0, check_excp_expected_cause
    li a1, check_excp_return_pc
    li a2, check_excp_expected_pc


    #mepc,mstatus,satp,medelg,


    ;#test_passed()

#####################
# test01: Misaligned Instruction Exception
#####################
;#discrete_test(test=test01)
test01:
    EXCEPTION INSTRUCTION_ADDRESS_MISALIGNED, misaligned_instruction

#####################
# test02: Illegal Instruction Exception
#####################
;#discrete_test(test=test02)
test02:
    EXCEPTION ILLEGAL_INSTRUCTION, illegal_instruction

#####################
# test03: Environment_call_from_S_mode
#####################
;#discrete_test(test=test03)
test03:
    .if PRIV_MODE_MACHINE
        EXCEPTION ECALL_FROM_MACHINE, syscall
    .endif
    .if PRIV_MODE_SUPER
        EXCEPTION ECALL_FROM_SUPER, syscall
    .endif
    .if PRIV_MODE_USER
        EXCEPTION ECALL_FROM_USER, syscall
    .endif
#####################
# test04: instruction_address_misaligned
#####################
#;#discrete_test(test=test04)
#test04:
#    EXCEPTION INSTRUCTION_ADDRESS_MISALIGNED, instruction_address_misaligned_instruction

#####################
# test06: load_access_fault
#####################
;#discrete_test(test=test06)
test06:
    EXCEPTION LOAD_ACCESS_FAULT, load_access_fault_instruction #Cause can be Misaligned or Access fault, depending on implementation

#####################
# test07: load_page_fault
#####################
;#discrete_test(test=test07)
test07:
    EXCEPTION LOAD_PAGE_FAULT, load_page_fault_instruction

#####################
# test08: store_page_fault
#####################
;#discrete_test(test=test08)
test08:
    EXCEPTION STORE_PAGE_FAULT, store_page_fault_instruction


#####################
# test09: instruction_page_fault
#####################
;#discrete_test(test=test09)
test09:
    EXCEPTION INSTRUCTION_PAGE_FAULT, instruction_page_fault_instruction

#####################
# test10: store_access_fault
#####################
;#discrete_test(test=test10)
test10:
    EXCEPTION STORE_ACCESS_FAULT, store_amo_access_fault_instruction #Cause can be Misaligned or Access fault, depending on implementation

#####################
# test11: store_access_fault
#####################
#;#discrete_test(test=test11)
#test11:
#    EXCEPTION INSTRUCTION_PAGE_FAULT, instruction_access_fault_instruction #Cause can be Misaligned or Access fault, depending on implementation

#####################
# test12: store_access_fault
#####################
;#discrete_test(test=test12)
test12:

    EXCEPTION LOAD_ADDRESS_MISALIGNED, load_address_misaligned_instruction #Cause can be Misaligned or Access fault, depending on implementation

# We return here after exception is handled
excp_ret:
    #If mtval is written with a nonzero value when a breakpoint, address-misaligned, access-fault, or page-fault exception occurs on an instruction fetch, load, or store, then mtval will contain the faulting virtual address.

    li t1, 0xc001c0de
    .if PRIV_MODE_MACHINE
        csrr x27, mtval
    .endif
    .if PRIV_MODE_SUPER
        csrr x27, stval
    .endif
    # bne t1, t2, failed

    # 'passed' and 'failed' are special RiESCUE defined labels, which each
    # discrete_test must use to indicate the end of the discrete_test

    # ;#test_failed()  <-- ';#test_failed()' should be used to indicate OS that discrete_test
    #               hit a fail condition and gracefully exit the test with errorcode

    ;#test_passed()  # <-- ';#test_passed()' should be used to indicate OS that discrete_test
    #               hit a pass condition and OS is free to schedule the next test

#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
test_cleanup:
    # Put your common initialization code here, e.g. initialize csr here if needed
    li t1, 0xc0010002
    ;#test_passed()



####################
# Labels for Exceptions to be passed to the macro
#####################

load_address_misaligned:
    .nop(10)

syscall:
    UPDATE_EPC
    ecall
    ;#test_failed()

misaligned_instruction:
    li x8, lin1 + misaligned_instruction_addr
    addi x9,x8,0x02 # Add 2bytes to the address to cause misalignment, 1byte shifts are not supported in RTL
    UPDATE_EPC
    jalr x9
    ;#test_failed()

illegal_instruction:
    UPDATE_EPC
    .dword invalid_opcode


load_access_fault_instruction:
    ;#test_passed() # Access fault dependendt misalignment support
    la x8, load_address_misaligned
    addi x9,x8,0x2 # Add 2bytes to the address to cause misalignment, 1byte shifts are not supported in RTL
    UPDATE_EPC
    lr.d x8, 0(x9)
    ;#test_failed()

store_amo_access_fault_instruction:
    ;#test_passed() # Access fault dependendt misalignment support
    la x8, load_address_misaligned
    addi x9,x8,0x2 # Add a byte to the address to cause misalignment, 1byte shifts are not supported in RTL
    UPDATE_EPC
    sc.d x7, x8, 0(x9) #.dword 0x1884B3AF
    # x7 is only updated if sc.d fails
    ;#test_failed()

instruction_access_fault_instruction:
    ;#test_passed() # Bringup after PMP/PMA setup

instruction_address_misaligned_instruction:
    la x8, load_address_misaligned
    addi x9,x8,0x2
    UPDATE_EPC
    jalr x9
    ;#test_failed()

load_address_misaligned_instruction:# TODO : Once we get clarity on misaligned hanndling from Joe/Radha
    la x8, load_address_misaligned
    addi x9,x8, 0x02
    UPDATE_EPC
    ld x8, 0(x9)
    ;#test_passed()# Test falls through if we enable misaligned support in whisper.json

load_page_fault_instruction:
    #;#test_passed() # Bug 611
    .ifeq PAGING_MODE_DISABLE # If paging not disabled
    li x8, lin2 + load_page_fault_addr
    UPDATE_EPC
    ld x9, 0(x8)
    ;#test_failed()
    .endif
    ;#test_passed()

store_page_fault_instruction:
    #;#test_passed() # Bug 611
    .ifeq PAGING_MODE_DISABLE
    li x8, lin2 + load_page_fault_addr
    addi x9,x0,0x0
    UPDATE_EPC
    sd x9, 0(x8)
    ;#test_failed()
    .endif
    ;#test_passed()

instruction_page_fault_instruction:
    #;#test_passed() # Bug 611
    .ifeq PAGING_MODE_DISABLE
    li x8, lin2 + load_page_fault_addr
    addi x9,x0,0x0
    sd x8,0(x12) #UPDATE_EPC
    jalr x8
    ;#test_failed()
    .endif
    ;#test_passed()

#####################
# Default data section
#####################
.section .data
    .dword 0xc001c0de
my_data:
    .dword 0xdeadbeee


#####################
# User defined data section located at address lin1
#####################
;#init_memory @lin1
# -> we convert above syntax to this -> .section .lin1
my_data1:
    .dword 0xc001c0de


        .nop #Initialize the space aroung the
.org misaligned_instruction_addr
        .nop

#illegal_instruction_addr:
        .nop
.org illegal_instruction_addr
        .dword 0x00000013

;#init_memory @lin2
.org my_data2
       .dword 0x0badc0de

       .nop
.org load_page_fault_addr
       .dword invalid_opcode
