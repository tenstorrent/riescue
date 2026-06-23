;#test.name       rand_mem_breakpoint
;#test.author     dkoshiya@tenstorrent.com
;#test.arch       rv64
;#test.priv       machine
;#test.env        bare_metal
;#test.cpus       1
;#test.paging     disable
;#test.features   ext_sdtrig.enable
;#test.category   arch
;#test.class      sdtrig
;#test.tags       sdtrig rand_mem_breakpoint multi_pool
;#test.summary
;#test.summary    Exercises --rand_mem_breakpoint_pct / --rand_mem_n_triggers /
;#test.summary    --rand_mem_max_fires against a pool supplied via *multiple*
;#test.summary    ;#rand_mem_breakpoint_pool directive instances. The default
;#test.summary    BREAKPOINT handler that Riescue-D registers when the feature
;#test.summary    rolls true should round-robin re-arm trigger tdata2 from the
;#test.summary    pool and let the load/store re-execute, so the test passes
;#test.summary    regardless of how many BPs fire (bounded by max_fires).
;#test.summary
;#test.summary    Run with feature on:
;#test.summary      riescued.py -t rand_mem_breakpoint.s \
;#test.summary        --rand_mem_breakpoint_pct 100 \
;#test.summary        --rand_mem_n_triggers 4 \
;#test.summary        --rand_mem_max_fires 20
;#test.summary    Run with feature off (default): same test passes with no BPs.
;#test.summary

# Three memory regions whose linear labels seed the pool.
;#random_addr(name=rmbp_a_lin, type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=rmbp_a_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=rmbp_a_lin, phys_name=rmbp_a_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=rmbp_b_lin, type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=rmbp_b_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=rmbp_b_lin, phys_name=rmbp_b_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=rmbp_c_lin, type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=rmbp_c_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=rmbp_c_lin, phys_name=rmbp_c_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

;#random_addr(name=rmbp_d_lin, type=linear,   size=0x1000, and_mask=0xfffffffffffff000)
;#random_addr(name=rmbp_d_phys, type=physical, size=0x1000, and_mask=0xfffffffffffff000)
;#page_mapping(lin_name=rmbp_d_lin, phys_name=rmbp_d_phys, v=1, r=1, w=1, x=0, a=1, d=1, pagesize=['4kb'])

;#init_memory @rmbp_a_lin
    .dword 0
;#init_memory @rmbp_b_lin
    .dword 0
;#init_memory @rmbp_c_lin
    .dword 0
;#init_memory @rmbp_d_lin
    .dword 0

# Two pool directives — the apply step must accumulate addresses from BOTH.
;#rand_mem_breakpoint_pool(addresses=[rmbp_a_lin, rmbp_b_lin])
;#rand_mem_breakpoint_pool(addresses=[rmbp_c_lin, rmbp_d_lin])

.section .code, "ax"

test_setup:
    ;#test_passed()

#####################
# rand_mem_bp_loads_and_stores
# A long mix of loads/stores across all four pooled memories. With the feature
# enabled, some of these accesses will trigger BPs and be transparently
# re-executed by the registered cause=3 handler. Without the feature, no BPs
# fire and the test passes the same way.
#####################
;#discrete_test(test=rand_mem_bp_loads_and_stores)
rand_mem_bp_loads_and_stores:
    # Base addrs in s0-s3, data values in s4-s6. The framework's trap dispatch
    # unconditionally clobbers t0/t1 (and our handler additionally uses t2/t3
    # via save/restore) before any default exception handler override runs, so
    # registers used across BP boundaries must be saved registers (s0-s11) or
    # registers >= t4 (which are not touched by the trap path or our handler).
    li      s0, rmbp_a_lin
    li      s1, rmbp_b_lin
    li      s2, rmbp_c_lin
    li      s3, rmbp_d_lin

    li      s4, 0x10
    li      s5, 0x20
    li      s6, 0x30

    # Mix of loads and stores — distinct sizes — to each pooled region.
    sd      s4, 0(s0)
    ld      a0, 0(s0)
    sw      s5, 8(s0)
    lw      a1, 8(s0)

    sd      s6, 0(s1)
    ld      a2, 0(s1)
    sh      s4, 16(s1)
    lh      a3, 16(s1)

    sd      s4, 0(s2)
    ld      a4, 0(s2)
    sb      s5, 24(s2)
    lb      a5, 24(s2)

    sd      s6, 0(s3)
    ld      a6, 0(s3)
    sb      s4, 32(s3)
    lb      a7, 32(s3)

    # Hit each region a second time so a re-armed trigger can fire again.
    ld      s7, 0(s0)
    ld      s8, 0(s1)
    ld      s9, 0(s2)
    ld      s10, 0(s3)

    sd      s4, 64(s0)
    sd      s5, 64(s1)
    sd      s6, 64(s2)
    sd      s4, 64(s3)

    ;#test_passed()

test_cleanup:
    ;#test_passed()

.section .data
    .dword 0xc001c0de
