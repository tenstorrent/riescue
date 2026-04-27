# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

#!/bin/bash
# Reads features.csv and generates riescuec tp-mode commands for all
# supported (privilege mode x paging mode x seed) combinations.
#
# Usage: ./generate_tp_tests.sh [--batch N] [--test_plan FEATURE] [--seed_count N]
#   --batch N          Run N commands in parallel at a time (default: 10)
#   --test_plan FEATURE  Only run the specified feature from features.csv
#   --seed_count N     Number of seeds to generate per test (default: 10)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV_FILE="${SCRIPT_DIR}/features.csv"
OUTPUT_FILE="${SCRIPT_DIR}/generated_commands.sh"

# Parse CLI args
BATCH_SIZE=10
TEST_PLAN=""
SEED_COUNT=10
while [[ $# -gt 0 ]]; do
    case "$1" in
        --batch)
            BATCH_SIZE="$2"
            shift 2
            ;;
        --test_plan)
            TEST_PLAN="$2"
            shift 2
            ;;
        --seed_count)
            SEED_COUNT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--batch N] [--test_plan FEATURE] [--seed_count N]"
            exit 1
            ;;
    esac
done

if [[ ! -f "$CSV_FILE" ]]; then
    echo "ERROR: $CSV_FILE not found"
    exit 1
fi

# Validate --test_plan against features.csv if provided
if [[ -n "$TEST_PLAN" ]]; then
    if ! tail -n +2 "$CSV_FILE" | cut -d',' -f1 | xargs -I{} echo {} | grep -qx "$TEST_PLAN"; then
        echo "ERROR: test_plan '$TEST_PLAN' not found in $CSV_FILE"
        exit 1
    fi
fi

echo "#!/bin/bash" > "$OUTPUT_FILE"
echo "# Auto-generated riescuec tp-mode commands" >> "$OUTPUT_FILE"
echo "# Generated on $(date)" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

total_cmds=0

# Read CSV, skip header
tail -n +2 "$CSV_FILE" | while IFS=',' read -r feature machine supervisor user disabled sv39 sv48 sv57 bare_metal virtualized g_disabled g_sv39 g_sv48 g_sv57 extra_args repeat_times; do
    # Skip empty lines
    [[ -z "$feature" ]] && continue

    # Trim whitespace
    feature=$(echo "$feature" | xargs)

    # If --test_plan is set, skip non-matching features
    [[ -n "$TEST_PLAN" && "$feature" != "$TEST_PLAN" ]] && continue

    # Build arrays of supported modes
    priv_modes=()
    [[ "$(echo "$machine" | xargs)" == "x" ]] && priv_modes+=("machine")
    [[ "$(echo "$supervisor" | xargs)" == "x" ]] && priv_modes+=("super")
    [[ "$(echo "$user" | xargs)" == "x" ]] && priv_modes+=("user")

    paging_modes=()
    [[ "$(echo "$disabled" | xargs)" == "x" ]] && paging_modes+=("disable")
    [[ "$(echo "$sv39" | xargs)" == "x" ]] && paging_modes+=("sv39")
    [[ "$(echo "$sv48" | xargs)" == "x" ]] && paging_modes+=("sv48")
    [[ "$(echo "$sv57" | xargs)" == "x" ]] && paging_modes+=("sv57")

    g_paging_modes=()
    [[ "$(echo "$g_disabled" | xargs)" == "x" ]] && g_paging_modes+=("disable")
    [[ "$(echo "$g_sv39" | xargs)" == "x" ]] && g_paging_modes+=("sv39")
    [[ "$(echo "$g_sv48" | xargs)" == "x" ]] && g_paging_modes+=("sv48")
    [[ "$(echo "$g_sv57" | xargs)" == "x" ]] && g_paging_modes+=("sv57")

    is_bare_metal="$(echo "$bare_metal" | xargs)"
    is_virtualized="$(echo "$virtualized" | xargs)"

    # Optional extra args
    extra_args_val=$(echo "$extra_args" | xargs)
    extra_suffix=""
    [[ -n "$extra_args_val" ]] && extra_suffix=" ${extra_args_val}"

    # Optional repeat_times suffix
    rt_suffix=""
    rt_val=$(echo "$repeat_times" | xargs)
    [[ -n "$rt_val" ]] && rt_suffix=" --repeat_times ${rt_val}"

    if [[ ${#priv_modes[@]} -eq 0 ]]; then
        echo "WARNING: No privilege modes marked for feature '$feature', skipping"
        continue
    fi
    if [[ ${#paging_modes[@]} -eq 0 ]]; then
        echo "WARNING: No paging modes marked for feature '$feature', skipping"
        continue
    fi

    # Count total commands for this feature to embed in progress prints
    total_count=0
    if [[ "$is_bare_metal" == "x" ]]; then
        for priv in "${priv_modes[@]}"; do
            for paging in "${paging_modes[@]}"; do
                ((total_count += SEED_COUNT))
            done
        done
    fi
    if [[ "$is_virtualized" == "x" && ${#g_paging_modes[@]} -gt 0 ]]; then
        for priv in "${priv_modes[@]}"; do
            for paging in "${paging_modes[@]}"; do
                for g_paging in "${g_paging_modes[@]}"; do
                    ((total_count += SEED_COUNT))
                done
            done
        done
    fi

    echo "# Feature: $feature ($total_count total commands)" >> "$OUTPUT_FILE"
    echo "mkdir -p ${feature}" >> "$OUTPUT_FILE"
    echo "COMPLETED_${feature}=0" >> "$OUTPUT_FILE"
    count=0

    # bare_metal tests: priv x paging (no --test_env flag, bare_metal is default)
    if [[ "$is_bare_metal" == "x" ]]; then
        for priv in "${priv_modes[@]}"; do
            for paging in "${paging_modes[@]}"; do
                if [[ "$priv" == "machine" ]]; then
                    run_dir="${feature}/${priv}"
                else
                    run_dir="${feature}/${priv}_${paging}"
                fi
                echo "mkdir -p ${run_dir}" >> "$OUTPUT_FILE"
                for seed in $(seq 1 $SEED_COUNT); do
                    stdout_log="${run_dir}/tp_${feature}_${seed}_stdout.log"
                    stderr_log="${run_dir}/tp_${feature}_${seed}_stderr.log"
                    echo "riescuec --mode tp --test_plan ${feature} --test_paging_mode ${paging} --test_priv_mode ${priv} --seed ${seed}${extra_suffix}${rt_suffix} --run_dir ${run_dir} > ${stdout_log} 2> ${stderr_log} &" >> "$OUTPUT_FILE"
                    ((count++))
                    if (( count % BATCH_SIZE == 0 )); then
                        echo "wait" >> "$OUTPUT_FILE"
                        echo "COMPLETED_${feature}=$count" >> "$OUTPUT_FILE"
                        echo "echo \"[${feature}] Progress: \${COMPLETED_${feature}}/${total_count} commands completed\"" >> "$OUTPUT_FILE"
                    fi
                done
            done
        done
    fi

    # virtualized tests: priv x paging x g_paging (with --test_env virtualized)
    if [[ "$is_virtualized" == "x" && ${#g_paging_modes[@]} -gt 0 ]]; then
        for priv in "${priv_modes[@]}"; do
            for paging in "${paging_modes[@]}"; do
                for g_paging in "${g_paging_modes[@]}"; do
                    run_dir="${feature}/virtualized/${priv}_${paging}_g${g_paging}"
                    echo "mkdir -p ${run_dir}" >> "$OUTPUT_FILE"
                    for seed in $(seq 1 $SEED_COUNT); do
                        stdout_log="${run_dir}/tp_${feature}_${seed}_stdout.log"
                        stderr_log="${run_dir}/tp_${feature}_${seed}_stderr.log"
                        echo "riescuec --mode tp --test_plan ${feature} --test_paging_mode ${paging} --test_paging_g_mode ${g_paging} --test_priv_mode ${priv} --test_env virtualized --seed ${seed}${extra_suffix}${rt_suffix} --run_dir ${run_dir} > ${stdout_log} 2> ${stderr_log} &" >> "$OUTPUT_FILE"
                        ((count++))
                        if (( count % BATCH_SIZE == 0 )); then
                            echo "wait" >> "$OUTPUT_FILE"
                            echo "COMPLETED_${feature}=$count" >> "$OUTPUT_FILE"
                            echo "echo \"[${feature}] Progress: \${COMPLETED_${feature}}/${total_count} commands completed\"" >> "$OUTPUT_FILE"
                        fi
                    done
                done
            done
        done
    fi

    # Final wait for any remaining commands in the last partial batch
    if (( count % BATCH_SIZE != 0 )); then
        echo "wait" >> "$OUTPUT_FILE"
        echo "COMPLETED_${feature}=$count" >> "$OUTPUT_FILE"
        echo "echo \"[${feature}] Progress: \${COMPLETED_${feature}}/${total_count} commands completed\"" >> "$OUTPUT_FILE"
    fi
    echo "" >> "$OUTPUT_FILE"
    echo "  $feature: $count commands generated (batch size: $BATCH_SIZE)"
done

chmod +x "$OUTPUT_FILE"
echo ""
echo "Commands saved to: $OUTPUT_FILE"
echo "Executing generated commands..."
echo ""
bash "$OUTPUT_FILE"

# Cleanup
rm -f riescuec_tp.testlog

# Move generated_commands.sh into each feature folder
tail -n +2 "$CSV_FILE" | while IFS=',' read -r feature _rest; do
    feature=$(echo "$feature" | xargs)
    [[ -n "$TEST_PLAN" && "$feature" != "$TEST_PLAN" ]] && continue
    [[ -n "$feature" && -d "$feature" ]] && cp "$OUTPUT_FILE" "$feature/generated_commands.sh"
done
rm -f "$OUTPUT_FILE"

# Report pass/fail results
echo ""
echo "========================================="
echo "          TEST RESULTS SUMMARY"
echo "========================================="

passed=0
failed=0
failed_files=()

for stderr_log in $(find . -name "*_stderr.log" -type f 2>/dev/null); do
    if grep -q "PASSED" "$stderr_log"; then
        ((passed++))
    elif grep -q "FAILED" "$stderr_log"; then
        ((failed++))
        failed_files+=("$stderr_log")
    fi
done

total=$((passed + failed))

echo "PASSED: $passed"
echo "FAILED: $failed"
echo "TOTAL:  $total"

if [[ $total -gt 0 ]]; then
    pass_rate=$(awk "BEGIN {printf \"%.1f\", ($passed/$total)*100}")
    fail_rate=$(awk "BEGIN {printf \"%.1f\", ($failed/$total)*100}")
    echo ""
    echo "Pass rate: ${pass_rate}%"
    echo "Fail rate: ${fail_rate}%"
fi

if [[ ${#failed_files[@]} -gt 0 ]]; then
    echo ""
    echo "FAILED test stderr logs:"
    for f in "${failed_files[@]}"; do
        echo "  $f"
    done
fi

echo "========================================="