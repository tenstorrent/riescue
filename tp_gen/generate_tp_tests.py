#!/usr/bin/env python3

# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""Generate RiescueC TP tests and associated RiescueD directed tests from coretp."""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Sequence

from coretp.env.solver import TestEnvSolver
from coretp.plans import get_plan, list_plans
from coretp.rv_enums import PagingMode, PrivilegeMode

try:
    from .config import PRESETS, PlanSelection, merged_extra_args, plan_config, resolve_preset
except ImportError:
    from config import PRESETS, PlanSelection, merged_extra_args, plan_config, resolve_preset


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_CPUCONFIG = REPO_ROOT / "riescue/dtest_framework/tests/cpu_config_default_tp_gen.json"
DEFAULT_WHISPER_CONFIG = REPO_ROOT / "riescue/dtest_framework/lib/whisper_config_default_tp_gen.json"
DEFAULT_RVMODEL_MACROS = REPO_ROOT / "riescue/dtest_framework/lib/rvmodel_macros/rvmodel_macros_tp_gen.h"

PRIVILEGE_NAMES = {
    PrivilegeMode.M: "machine",
    PrivilegeMode.S: "super",
    PrivilegeMode.U: "user",
}
PRIVILEGE_ORDER = {PrivilegeMode.M: 0, PrivilegeMode.S: 1, PrivilegeMode.U: 2}
PAGING_NAMES = {
    PagingMode.DISABLED: "disable",
    PagingMode.SV39: "sv39",
    PagingMode.SV48: "sv48",
    PagingMode.SV57: "sv57",
}
PAGING_ORDER = {
    PagingMode.DISABLED: 0,
    PagingMode.SV39: 1,
    PagingMode.SV48: 2,
    PagingMode.SV57: 3,
}


@dataclass(frozen=True)
class Environment:
    privilege: PrivilegeMode
    paging: PagingMode
    virtualized: bool
    g_paging: PagingMode

    def sort_key(self) -> tuple[int, int, bool, int]:
        return (
            PRIVILEGE_ORDER[self.privilege],
            PAGING_ORDER[self.paging],
            self.virtualized,
            PAGING_ORDER[self.g_paging],
        )

    def run_dir(self, feature: str, seed: int, testsuite_dir: Path) -> Path:
        """
        Every seed gets its own directory. RiescueD writes rvmodel_macros.h and
        riescue_aplic_mmr.h into the run dir under fixed, non-seed-prefixed
        names, so two seeds sharing a directory race on those two files.
        """
        feature_dir = testsuite_dir / feature
        privilege = PRIVILEGE_NAMES[self.privilege]
        paging = PAGING_NAMES[self.paging]
        if self.privilege == PrivilegeMode.M:
            env_dir = feature_dir / privilege
        elif self.virtualized:
            g_paging = PAGING_NAMES[self.g_paging]
            env_dir = feature_dir / "virtualized" / f"{privilege}_{paging}_g{g_paging}"
        else:
            env_dir = feature_dir / f"{privilege}_{paging}"
        return env_dir / f"seed{seed}"


@dataclass(frozen=True)
class TestCommand:
    feature: str
    seed: int
    run_dir: Path
    argv: tuple[str, ...]
    label: str

    @property
    def basename(self) -> str:
        return self.label

    @property
    def stdout_log(self) -> Path:
        return self.run_dir / f"{self.basename}_stdout.log"

    @property
    def stderr_log(self) -> Path:
        return self.run_dir / f"{self.basename}_stderr.log"


@dataclass(frozen=True)
class TestResult:
    command: TestCommand
    returncode: int
    passed: bool


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse generator options and preserve arguments after ``--`` for RiescueC."""
    argv = list(argv)
    if "--" in argv:
        separator = argv.index("--")
        generator_args = argv[:separator]
        riescuec_args = argv[separator + 1 :]
    else:
        generator_args = argv
        riescuec_args = []

    parser = argparse.ArgumentParser(description=("Generate RiescueC TP tests for the exact privilege, paging, and " "virtualization environments declared by each coretp plan."))
    parser.add_argument("--preset", default="all", help="Named preset from tp_gen/config.py (default: all)")
    parser.add_argument("--batch", type=int, default=10, help="Maximum concurrent RiescueC/RiescueD processes (default: 10)")
    parser.add_argument("--test_plan", "--test-plan", help="Only run this plan from the selected preset")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=None,
        help="Only generate these scenario names. Requires --test_plan.",
    )
    parser.add_argument("--seed_count", "--seed-count", type=int, default=2, help="Seeds per environment (default: 2)")
    parser.add_argument("--cpuconfig", type=Path, help="CPU config JSON passed to RiescueC/RiescueD (default: cpu_config_default_tp_gen.json)")
    parser.add_argument(
        "--whisper_config_json",
        "--whisper-config-json",
        type=Path,
        help="Whisper config JSON passed to RiescueC/RiescueD (default: whisper_config_default_tp_gen.json)",
    )
    parser.add_argument("--rvmodel_macros", type=Path, help="rvmodel_macros.h passed to RiescueC/RiescueD (default: rvmodel_macros_tp_gen.h)")
    parser.add_argument("--save_intermediate_files", "--save-intermediate-files", action="store_true")
    parser.add_argument(
        "--output_dir",
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory that will contain testsuite/<feature>/... (default: current directory)",
    )
    parser.add_argument("--dry_run", "--dry-run", action="store_true", help="Write command manifests without running generators")
    args = parser.parse_args(generator_args)

    if args.batch < 1:
        parser.error("--batch must be at least 1")
    if args.seed_count < 1:
        parser.error("--seed_count must be at least 1")
    if args.scenarios is not None and not args.test_plan:
        parser.error("--scenarios requires --test_plan")
    if args.preset not in PRESETS:
        parser.error(f"unknown preset {args.preset!r}; known presets: {', '.join(PRESETS)}")
    return args, riescuec_args


def _has_flag(args: Sequence[str], flag: str) -> bool:
    """True if ``args`` already set ``flag`` (including ``flag=value``)."""
    prefix = f"{flag}="
    return any(arg == flag or arg.startswith(prefix) for arg in args)


def _selected_scenarios(plan, scenario_names: tuple[str, ...] | None):
    if scenario_names is None:
        return plan.scenarios
    by_name = {scenario.name: scenario for scenario in plan.scenarios}
    available = [scenario.name for scenario in plan.scenarios]
    missing = [name for name in scenario_names if name not in by_name]
    if missing:
        raise ValueError(f"Unknown scenario name(s) for {plan.name}: {', '.join(missing)}. Available: {', '.join(available)}")
    selected = [by_name[name] for name in scenario_names]
    if not selected:
        raise ValueError(f"No scenarios selected for test plan '{plan.name}'")
    return selected


def query_environments(plan_name: str, scenario_names: tuple[str, ...] | None = None) -> list[Environment]:
    """Return exact, CLI-relevant environments supported by a coretp plan (or a scenario subset)."""
    plan = get_plan(plan_name)
    solved = TestEnvSolver().solve([scenario.env for scenario in _selected_scenarios(plan, scenario_names)])
    environments = set()
    for env in solved:
        if env.priv not in PRIVILEGE_NAMES:
            continue
        # M-mode does not use satp; RiescueC rejects machine + any paging mode
        # other than disable. Collapse every M-mode solve to that one combo.
        if env.priv == PrivilegeMode.M:
            paging = PagingMode.DISABLED
            g_paging = PagingMode.DISABLED if env.g_paging_mode not in PAGING_NAMES else env.g_paging_mode
        elif env.paging_mode not in PAGING_NAMES or env.g_paging_mode not in PAGING_NAMES:
            # RiescueC exposes DISABLED, Sv39, Sv48, and Sv57. Coretp also has
            # BARE and Sv32, which tp.py cannot translate to a Riescue paging mode.
            continue
        else:
            paging = env.paging_mode
            g_paging = env.g_paging_mode
        environments.add(
            Environment(
                privilege=env.priv,
                paging=paging,
                virtualized=env.virtualized,
                g_paging=g_paging,
            )
        )
    return sorted(environments, key=Environment.sort_key)


def _toolchain_args(args: argparse.Namespace, extra_args: Sequence[str], forwarded_args: Sequence[str]) -> list[str]:
    argv: list[str] = []
    already = tuple(extra_args) + tuple(forwarded_args)
    if args.cpuconfig:
        argv.extend(["--cpuconfig", str(args.cpuconfig.resolve())])
    elif not _has_flag(already, "--cpuconfig"):
        argv.extend(["--cpuconfig", str(DEFAULT_CPUCONFIG)])
    if args.whisper_config_json:
        argv.extend(["--whisper_config_json", str(args.whisper_config_json.resolve())])
    elif not _has_flag(already, "--whisper_config_json"):
        argv.extend(["--whisper_config_json", str(DEFAULT_WHISPER_CONFIG)])
    if args.rvmodel_macros:
        argv.extend(["--rvmodel_macros", str(args.rvmodel_macros.resolve())])
    elif not _has_flag(already, "--rvmodel_macros"):
        argv.extend(["--rvmodel_macros", str(DEFAULT_RVMODEL_MACROS)])
    return argv


def build_riescuec_command(
    selection: PlanSelection,
    extra_args: Sequence[str],
    repeat_times: int | None,
    environment: Environment,
    seed: int,
    args: argparse.Namespace,
    forwarded_args: Sequence[str],
    testsuite_dir: Path,
) -> TestCommand:
    run_dir = environment.run_dir(selection.name, seed, testsuite_dir)
    argv = [
        "riescuec",
        "--mode",
        "tp",
        "--test_plan",
        selection.name,
        "--print_rvcp_passed",
        "--print_rvcp_failed",
        "--test_paging_mode",
        PAGING_NAMES[environment.paging],
        "--test_priv_mode",
        PRIVILEGE_NAMES[environment.privilege],
        "--seed",
        str(seed),
    ]
    if environment.virtualized:
        argv.extend(
            [
                "--test_paging_g_mode",
                PAGING_NAMES[environment.g_paging],
                "--test_env",
                "virtualized",
            ]
        )
    if selection.scenarios is not None:
        argv.extend(["--scenarios", *selection.scenarios])
    if repeat_times is not None:
        argv.extend(["--repeat_times", str(repeat_times)])
    argv.extend(extra_args)
    argv.extend(["--run_dir", str(run_dir)])
    argv.extend(_toolchain_args(args, extra_args, forwarded_args))
    argv.extend(forwarded_args)
    return TestCommand(selection.name, seed, run_dir, tuple(argv), f"tp_{selection.name}_{seed}")


def build_riescued_command(
    plan_name: str,
    extra_args: Sequence[str],
    repeat_times: int | None,
    source: Path,
    seed: int,
    args: argparse.Namespace,
    testsuite_dir: Path,
) -> TestCommand:
    stem = source.stem
    run_dir = testsuite_dir / plan_name / "directed" / stem / f"seed{seed}"
    argv = [
        "riescued",
        "--testfile",
        str(source),
        "--run_iss",
        "--seed",
        str(seed),
        "--run_dir",
        str(run_dir),
    ]
    if repeat_times is not None:
        argv.extend(["--repeat_times", str(repeat_times)])
    argv.extend(extra_args)
    argv.extend(_toolchain_args(args, extra_args, ()))
    return TestCommand(plan_name, seed, run_dir, tuple(argv), f"rd_{plan_name}_{stem}_{seed}")


def collect_commands(
    selection: PlanSelection,
    extra_args: Sequence[str],
    repeat_times: int | None,
    args: argparse.Namespace,
    forwarded_args: Sequence[str],
    testsuite_dir: Path,
) -> tuple[list[TestCommand], list[TestCommand]]:
    environments = query_environments(selection.name, selection.scenarios)
    riescuec_commands = [
        build_riescuec_command(selection, extra_args, repeat_times, environment, seed, args, forwarded_args, testsuite_dir) for environment in environments for seed in range(1, args.seed_count + 1)
    ]
    riescued_commands: list[TestCommand] = []
    if selection.wants_directed_tests():
        plan = get_plan(selection.name)
        riescued_commands = [
            build_riescued_command(selection.name, extra_args, repeat_times, source, seed, args, testsuite_dir) for source in plan.directed_tests for seed in range(1, args.seed_count + 1)
        ]
    return riescuec_commands, riescued_commands


def write_manifest(plan_name: str, commands: Sequence[TestCommand], testsuite_dir: Path, filename: str, header: str) -> Path | None:
    if not commands:
        return None
    feature_dir = testsuite_dir / plan_name
    feature_dir.mkdir(parents=True, exist_ok=True)
    manifest = feature_dir / filename
    with manifest.open("w", encoding="utf-8") as file:
        file.write("#!/bin/bash\n")
        file.write(header)
        for command in commands:
            file.write(f"{shlex.join(command.argv)}\n")
    manifest.chmod(0o755)
    return manifest


def run_command(command: TestCommand) -> TestResult:
    command.run_dir.mkdir(parents=True, exist_ok=True)
    with command.stdout_log.open("wb") as stdout, command.stderr_log.open("wb") as stderr:
        try:
            process = subprocess.run(command.argv, stdout=stdout, stderr=stderr, check=False)
            returncode = process.returncode
        except OSError as error:
            stderr.write(f"ERROR: could not execute {command.argv[0]}: {error}\n".encode())
            returncode = 127
    passed = b"PASSED" in command.stderr_log.read_bytes()
    return TestResult(command, returncode, passed)


def execute_feature(plan_name: str, commands: Sequence[TestCommand], batch_size: int) -> list[TestResult]:
    total = len(commands)
    completed = 0
    progress_lock = Lock()
    results: list[TestResult] = []
    with ThreadPoolExecutor(max_workers=batch_size) as executor:
        futures = [executor.submit(run_command, command) for command in commands]
        for future in as_completed(futures):
            results.append(future.result())
            with progress_lock:
                completed += 1
                print(f"[{plan_name}] Progress: {completed}/{total} commands completed", flush=True)
    return results


def organize_result(result: TestResult, save_intermediate: bool) -> Path | None:
    """Keep the .S next to the run dir and move everything else aside."""
    command = result.command
    intermediate_dir = command.run_dir / f"{command.basename}_intermediate"
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    for path in command.run_dir.iterdir():
        if path == intermediate_dir or path.is_dir() or path.suffix == ".S":
            continue
        shutil.move(str(path), intermediate_dir / path.name)

    if not save_intermediate and result.passed:
        shutil.rmtree(intermediate_dir)
        return None
    return intermediate_dir / command.stderr_log.name


def organize_results(results: Sequence[TestResult], save_intermediate: bool) -> dict[TestCommand, Path | None]:
    return {result.command: organize_result(result, save_intermediate) for result in results}


def print_summary(results: Sequence[TestResult], log_locations: dict[TestCommand, Path | None]) -> None:
    passed = sum(result.passed for result in results)
    failed_results = [result for result in results if not result.passed]
    total = len(results)

    print("\n=========================================")
    print("          TEST RESULTS SUMMARY")
    print("=========================================")
    print(f"PASSED: {passed}")
    print(f"FAILED: {len(failed_results)}")
    print(f"TOTAL:  {total}")
    if total:
        print(f"\nPass rate: {passed / total * 100:.1f}%")
        print(f"Fail rate: {len(failed_results) / total * 100:.1f}%")
    if failed_results:
        print("\nFAILED test stderr logs:")
        for result in failed_results:
            print(f"  {log_locations[result.command]}")
    print("=========================================")


def main(argv: Sequence[str] | None = None) -> int:
    args, forwarded_args = parse_args(sys.argv[1:] if argv is None else argv)
    selections = resolve_preset(args.preset, args.test_plan, args.scenarios)
    available_plans = set(list_plans())
    unknown = sorted({selection.name for selection in selections} - available_plans)
    if unknown:
        raise ValueError(f"preset contains unknown coretp plans: {', '.join(unknown)}")

    testsuite_dir = args.output_dir.expanduser().resolve() / "testsuite"
    testsuite_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {testsuite_dir.parent}")
    print(f"Testsuite: {testsuite_dir}")
    print(f"Preset: {args.preset}")

    all_results: list[TestResult] = []
    for selection in selections:
        extra_args = merged_extra_args(PRESETS[args.preset], selection.name)
        repeat_times = plan_config(selection.name).repeat_times
        riescuec_commands, riescued_commands = collect_commands(selection, extra_args, repeat_times, args, forwarded_args, testsuite_dir)
        write_manifest(selection.name, riescuec_commands, testsuite_dir, f"{selection.name}_tp_commands.sh", "# Auto-generated RiescueC TP-mode commands\n\n")
        write_manifest(selection.name, riescued_commands, testsuite_dir, f"{selection.name}_rd_commands.sh", "# Auto-generated RiescueD directed-test commands\n\n")
        print(
            f"  {selection.name}: {len(riescuec_commands)} RiescueC commands"
            f" from {len(riescuec_commands) // args.seed_count if args.seed_count else 0} environments"
            f" + {len(riescued_commands)} RiescueD commands (batch size: {args.batch})"
        )
        if not args.dry_run:
            all_results.extend(execute_feature(selection.name, [*riescuec_commands, *riescued_commands], args.batch))

    if args.dry_run:
        print(f"\nDry run complete; command manifests are under {testsuite_dir}/<feature>/.")
        return 0

    print("\nOrganizing output files...")
    log_locations = organize_results(all_results, args.save_intermediate_files)
    print_summary(all_results, log_locations)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
