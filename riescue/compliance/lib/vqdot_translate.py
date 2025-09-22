# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


def translate_vqdot_string_to_word(line) -> str:

    table = {
        "vqdot.vv": 0xB000205B,
        "vqdot.vx": 0xB000605B,
        "vqdotu.vv": 0xA000205B,
        "vqdotu.vx": 0xA000605B,
        "vqdotsu.vv": 0xA800205B,
        "vqdotsu.vx": 0xA800605B,
        "vqdotus.vx": 0xB800605B,
    }

    stripped = line.strip()
    assert stripped.startswith("vqdot"), f"Invalid instruction: {stripped}"

    instr = stripped.split(" ")[0]  # First string in line is the instruction
    assert instr in table, f"Invalid instruction: {instr}"

    op_str = stripped.replace(instr, "", 1)  # Drop instruction to get operands string

    # Remove comment in line
    ix = op_str.find("#")
    if ix >= 0:
        op_str = op_str[:ix]

    # Split around ',' to get operands then strip white space and lower case.
    operands = [x.strip().lower() for x in op_str.split(",")]
    op_count = len(operands)
    assert not (op_count != 3 and op_count != 4), "Invalid instruction: Expecting 3 or 4 operands"

    assert not (op_count == 4 and operands[3] != "v0.t"), f"Invalid instruction: Expecting 4th operand to be v0, got: {stripped}"

    is_vx = instr.endswith(".vx")
    op_vals = []

    for ix, op in enumerate(operands):
        if len(op) < 2:
            continue  # No enough characters in operand
        if ix == 2:
            if is_vx and op[0] != "x":
                continue  # Operand must be x0 to x31
            if not is_vx and op[0] != "v":
                continue  # Operand must be v0 to v31
        else:
            if op[0] != "v":
                continue  # Operand must be v0 to v31

        op = op[1:]  # Drop leading 'v' or 'x'.
        try:
            val = int(op)
            if val < 0 or val > 31:
                continue  # Register index must be 0 to 31
            op_vals.append(val)
        except Exception:
            # FIXME: Need specific exception
            val = int(op[0])
            if val < 0 or val > 31:
                continue
            op_vals.append(val)

    assert not (len(op_vals) != op_count), f"Invalid operand: Expecting v0 to v31 or x0 to x31: {line}"

    opcode = table[instr]
    opcode = opcode | (op_vals[0] << 7)  # Insert vd
    opcode = opcode | (op_vals[1] << 20)  # Insert vs2
    opcode = opcode | (op_vals[2] << 15)  # Insert vs1/rs1
    if op_count == 3:
        opcode = opcode | (1 << 25)  # Mark opcode as non-masked

    return ".word 0x{:08x}  # {}".format(opcode, line)
