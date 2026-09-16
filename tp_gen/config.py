# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""tp_gen plan configs and named presets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PlanConfig:
    extra_args: tuple[str, ...] = ()
    repeat_times: int | None = None


@dataclass(frozen=True)
class PlanSelection:
    """scenarios is None => whole plan (all RiescueC scenarios + directed tests).
    scenarios=("X",) => only those scenarios; directed tests omitted unless include_directed_tests=True.
    include_directed_tests None => True iff scenarios is None.
    """

    name: str
    scenarios: tuple[str, ...] | None = None
    include_directed_tests: bool | None = None

    def wants_directed_tests(self) -> bool:
        if self.include_directed_tests is not None:
            return self.include_directed_tests
        return self.scenarios is None


@dataclass(frozen=True)
class Preset:
    description: str = ""
    plans: tuple[PlanSelection | str, ...] | None = None  # None => every key in PLANS, full
    extra_args: tuple[str, ...] = ()


def normalize_selection(item: PlanSelection | str) -> PlanSelection:
    if isinstance(item, PlanSelection):
        return item
    return PlanSelection(name=item)


def merge_selections(selections: Sequence[PlanSelection]) -> list[PlanSelection]:
    """Duplicate plan names merge: union of scenario lists; None (all) wins; directed tests included if any selection wants them."""
    merged: dict[str, PlanSelection] = {}
    for selection in selections:
        existing = merged.get(selection.name)
        if existing is None:
            merged[selection.name] = selection
            continue
        if existing.scenarios is None or selection.scenarios is None:
            scenarios: tuple[str, ...] | None = None
        else:
            seen: set[str] = set()
            union: list[str] = []
            for scenario in (*existing.scenarios, *selection.scenarios):
                if scenario not in seen:
                    seen.add(scenario)
                    union.append(scenario)
            scenarios = tuple(union)
        merged[selection.name] = PlanSelection(
            name=selection.name,
            scenarios=scenarios,
            include_directed_tests=existing.wants_directed_tests() or selection.wants_directed_tests(),
        )
    return list(merged.values())


def resolve_preset(preset_name: str, test_plan: str | None = None, cli_scenarios: Sequence[str] | None = None) -> list[PlanSelection]:
    """
    - unknown preset: ValueError listing keys
    - plans is None: full PlanSelection for every PLANS key (preserve PLANS insertion order)
    - str entries become PlanSelection(name)
    - merge duplicates
    - if test_plan set: must be in the resolved list; return only that selection
    - cli_scenarios only valid if test_plan is set (ValueError otherwise)
    - cli_scenarios further restricts: intersection with selection.scenarios if selection is already a subset; if selection.scenarios is None, use cli_scenarios. After CLI restrict, do NOT add directed tests (wants_directed_tests becomes False unless selection was full AND cli_scenarios is None — if cli_scenarios set, skip dtests)
    """
    preset = PRESETS.get(preset_name)
    if preset is None:
        known = ", ".join(PRESETS)
        raise ValueError(f"Unknown preset {preset_name!r}. Known presets: {known}")
    if cli_scenarios is not None and test_plan is None:
        raise ValueError("cli_scenarios is only valid when test_plan is set")

    if preset.plans is None:
        selections = [PlanSelection(name) for name in PLANS]
    else:
        selections = [normalize_selection(item) for item in preset.plans]
    selections = merge_selections(selections)

    if test_plan is None:
        return selections

    match = next((selection for selection in selections if selection.name == test_plan), None)
    if match is None:
        known_plans = ", ".join(selection.name for selection in selections)
        raise ValueError(f"test_plan {test_plan!r} is not in preset {preset_name!r}. Plans: {known_plans}")

    if cli_scenarios is None:
        return [match]

    if match.scenarios is None:
        restricted = tuple(cli_scenarios)
    else:
        allowed = set(match.scenarios)
        restricted = tuple(scenario for scenario in cli_scenarios if scenario in allowed)
    if not restricted:
        raise ValueError(f"cli_scenarios {list(cli_scenarios)} matched no scenarios of {test_plan!r}")
    return [
        PlanSelection(
            name=match.name,
            scenarios=restricted,
            include_directed_tests=False,
        )
    ]


def plan_config(name: str) -> PlanConfig:
    config = PLANS.get(name)
    if config is None:
        known = ", ".join(PLANS)
        raise ValueError(f"Unknown plan {name!r}. Known plans: {known}")
    return config


def merged_extra_args(preset: Preset, plan_name: str) -> tuple[str, ...]:
    seen: set[str] = set()
    args: list[str] = []
    for arg in (*plan_config(plan_name).extra_args, *preset.extra_args):
        if arg not in seen:
            seen.add(arg)
            args.append(arg)
    return tuple(args)


PLANS: dict[str, PlanConfig] = {
    "zicsr": PlanConfig(),
    "zicond": PlanConfig(),
    "zifencei": PlanConfig(),
    "za64rs": PlanConfig(),
    "zicntr_zihpm_sscounterenw": PlanConfig(),
    "sstc": PlanConfig(),
    "zihintntl": PlanConfig(repeat_times=1),
    "exceptions": PlanConfig(repeat_times=1),
    "zihintpause": PlanConfig(),
    "zawrs": PlanConfig(),
    "zicbom_zicboz_zicbop_zic64b": PlanConfig(),
    "svinval": PlanConfig(repeat_times=1),
    "svadu": PlanConfig(repeat_times=1),
    "svade": PlanConfig(),
    "svnapot": PlanConfig(repeat_times=1),
    "paging": PlanConfig(extra_args=("--more_os_pages",), repeat_times=1),
    "sscofpmf": PlanConfig(repeat_times=1),
    "zkt": PlanConfig(),
    "smstateen_ssstateen": PlanConfig(),
    "ssu64xl": PlanConfig(),
    "hypervisor_paging_basic": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_csr": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_faults_vs": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_faults_g_invalid": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_faults_g_reserved": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_faults_g_misaligned": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_permissions_023": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_permissions_024": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_a_bit": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_paging_d_bit": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_exceptions": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "hypervisor_interrupts": PlanConfig(extra_args=("--check_xtinst", "--map_imsic_pages"), repeat_times=1),
    "hypervisor_tlb_fence": PlanConfig(extra_args=("--check_xtinst",), repeat_times=1),
    "zjpm": PlanConfig(),
    "sdtrig": PlanConfig(),
    "sdtrig_icount": PlanConfig(),
}

PRESETS: dict[str, Preset] = {
    "all": Preset(description="Every plan in PLANS, all scenarios and directed tests"),
    "hypervisor": Preset(
        description="Every hypervisor_* plan, plus the H-relevant scenarios of other plans",
        plans=(
            *(PlanSelection(name) for name in PLANS if name.startswith("hypervisor_")),
            PlanSelection(
                "sstc",
                scenarios=(
                    "SID_SSTC_01",
                    "SID_SSTC_02_V",
                    "SID_SSTC_03_VU",
                    "SID_SSTC_05_VS_VU",
                    "SID_SSTC_10",
                    "SID_SSTC_13_V",
                ),
            ),
            # Scenarios touching the hstateen CSRs.
            PlanSelection(
                "smstateen_ssstateen",
                scenarios=(
                    "SID_SMSTATEEN_006",
                    "SID_SMSTATEEN_007",
                    "SID_SMSTATEEN_008",
                    "SID_SMSTATEEN_009",
                    "SID_SMSTATEEN_011_case1",
                    "SID_SMSTATEEN_011_case2",
                    "SID_SMSTATEEN_012_U",
                    "SID_SMSTATEEN_012_VS",
                    "SID_SMSTATEEN_012_VU",
                    "SID_SMSTATEEN_019_case1",
                    "SID_SMSTATEEN_019_case2",
                    "SID_SMSTATEEN_019_case3",
                    "SID_SMSTATEEN_019_case4",
                    "SID_SMSTATEEN_026",
                ),
            ),
            # sscofpmf has no scenario that enables counter overflow for VS/VU
            # mode, so it contributes nothing here yet.
            # The remaining plans contribute their virtualized=[True] scenarios.
            PlanSelection(
                "zicbom_zicboz_zicbop_zic64b",
                scenarios=(
                    "SID_ZICBO_007",
                    "SID_ZICBO_008",
                    "SID_ZICBO_009",
                    "SID_ZICBO_010",
                    "SID_ZICBO_011",
                    "SID_ZICBO_012",
                ),
            ),
            PlanSelection("ssu64xl", scenarios=("SID_SSU64XL_02",)),
            PlanSelection(
                "zawrs",
                scenarios=(
                    "SID_ZAWRS_22_WRS_VTW_VIRTUAL_EXCEPTION",
                    "SID_ZAWRS_23_WRS_TW_VTW_ILLEGAL_EXCEPTION_1",
                    "SID_ZAWRS_24_WRS_TW_VTW_ILLEGAL_EXCEPTION_2",
                ),
            ),
            PlanSelection(
                "zicntr_zihpm_sscounterenw",
                scenarios=(
                    "SID_XCOUNTEREN_01_VS",
                    "SID_XCOUNTEREN_01_VU",
                    "SID_XCOUNTEREN_02_VS",
                    "SID_XCOUNTEREN_02_VU",
                    "SID_XCOUNTEREN_05_VS",
                    "SID_XCOUNTEREN_05_VU",
                    "SID_XCOUNTEREN_06_VS",
                    "SID_XCOUNTEREN_06_VU",
                    "SID_XCOUNTEREN_07_VS",
                    "SID_XCOUNTEREN_07_VU",
                    "SID_XCOUNTEREN_08_VS",
                    "SID_XCOUNTEREN_08_VU",
                    "SID_XCOUNTEREN_09_VS",
                    "SID_XCOUNTEREN_09_VU",
                ),
            ),
            PlanSelection(
                "zjpm",
                scenarios=(
                    "SID_14_pm_enabled_vs_mode",
                    "SID_06_pm_disabled_vs_mode",
                ),
            ),
        ),
        extra_args=("--check_xtinst",),
    ),
}
