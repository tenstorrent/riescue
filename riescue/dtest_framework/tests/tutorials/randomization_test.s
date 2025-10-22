
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.paging     disable

.section .code, "ax"

test_setup:
    ;#test_passed()

.equ data_region2_val, 0xACEDBEEF

;#random_data(name=my_8_bit_number, type=bits8, and_mask=0xFF)
;#random_addr(name=aligned_addr, type=physical, size=0x1000, and_mask=0xFFFFFF00)
;#random_data(name=my_16_bit_number, type=bits16, and_mask=0xFFFF)

;#discrete_test(test=test_random_data)
test_random_data:
    li t0, my_8_bit_number
    li t1, 0xFF
    bleu t0, t1, test_16_bit_random_data # Assert the 8-bit value <= 255
    ;#test_failed()
test_16_bit_random_data:
    li t2, my_16_bit_number
    li t3, 0xFFFF
    bleu t2, t3, random_data_passed
    ;#test_failed()
random_data_passed:
    ;#test_passed()

;#discrete_test(test=test_aligned_addr)
test_aligned_addr:
    li t0, aligned_addr
    andi t1, t0, 0xFF
    bnez t1, failed     # Should be zero (256-byte aligned)
    ;#test_passed()

;#discrete_test(test=load_from_data_regions)
load_from_data_regions:
    li t1, data_region1
    lw t2, 0(t1) # Load word from data_region1
    li t1, data_region2
    lw t3, 0(t1) # Load word from data_region2
    li t4, 0xFFFFFFFF
    and t3, t3, t4
    li t2, data_region2_val
    beq t2, t3, test_random_data_pass # Assert the word from data_region2 is equal to data_region2_val
    ;#test_failed()
test_random_data_pass:
    ;#test_passed()

test_cleanup:
    ;#test_passed()


.section .data

;#random_addr(name=data_region1, type=physical)
;#random_addr(name=data_region2, type=physical)

;#init_memory @data_region1
    .byte my_8_bit_number

;#init_memory @data_region2
    .word data_region2_val
