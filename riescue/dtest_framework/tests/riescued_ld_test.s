;#test.name       riescued_ld_test
;#test.author     rgovindaradjou@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      rv64i
;#test.features   ext_i.enable wysiwyg
;#test.tags       int ld lb lh lw
;#test.summary
;#test.summary    This section is used for documenting of description of
;#test.summary    overall intention of the test and what each individual
;#test.summary    discrete_test(s) are supposed to verify
;#test.summary
;#test.summary    test01: sample test 1
;#test.summary
;#test.summary    test02: sample test 2
;#test.summary
;#test.summary



#####################
# Define random data here
#####################
;#random_data(name=data1, type=bits32, and_mask=0xfffffff0)
;#random_data(name=data2, type=bits20, and_mask=0xffffffff)
;#random_data(name=data3, type=bits22)
;#random_data(name=mut_lin7_data_0, type=bits32)
;#random_data(name=mut_lin7_data_1, type=bits32)
;#random_data(name=mut_lin7_data_2, type=bits32)
;#random_data(name=mut_lin7_data_3, type=bits32)
;#random_data(name=mut_lin8_data_0, type=bits32)
;#random_data(name=mut_lin8_data_1, type=bits32)
;#random_data(name=mut_lin8_data_2, type=bits32)
;#random_data(name=mut_lin8_data_3, type=bits32)

#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin1, phys_name=phys1, v=1, r=1, w=1)

# Another random_data and page_mapping entry
;#random_addr(name=lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=lin2, phys_name=&random, v=1, r=1, w=1)

# Another random_data and page_mapping entry
;#reserve_memory(start_addr=0x5000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x6000, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x5000, addr_type=physical, size=0x1000)

;#page_mapping(lin_addr=0x5000, phys_addr=0x5000, v=1, r=1, w=1)
;#page_mapping(lin_addr=0x6000, phys_name=&random, v=1, r=1, w=1)

.equ lin1_offset, 0x400
.equ lin1_offset_1, 0x9c0
.equ lin2_offset, 0x198
.equ lin3_offset, 0xf0
.equ lin4_offset, 0x7e0
.equ lin5_offset, 0x700
.equ lin5_offset_1, 0x1f9
.equ lin5_offset_1_d, lin5_offset + lin5_offset_1
.equ lin6_offset, 0xf0
.equ lin7_offset, 0x300
.equ lin7_offset_1, 0x100
.equ lin7_offset_1_d, lin7_offset + lin7_offset_1
.equ lin7_offset_2, 0x200
.equ lin7_offset_2_d, lin7_offset_1_d + lin7_offset_2
.equ lin8_offset, 0x8f0
.equ lin8_offset_1, 0x1e0
.equ lin8_offset_1_d, lin8_offset + lin8_offset_1
.equ lin8_offset_2, 0x2e0
.equ lin8_offset_2_d, lin8_offset_1_d + lin8_offset_2
.equ lin9_offset_1, 0x80

// Capturing the MUT addresses that are working
//.equ lin1                               , 0x0000000001f3a000
//.equ mut_lin1                           , 0x000000007e4cf000
//.equ mut_lin2                           , 0x0000000015dfb000
//.equ mut_lin3                           , 0x0000000000376000
//.equ mut_lin4                           , 0x0000000000357000
//.equ mut_lin5                           , 0x000000000cb4b000
//.equ mut_lin6                           , 0x0000000000575000
//.equ mut_lin7                           , 0x0000000003331000
//.equ mut_lin8                           , 0x0000000004213000
//.equ mut_lin9                           , 0x000000000043e000
//.equ mut_lin10                          , 0x00000000099d9000

#;#random_addr(name=mut_lin1,  type=linear57,   size=0x1000, and_mask=0xfffffffffffff000)
#;#random_addr(name=mut_phys1, type=physical56, size=0x1000, and_mask=0xfffffffffffff000)
#;#page_mapping(lin_name=mut_lin1, phys_name=mut_phys1, v=1, r=1, w=1)

;#random_addr(name=mut_lin1,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys1, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin1, phys_name=mut_phys1, v=1, r=1, w=1)

;#random_addr(name=mut_lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin2, phys_name=mut_phys2, v=1, r=1, w=1)

;#random_addr(name=mut_lin3,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys3, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin3, phys_name=mut_phys3, v=1, r=1, w=1)

;#random_addr(name=mut_lin4,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys4, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin4, phys_name=mut_phys4, v=1, r=1, w=1)

;#random_addr(name=mut_lin5,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys5, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin5, phys_name=mut_phys5, v=1, r=1, w=1)

;#random_addr(name=mut_lin6,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys6, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin6, phys_name=mut_phys6, v=1, r=1, w=1)

;#random_addr(name=mut_lin7,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys7, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin7, phys_name=mut_phys7, v=1, r=1, w=1)

;#random_addr(name=mut_lin8,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys8, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin8, phys_name=mut_phys8, v=1, r=1, w=1)

;#random_addr(name=mut_lin9,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys9, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin9, phys_name=mut_phys9, v=1, r=1, w=1)

;#random_addr(name=mut_lin10,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=mut_phys10, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=mut_lin10, phys_name=mut_phys10, v=1, r=1, w=1)

.section .text

cl0:
    ld x1, 0(x0)
    li   x7, mut_lin1
    addi x5, x7, lin1_offset
    li   x20, mut_lin3
    addi x2, x20, 0x100
    lb   x1, 15(x2)
    lw   x4, 8(x5)
    lb   x4, 9(x5)
    ld   x4, 0(x5)
    lh   x4, 4(x5)
    li   x6, mut_lin2
    li   x21, mut_lin4
    addi x21, x21, 0x7e0
    lw   x4, 8(x6)
    addi x20, x20, 0xfe
    lh   x2, 10(x5)
    ld   x1, 0(x20) // Cache line crossing
    lb   x15, 15(x21) // Offset > cache line boundary

cl1:
    li   x13, mut_lin5
    addi x19, x13, lin5_offset
    lw   x4, 8(x19)
    lb   x4, 9(x19)
    ld   x4, 0(x19)
    lh   x4, 4(x19)
    addi x19, x19, lin5_offset_1
    addi x14, x19, 0xa
    lb   x2, (x14)
    li   x15, mut_lin1
    li   x20, mut_lin2
    li   x21, mut_lin6
    addi x21, x21, lin6_offset
    addi x15, x15, lin1_offset
    lw   x4, 8(x15)
    ld   x2, 10(x19)
    lh   x1, 7(x20)
    lb   x19, 5(x21)

cl2:
    li   x7, mut_lin4
    addi x5, x7, lin4_offset
    lw   x4, 17(x5)
    lb   x4, 9(x5)
    ld   x4, 0(x5)
    lh   x4, 4(x5)
    addi x7, x5, 0x270
    li   x6, mut_lin7
    lw   x4, 8(x6)
    addi  x5, x6,lin7_offset
    ld   x2, 10(x5)
    addi x5, x5, lin7_offset_1
    lh   x1, 7(x5)
    lb   x15, 15(x6)
    addi x5, x5, lin7_offset_2

cl3: // Each load targets different bytes of same cache line
    lw   x4, 10(x5)
    ld   x4, 14(x5)
    lw   x4, 17(x5)
    lb   x4, 9(x5)
    ld   x4, 0(x5)
    lh   x4, 4(x5)
    lb   x9, 12(x5)
    lh   x10, 1(x5)
    lw   x19, 18(x5)
    ld   x12, 11(x5)
    lb   x25, 13(x5)
    lb   x21, 10(x5)
    lw   x4, 8(x5)
    ld   x2, 10(x5)
    lh   x1, 7(x5)
    lb   x15, 15(x5)
    ld   x6, 11(x5)

cl4:  // WAW, RAW , WAR hazard
    li   x13, mut_lin7
    addi x19, x13, lin7_offset
    li   x12, mut_lin3
    addi x20, x12, lin3_offset
    lb   x4, 1(x19)
    lw   x4, 9(x20)
    ld   x4, 19(x19)  // WAW
    lh   x3, 2(x20)
    ld   x3, 16(x19)
    lh   x3, 22(x19)  // WAW
    addi x19, x13, lin7_offset_1_d
    lb   x4, 1(x19)
    lw   x4, 9(x19)
    ld   x4, 10(x19)
    lh   x4, 26(x19)
    addi x19, x19, lin7_offset_2
    ld   x1, 0(x19)
    lh   x19, 0(x19)  // RAW & WAR

cl5: // CL crossing loads
    li   x13, mut_lin7
    addi x21, x13, lin7_offset
    addi x21, x21, 0x1e
    lh   x1, 1(x21)
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    lw   x4, 0(x21)
    ld   x2, -31(x21)
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    lw   x3, -6(x21)
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    lw   x4, 0(x21)
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0

cl6:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    li   x5, mut_lin1
    lw   x4, 0(x5)
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    li   x5, mut_lin1
    lw   x4, 0(x5)

cl7:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    li   x5, mut_lin1
    lw   x4, 0(x5)
    //addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0

cl8:
    addi x1, x0, 0x400
    li   x5, mut_lin1
    lw   x4, 0(x5)
    //addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    li   x5, mut_lin1
    lw   x4, 0(x5)
    //addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0

cl0_1:
    li   x7, mut_lin1
    addi x5, x7, lin1_offset
    li   x20, mut_lin3
    addi x2, x20, 0x100
    lb   x1, 15(x2)
    lw   x4, 8(x5)
    lb   x4, 9(x5)
    ld   x4, 0(x5)
    lh   x4, 4(x5)
    li   x6, mut_lin2
    li   x21, mut_lin4
    addi x21, x21, 0x7e0
    lw   x4, 8(x6)
    addi x20, x20, 0xfe
    lh   x2, 10(x5)
    ld   x1, 0(x20) // Cache line crossing
    lb   x15, 15(x21) // Offset > cache line boundary

cl1_1:
    li   x13, mut_lin5
    addi x19, x13, lin5_offset
    lw   x4, 8(x19)
    lb   x4, 9(x19)
    ld   x4, 0(x19)
    lh   x4, 4(x19)
    addi x19, x19, lin5_offset_1
    addi x14, x19, 0xa
    lb   x2, (x14)
    li   x15, mut_lin1
    li   x20, mut_lin2
    li   x21, mut_lin6
    addi x21, x21, lin6_offset
    addi x15, x15, lin1_offset
    lw   x4, 8(x15)
    ld   x2, 10(x19)
    lh   x1, 7(x20)
    lb   x19, 5(x21)

cl2_1:
    li   x7, mut_lin4
    addi x5, x7, lin4_offset
    lw   x4, 17(x5)
    lb   x4, 9(x5)
    ld   x4, 0(x5)
    lh   x4, 4(x5)
    addi x7, x5, 0x270
    li   x6, mut_lin7
    lw   x4, 8(x6)
    addi  x5, x6,lin7_offset
    ld   x2, 10(x5)
    addi x5, x5, lin7_offset_1
    lh   x1, 7(x5)
    lb   x15, 15(x6)
    addi x5, x5, lin7_offset_2

cl3_1: // Each load targets different bytes of same cache line
    lw   x4, 10(x5)
    ld   x4, 14(x5)
    lw   x4, 17(x5)
    lb   x4, 9(x5)
    ld   x4, 0(x5)
    lh   x4, 4(x5)
    lb   x9, 12(x5)
    lh   x10, 1(x5)
    lw   x19, 18(x5)
    ld   x12, 11(x5)
    lb   x25, 13(x5)
    lb   x21, 10(x5)
    lw   x4, 8(x5)
    ld   x2, 10(x5)
    lh   x1, 7(x5)
    lb   x15, 15(x5)
    ld   x6, 11(x5)

cl4_1: // Load data becomes the address of the next load
    // Below sequence ensures that there is no load cancel
    li   x20, mut_lin9
    ld   x10, (x20)
    lw   x5, (x10)
    ld   x10, 64(x20)
    lw   x5, (x10)
    ld   x10, 128(x20)
    lw   x5, (x10)
    ld   x10, 192(x20)
    lw   x5, (x10)
/*  // Following accesses will lead to load cancel due to bank conflict and will be commented till the load cancels are supported
    ld   x10, 8(x20)
    lw   x5, (x10)
    ld   x10, 72(x20)
    lw   x5, (x10)
    ld   x10, 136(x20)
    lw   x5, (x10)
    ld   x10, 198(x20)
    lw   x5, (x10)
*/
x31:
    li x31, 0xc001c0de

end:
    la x1, tohost
    li x2, 1
    sw x2, 0(x1)
    j end

cl9:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl10:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl11:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl12:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl13:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl14:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl15:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl16:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl17:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl18:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl19:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl20:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl21:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl22:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl23:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0

cl24:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl25:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl26:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl27:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl28:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl29:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl30:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl31:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl32:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl33:
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl34:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl35:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
cl36:
    addi x0, x0, 0xd0
    addi x1, x0, 0x400
    add  x5, x1, x1
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0
    addi x0, x0, 0xd0

.align 6
.global tohost
tohost:
    .dword 0

;#init_memory @mut_lin1
     .word data1
     .word data2
     .word data3
     .word data1
.org lin1_offset
    .word 0x457b5672
    .word 0xa78c6811
    .word 0x9abce234
    .word 0xddeee456
    .word 0xa78c6811
    addi  x23, x2, 0x356
.org lin1_offset_1
mut_lin1_data:
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0xaabbcc11

;#init_memory @mut_lin2
    .word 0x9abce234
    .word 0xddeee456
    .word 0x9abce234
    .word 0xddeee456
.org lin2_offset
mut_lin2_data:
    .word 0xddeeff00
    .word 0x9abce234
    .word 0xddeee456
    .word 0xaab34677
    .word 0xdacb5672
    .word 0xaabbcc11


;#init_memory @mut_lin3
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0x9abce234
    .word 0xaab34677
.org lin3_offset
mut_lin3_data:
    .word 0xaab34677
    .word 0xdacb5672
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0x9abce234
    .word 0xddeee456

;#init_memory @mut_lin4
    .word 0xdacb5672
    .word 0xaabbcc11
    .word 0xdacb5672
    .word 0xaabbcc11
.org lin4_offset
mut_lin4_data:
    .word 0xdacb5672
    .word 0xaabbcc11
    .word 0xddeeff00
    .word 0x9abce234
    .word 0xddeee456
    .word 0xaab34677

;#init_memory @mut_lin5
.org lin5_offset
    .word 0xddeeff00
    .word 0x9abce234
    .word 0xddeee456
    .word 0xaab34677
mut_lin5_data:
    .word 0x345dd348
    .word 0xdacb5672
    .word 0xaabbc621
    .word 0xdd45ff00
    .word 0x9ab14768
    .word 0x892ee456
.org lin5_offset_1_d
    .word 0xddeeff00
    .word 0x9abce234
    .word 0x9324acbe
    .word 0xd57234a6


;#init_memory @mut_lin6
    .word 0x9a56b98e
    .word 0xd587e4a6
    .word 0xa09ab457
.org lin6_offset
mut_lin6_data:
    .word 0x457b5672
    .word 0xa78c6811
    .word 0xd579f90f
    .word 0x9a56b98e
    .word 0xd587e4a6
    .word 0xa09ab457

;#init_memory @mut_lin7
    .word 0xd579f90f
    .word 0x9a56b98e
    .word 0xd587e4a6
    .word 0xa09ab457
    .word 0x9a56b98e
    .word 0x9a56b98e
    .word 0xd587e4a6
    .word 0xd579f90f
    .word mut_lin7_data_0
    .word mut_lin7_data_1
    .word mut_lin7_data_2
    .word mut_lin7_data_3
    .word mut_lin7_data_3
    .word mut_lin7_data_2
    .word mut_lin7_data_1
    .word mut_lin7_data_0
.org lin7_offset
    .word mut_lin7_data_0
    .word 0xd587e4a6
    .word mut_lin7_data_1
    .word 0x9a56b98e
    .word mut_lin7_data_2
    .word 0xd579f90f
    .word mut_lin7_data_3
    .word 0xa09ab457
    .word mut_lin7_data_3
    .word 0x9a56b98e
    .word mut_lin7_data_2
    .word 0x9a56b98e
    .word mut_lin7_data_1
    .word 0xd587e4a6
    .word mut_lin7_data_0
    .word 0xd579f90f
.org lin7_offset_1_d
    .word mut_lin7_data_3
    .word mut_lin7_data_2
    .word mut_lin7_data_1
    .word mut_lin7_data_0
    .word mut_lin7_data_0
    .word mut_lin7_data_1
    .word mut_lin7_data_2
    .word mut_lin7_data_3
    .word 0xd579f90f
    .word 0x9a56b98e
    .word 0xd587e4a6
    .word 0xa09ab457
.org lin7_offset_2_d
    .word 0xd587e4a6
    .word 0xa09ab457
    .word mut_lin7_data_0
    .word mut_lin7_data_1
    .word 0xd579f90f
    .word 0x9a56b98e
    .word mut_lin7_data_2
    .word mut_lin7_data_3
    .word 0xd587e4a6
    .word 0xa09ab457
    .word mut_lin7_data_3
    .word mut_lin7_data_2
    .word 0xd579f90f
    .word 0x9a56b98e
    .word mut_lin7_data_1
    .word mut_lin7_data_0


;#init_memory @mut_lin9
    .dword mut_lin1
    .dword mut_lin2
    .dword mut_lin3
    .dword mut_lin3 + lin3_offset
    .dword mut_lin4
    .dword mut_lin4 + lin4_offset
    .dword mut_lin5
    .dword mut_lin5 + lin5_offset
    .dword mut_lin5 + lin5_offset_1
    .dword mut_lin6
    .dword mut_lin6 + lin6_offset
    .dword mut_lin7
    .dword mut_lin7 + lin7_offset
    .dword mut_lin7 + lin7_offset_1
    .dword mut_lin7 + lin7_offset_1 + lin7_offset_2
    .dword mut_lin8
    .dword mut_lin9
    .dword mut_lin10
    .dword mut_lin6 + lin6_offset
    .dword mut_lin7
    .dword mut_lin7 + lin7_offset
    .dword mut_lin7 + lin7_offset_1
    .dword mut_lin7 + lin7_offset_1 + lin7_offset_2
    .dword mut_lin9
    .dword mut_lin10
    .dword mut_lin6 + lin6_offset
    .dword mut_lin2
    .dword mut_lin3
    .dword mut_lin3 + lin3_offset
    .dword mut_lin4
    .dword mut_lin4 + lin4_offset
    .dword mut_lin4 + lin4_offset
    .dword mut_lin5
    .dword mut_lin5 + lin5_offset
    .dword mut_lin5 + lin5_offset_1
    .dword mut_lin1
    .dword mut_lin2
    .dword mut_lin3
    .dword mut_lin5 + lin5_offset_1
    .dword mut_lin6
    .dword mut_lin6 + lin6_offset
    .dword mut_lin7
    .dword mut_lin7 + lin7_offset
    .dword mut_lin1
    .dword mut_lin2
    .dword mut_lin3


;#reserve_memory(start_addr=0x0, addr_type=linear, size=0x1000)
;#reserve_memory(start_addr=0x0, addr_type=physical, size=0x1000)
;#page_mapping(lin_addr=0x0, phys_addr=0x0, v=1, r=1, w=1, x=1, a=1, d=1, pagesize=['4kb'])

;#init_memory @0x0
  .dword 0xc001c00de
