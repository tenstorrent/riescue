;#test.name       riescued_floating_point_test
;#test.author     creddy@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      rv64imf
;#test.features   ext_fp.enable
;#test.tags       int rv64i fp16
;#test.summary
;#test.summary    This test will can include
;#test.summary    floating point tests targeted to cover Zfbfmin extension
;#test.summary

#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1)

#####################
#NOTE: Please read the #Convention and #Macros sections
#####################

.equ h_zero_p, 0x0000
.equ h_one_p, 0x3c00
.equ h_two_p, 0x4000
.equ h_three_p, 0x4200
.equ h_inf_p, 0x7c00
.equ h_sNaN_p, 0x7c01
.equ h_qNaN_p, 0x7e00
.equ h_smallestN_p, 0x0400
.equ h_smallestSN_p, 0x0001
.equ h_largestSN_p, 0x03ff
.equ h_largestN_p, 0x7bff

.equ h_zero_n, 0x8000
.equ h_one_n, 0xbc00
.equ h_two_n, 0xc000
.equ h_three_n, 0xc200
.equ h_inf_n, 0xfc00
.equ h_sNaN_n, 0xfc01
.equ h_qNaN_n, 0xfe00
.equ h_smallestN_n, 0x8400
.equ h_smallestSN_n, 0x8001
.equ h_largestSN_n, 0x83ff
.equ h_largestN_n, 0xfbff

.section .code, "aw"

#####################
#test setup
#####################
test_setup:
    csrr t2, mstatus
    li  x3, 0x2000
    or t2, t2, x3
    csrw mstatus, t2
    csrr t2, fcsr
    j passed

    #####################
# Convention:
# x1/f1 will hold the integer/floating point computed result
# t2/f2 will hold the integer/floating point expected result
# (x3, x4 ,x5)/(f3, f4, f5) will hold integer/floating point operands
# x6, f6 are temporary registers
#####################

#####################
# Macros
#####################

.macro reset_all fmt
# Resets all registers in use
    csrw fcsr, x0
    li x1, 0
    li t2, 0
    li x3, 0
    li x4, 0
    li x5, 0
    fcvt.\fmt\().w f1, x0
    fcvt.\fmt\().w f2, x0
    fcvt.\fmt\().w f3, x0
    fcvt.\fmt\().w f4, x0
    fcvt.\fmt\().w f5, x0
.endm


.macro load_compute_compare_intrd fmt instruction expected operand1 operand2=0 operand3=0
# Macro for all floating point instructions with an Integer destination register
    li t2, \expected
    li x3, \operand1
    li x4, \operand2
    li x5, \operand3
    fmv.\fmt\().x f3, x3
    fmv.\fmt\().x f4, x4
    fmv.\fmt\().x f5, x5
    \instruction
    # bne x1, t2, failed
.endm

.macro load_compute_compare_FPrd fmt instruction expected operand1 operand2=0 operand3=0
# Macro for all floating point instructions with a Floating destination register
    li t2, \expected
    li x3, \operand1
    li x4, \operand2
    li x5, \operand3
    fmv.\fmt\().x f3, x3
    fmv.\fmt\().x f4, x4
    fmv.\fmt\().x f5, x5
    \instruction
    # fmv.x.\fmt x1, f1
    fsh f1, (a0)
    lh x1, (a0)
    # bne x1, t2, failed
.endm

.macro load_selfcheck fmt_encoding expected data_label offset=0
# Macro to self-check a load instruction.
# Note on fmt_encoding: w for s; d for d; h for h.
    la a0, \data_label
    li t2, \expected
    fl\fmt_encoding f1, \offset\()(a0)
    fmv.x.\fmt_encoding x1, f1
    # bne x1, t2, failed
.endm

.macro store_selfcheck fmt_encoding store_data data_label offset=0
# Macro to self-check a store instruction.
# Note on fmt_encoding: w for s; d for d; h for h.
    la a0, \data_label
    li x6, \store_data
    sh x6, \offset\()(a0)
    lh t2, \offset\()(a0)
    li x3, \store_data
    fmv.\fmt_encoding\().x f3, x3
    fs\fmt_encoding f3, \offset\()(a0)
    fl\fmt_encoding f1, \offset\()(a0)
    fmv.x.\fmt_encoding x1, f1
    # bne x1, t2, failed
.endm

.macro fflags_load_compute_compare fmt instruction expected_fflags operand1 operand2=0 operand3=0
# Macro to self-check the fflags section of the fcsr.
    li t2, \expected_fflags
    li x3, \operand1
    li x4, \operand2
    li x5, \operand3
    fmv.\fmt\().x f3, x3
    fmv.\fmt\().x f4, x4
    fmv.\fmt\().x f5, x5
    fsflags x0
    \instruction
    frflags x1
    bne x1, t2, failed
.endm

.macro set_dyn_rm RM
    li x10, \RM
    slli x10, x10, 5
    csrw fcsr, x10
    csrr x10, fcsr
.endm

#####################
#test01: Basic conversion instructions
#####################
;#discrete_test(test=test01)
test01:
    fcvt.bf16.s f1, f2
    fcvt.s.bf16 f1, f2

#####################
#test cleanup
#####################
test_cleanup:
    li x1, 0xc0010002
    j passed

.section .data
# h_data:
    .dword 0x4000
