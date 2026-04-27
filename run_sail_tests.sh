#!/usr/bin/env bash
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0
#
# Run riescuec tp-mode tests with Sail as the second-pass ISS.
# Reads features.csv directly — does NOT call generate_tp_tests.sh.
#
# Usage:
#   bash run_sail_tests.sh [OPTIONS]
#
# Options:
#   --test_plan FEATURE   Only run a specific feature (default: all safe features)
#   --seed_count N        Seeds per test combination (default: 1)
#   --batch N             Max parallel jobs (default: 1)
#   --machine_only        Only run machine-mode combinations (avoids Issue #2)
#   --sail_trace CATS     Sail trace: instr reg mem exception ptw all
#   --dry_run             Print commands without executing
#   --help                Show this help
#
# Required env vars:
#   WHISPER_PATH          Path to whisper binary
#   SAIL_PATH             Path to sail_riscv_sim binary
#
# Known issues skipped by default:
#   hypervisor_* features  — H-extension CSRs (mtinst) cause Sail to hang
#   supervisor + Sail      — mtinst in random CSR reads causes infinite loop

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV_FILE="${SCRIPT_DIR}/tp_gen/features.csv"
CPU_CONFIG="${SCRIPT_DIR}/iss_cpu_config.json"
SAIL_CONFIG_OVERRIDE="${SCRIPT_DIR}/sail_config_override.json"
RESULTS_DIR="${SCRIPT_DIR}/sail_results"

# Defaults
TEST_PLAN=""
SEED_COUNT=1
BATCH=1
MACHINE_ONLY=false
DRY_RUN=false
SAIL_TRACE_CATS=()

# Features to always skip regardless of flags
# These use H-extension CSRs (mtinst etc.) that cause Sail to hang
SKIP_ALWAYS=(
    "hypervisor_paging"
    "hypervisor_paging_basic"
    "hypervisor_paging_csr_ad"
    "hypervisor_paging_faults"
    "hypervisor_paging_permissions_023"
    "hypervisor_paging_permissions_024"
    "hypervisor_exceptions"
)

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --test_plan)    TEST_PLAN="$2";    shift 2 ;;
        --seed_count)   SEED_COUNT="$2";   shift 2 ;;
        --batch)        BATCH="$2";        shift 2 ;;
        --machine_only) MACHINE_ONLY=true; shift ;;
        --dry_run)      DRY_RUN=true;      shift ;;
        --sail_trace)
            shift
            while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                SAIL_TRACE_CATS+=("$1"); shift
            done ;;
        --help|-h)
            grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate environment
# ---------------------------------------------------------------------------
WHISPER_PATH="${WHISPER_PATH:-$(command -v whisper 2>/dev/null || true)}"
SAIL_PATH="${SAIL_PATH:-$(command -v sail_riscv_sim 2>/dev/null || true)}"

[[ -z "$WHISPER_PATH" ]] && { echo "ERROR: WHISPER_PATH not set"; exit 1; }
[[ -z "$SAIL_PATH" ]]    && { echo "ERROR: SAIL_PATH not set"; exit 1; }
[[ -f "$CSV_FILE" ]]     || { echo "ERROR: $CSV_FILE not found"; exit 1; }
[[ -f "$CPU_CONFIG" ]]   || { echo "ERROR: $CPU_CONFIG not found"; exit 1; }
[[ -f "$SAIL_CONFIG_OVERRIDE" ]] || { echo "ERROR: $SAIL_CONFIG_OVERRIDE not found"; exit 1; }

# Build sail trace flag string
SAIL_TRACE_FLAG=""
if [[ ${#SAIL_TRACE_CATS[@]} -gt 0 ]]; then
    SAIL_TRACE_FLAG="--sail_trace ${SAIL_TRACE_CATS[*]}"
fi

# Fixed sail args appended to every riescuec command
SAIL_ARGS=(
    "--whisper_path" "$WHISPER_PATH"
    "--sail_path"    "$SAIL_PATH"
    "--first_pass_iss" "whisper"
    "--second_pass_iss" "sail"
    "--cpuconfig"    "$CPU_CONFIG"
    "--sail_config_override" "$SAIL_CONFIG_OVERRIDE"
)
[[ -n "$SAIL_TRACE_FLAG" ]] && SAIL_ARGS+=($SAIL_TRACE_FLAG)

mkdir -p "$RESULTS_DIR"

# ---------------------------------------------------------------------------
# Print header
# ---------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  RiESCUE + Sail ISS — tp-mode Test Run"
echo "=============================================="
echo "  Whisper:     $WHISPER_PATH"
echo "  Sail:        $SAIL_PATH"
echo "  CPU config:  $CPU_CONFIG"
echo "  Sail config: $SAIL_CONFIG_OVERRIDE"
echo "  Seeds:       $SEED_COUNT"
echo "  Batch:       $BATCH"
echo "  Machine only:$MACHINE_ONLY"
[[ -n "$SAIL_TRACE_FLAG" ]] && echo "  Trace:       ${SAIL_TRACE_CATS[*]}"
[[ "$DRY_RUN" == "true" ]] && echo "  DRY RUN: commands will be printed, not executed"
echo "=============================================="

# ---------------------------------------------------------------------------
# Read features.csv and build riescuec commands
# CSV columns:
#   feature,machine,supervisor,user,disabled,sv39,sv48,sv57,
#   bare_metal,virtualized,g_disabled,g_sv39,g_sv48,g_sv57,extra_args,repeat_times
# ---------------------------------------------------------------------------
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_SKIPPED=0
FAILED_CMDS=()
JOBS=()

run_cmd() {
    local feature="$1"
    local priv="$2"
    local paging="$3"
    local env_mode="$4"      # bare_metal or virtualized
    local g_paging="$5"      # only for virtualized
    local extra_args="$6"
    local repeat_times="$7"
    local seed="$8"

    # Build run directory — matches generate_tp_tests.sh naming
    local priv_paging="${priv}"
    if [[ "$paging" != "disable" ]]; then
        priv_paging="${priv}_${paging}"
    fi
    local run_dir="${RESULTS_DIR}/${feature}/${priv_paging}/seed_${seed}"
    [[ "$env_mode" == "virtualized" ]] && run_dir="${RESULTS_DIR}/${feature}/${priv_paging}_virt_g${g_paging}/seed_${seed}"

    # Repeat times suffix
    local rt_suffix=""
    [[ -n "$repeat_times" ]] && rt_suffix="--repeat_times $repeat_times"

    # Extra args suffix
    local extra_suffix=""
    [[ -n "$extra_args" ]] && extra_suffix="$extra_args"

    # Paging g mode suffix (virtualized only)
    local g_suffix=""
    [[ "$env_mode" == "virtualized" ]] && g_suffix="--test_paging_g_mode $g_paging --test_env virtualized"

    # Full riescuec command
    local cmd=(
        riescuec
        --mode tp
        --test_plan "$feature"
        --print_rvcp_passed
        --print_rvcp_failed
        --test_paging_mode "$paging"
        --test_priv_mode "$priv"
        --seed "$seed"
        --run_dir "$run_dir"
        "${SAIL_ARGS[@]}"
    )
    [[ -n "$rt_suffix" ]]    && cmd+=($rt_suffix)
    [[ -n "$extra_suffix" ]] && cmd+=($extra_suffix)
    [[ -n "$g_suffix" ]]     && cmd+=($g_suffix)

    local log_file="${run_dir}/sail_run.log"
    mkdir -p "$run_dir"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "[DRY RUN] ${cmd[*]}"
        return 0
    fi

    printf "  %-40s %-12s %-8s seed=%-10s" "$feature" "$priv" "$paging" "$seed"
    if "${cmd[@]}" > "$log_file" 2>&1; then
        echo "✅ PASSED"
        return 0
    else
        echo "❌ FAILED  (log: $log_file)"
        # Show the error line
        grep -i "error\|failed\|tohost\|sail" "$log_file" 2>/dev/null | tail -3 | sed 's/^/     /'
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Parse CSV and dispatch commands
# ---------------------------------------------------------------------------
first_line=true
while IFS=',' read -r feature machine supervisor user disabled sv39 sv48 sv57 \
                        bare_metal virtualized g_disabled g_sv39 g_sv48 g_sv57 \
                        extra_args repeat_times; do

    # Skip header
    if $first_line; then first_line=false; continue; fi

    # Trim whitespace
    feature="$(echo "$feature" | tr -d '[:space:]')"
    extra_args="$(echo "$extra_args" | tr -d '[:space:]')"
    repeat_times="$(echo "$repeat_times" | tr -d '[:space:]')"

    # Apply --test_plan filter
    if [[ -n "$TEST_PLAN" && "$feature" != "$TEST_PLAN" ]]; then continue; fi

    # Skip hypervisor features always
    skip=false
    for s in "${SKIP_ALWAYS[@]}"; do
        [[ "$feature" == "$s" ]] && skip=true && break
    done
    if $skip; then
        echo "  SKIP (hypervisor — Issue #2): $feature"
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
        continue
    fi

    echo ""
    echo "--- Feature: $feature ---"

    # Build privilege mode list
    priv_modes=()
    [[ "$machine"    == "x" ]] && priv_modes+=("machine")
    [[ "$supervisor" == "x" ]] && ! $MACHINE_ONLY && priv_modes+=("super")
    [[ "$user"       == "x" ]] && ! $MACHINE_ONLY && priv_modes+=("user")

    # Build paging mode list
    paging_modes=()
    [[ "$disabled" == "x" ]] && paging_modes+=("disable")
    [[ "$sv39"     == "x" ]] && paging_modes+=("sv39")
    [[ "$sv48"     == "x" ]] && paging_modes+=("sv48")
    [[ "$sv57"     == "x" ]] && paging_modes+=("sv57")

    # Build g-stage paging list (virtualized only)
    g_paging_modes=()
    [[ "$g_disabled" == "x" ]] && g_paging_modes+=("disable")
    [[ "$g_sv39"     == "x" ]] && g_paging_modes+=("sv39")
    [[ "$g_sv48"     == "x" ]] && g_paging_modes+=("sv48")
    [[ "$g_sv57"     == "x" ]] && g_paging_modes+=("sv57")

    # Generate seeds
    seeds=()
    for ((i=1; i<=SEED_COUNT; i++)); do
        seeds+=($((RANDOM * RANDOM + i)))
    done

    # Dispatch bare_metal combinations
    if [[ "$bare_metal" == "x" ]]; then
        for priv in "${priv_modes[@]}"; do
            # Validate: machine mode cannot have paging
            for paging in "${paging_modes[@]}"; do
                [[ "$priv" == "machine" && "$paging" != "disable" ]] && continue
                for seed in "${seeds[@]}"; do
                    if run_cmd "$feature" "$priv" "$paging" "bare_metal" "" "$extra_args" "$repeat_times" "$seed"; then
                        TOTAL_PASSED=$((TOTAL_PASSED + 1))
                    else
                        TOTAL_FAILED=$((TOTAL_FAILED + 1))
                        FAILED_CMDS+=("$feature/$priv/$paging/seed_$seed")
                    fi
                done
            done
        done
    fi

    # Dispatch virtualized combinations
    if [[ "$virtualized" == "x" ]] && ! $MACHINE_ONLY; then
        for priv in "${priv_modes[@]}"; do
            for paging in "${paging_modes[@]}"; do
                [[ "$priv" == "machine" && "$paging" != "disable" ]] && continue
                for g_paging in "${g_paging_modes[@]}"; do
                    for seed in "${seeds[@]}"; do
                        if run_cmd "$feature" "$priv" "$paging" "virtualized" "$g_paging" "$extra_args" "$repeat_times" "$seed"; then
                            TOTAL_PASSED=$((TOTAL_PASSED + 1))
                        else
                            TOTAL_FAILED=$((TOTAL_FAILED + 1))
                            FAILED_CMDS+=("$feature/$priv/$paging/virt_g${g_paging}/seed_$seed")
                        fi
                    done
                done
            done
        done
    fi

done < "$CSV_FILE"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
TOTAL=$((TOTAL_PASSED + TOTAL_FAILED))
echo ""
echo "=============================================="
echo "  SAIL ISS TEST RESULTS SUMMARY"
echo "=============================================="
printf "  PASSED:  %d\n" "$TOTAL_PASSED"
printf "  FAILED:  %d\n" "$TOTAL_FAILED"
printf "  SKIPPED: %d (hypervisor features)\n" "$TOTAL_SKIPPED"
printf "  TOTAL:   %d\n" "$TOTAL"
if [[ $TOTAL -gt 0 ]]; then
    printf "  Pass rate: %.1f%%\n" "$(echo "scale=1; $TOTAL_PASSED * 100 / $TOTAL" | bc)"
fi
if [[ ${#FAILED_CMDS[@]} -gt 0 ]]; then
    echo ""
    echo "  Failed combinations:"
    for f in "${FAILED_CMDS[@]}"; do
        echo "    ❌ $f"
    done
fi
echo ""
echo "  Skipped always (hypervisor — Issue #2):"
for f in "${SKIP_ALWAYS[@]}"; do echo "    - $f"; done
echo ""
echo "  Results in: $RESULTS_DIR"
echo "=============================================="

[[ $TOTAL_FAILED -eq 0 ]]
