# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import argparse
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Command line arguments for ``FeatMgr``.  Runtime arguments for ``RiescueD`` are in ``riescued.py``

    All arguments should default to None, so the CLI adapter can set them to the default values.

    """

    parser.add_argument(
        "--tohost_nonzero_terminate",
        "-tnt",
        action="store_true",
        help="Spike option to be forwarded. Ends simulation immediately when tohost becomes nonzero.",
    )
    parser.add_argument(
        "--counter_event_path",
        type=Path,
        help="path to counter event file, used to randomize events in counter files",
    )

    logger_args = parser.add_argument_group("Logger", "Arguments to pass to logger")
    logger_args.add_argument(
        "--max_logger_file_gb",
        default=None,
        help="Max size of testlog file in GB",
        type=float,
    )

    test_env_args = parser.add_argument_group(
        "Test Environment", "Arguments that control test environment. Will override Riescue Directives in test file \ne.g.\t--test_priv_mode user \nwill take precedence over \n\t;#test.priv any"
    )
    test_env_args.add_argument(
        "--test_priv_mode",
        default=None,
        help="Specify privilege mode for the test to be forced. e.g. \n\t--test_priv_mode user. Legal values are: user, super, machine",
        type=str,
    )
    test_env_args.add_argument(
        "--test_paging_mode",
        default=None,
        help="Specify paging mode for the test to be forced. e.g. \n\t--test_paging_mode sv57",
        type=str,
    )
    test_env_args.add_argument(
        "--test_paging_g_mode",
        default=None,
        help="Specify g-stage paging mode for the test to be forced. e.g. \n\t--test_paging_g_mode sv57",
        type=str,
    )
    test_env_args.add_argument(
        "--test_env",
        default=None,
        help="Specify environment for the test to be forced. e.g. \n\t--test_env bare_metal",
        choices=["bare_metal", "virtualized"],
        type=str,
    )
    test_env_args.add_argument(
        "--test_env_any",
        action="store_true",
        default=None,
        help="Allow test environment to be randomly selected between bare_metal and virtualized if any is specified",
    )
    test_env_args.add_argument(
        "--test_secure_mode",
        default=None,
        choices=["on", "off", "random", "any"],
        help="Specify secure mode for the test to be forced. e.g. \n\t--test_secure_mode random \nIf random or any is selected then the probability of secure mode is 20%%",
        type=str,
    )
    test_env_args.add_argument(
        "--supported_priv_modes",
        default=None,
        help="Specify privilege modes supported by the platform. e.g. \n\t--supported_priv_modes MSU. Default to MSU if not specified",
        choices=["MSU", "MS", "MU", "M"],
        type=str,
    )

    eot_args = parser.add_argument_group("End of Test", "Arguments that affect the end of test")
    eot_args.add_argument(
        "--tohost",
        default=None,
        help="Hardcoded address for tohost and HTIF IO section; Leaving blank uses value in memap, if none in memmap puts as variables at end of os_code",
        type=str,
    )
    eot_args.add_argument(
        "--eot_pass_value",
        default=None,
        help="Sets the end of test pass value; defaults to 1",
        type=lambda x: int(x, 0),
    )
    eot_args.add_argument(
        "--eot_fail_value",
        default=None,
        help="Sets the end of test fail value; defaults to 1",
        type=lambda x: int(x, 0),
    )

    mp_args = parser.add_argument_group("Multiprocessor", "Arguments for multiprocessor tests")
    mp_args.add_argument(
        "--mp",
        default=None,
        help="Overrides MP enablement in cpuconfig file or overridden in test file. Legal values are: 'on', 'off'",
        type=str,
    )
    mp_args.add_argument(
        "--mp_mode",
        default=None,
        help="Overrides MP mode provided in cpuconfig file or overridden in test file. Legal values are: 'simultaneous', 'parallel'",
        type=str,
    )
    mp_args.add_argument(
        "--parallel_scheduling_mode",
        default=None,
        help="Overrides parallel scheduling mode provided in cpuconfig file or overridden in test file. Legal values are: 'round_robin', 'exhaustive'",
        type=str,
    )
    mp_args.add_argument(
        "--num_cpus",
        default=None,
        help="Overrides number of CPUs provided in cpuconfig file or overridden in test file. Legal values are positive integers",
        type=int,
    )

    test_generation_args = parser.add_argument_group("Test Generation", "Arguments that affect test generation")
    test_generation_args.add_argument(
        "--single_assembly_file",
        "-saf",
        action="store_true",
        default=None,
        help="Indicates that all assembly is written to a single file. Ignoring this option means writing to a handful of .inc files",
    )
    test_generation_args.add_argument(
        "--force_alignment",
        action="store_true",
        default=None,
        help="Forces all data and code to be byte-aligned; Removes --misaligned from spike run_iss's args",
    )
    test_generation_args.add_argument(
        "--c_used",
        action="store_true",
        default=None,
        help="Use C sections when generating code",
    )
    test_generation_args.add_argument(
        "--small_bss",
        action="store_true",
        default=None,
        help="Sets bss section to 1200 4kb page instead of default",
    )
    test_generation_args.add_argument(
        "--big_bss",
        action="store_true",
        default=None,
        help="Sets bss section to 3080 4kb pages instead of default",
    )
    test_generation_args.add_argument(
        "--big_endian",
        "-big_e",
        action="store_true",
        default=None,
        help="Mode for enabling big-endian for cross-compilers and ISS",
    )
    test_generation_args.add_argument(
        "--more_os_pages",
        action="store_true",
        default=None,
        help="Ask riescued to generate more code and os_stack pages for long tests",
    )
    test_generation_args.add_argument(
        "--add_gcc_cstdlib_sections",
        action="store_true",
        default=None,
        help="Add a gcc cstdlib section to the test for each library function as GCC likes to do.",
    )
    test_generation_args.add_argument(
        "--addrgen_limit_indices",
        action="store_true",
        default=None,
        help="Limit addrgen to not generate more than 4 addresses with the same index",
    )
    test_generation_args.add_argument(
        "--code_offset",
        default=None,
        help="Specify code offset where the test code should start. Default: Randomized 0-140h",
        type=int,
    )
    test_generation_args.add_argument(
        "--randomize_code_location",
        action="store_true",
        default=None,
        help="Randomize the code location in the test. Default: follows .text section",
    )
    test_generation_args.add_argument(
        "--repeat_times",
        "-rt",
        default=None,
        help="Number of times each discrete test should be run",
        type=int,
    )

    new_args = parser.add_argument_group("New Arguments", "Arguments that are not yet fully enabled in FeatMgr")
    new_args.add_argument("--private_maps", action="store_true", default=None, help="Setup isolated page map address spaces")
    new_args.add_argument("--cfile", type=Path, action="append", default=None, help="Use runtime c-files that must be specified with this arg. Can be specified multiple times")

    new_args.add_argument(
        "--enable_machine_paging",
        action="store_true",
        default=None,
        help="Enable Machine privilege + paging mode for the test. Guest paging mode disabled",
    )

    bringup_args = parser.add_argument_group("Bringup", "Arguments for bringing up tests with minimal OS code")
    bringup_args.add_argument(
        "--fe_tb",
        "-fe_tb",
        action="store_true",
        default=None,
        help="Special considerations for FE testbench. They really can't know taken vs not-taken branches. So, jump to passed|failed is same for them. So, making passed=failed label",
    )
    bringup_args.add_argument(
        "--wysiwyg",
        action="store_true",
        default=None,
        help="'What You See Is What You Get mode; no OS code is added, only the test code is executed as it is. Intended for environments not monitoring tohost writes",
    )
    bringup_args.add_argument(
        "--linux_mode",
        action="store_true",
        default=None,
        help="Generate riescued OS code and prepare test to run it in the Linux environment. Runs endlessly",
    )
    bringup_args.add_argument(
        "--bringup_pagetables",
        action="store_true",
        default=None,
        help="Implies --wysiwyg and --addrgen_limit_indices, but enables switch to super and paging in the loader code",
    )

    addrgen_args = parser.add_argument_group("Address Generation", "Arguments that affect address generation")
    addrgen_args.add_argument(
        "--reserve_partial_phys_memory",
        action="store_true",
        default=None,
        help="\n".join(
            [
                "By default, Riescue-D will only reserve full physical address size specified by ;page_mapping(pagesize) or ;random_addr(size).",
                "If you want Riescued to only reserve 4kb size for all the addresses, use this option",
            ]
        ),
    )
    addrgen_args.add_argument(
        "--all_4kb_pages",
        action="store_true",
        default=None,
        help="Ask riescued to generate all 4KB pages",
    )
    addrgen_args.add_argument(
        "--disallow_mmio",
        action="store_true",
        default=None,
        help="Disallow MMIO in the test",
    )
    addrgen_args.add_argument(
        "--addrgen_limit_way_predictor_multihit",
        "-ag_limit_wp_multihit",
        action="store_true",
        default=None,
        help="Limit addrgen to not generate address with multi-hit in the way predictor",
    )

    trap_handler_args = parser.add_argument_group("Test OS Code", "Argumnets that affect Test OS code generation")
    trap_handler_args.add_argument(
        "--deleg_excp_to",
        "-deleg_excp_to",
        default=None,
        choices=["machine", "super"],
        help="Specify privilege where exceptions are handled. Default: machine or super (random)",
        type=str,
    )
    trap_handler_args.add_argument(
        "--switch_to_machine_page",
        "-switch_to_machine_page",
        default=None,
        help="Specify the page where the handler will return control after using ecall function 0xf0010001",
        type=str,
    )
    trap_handler_args.add_argument(
        "--switch_to_super_page",
        "-switch_to_super_page",
        default=None,
        help="Specify the page where the handler will return control after using ecall function 0xf0010002",
        type=str,
    )
    trap_handler_args.add_argument(
        "--switch_to_user_page",
        "-switch_to_user_page",
        default=None,
        help="Specify the page where the handler will return control after using ecall function 0xf0010003",
        type=str,
    )
    trap_handler_args.add_argument(
        "--user_interrupt_table",
        action="store_true",
        default=None,
        help="Overrides jump to default interrupt table with user symbol 'USER_INTERRUPT_TABLE'",
    )
    trap_handler_args.add_argument(
        "--excp_hooks",
        "-excp_hooks",
        action="store_true",
        default=None,
        help="\n".join(
            [
                "Insert exception handler hooks. RiescueD will call excp_handler_pre: and excp_handler_post:",
                "functions right before entering the exception hndler and after before returning from exception handler respectively",
            ]
        ),
    )
    trap_handler_args.add_argument(
        "--interrupts_enabled",
        "-ie",
        action="store_true",
        default=None,
        help="Enable interrupts",
    )
    trap_handler_args.add_argument(
        "--skip_instruction_for_unexpected",
        action="store_true",
        default=None,
        help="Ambigious name - this should be something like 'ignore unexpected exceptions'.",
    )
    trap_handler_args.add_argument(
        "--disable_wfi_wait",
        "-disable_wfi_wait",
        action="store_true",
        default=None,
        help="[Required for MP spike runs] Disable wfi wait in sync loops.",
    )

    pma_pmp_args = parser.add_argument_group("PMA/PMP", "Arguments for PMA/PMP tests")
    pma_pmp_args.add_argument(
        "--setup_pmp",
        action="store_true",
        default=None,
        help="Ask riescued to setup PMP registers",
    )
    pma_pmp_args.add_argument(
        "--needs_pma",
        action="store_true",
        default=None,
        help="Indicates if the test wants to enable PMA functionality of Riescue-D",
    )
    pma_pmp_args.add_argument(
        "--num_pmas",
        default=None,
        help="Number of PMACFG registers implemented. Default is 16. Changing this number requires an update in the whisper_config.json",
        type=int,
    )

    hypervisor_args = parser.add_argument_group("Hypervisor", "Arguments for hypervisor tests")
    hypervisor_args.add_argument(
        "--vmm_hooks",
        "-vmm_hooks",
        action="store_true",
        default=None,
        help="Insert vmm hooks in the virtualized mode. RiescueD will call vmm_handler_pre: and vmm_handler_post: functions before launching the guest and after exiting from guest respectively",
    )
    hypervisor_args.add_argument(
        "--setup_stateen",
        action="store_true",
        default=None,
        help="Ask riescued to setup stateen registers as per smstateen extension",
    )

    csr_init_args = parser.add_argument_group("CSR Initialization", "")
    csr_init_args.add_argument(
        "--csr_init",
        action="append",
        default=None,
        help="Initialize a CSR with a value. Format: 'csr_name=value' or 'csr_number=value'. Can be specified multiple times. Example: --csr_init mstatus=0x8000 --csr_init 0x300=0x1",
        type=str,
    )
    csr_init_args.add_argument(
        "--csr_init_mask",
        action="append",
        default=None,
        help="\n".join(
            [
                "Initialize a CSR using read-modify-write with a mask. Format: 'csr_name=mask=value' or 'csr_number=mask=value'. Can be specified multiple times.",
                "Example: --csr_init_mask mstatus=0x8000=0x8000",
            ]
        ),
        type=str,
    )
    csr_init_args.add_argument(
        "--no_random_csr_reads",
        "-no_random_csr_reads",
        action="store_true",
        default=None,
        help="Disable random CSR read randomization that happens in the OS scheduler code",
    )
    csr_init_args.add_argument(
        "--max_random_csr_reads",
        "-max_random_csr_read",
        default=None,
        help="Maximum number of CSRs read to inject for the randomization. Default: 16. Minimum: 3.",
        type=int,
    )
    csr_init_args.add_argument(
        "--random_machine_csr_list",
        "-random_machine_csr_list",
        default=None,
        help="List csr name that the CSR read randomization logic must include when OS is in machine mode. Specify list this: --random_machine_csr_list mstatus,mcause FIXME: use choices",
        type=str,
    )
    csr_init_args.add_argument(
        "--random_supervisor_csr_list",
        "-random_supervisor_csr_list",
        default=None,
        help="List csr name that the CSR read randomization logic must include when OS is in supervisor/machine mode. Specify list this: --random_supervisor_csr_list sstatus,scause",
        type=str,
    )
    csr_init_args.add_argument(
        "--random_user_csr_list",
        "-random_user_csr_list",
        default=None,
        help="List csr name that the CSR read randomization logic must include when OS is in user/supervisor/machine mode. Specify list this: --random_supervisor_csr_list fcsr,time",
        type=str,
    )

    csr_init_args.add_argument(
        "--medeleg",
        default=None,
        help="Override medeleg when --test_env supervisor/user",
        type=lambda x: int(x, 16),
    )
    csr_init_args.add_argument(
        "--mideleg",
        default=None,
        help="Override mideleg when --test_env supervisor/user",
        type=lambda x: int(x, 16),
    )
    csr_init_args.add_argument(
        "--hedeleg",
        default=None,
        help="Override hedeleg when '--test_env supervisor/user' and '--test_env virtualized' in VS mode OS",
        type=lambda x: int(x, 16),
    )
    csr_init_args.add_argument(
        "--hideleg",
        default=None,
        help="Override hideleg when '--test_env supervisor/user' and '--test_env virtualized' in VS mode OS",
        type=lambda x: int(x, 16),
    )
    csr_init_args.add_argument(
        "--menvcfg",
        default=None,
        help="Override menvcfg value to write in loader",
        type=lambda x: int(x, 16),
    )
    csr_init_args.add_argument(
        "--henvcfg",
        default=None,
        help="Override henvcfg value to write in loader",
        type=lambda x: int(x, 16),
    )
    csr_init_args.add_argument(
        "--senvcfg",
        default=None,
        help="Override senvcfg value to write in loader. Default 0x0.",
        type=lambda x: int(x, 16),
    )

    test_probability_args = parser.add_argument_group("Test Probability", "Arguments that affect test probability")
    test_probability_args.add_argument(
        "--secure_access_probability",
        "-sap",
        default=None,
        help="Probability of secure access in the test. Default is 30%%",
        type=int,
    )
    test_probability_args.add_argument(
        "--secure_pt_probability",
        default=None,
        help="Probability of secure pagetable in the test",
        type=int,
    )
    test_probability_args.add_argument(
        "--a_d_bit_randomization",
        default=None,
        help="Probability of randomizing A and D bits in page table entries (0-100)",
        type=int,
    )
    test_probability_args.add_argument(
        "--pbmt_ncio_randomization",
        default=None,
        help="Probability of randomizing PBMT NC vs IO bits in page table entries (0-100)",
        type=int,
    )
