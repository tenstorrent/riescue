// SPDX-FileCopyrightText: (c) 2026 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0

#ifndef _RVMODEL_MACROS_H
#define _RVMODEL_MACROS_H

// Default HTIF-based I/O macros for RVCP message printing
// HTIF console: device=1, cmd=1 (putchar)
// tohost = (1 << 56) | (1 << 48) | char

#define RVMODEL_IO_INIT(_R1, _R2, _R3)

// Prints null-terminated string at address in _STR_PTR.
// _R1, _R2, _R3 are scratch registers.
#define RVMODEL_IO_WRITE_STR(_R1, _R2, _R3, _STR_PTR) \
1:                                                     \
    lbu _R1, 0(_STR_PTR);                             \
    beqz _R1, 3f;                                     \
    li _R2, 0x0101000000000000;                       \
    or _R1, _R2, _R1;                                 \
    la _R2, tohost;                                   \
2:                                                     \
    ld _R3, 0(_R2);                                   \
    bnez _R3, 2b;                                     \
    sd _R1, 0(_R2);                                   \
    addi _STR_PTR, _STR_PTR, 1;                       \
    j 1b;                                             \
3:

#endif
