# RiESCUE C

The RiESCUE Compliance suite is used to generate self-checking tests for a given set of extensions. The suite is ran using the `riescue_c.py` wrapper script found at the top of the repo.


# Running RiESCUE C
The RiESCUE C suite is started by calling the wrapper script at the top of the repo using the `Apptainer` container that can be launched with:
```
./infra/container-run "./riescue_c.py <arguments>"
```
* NOTE: Quotes should be added after the container run call to ensure they get passed through in the container.

## `test.json`
The `riescue_c.py` script requires a `--json` flag with a JSON file be passed in to configure the test. An example `test.json` looks like:
```json
{
    "arch" : "rv64",
    "include_extensions" : [
        "i_ext"
    ],
    "include_groups" : [
    ],
    "include_instrs" : [
    ],
    "exclude_groups" : [
    ],
    "exclude_instrs" : [
        "wfi", "ebreak", "mret", "sret", "ecall", "fence", "fence.i", "c.ebreak"
    ]
}
```
Which includes the `arch` and information about the instructions to be tested. The included instructions can be selected as candidates for self-checking tests. E.g. the `addi` being in the `include_instrs` category means it can be selected as a possible instruction to verify.

Extensions roughly correspond to a RISC-V extension, e.g. `V` or `M`. `groups` are a unique set of instructions specific to an extension. E.g. `rv64i_compute_register_register` contains `rv64i` instructions related to the register-register formatting like `addw`, `subw`. Instructions are the whole plain text instruction name and can be selectively included or excluded.

## Example
`test.json` files for each of the different supported RISC-V extensions can be found in the `compliance/tests/` directory and ran using the relative path to the file. For example:
```
./infra/container-run "riescue_c.py --json compliance/tests/rv_i/rv64i.json
```
Will generate self-checking tests for 64-bit I extension instructions.

## Finding out more information about extensions, groups and instructions
More information can be found using the `lib/instr_info/instr_lookup_json.py` script. It contains info on the mapping of Riescue extensions to groups and instructions.

## Additional Command line options
Command line options can be viewed using
```
./infra/container-run "./riescue_c.py --help"
```