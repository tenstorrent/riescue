# RiescueC - RISC-V Compliance Test Generator

RiescueC is a Python library that generates self-checking compliance tests for RISC-V instruction set extensions, providing comprehensive instruction coverage and verification capabilities.

## Features

### Core Features
1. **Self-Checking Test Generation**
   - Two-pass test methodology for golden reference generation
   - Automatic instruction verification against ISS execution
   - Support for multiple instruction set extensions

2. **Instruction Coverage**
   - Extension-based test selection (I, M, F, D, V, Zb*, etc.)
   - Group-based instruction organization
   - Individual instruction inclusion/exclusion control

3. **Multi-ISS Support**
   - Spike and Whisper ISS integration
   - Configurable first/second pass ISS selection
   - Cross-ISS verification and comparison

4. **RISC-V Extension Support**
   - Base integer extensions (RV32I, RV64I)
   - Standard extensions (M, F, D, V, A, C)
   - Bit manipulation extensions (Zba, Zbb, Zbc, Zbs, Zb)
   - Half-precision floating point (Zfh, Zfbfmin)



### Configuration Format
```json
{
    "arch": "rv64",
    "include_extensions": ["i_ext", "m_ext"],
    "include_groups": ["rv64i_compute_register_register"],
    "include_instrs": ["add", "sub"],
    "exclude_instrs": ["wfi", "ebreak", "mret"]
}
```


## Use Cases

### 1. Instruction Set Compliance Verification
- Systematic testing of RISC-V instruction behavior
- Cross-ISS verification for implementation consistency
- Extension-specific compliance validation

### 2. Regression Testing
- Automated test generation for continuous integration
- Configurable instruction coverage for targeted testing
- Scalable test suite generation

### 3. ISS Validation
- Compare behavior between different instruction set simulators
- Identify implementation discrepancies
- Validate new ISS implementations

## Requirements
- RISC-V GNU toolchain
- Supported simulators (Spike, Whisper)
- Python 3.9+
- RiescueD framework (for test execution)

For detailed installation instructions, see the [main README](../../README.md).
