# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

;#test.name       test_custom_mem
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.category   arch
;#test.class      memory
;#test.tags       custom_mem custom_region
;#test.summary
;#test.summary    Exercises custom memory region tagging via the custom_region= parameter
;#test.summary    on ;#random_addr(). The physical address is constrained to the named
;#test.summary    region defined in mmap.custom (probe_buf, probe_buf_io) in test_custom_mem_cpuconfig.json
;#test.summary    or the default dtest_framework/lib/config.json; pass --cpuconfig for the dedicated file.
;#test.summary    PMA setup for this region is the user's responsibility.
;#test.summary
;#test.summary    test01: store then reload from a custom-region physical address mapped
;#test.summary            with a 4KB page and verify the data round-trips correctly.
;#test.summary
;#test.summary    test02: two independent allocations within the same custom region,
;#test.summary            verifying they do not overlap.
;#test.summary
;#test.summary    test03: same as test01 but uses a 2MB page (largest that fits in the 16MB
;#test.summary            probe_buf region). Exercises the 2MB page path in addrgen.
;#test.summary
;#test.summary    test04: same as test02 with two 2MB pages in probe_buf.
;#test.summary
;#test.summary    test05: separate custom region probe_buf_io (64KB near MMIO gap before
;#test.summary            pcie_tc); 4KB page only. PMA is the user's responsibility.
;#test.summary
;#test.summary    test06: probe_buf_rw (permissions="rw") — load and store succeed,
;#test.summary            confirming the PMP entry was wired with correct R/W bits.
;#test.summary
;#test.summary    test07: probe_buf_ro (permissions="r") — load succeeds in all privilege
;#test.summary            modes. Store-fault enforcement requires functional PMP which the
;#test.summary            ISS (Whisper) does not implement; fault path is HW-only.
;#test.summary


#####################
# Addresses for test01 — single page in probe_buf
#####################
;#random_addr(name=probe_lin,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=probe_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000, custom_region=probe_buf)
;#page_mapping(lin_name=probe_lin, phys_name=probe_phys, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

#####################
# Addresses for test02 — two pages in probe_buf (must not overlap)
#####################
;#random_addr(name=probe_lin2,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=probe_phys2, type=physical, size=0x1000, and_mask=0xfffffffffffff000, custom_region=probe_buf)
;#page_mapping(lin_name=probe_lin2, phys_name=probe_phys2, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

#####################
# Addresses for test03 — probe_buf, 2MB page (largest that fits in the 16MB region)
#####################
;#random_addr(name=probe_lin_rps,  type=linear,   size=0x200000, and_mask=0xffffffffffe00000)
;#random_addr(name=probe_phys_rps, type=physical, size=0x200000, and_mask=0xffffffffffe00000, custom_region=probe_buf)
;#page_mapping(lin_name=probe_lin_rps, phys_name=probe_phys_rps, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

#####################
# Addresses for test04 — two 2MB pages in probe_buf
#####################
;#random_addr(name=probe_lin_rps2,  type=linear,   size=0x200000, and_mask=0xffffffffffe00000)
;#random_addr(name=probe_phys_rps2, type=physical, size=0x200000, and_mask=0xffffffffffe00000, custom_region=probe_buf)
;#page_mapping(lin_name=probe_lin_rps2, phys_name=probe_phys_rps2, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

;#random_addr(name=probe_lin_rps3,  type=linear,   size=0x200000, and_mask=0xffffffffffe00000)
;#random_addr(name=probe_phys_rps3, type=physical, size=0x200000, and_mask=0xffffffffffe00000, custom_region=probe_buf)
;#page_mapping(lin_name=probe_lin_rps3, phys_name=probe_phys_rps3, v=1, r=1, w=1, a=1, d=1, pagesize=['2mb'])

#####################
# Addresses for test05 — second custom region probe_buf_io (64KB; 4KB page only)
#####################
;#random_addr(name=probe_io_lin,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=probe_io_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000, custom_region=probe_buf_io)
;#page_mapping(lin_name=probe_io_lin, phys_name=probe_io_phys, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

#####################
# Addresses for test06 — probe_buf_rw (PMP rw, no execute)
# PTE has w=1 so any fault comes from PMP, not page table.
# In machine mode PMP is not enforced (no L bit), so load+store succeed.
#####################
;#random_addr(name=probe_rw_lin,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=probe_rw_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000, custom_region=probe_buf_rw)
;#page_mapping(lin_name=probe_rw_lin, phys_name=probe_rw_phys, v=1, r=1, w=1, a=1, d=1, pagesize=['4kb'])

#####################
# Addresses for test07 — probe_buf_ro (PMP r only)
# ISS load-only path. On HW a store to this region faults via PMP in S/U mode.
#####################
;#random_addr(name=probe_ro_lin,  type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=probe_ro_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000, custom_region=probe_buf_ro)
;#page_mapping(lin_name=probe_ro_lin, phys_name=probe_ro_phys, v=1, r=1, w=0, a=1, d=1, pagesize=['4kb'])


.section .code, "ax"

#####################
# test_setup
#####################
test_setup:
    ;#test_passed()


#####################
# test01: store → load round-trip through a custom-region page
#####################
;#discrete_test(test=test01)
test01:
    # Load base address of the mapped custom-region page
    li   t0, probe_lin

    # Write a known pattern
    li   t1, 0xdeadbeef
    sw   t1, 0(t0)

    # Read it back
    lwu  t2, 0(t0)

    # Verify
    bne  t1, t2, failed01

    ;#test_passed()

failed01:
    ;#test_failed()


#####################
# test02: two non-overlapping pages within the same custom region
#####################
;#discrete_test(test=test02)
test02:
    li   t0, probe_lin
    li   t3, probe_lin2

    # Write distinct sentinels to each page
    li   t1, 0x11111111
    sw   t1, 0(t0)

    li   t2, 0x22222222
    sw   t2, 0(t3)

    # Reload and verify each page holds its own value (no aliasing)
    lwu  t4, 0(t0)
    bne  t1, t4, failed02

    lwu  t5, 0(t3)
    bne  t2, t5, failed02

    ;#test_passed()

failed02:
    ;#test_failed()


#####################
# test03: round-trip with page_mapping without explicit pagesize
#####################
;#discrete_test(test=test03)
test03:
    li   t0, probe_lin_rps

    li   t1, 0xcafebabe
    sw   t1, 0(t0)

    lwu  t2, 0(t0)
    bne  t1, t2, failed03

    ;#test_passed()

failed03:
    ;#test_failed()


#####################
# test04: two pages in probe_buf without explicit pagesize
#####################
;#discrete_test(test=test04)
test04:
    li   t0, probe_lin_rps2
    li   t3, probe_lin_rps3

    li   t1, 0x33333333
    sw   t1, 0(t0)

    li   t2, 0x44444444
    sw   t2, 0(t3)

    lwu  t4, 0(t0)
    bne  t1, t4, failed04

    lwu  t5, 0(t3)
    bne  t2, t5, failed04

    ;#test_passed()

failed04:
    ;#test_failed()


#####################
# test05: IO-adjacent custom region probe_buf_io
#####################
;#discrete_test(test=test05)
test05:
    li   t0, probe_io_lin

    li   t1, 0x55aa66bb
    sw   t1, 0(t0)

    lwu  t2, 0(t0)
    bne  t1, t2, failed05

    ;#test_passed()

failed05:
    ;#test_failed()


#####################
# test06: PMP rw region — load and store both succeed
# probe_buf_rw has permissions="rw" in config, so the framework programs a PMP
# entry with R=1, W=1, X=0. In machine mode PMP is not locked so M-mode accesses
# are unrestricted; the test simply confirms the region is accessible for R/W.
#####################
;#discrete_test(test=test06)
test06:
    li   t0, probe_rw_lin

    li   t1, 0x77665544
    sw   t1, 0(t0)

    lwu  t2, 0(t0)
    bne  t1, t2, failed06

    ;#test_passed()

failed06:
    ;#test_failed()


#####################
# test07: PMP read-only region — load succeeds (ISS-verifiable subset)
# probe_buf_ro has permissions="r" in config. The ISS (Whisper) does not
# implement PMP CSRs, so fault-path testing is skipped here. On real
# hardware, a store to this region raises STORE_ACCESS_FAULT in S/U mode.
#####################
;#discrete_test(test=test07)
test07:
    li   t0, probe_ro_lin

    # Load from the read-only region must succeed in all privilege modes.
    lwu  t1, 0(t0)

    ;#test_passed()


#####################
# test_cleanup
#####################
test_cleanup:
    ;#test_passed()
