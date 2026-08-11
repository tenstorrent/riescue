# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Random memory breakpoint feature.

Driven by ``--rand_mem_breakpoint_pct`` / ``--rand_mem_n_triggers`` /
``--rand_mem_max_fires`` against an address pool supplied via one or more
``;#rand_mem_breakpoint_pool(addresses=[label1,label2,...])`` directives in
the test source. Multiple directives accumulate into one pool.

When ``apply()`` rolls true and the pool is non-empty:

  * Samples ``K = min(2*N_eff, len(pool))`` distinct addresses.
  * Registers an ``M_LOADER`` hook that emits, in M-mode startup:
      - The pool table + state struct (``_rmbp_state_``, ``_rmbp_pool_``) in
        ``.data`` via ``.pushsection``/``.popsection``.
      - Initial CSR writes to arm N watchpoints (``tselect``/``tdata1``/
        ``tdata2``) at indices ``BASE..BASE+N-1`` on ``pool[0..N-1]``.
  * Registers a default BREAKPOINT (cause=3) handler that:
      - Reads ``fires_remaining``; if 0, disables every armed trigger and
        ``xret``s.
      - Otherwise decrements the counter, advances ``pool_idx`` (wrap at K),
        computes ``slot = pool_idx % N`` (cheap because K=2N → slot is
        ``t1`` or ``t1-N``), fetches ``pool[pool_idx]``, writes ``tdata2`` of
        trigger ``BASE+slot``, and ``xret``s. ``xepc`` is left untouched, so
        the faulting load/store re-executes — but tdata2 of the firing
        trigger is now different, so it does not refire on this access.

Only ``t0``/``t1`` are clobberable per the framework's trap preamble; ``t2``
and ``t3`` are saved to scratch slots inside ``_rmbp_state_``.

medeleg requirement
-------------------
The handler writes ``tselect``/``tdata1``/``tdata2``, which are M-mode-only
CSRs.  Therefore BREAKPOINT (cause=3) MUST be handled in M-mode — i.e.
``medeleg`` bit 3 must be **clear**.  ``apply()`` enforces this as follows:

  * If the user did NOT supply ``--medeleg`` or ``--deleg_excp_to``
    (``featmgr.medeleg_forced == False``), and the default delegation has
    bit 3 set, ``apply()`` clears it for them and logs an INFO line.
  * If the user explicitly supplied either flag (``medeleg_forced == True``):
    - Bit 3 already clear → proceed.
    - Bit 3 still set → respect the user's choice and **disable the feature**
      for this run with a WARNING.  We refuse to silently override an
      explicit delegation override.

Single-hart only; caller is responsible for skipping in MP (``apply()``
already does so via ``featmgr.num_cpus``).

Conflict avoidance
------------------
If the test already contains any ``;#trigger_config(...)`` directive — from
coretp's ``--test_plan sdtrig`` stimulus, voyager2's ``sdtrig_stress``
plugin, or a hand-written ``.s`` test — ``apply()`` auto-disables the
feature for this run with a WARNING.  Both sources program the same
trigger CSR space (indices 4–7 are the only load/store-capable slots in
the standard whisper config) and would collide otherwise.

Icount injection (optional)
---------------------------
When ``--rand_mem_inject_icount_pct`` is set, an inner gate is rolled
*after* the master ``--rand_mem_breakpoint_pct`` rolls true. If it also
rolls true, an additional icount trigger is armed on slot 8 (the only
icount-capable slot in the standard whisper config) with a count drawn
randomly from the ``(min, max)`` tuple carried by
``featmgr.rand_mem_icount_density.value`` — see
``RandMemIcountDensity`` in ``riescue.lib.enums``.
The injected icount trigger shares the ``--rand_mem_max_fires`` re-arm
budget with the mcontrol6 watchpoints; on each re-arm the handler picks
the next pre-randomized count from a small inline table. Both fire types
are dispatched by the same cause=3 BREAKPOINT handler — the mcontrol6
search runs first (matched via ``mtval == tdata2``), and on no match the
handler falls through to an icount hit-bit check on slot 8.
"""

from typing import List, Optional

import riescue.lib.enums as RV
from riescue.dtest_framework.config.featmanager import FeatMgr
from riescue.dtest_framework.lib.sdtrig import (
    TriggerAction,
    TriggerMatch,
    TriggerType,
    build_tdata1_icount,
    build_tdata1_mcontrol6,
)
from riescue.dtest_framework.pool import Pool
from riescue.dtest_framework.trap_context import TrapContext
from riescue.lib.rand import RandNum

import logging

log = logging.getLogger(__name__)


# Triggers 4-7 are the load/store-capable slots in the standard whisper config
# (per riescue/dtest_framework/lib/whisper_config_privatecsr.json:316-356 — slots 0-3
# mask out the load/store bits and only support execute, slots 4-7 are the inverse).
# Constrains us to N <= 4 for load/store watchpoints.
_BASE_INDEX = 4
_N_CAP = 4
_TRIGGER_TYPES = [TriggerType.LOAD, TriggerType.STORE, TriggerType.LOAD_STORE]
_SIZES = [1, 2, 4, 8]

# Slot 8 is the only icount-capable slot in the standard whisper config (its
# tdata1 mask 0xf800000007ffffc7 differs from slots 0-7's 0xf800000001c077d{b,c}
# — the wider field accommodates icount's 14-bit count at bits [23:10]).
_ICOUNT_SLOT = 8

# icount tdata1 hit bit per Sdtrig spec (bit 24 in the icount type encoding).
_ICOUNT_HIT_BIT = 1 << 24

# How many pre-randomized icount tdata1 values to bake into the handler's data
# block. One is consumed at startup (slot 8 init) and one per re-arm; we provide
# max_fires + 1 with a floor of 2 and a cap of 64 to keep the handler's inline
# data small while still covering reasonable max_fires settings.
_ICOUNT_TABLE_FLOOR = 2
_ICOUNT_TABLE_CAP = 64


# --- icount park mode ---------------------------------------------------------------------------
#
# Every icount coverpoint in the architectural coverage model is gated on
# ``tdata1.type == 3 && tselect == 8``, so none of them sample unless an icount trigger is the
# selected trigger while instructions retire. Directed sdtrig tests establish that state only inside
# a scenario body (each discrete test saves/restores the trigger CSRs around itself), so parking has
# to happen in the M-mode loader to survive for the whole test.
#
# Actions are drawn from the trace actions only. A trace action fire starts/stops/emits trace and
# raises no exception, so the parked trigger is safe with no handler registered -- whereas
# action=breakpoint would raise a BREAKPOINT with nothing to service it, and action=debug_mode needs
# debug-module entry that the framework cannot drive.
_ICOUNT_PARK_ACTIONS = [TriggerAction.TRACE_ON, TriggerAction.TRACE_OFF, TriggerAction.TRACE_NOTIFY]

# Counts near the top of the 14-bit field. The countdown only advances in the enabled privilege
# modes, so a large count keeps fires rare while still exercising the count field's high bins.
_ICOUNT_PARK_COUNT_RANGE = (0x3000, 0x3FFF)

_ICOUNT_PARK_MODES = ("m", "s", "u", "vs", "vu")


def apply_icount_park(featmgr: FeatMgr, pool: Pool, rng: RandNum, icount_slot_claimed: bool = False) -> None:
    """Roll the park gate; if it fires, arm a non-firing icount trigger on slot 8 and leave it selected.

    Independent of ``--rand_mem_breakpoint_pct``: needs no address pool, registers no handler, and
    touches only the icount slot, so it composes with the mcontrol6 watchpoints rather than
    replacing them.

    :param icount_slot_claimed: True when ``apply()`` already armed an injected icount trigger on
        slot 8. That trigger has a live re-arm handler behind it, so it wins and the park stands
        down -- otherwise the park's later loader write would silently overwrite it.
    """
    pct = featmgr.rand_mem_icount_park_pct
    if pct <= 0:
        return
    if not featmgr.feature.is_feature_enabled("sdtrig"):
        log.warning("rand_mem_icount_park_pct is set but sdtrig feature is not enabled in cpuconfig; skipping")
        return
    if icount_slot_claimed:
        log.warning(
            f"rand_mem_icount_park_pct is set but --rand_mem_inject_icount_pct already armed slot {_ICOUNT_SLOT} "
            "with a firing icount trigger and its re-arm handler; skipping the park so the injected "
            "trigger is not overwritten."
        )
        return

    # Only slot 8 matters here, so unlike the watchpoint path this does not have to stand down for
    # every ;#trigger_config -- just for one that already owns slot 8.
    conflicting = [cfg for cfg in pool.get_parsed_trigger_configs() if cfg.index == _ICOUNT_SLOT]
    if conflicting:
        log.warning(f"rand_mem_icount_park_pct is set but the test already programs trigger slot {_ICOUNT_SLOT} " "via ;#trigger_config; skipping the park to avoid a conflict.")
        return

    if not rng.with_probability_of(pct):
        log.info(f"rand_mem_icount_park: pct roll missed ({pct}%); park disabled this run")
        return

    action = rng.choice(_ICOUNT_PARK_ACTIONS)
    count = rng.randint(*_ICOUNT_PARK_COUNT_RANGE)
    hit = rng.randint(0, 1)
    pending = rng.randint(0, 1)
    # Random non-empty subset of the five modes, so the per-mode enable bits and the multi-mode
    # crosses see more than the all-modes case that ("any",) would always produce.
    n_modes = rng.randint(1, len(_ICOUNT_PARK_MODES))
    priv_mode = tuple(rng.sample(list(_ICOUNT_PARK_MODES), n_modes))

    tdata1 = build_tdata1_icount(count=count, action=action, priv_mode=priv_mode, pending=pending, hit=hit)
    log.info(f"rand_mem_icount_park: parking slot {_ICOUNT_SLOT} tdata1=0x{tdata1:x} " f"(count={count}, action={action.name}, hit={hit}, pending={pending}, priv={priv_mode})")
    featmgr.register_hook(RV.HookPoint.M_LOADER, _build_icount_park_hook(tdata1))


def _build_icount_park_hook(tdata1: int):
    """Return a Hookable that arms slot 8 and leaves tselect pointing at it."""

    def hook(featmgr: FeatMgr) -> str:
        # tselect is written last so the icount trigger stays the selected trigger for the whole
        # test -- that selection is what the icount coverpoints are gated on.
        return (
            "    # rand_mem_icount_park: park a non-firing icount trigger and leave it selected\n"
            f"    csrwi tselect, {_ICOUNT_SLOT}\n"
            "    csrw  tdata2, x0\n"
            f"    li    t0, 0x{tdata1:x}\n"
            "    csrw  tdata1, t0\n"
            f"    csrwi tselect, {_ICOUNT_SLOT}\n"
        )

    return hook


def apply(featmgr: FeatMgr, pool: Pool, rng: RandNum) -> bool:
    """Roll the pct gate; if it fires, arm N triggers + register the BP handler.

    :returns: True if an injected icount trigger was armed on slot 8. The caller passes this to
        ``apply_icount_park`` so the park does not overwrite it -- both write slot 8 in the same
        M_LOADER hook chain, and the later write would win silently.
    """
    pct = featmgr.rand_mem_breakpoint_pct
    if pct <= 0:
        return False
    if featmgr.num_cpus > 1:
        log.warning("rand_mem_breakpoint_pct is single-hart only; skipping for MP run")
        return False
    if not featmgr.feature.is_feature_enabled("sdtrig"):
        log.warning("rand_mem_breakpoint_pct is set but sdtrig feature is not enabled in cpuconfig; skipping")
        return False

    # Auto-disable if any other source already programs sdtrig triggers via
    # ``;#trigger_config(...)``. This catches coretp's --test_plan sdtrig
    # stimulus, the sdtrig_stress voyager2 plugin, and any hand-written test
    # that configures triggers directly. They all share the same trigger CSR
    # space (indices 4-7 are the load/store-capable slots), so arming our
    # watchpoints alongside them produces undefined / last-writer-wins
    # behavior. Refuse to silently conflict.
    existing_trigger_configs = pool.get_parsed_trigger_configs()
    if existing_trigger_configs:
        log.warning(
            f"rand_mem_breakpoint_pct is set but the test already has "
            f"{len(existing_trigger_configs)} ;#trigger_config directive(s) "
            "(e.g. from --test_plan sdtrig, the sdtrig_stress plugin, or a "
            "hand-written test). Disabling rand_mem_breakpoint to avoid "
            "trigger CSR conflicts."
        )
        return False

    addresses = list(dict.fromkeys(pool.get_parsed_rand_mem_bp_pool()))  # dedupe, preserve order
    if not addresses:
        log.warning("rand_mem_breakpoint_pct is set but no ;#rand_mem_breakpoint_pool directive supplied addresses; skipping")
        return False

    if not rng.with_probability_of(pct):
        log.info(f"rand_mem_breakpoint: pct roll missed ({pct}%); feature disabled this run")
        return False

    n_req = max(1, featmgr.rand_mem_n_triggers)
    n_eff = min(n_req, _N_CAP, len(addresses))
    if n_eff < n_req:
        log.warning(f"rand_mem_breakpoint: clamping n_triggers from {n_req} to {n_eff} (cap={_N_CAP}, available={len(addresses)})")
    max_fires = max(0, featmgr.rand_mem_max_fires)
    k = min(2 * n_eff, len(addresses))

    pool_addrs = rng.sample(addresses, k)
    # Per-trigger random type/size; baked into tdata1 at apply time.
    trigger_types = [rng.choice(_TRIGGER_TYPES) for _ in range(n_eff)]
    trigger_sizes = [rng.choice(_SIZES) if t != TriggerType.LOAD_STORE else 4 for t in trigger_types]
    tdata1_vals = [
        build_tdata1_mcontrol6(
            trigger_type=trigger_types[i],
            action=TriggerAction.BREAKPOINT,
            size=trigger_sizes[i],
            chain=0,
            match=TriggerMatch.EQUAL,
            priv_mode=("any",),
        )
        for i in range(n_eff)
    ]

    for i in range(n_eff):
        log.info(f"rand_mem_breakpoint: armed index={_BASE_INDEX + i} type={trigger_types[i].value} " f"size={trigger_sizes[i]} addr={pool_addrs[i]}")

    # The handler writes tselect/tdata1/tdata2 which are M-mode-only CSRs, so
    # BREAKPOINT (cause=3) must be handled in M-mode (medeleg bit 3 clear).
    #   * If the user did not force medeleg, auto-clear bit 3 and log INFO.
    #   * If the user did force medeleg/deleg_excp_to and bit 3 is still set,
    #     respect their choice and disable the feature for this run.
    if featmgr.medeleg & (1 << 3):
        if featmgr.medeleg_forced:
            log.warning(
                "rand_mem_breakpoint_pct is set but the user-supplied medeleg "
                f"(0x{featmgr.medeleg:x}) delegates BREAKPOINT (bit 3) to S-mode. "
                "The handler requires M-mode-only CSRs; respecting your medeleg "
                "and disabling the feature for this run. Clear bit 3 to enable."
            )
            return False
        log.info("rand_mem_breakpoint: clearing medeleg bit 3 so BREAKPOINT (cause=3) " f"is handled in M-mode (was 0x{featmgr.medeleg:x}).")
        featmgr.medeleg &= ~(1 << 3)

    # Optional icount injection — inner gate rolled only because the master
    # gate already rolled true above. Slot 8 is the only icount-capable slot
    # in the standard whisper config, so at most one icount trigger.
    icount_tdata1_vals: List[int] = []
    icount_pct = max(0, featmgr.rand_mem_inject_icount_pct)
    if icount_pct > 0 and rng.with_probability_of(icount_pct):
        density = featmgr.rand_mem_icount_density
        cmin, cmax = density.value
        # Pre-bake (max_fires + 1) random tdata1 values: one for the initial
        # arming + one per allowed re-arm. Floor/cap keeps the inline data
        # block bounded.
        n_icount_vals = max(_ICOUNT_TABLE_FLOOR, min(max_fires + 1, _ICOUNT_TABLE_CAP))
        icount_counts = [rng.randint(cmin, cmax) for _ in range(n_icount_vals)]
        icount_tdata1_vals = [
            build_tdata1_icount(
                count=c,
                action=TriggerAction.BREAKPOINT,
                priv_mode=("any",),
            )
            for c in icount_counts
        ]
        log.info(
            f"rand_mem_breakpoint: icount injection ENABLED on slot {_ICOUNT_SLOT} " f"(density={density}, range=[{cmin},{cmax}], " f"initial_count={icount_counts[0]}, table_size={n_icount_vals})"
        )
    elif icount_pct > 0:
        log.info(f"rand_mem_breakpoint: icount inner gate missed ({icount_pct}%); icount injection disabled this run")

    featmgr.register_hook(RV.HookPoint.M_LOADER, _build_m_loader_hook(pool_addrs, tdata1_vals, icount_tdata1_vals))
    featmgr.register_default_exception_handler(
        cause=3,
        label="rand_mem_bp_handler",
        assembly=_build_handler(n_eff, max_fires, pool_addrs, k, tdata1_vals, icount_tdata1_vals),
    )

    # Report whether slot 8 now holds an injected icount trigger so the park does not clobber it.
    return bool(icount_tdata1_vals)


def _build_m_loader_hook(pool_addrs: list, tdata1_vals: list, icount_tdata1_vals: list):
    """Return a Hookable that emits initial trigger arming.

    Pool data and counter state live alongside the handler asm (see
    ``_build_handler``) so PC-relative reach from the handler is short.

    When ``icount_tdata1_vals`` is non-empty, the loader additionally arms slot
    ``_ICOUNT_SLOT`` with ``icount_tdata1_vals[0]`` (the rest of the list is
    consumed by the handler on re-arm via the inline ``_rmbp_icount_tdata1_``
    table).
    """

    def hook(featmgr: FeatMgr) -> str:
        # Use `li` for the address — these come from ;#random_addr / ;#page_mapping
        # which become equates (numeric values), so `la`'s PC-relative offset would
        # overflow once .runtime and .data are far apart in the linker layout.
        arm_block = ["    # rand_mem_breakpoint: arm initial N triggers"]
        for i, (addr, tdata1) in enumerate(zip(pool_addrs, tdata1_vals)):
            arm_block.append(f"    csrwi tselect, {_BASE_INDEX + i}")
            arm_block.append(f"    li    t0, {addr}")
            arm_block.append("    csrw  tdata2, t0")
            arm_block.append(f"    li    t0, 0x{tdata1:x}")
            arm_block.append("    csrw  tdata1, t0")
        if icount_tdata1_vals:
            arm_block.append("    # rand_mem_breakpoint: arm icount trigger on slot 8")
            arm_block.append(f"    csrwi tselect, {_ICOUNT_SLOT}")
            # tdata2 is unused for icount; clear it for cleanliness.
            arm_block.append("    csrw  tdata2, x0")
            arm_block.append(f"    li    t0, 0x{icount_tdata1_vals[0]:x}")
            arm_block.append("    csrw  tdata1, t0")
        return "\n".join(arm_block) + "\n"

    return hook


def _build_handler(n_eff: int, max_fires: int, pool_addrs: list, k: int, tdata1_vals: list, icount_tdata1_vals: list):
    """Return a TrapHookable that codegens the round-robin re-arm BP handler.

    The pool table + state struct are emitted *inline* after the handler's
    ``xret`` (still in the trap handler's ``.runtime`` section) so PC-relative
    ``la`` from the handler reaches them in one ``auipc``+``addi``.

    To avoid infinite loop on re-execution, the handler must re-arm the
    firing trigger (not an arbitrary one). The firer is identified by
    matching ``mtval`` (the access address that took the BP) against each
    trigger's ``tdata2``: with ``match=EQUAL``, only the firer's ``tdata2``
    equals the access address. The pool-index advance remains round-robin —
    that part picks *which* pool entry to rotate in.

    ``k`` is the *actual* pool size (``min(2*n_eff, len(supplied_addresses))``)
    — pool_idx wraps modulo k. Initial value of pool_idx is ``min(n_eff, k)``;
    when k < 2*n_eff, the wrap happens earlier and the rotation cycles through
    fewer addresses but still progresses.
    """
    assert k == len(pool_addrs)
    assert len(tdata1_vals) == n_eff
    # apply() guarantees len(icount_tdata1_vals) >= _ICOUNT_TABLE_FLOOR when enabled.
    assert not icount_tdata1_vals or len(icount_tdata1_vals) >= _ICOUNT_TABLE_FLOOR
    pool_lines = "\n".join(f"    .dword {addr}    # pool[{i}]" for i, addr in enumerate(pool_addrs))
    # Unrolled search: walk tselect=BASE..BASE+N-1, compare tdata2 to mtval,
    # on first match jump to that slot's "found" arm which loads the original
    # tdata1 (hit bit clear) into t3 and falls through to the common write
    # block. Rewriting tdata1 on every re-arm clears the mcontrol6 hit bit;
    # without that, some implementations leave the trigger in a state where
    # subsequent fetches misbehave.
    search_lines = []
    for i in range(n_eff):
        search_lines.append(f"    csrwi tselect, {_BASE_INDEX + i}")
        search_lines.append("    csrr  t3, tdata2")
        search_lines.append(f"    beq   t2, t3, {40 + i}f")
    search_block = "\n".join(search_lines)
    found_arms = []
    for i, v in enumerate(tdata1_vals):
        found_arms.append(f"{40 + i}:")
        found_arms.append(f"    li    t3, 0x{v:x}")
        found_arms.append("    j     4f")
    found_block = "\n".join(found_arms)

    disable_lines = []
    for i in range(n_eff):
        disable_lines.append(f"    csrwi tselect, {_BASE_INDEX + i}")
        disable_lines.append("    csrw  tdata1, x0")
    disable_block = "\n".join(disable_lines)

    icount_enabled = bool(icount_tdata1_vals)
    k_icount = len(icount_tdata1_vals)

    if icount_enabled:
        # No-mcontrol6-match path: check slot 8's icount hit bit. If set, jump
        # to the icount re-arm path (label 6); otherwise fall through to the
        # plain return path (label 5). The "icount inject" disable also clears
        # slot 8 to keep the disable path tidy.
        no_match_block = f"""\
    # No mcontrol6 match — check slot {_ICOUNT_SLOT} for an icount fire.
    csrwi tselect, {_ICOUNT_SLOT}
    csrr  t3, tdata1
    li    t0, 0x{_ICOUNT_HIT_BIT:x}    # icount hit bit
    and   t0, t3, t0
    bnez  t0, 6f
    j     5f"""
        # Re-arm path: index _rmbp_icount_state_ → fetch tdata1 from the
        # pre-baked table → write it back to slot 8. tdata2 is unused for
        # icount, so we only touch tdata1. Wrap icount_idx mod K_icount.
        icount_rearm_block = f"""\
6:
    # icount fire path. Advance icount_idx (mod {k_icount}), fetch next
    # pre-baked tdata1 from _rmbp_icount_tdata1_, write it to slot 8.
    la    t0, _rmbp_icount_state_
    ld    t1, 0(t0)               # t1 = icount_idx (in [0, K_icount))
    addi  t3, t1, 1
    addi  t3, t3, -{k_icount}
    bgez  t3, 7f
    addi  t3, t3, {k_icount}
7:
    sd    t3, 0(t0)               # store new icount_idx

    la    t0, _rmbp_icount_tdata1_
    slli  t1, t1, 3
    add   t0, t0, t1
    ld    t3, 0(t0)               # t3 = next pre-baked icount tdata1

    csrwi tselect, {_ICOUNT_SLOT}
    csrw  tdata1, x0              # disable before rewriting (Debug Spec)
    csrw  tdata1, t3              # re-enable with fresh count
    j     5f
"""
        icount_disable_lines = "\n".join(
            [
                f"    csrwi tselect, {_ICOUNT_SLOT}",
                "    csrw  tdata1, x0",
            ]
        )
        icount_data_block = "\n".join(
            [
                "_rmbp_icount_state_:",
                "    .dword 1              # icount_idx (next re-arm picks tdata1[1]; tdata1[0] armed at startup)",
                "_rmbp_icount_tdata1_:",
            ]
            + [f"    .dword 0x{v:x}    # icount_tdata1[{i}]" for i, v in enumerate(icount_tdata1_vals)]
        )
    else:
        no_match_block = "    j     5f                      # no match: pool already advanced; just return"
        icount_rearm_block = ""
        icount_disable_lines = ""
        icount_data_block = ""

    def assembly_fn(ctx: TrapContext) -> str:
        return f"""
    # ---- rand_mem_bp_handler ----
    la    t0, _rmbp_state_
    sd    t2, 16(t0)
    sd    t3, 24(t0)

    ld    t1, 0(t0)              # fires_remaining
    beqz  t1, 3f                  # 0 → disable & return

    addi  t1, t1, -1
    sd    t1, 0(t0)               # store decremented counter

    ld    t1, 8(t0)               # t1 = pool_idx (in [0, K))
    addi  t2, t1, 1
    addi  t2, t2, -{k}
    bgez  t2, 1f
    addi  t2, t2, {k}
1:
    sd    t2, 8(t0)               # store new pool_idx

    # Fetch new pool[pool_idx_old] into t1
    la    t0, _rmbp_pool_
    slli  t1, t1, 3
    add   t0, t0, t1
    ld    t1, 0(t0)               # t1 = next pool address

    # Identify firing trigger via mtval (= access address = firer's tdata2).
    # Walk tselect = BASE..BASE+N-1; on first tdata2 match, branch into that
    # slot's "found" arm which loads its original tdata1 into t3 and falls
    # through to the common write block.
    csrr  t2, {ctx.xtval}
{search_block}
{no_match_block}

{found_block}

4:
    # Per Debug Spec: don't modify tdata2 while trigger is enabled.
    # Disable, update tdata2, then re-enable with the original tdata1
    # (which has hit=0).
    csrw  tdata1, x0
    csrw  tdata2, t1
    csrw  tdata1, t3
    j     5f

{icount_rearm_block}
5:
    la    t0, _rmbp_state_
    ld    t2, 16(t0)
    ld    t3, 24(t0)
    {ctx.xret}

3:
{disable_block}
{icount_disable_lines}
    la    t0, _rmbp_state_
    ld    t2, 16(t0)
    ld    t3, 24(t0)
    {ctx.xret}

    # Pool/state are inlined after xret (still in this section) so the handler's
    # `la` reaches them via short PC-relative offsets.
    .balign 8
_rmbp_state_:
    .dword {max_fires}    # fires_remaining
    .dword {n_eff % k}    # pool_idx (round-robin pointer; first re-arm picks pool[n_eff % k])
    .dword 0              # scratch save: t2
    .dword 0              # scratch save: t3
_rmbp_pool_:
{pool_lines}
{icount_data_block}
"""

    return assembly_fn
