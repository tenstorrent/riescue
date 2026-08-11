# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

;#test.name       pma_carveout
;#test.author     test_author
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.category   arch
;#test.class      pma
;#test.features   not_hooked_up_yet
;#test.tags       pma pma_carveout memory_tags
;#test.summary
;#test.summary    Worked example of memory-map region tags and the pma_randomization flag.
;#test.summary
;#test.summary    Run with:
;#test.summary      riescued --testfile pma_carveout.s \
;#test.summary               --cpuconfig cpu_config_pma_carveout.json \
;#test.summary               --enable_pma_randomization --run_iss
;#test.summary
;#test.summary    Two independent mechanisms, both on a cpuconfig memory-map region:
;#test.summary
;#test.summary      "tags": [...]              free-form labels. A ;#random_addr can select the
;#test.summary                                 region with custom_region=<tag>, exactly as it can
;#test.summary                                 with custom_region=<region name>. RiescueD attaches
;#test.summary                                 no meaning to a tag beyond that.
;#test.summary
;#test.summary      "pma_randomization": false the region becomes a fixed window nothing random
;#test.summary                                 touches: a named PMA entry at high priority, skipped
;#test.summary                                 by decoy placement and random pmamask windows, exempt
;#test.summary                                 from carve-out mask stress, and kept out of the
;#test.summary                                 general DRAM pool so nothing lands there by accident.
;#test.summary
;#test.summary    A tag never implies pma_randomization: false - a region is a fixed window only when
;#test.summary    the config says so. What a label like derr/nderr/stee *means* (poisoned data,
;#test.summary    scrub-on-read, TEE) is between the DUT and the testbench.
;#test.summary
;#test.summary    test01: reach a fixed window through its generated equates
;#test.summary    test02: place a random_addr inside a window by region name
;#test.summary    test03: place a random_addr inside a window by tag
;#test.summary    test04: program a reserved pmacfg entry the test owns

#####################
# Reaching a fixed window
#####################
# Every fixed window is published as pma_<region name>_base / _size / _end equates, so
# cpu_config_pma_carveout.json yields:
#
#   pma_poison_window_base = 0x1_0000_0000   (tags: derr)
#   pma_scrub_window_base  = 0x1_1000_0000   (tags: nderr)
#   pma_tee_window_base    = 0x1_2000_0000   (tags: stee)
#   pma_spare_window_base  = 0x1_3000_0000   (tags: derr, spare)
#
# All four carry "pma_randomization": false in that config - that key alone is what makes a window.
#
# Nothing else in the test can be placed there, so these addresses are stable across seeds.

# Address generation skips fixed windows by default. To land inside one on purpose, name the
# region in custom_region=.
;#random_addr(name=phys_in_poison, type=physical, custom_region=poison_window, size=0x1000, and_mask=0xfffffffffffff000)

# Or select it by one of its tags. "spare" is carried only by spare_window, so this resolves to
# exactly one region. A tag carried by several regions resolves to one of them per address.
;#random_addr(name=phys_in_spare, type=physical, custom_region=spare, size=0x1000, and_mask=0xfffffffffffff000)

# An ordinary DRAM address, for contrast: this one can never land in a fixed window.
;#random_addr(name=phys_ordinary, type=physical, size=0x1000, and_mask=0xfffffffffffff000)

#####################
# Reserving PMA entries for the test
#####################
# The test programs pmacfg entries itself, so it needs some left free. This header states the
# requirement; RiescueD reserves [0..N) and never programs or invalidates them. It is a floor,
# not an override - if the cpuconfig or --user_programmable_pmacfg asks for more, the larger
# value wins, because a test can use extra free entries but cannot cope with fewer.
;#test.user_programmable_pmacfg 2

.section .code, "ax"

test_setup:
    ;#test_passed()

#####################
# test01: reach each fixed window through its equates
#####################
;#discrete_test(test=test01)
test01:
    # The equates identify the windows; a store/load proves they are backed and accessible.
    li t0, pma_poison_window_base
    li t1, 0xDEADBEEF
    sd t1, 0(t0)
    ld t2, 0(t0)
    bne t1, t2, test01_fail

    li t0, pma_scrub_window_base
    li t1, 0xCAFEBABE
    sd t1, 0(t0)
    ld t2, 0(t0)
    bne t1, t2, test01_fail

    li t0, pma_tee_window_base
    li t1, 0x5EE5EE5EE
    sd t1, 0(t0)
    ld t2, 0(t0)
    bne t1, t2, test01_fail

    # spare_window set pma_randomization explicitly rather than relying on a tag default
    li t0, pma_spare_window_base
    li t1, 0x0F1E2D3C4B5A6978
    sd t1, 0(t0)
    ld t2, 0(t0)
    bne t1, t2, test01_fail

    # The _size/_end equates exist too, so a test can sweep a window rather than poke its base.
    li t0, pma_poison_window_base
    li t1, pma_poison_window_end
    bgeu t0, t1, test01_fail

    j test01_pass

test01_pass:
    ;#test_passed()

test01_fail:
    ;#test_failed()

#####################
# test02: an address deliberately placed inside a window, selected by region name
#####################
;#discrete_test(test=test02)
test02:
    # phys_in_poison was allocated inside poison_window via custom_region=poison_window, so it
    # must fall in [pma_poison_window_base, pma_poison_window_end). Without that request it
    # could never land here.
    li t0, phys_in_poison
    li t1, pma_poison_window_base
    bltu t0, t1, test02_fail
    li t1, pma_poison_window_end
    bgeu t0, t1, test02_fail

    # And the ordinary address must NOT be in the window
    li t0, phys_ordinary
    li t1, pma_poison_window_base
    bltu t0, t1, test02_ordinary_ok
    li t1, pma_poison_window_end
    bltu t0, t1, test02_fail

test02_ordinary_ok:
    # The window address is writable like any other DRAM
    li t0, phys_in_poison
    li t1, 0x1234567890ABCDEF
    sd t1, 0(t0)
    ld t2, 0(t0)
    bne t1, t2, test02_fail
    j test02_pass

test02_pass:
    ;#test_passed()

test02_fail:
    ;#test_failed()

#####################
# test03: an address placed inside a window selected by tag
#####################
;#discrete_test(test=test03)
test03:
    # phys_in_spare named custom_region=spare, and spare_window is the only region carrying that
    # tag, so the address resolves into it just as a by-name request would.
    li t0, phys_in_spare
    li t1, pma_spare_window_base
    bltu t0, t1, test03_fail
    li t1, pma_spare_window_end
    bgeu t0, t1, test03_fail

    li t0, phys_in_spare
    li t1, 0x00C0FFEE00C0FFEE
    sd t1, 0(t0)
    ld t2, 0(t0)
    bne t1, t2, test03_fail

    j test03_pass

test03_pass:
    ;#test_passed()

test03_fail:
    ;#test_failed()

#####################
# test04: program a reserved pmacfg entry
#####################
;#discrete_test(test=test04)
test04:
    # ;#test.user_programmable_pmacfg 2 reserves entries 0 and 1. Under --enable_pma_randomization
    # the loader zeroes them once at boot, so a stale boot catchall cannot outrank every real
    # entry, and then never touches them again - the test owns them from that point on. With
    # randomization off the loader does not emit them at all.
    #   pmacfg0 = 0x7E0, pmamask0 = 0x7F0
    li t0, 0x1E7                    # memory, cacheable-coherent, rwx, arithmetic AMO
    csrw 0x7e0, t0
    csrr t1, 0x7e0
    bne t0, t1, test04_fail

    # pmamask0 is cleared by the pmacfg write; program it after, never before
    csrw 0x7f0, x0
    csrr t1, 0x7f0
    bnez t1, test04_fail

    # Entry 1 is reserved too and still ours to use
    li t0, 0x1E7
    csrw 0x7e1, t0
    csrr t1, 0x7e1
    bne t0, t1, test04_fail

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
