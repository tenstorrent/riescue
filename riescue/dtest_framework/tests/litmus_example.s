

;#test.name       sample_LITMUS_test
;#test.author     mbrothers@tenstorrent.com
;#test.arch       rv64
;#test.priv       user
;#test.env        virtualized bare_metal
;#test.mp_mode    simultaneous
;#test.cpus       2
;#test.paging     sv57
;#test.category   arch
;#test.class      vector
;#test.features
;#test.tags
;#test.summary

#####################
# Define random address and page_mapping entries here
#####################
;#random_addr(name=linx,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=physx, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=linx, phys_name=physx, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=liny,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=physy, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=liny, phys_name=physy, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

;#random_addr(name=linz,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=physz, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=linz, phys_name=physz, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb', '2mb', '1gb', '512gb', '256tb', 'any'])

.section .code, "ax"

#####################
# test_setup: RiESCUE defined label
#             Add code below which is needed as common initialization sequence
#             for entire testcase (simulation)
#             This label is executed exactly once _before_ running any of the
#             discrete_test(s)
#####################
test_setup:
    ;#test_passed()


#####################
# test01: litmus test example
#####################
;#discrete_test(test=test01)
test01:
	# Send hartid 0 to litmus_P0 and hartid 1 to litmus_P1
	# s1 is cached with hartid in the 'loader' code
	bnez s1, litmus_P1

	litmus_P0:
		li a2, linx
		li a1, liny
		lw a3,0(a2)
		fence rw,rw
		ori a5,x0,1
		sw a5,0(a1)
		j test01_sync

	litmus_P1:
		li a5, liny
		li a4, linz
		li a3, linx
		lw t1,0(a5)
		ori a2,x0,1
		amoadd.w.rl a6,a2,(a4)
		ld t4,0(a4)
		lw t6,4(a4)
		xor a1,t6,t6
		ori a1,a1,1
		sw a1,0(a3)
		j test01_sync

	test01_sync:
	OS_SYNC_HARTS test01

	#
	# Check illegal values in the memory here
	#
	li a1, linx
	li a2, liny
	li a3, linz
	lw t1,0(a1)
	lw t2,0(a2)
	lw t3,0(a3)

	;#test_passed()


#####################
# test_cleanup: RiESCUE defined label
#             Add code below which is needed to perform any cleanup activity
#             This label is executed exactly once _after_ running all of the
#             discrete_test(s)
#####################
test_cleanup:
	;#test_passed()


.section .data

;#init_memory @linx
.dword 0x1111111111111111

;#init_memory @liny
.dword 0x2222222222222222

;#init_memory @linz
.dword 0x3333333333333333