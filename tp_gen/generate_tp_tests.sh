# SPDX-FileCopyrightText: © 2026 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

#!/bin/bash
# Reads features.csv and generates riescuec tp-mode commands for all
# supported (privilege mode x paging mode x seed) combinations.
#
# Usage: ./generate_tp_tests.sh [--batch N] [--test_plan FEATURE] [--seed_count N] [--save_intermediate_files]
#   --batch N                  Run N commands in parallel at a time (default: 10)
#   --test_plan FEATURE        Only run the specified feature from features.csv
#   --seed_count N             Number of seeds to generate per test (default: 2)
#   --save_intermediate_files  Keep intermediate build files (.o, .ld, .dis, .inc, logs, etc.)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV_FILE="${SCRIPT_DIR}/features.csv"
OUTPUT_FILE="${SCRIPT_DIR}/generated_commands.sh"

# Parse CLI args
BATCH_SIZE=10
TEST_PLAN=""
SEED_COUNT=2
SAVE_INTERMEDIATE=false
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
        --save_intermediate_files)
            SAVE_INTERMEDIATE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--batch N] [--test_plan FEATURE] [--seed_count N] [--save_intermediate_files]"
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
echo "mkdir -p testsuite" >> "$OUTPUT_FILE"

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

    # Per-feature intermediate file with only the riescuec commands
    FEATURE_CMD_FILE="${SCRIPT_DIR}/${feature}_tp_commands.sh"
    > "$FEATURE_CMD_FILE"

    echo "# Feature: $feature ($total_count total commands)" >> "$OUTPUT_FILE"
    echo "mkdir -p testsuite/${feature}" >> "$OUTPUT_FILE"
    echo "COMPLETED_${feature}=0" >> "$OUTPUT_FILE"
    count=0

    # bare_metal tests: priv x paging (no --test_env flag, bare_metal is default)
    if [[ "$is_bare_metal" == "x" ]]; then
        for priv in "${priv_modes[@]}"; do
            for paging in "${paging_modes[@]}"; do
                if [[ "$priv" == "machine" ]]; then
                    run_dir="testsuite/${feature}/${priv}"
                else
                    run_dir="testsuite/${feature}/${priv}_${paging}"
                fi
                echo "mkdir -p ${run_dir}" >> "$OUTPUT_FILE"
                for seed in $(seq 1 $SEED_COUNT); do
                    stdout_log="${run_dir}/tp_${feature}_${seed}_stdout.log"
                    stderr_log="${run_dir}/tp_${feature}_${seed}_stderr.log"
                    echo "riescuec --mode tp --test_plan ${feature} --print_rvcp_passed --print_rvcp_failed --test_paging_mode ${paging} --test_priv_mode ${priv} --seed ${seed}${rt_suffix}${extra_suffix} --run_dir ${run_dir} > ${stdout_log} 2> ${stderr_log} &" >> "$OUTPUT_FILE"
                    echo "riescuec --mode tp --test_plan ${feature} --print_rvcp_passed --print_rvcp_failed --test_paging_mode ${paging} --test_priv_mode ${priv} --seed ${seed}${rt_suffix}${extra_suffix} --run_dir ${run_dir}" >> "$FEATURE_CMD_FILE"
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
                    run_dir="testsuite/${feature}/virtualized/${priv}_${paging}_g${g_paging}"
                    echo "mkdir -p ${run_dir}" >> "$OUTPUT_FILE"
                    for seed in $(seq 1 $SEED_COUNT); do
                        stdout_log="${run_dir}/tp_${feature}_${seed}_stdout.log"
                        stderr_log="${run_dir}/tp_${feature}_${seed}_stderr.log"
                        echo "riescuec --mode tp --test_plan ${feature} --print_rvcp_passed --print_rvcp_failed --test_paging_mode ${paging} --test_paging_g_mode ${g_paging} --test_priv_mode ${priv} --test_env virtualized --seed ${seed}${rt_suffix}${extra_suffix} --run_dir ${run_dir} > ${stdout_log} 2> ${stderr_log} &" >> "$OUTPUT_FILE"
                        echo "riescuec --mode tp --test_plan ${feature} --print_rvcp_passed --print_rvcp_failed --test_paging_mode ${paging} --test_paging_g_mode ${g_paging} --test_priv_mode ${priv} --test_env virtualized --seed ${seed}${rt_suffix}${extra_suffix} --run_dir ${run_dir}" >> "$FEATURE_CMD_FILE"
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
    mkdir -p "testsuite/${feature}"
    cp "$FEATURE_CMD_FILE" "testsuite/${feature}/${feature}_tp_commands.sh"
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
rm -f "$OUTPUT_FILE"
rm -f "${SCRIPT_DIR}"/*_tp_commands.sh

# Report pass/fail results
echo ""
echo "========================================="
echo "          TEST RESULTS SUMMARY"
echo "========================================="

passed=0
failed=0
failed_files=()

for stderr_log in $(find testsuite -name "*_stderr.log" -type f 2>/dev/null); do
    if grep -q "PASSED" "$stderr_log"; then
        ((passed++))
    else
        ((failed++))
        # Point to the intermediate folder location where the log will be moved
        log_dir=$(dirname "$stderr_log")
        log_name=$(basename "$stderr_log")
        seed_basename="${log_name%_stderr.log}"
        failed_files+=("${log_dir}/${seed_basename}_intermediate/${log_name}")
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

# Organize files: keep only .S and ELF in run_dir, move rest to intermediate subfolder
echo ""
echo "Organizing output files..."
mkdir -p testsuite
# Build list of unique seed basenames from stderr logs (these always exist for every seed)
for stderr_log in $(find testsuite -name "*_stderr.log" -type f 2>/dev/null); do
    log_dir=$(dirname "$stderr_log")
    log_name=$(basename "$stderr_log")
    # Strip _stderr.log suffix to get the seed basename (e.g., tp_paging_1)
    seed_basename="${log_name%_stderr.log}"
    intermediate_dir="${log_dir}/${seed_basename}_intermediate"
    mkdir -p "$intermediate_dir"
    # Move all files matching the seed prefix with extensions, except .S and the ELF
    for f in "${log_dir}/${seed_basename}".*; do
        [[ ! -e "$f" ]] && continue
        [[ "$f" == *.S ]] && continue
        mv "$f" "$intermediate_dir/"
    done
    # Move _suffix files (e.g., tp_paging_1_whisper.log, tp_paging_1_equates.inc)
    for f in "${log_dir}/${seed_basename}_"*; do
        [[ ! -e "$f" ]] && continue
        [[ -d "$f" ]] && continue
        mv "$f" "$intermediate_dir/"
    done
    # Move shared header files if present
    mv "${log_dir}/rvmodel_macros.h" "$intermediate_dir/" 2>/dev/null
    mv "${log_dir}/riescue_aplic_mmr.h" "$intermediate_dir/" 2>/dev/null
    # Keep intermediate files if test failed or --save_intermediate_files was passed
    test_failed=false
    if ! grep -q "PASSED" "$intermediate_dir/${seed_basename}_stderr.log" 2>/dev/null; then
        test_failed=true
    fi
    if [[ "$SAVE_INTERMEDIATE" == "false" && "$test_failed" == "false" ]]; then
        rm -rf "$intermediate_dir"
    fi
done
echo "Done."