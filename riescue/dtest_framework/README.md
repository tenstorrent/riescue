# RiescueD - Directed Test Framework

RiescueD is a Python library serving as a Directed Test Framework for RISC-V, offering a comprehensive set of tools for writing and generating assembly tests.

## Features

### Core Features
1. **OS Code Simulation**
   - Pseudo OS for scheduling
   - Exception handling
   - Pass/fail conditions

2. **Memory Management**
   - Random address generation
   - Page table generation
   - Memory mapping support

3. **Environment Randomization**
   - Privilege modes (machine/supervisor/user)
   - Paging modes (sv39/sv48/sv57/bare)
   - Virtualization modes (bare metal/virtualized)

4. **RISC-V Features**
   - RVA23 extension support
   - CSR management
   - Feature configuration

## Workflow

### Test Generation Process
1. Write `.s` file using assembly with RiescueD Directives
2. Provide system memory map and configuration in cpuconfig JSON file
3. Run RiescueD to produce:
   - `.S` assembly file
   - `.ld` Linker Script
   - ELF binary

### Writing Tests
Here's a minimal example of a RiescueD test:
```s
;#test.name       sample_test
;#test.author     name@email.com
;#test.arch       rv64
;#test.priv       machine super user any
;#test.env        virtualized bare_metal
;#test.cpus       1
;#test.paging     sv39 sv48 sv57 disable any
;#test.category   arch
;#test.class      vector
;#test.features   ext_v.enable ext_fp.disable
;#test.tags       vectors vector_ld_st

.section .code, "ax"
test_setup:
    # Common initialization sequence
    j passed

;#discrete_test(test=test01)
test01:
    nop
    beq x0, t0, failed
    j passed

test_cleanup:
    # Common cleanup sequence
    j passed

```

## Usage

### Command Line Interface
```bash
riescue_d.py [options] <test_file.s>
```

### Key Options
1. **Environment Configuration**
   ```bash
   --paging_mode <mode>      # Set paging mode (sv39/sv48/sv57/disable/any)
   --privilege_mode <mode>   # Set privilege mode (machine/supervisor/user/any)
   --test_env <env>         # Set test environment (bare_metal/virtualized)
   --cpuconfig <file>       # Path to CPU feature configuration file
   ```

2. **Output Control**
   ```bash
   --output_file <name>     # Specify output filename
   --output_format <fmt>    # Set output format (all/s/ld/dis/log)
   --run_dir <dir>         # Set run directory (default: ./)
   ```

### Example Usage
```bash
# Basic test generation
riescue_d.py test.s --cpuconfig cpu_config.json

# Test with specific environment
riescue_d.py test.s \
  --paging_mode sv39 \
  --privilege_mode machine \
  --test_env bare_metal \
  --cpuconfig cpu_config.json
```

## Configuration

### CPU Configuration File
Example `cpu_config.json`:
```json
{
    "memory_map": {
        "ram": {
            "start": "0x80000000",
            "size": "0x10000000"
        }
    },
    "features": {
        "ext_v": true,
        "ext_fp": false
    }
}
```

## Use Cases

### 1. Quick Assembly Test Development
- Random address generation for load/store instructions
- Random data generation with RiescueD Directives
- Automatic page table generation
- Environment randomization

### 2. Test Generator Development
- Reusable components for random test generation
- Address generation
- Page table generation
- Exception handling
- Environment constraints

## Requirements
- RISC-V GNU toolchain
- Supported simulators (whisper, spike)
- Python 3.9+

For detailed installation instructions, see the [main README](../../README.md).

[Detailed RiescueD User Guide](https://github.com/tenstorrent/riescue/blob/main/docs/public_source/user_guides/riescued_tutorial.rst)
