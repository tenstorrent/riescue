# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

;#test.name       pma_amo_ncio
;#test.author     test_author
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      pma
;#test.features   not_hooked_up_yet
;#test.tags       pma amo atomicity allow_amos_in_pma_ncio
;#test.summary
;#test.summary    Atomics against noncacheable and IO PMA space, both sides of the
;#test.summary    mmap.pma.allow_amos_in_pma_ncio knob.
;#test.summary
;#test.summary    Run with:
;#test.summary      riescued --testfile pma_amo_ncio.s --run_iss --needs_pma \
;#test.summary               --cpuconfig cpu_config_pma_amo_ncio.json      # knob on
;#test.summary      riescued --testfile pma_amo_ncio.s --run_iss --needs_pma \
;#test.summary               --cpuconfig cpu_config_pma_amo_ncio_off.json  # knob off (the default)
;#test.summary
;#test.summary    All three regions below ask for pma_amo_type=arithmetic. What gets programmed
;#test.summary    depends on the knob, and RiescueD publishes which way it went as the
;#test.summary    PMA_ALLOW_AMOS_IN_NCIO equate:
;#test.summary
;#test.summary      knob on   pmacfg[6:5]=0b11 everywhere -> every atomic below succeeds
;#test.summary      knob off  pmacfg[6:5]=0b00 on NC/IO   -> AMO takes cause 7, LR takes cause 5
;#test.summary
;#test.summary    Cacheable main memory is pinned to 0b11 either way, so test01 is the control
;#test.summary    that proves the harness itself is not what changed.
;#test.summary
;#test.summary    test01: AMO on cacheable memory succeeds (both configs)
;#test.summary    test02: AMO on noncacheable memory - succeeds or faults, per the knob
;#test.summary    test03: AMO on IO space           - succeeds or faults, per the knob
;#test.summary    test04: LR on noncacheable memory - the clamp zeroes bit 6 (Rsrv) too

# Paging is disabled, so these physical addresses are what the code loads directly and the PMA
# is the only thing standing between the atomic and the memory behind it.
;#random_addr(name=phys_cache, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=memory, pma_cacheability=cacheable, pma_read=1, pma_write=1, pma_execute=1, pma_amo_type=arithmetic)
;#random_addr(name=phys_nc, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=memory, pma_cacheability=noncacheable, pma_read=1, pma_write=1, pma_execute=1, pma_amo_type=arithmetic)
;#random_addr(name=phys_io, type=physical, size=0x1000, and_mask=0xfffffffffffff000, in_pma=1, pma_size=0x1000, pma_memory_type=io, pma_combining=noncombining, pma_read=1, pma_write=1, pma_execute=0, pma_amo_type=arithmetic)

.section .code, "ax"

test_setup:
    ;#test_passed()

#####################
# test01: control - cacheable main memory is pinned to arithmetic whatever the knob says
#####################
;#discrete_test(test=test01)
test01:
    li t0, phys_cache
    li t1, 0x1111111100000000
    sd t1, 0(t0)
    li t2, 0x0000000022222222
    amoadd.d t3, t2, (t0)

    # amoadd returns the value that was there before the add
    bne t3, t1, test01_fail
    ld t4, 0(t0)
    li t5, 0x1111111122222222
    bne t4, t5, test01_fail
    j test01_pass

test01_pass:
    ;#test_passed()

test01_fail:
    ;#test_failed()

#####################
# test02: AMO on noncacheable main memory
#####################
;#discrete_test(test=test02)
test02:
.ifne PMA_ALLOW_AMOS_IN_NCIO
    # pmacfg[6:5]=0b11 survived onto the NC region, so the atomic just works
    li t0, phys_nc
    li t1, 0x00000000deadbeef
    sd t1, 0(t0)
    li t2, 0x0000000000000011
    amoadd.d t3, t2, (t0)
    bne t3, t1, test02_fail
    ld t4, 0(t0)
    li t5, 0x00000000deadbf00
    bne t4, t5, test02_fail
.else
    # Clamped to AMONone: whisper has no Amo attribute to match, so amoLoad raises cause 7
    OS_SETUP_CHECK_EXCP STORE_ACCESS_FAULT, test02_bad, test02_ret, phys_nc
    li t0, phys_nc
    li t2, 0x0000000000000011
test02_bad:
    amoadd.d t3, t2, (t0)
    ;#test_failed()   # the AMO must not retire; falling through here is the failure
test02_ret:
.endif
    j test02_pass

test02_pass:
    ;#test_passed()

test02_fail:
    ;#test_failed()

#####################
# test03: AMO on IO space
#####################
;#discrete_test(test=test03)
test03:
.ifne PMA_ALLOW_AMOS_IN_NCIO
    li t0, phys_io
    li t1, 0x0000000012340000
    sd t1, 0(t0)
    li t2, 0x0000000000005678
    amoadd.d t3, t2, (t0)
    bne t3, t1, test03_fail
    ld t4, 0(t0)
    li t5, 0x0000000012345678
    bne t4, t5, test03_fail
.else
    OS_SETUP_CHECK_EXCP STORE_ACCESS_FAULT, test03_bad, test03_ret, phys_io
    li t0, phys_io
    li t2, 0x0000000000005678
test03_bad:
    amoadd.d t3, t2, (t0)
    ;#test_failed()
test03_ret:
.endif
    j test03_pass

test03_pass:
    ;#test_passed()

test03_fail:
    ;#test_failed()

#####################
# test04: LR on noncacheable memory - AMONone is the whole [6:5] field, Rsrv included
#####################
;#discrete_test(test=test04)
test04:
.ifne PMA_ALLOW_AMOS_IN_NCIO
    # 0b11 grants Rsrv as well as Amo, so the reservation is honoured
    li t0, phys_nc
    li t1, 0x000000005a5a5a5a
    sd t1, 0(t0)
    lr.d t2, (t0)
    bne t2, t1, test04_fail
.else
    # No Rsrv attribute either: the load half of the reservation access-faults with cause 5
    OS_SETUP_CHECK_EXCP LOAD_ACCESS_FAULT, test04_bad, test04_ret, phys_nc
    li t0, phys_nc
test04_bad:
    lr.d t2, (t0)
    ;#test_failed()
test04_ret:
.endif
    j test04_pass

test04_pass:
    ;#test_passed()

test04_fail:
    ;#test_failed()

test_cleanup:
    ;#test_passed()

.section .data
test_data:
    .word 0x0
