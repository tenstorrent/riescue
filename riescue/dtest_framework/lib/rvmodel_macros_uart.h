// SPDX-FileCopyrightText: (c) 2026 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0

#ifndef _RVMODEL_MACROS_H
#define _RVMODEL_MACROS_H

// UART 8250 base address (from config.json uart0)
// Register shift = 2 (4-byte spacing)
#define UART_BASE   0x02000000
#define UART_THR    (UART_BASE + (0 << 2))   // Transmit Holding Register
#define UART_LSR    (UART_BASE + (5 << 2))   // Line Status Register
#define UART_LCR    (UART_BASE + (3 << 2))   // Line Control Register

#define RVMODEL_IO_INIT(_R1, _R2, _R3)        \
    li _R1, UART_LCR;                        \
    li _R2, 0x03;                            \
    sb _R2, 0(_R1);

// Prints null-terminated string at address in _STR_PTR.
// _R1, _R2, _R3 are scratch registers.
#define RVMODEL_IO_WRITE_STR(_R1, _R2, _R3, _STR_PTR) \
1:                                            \
    lbu _R1, 0(_STR_PTR);                    \
    beqz _R1, 3f;                            \
2:                                            \
    li _R2, UART_LSR;                        \
    lbu _R3, 0(_R2);                         \
    andi _R3, _R3, 0x20;                     \
    beqz _R3, 2b;                            \
    li _R2, UART_THR;                        \
    sb _R1, 0(_R2);                          \
    addi _STR_PTR, _STR_PTR, 1;              \
    j 1b;                                    \
3:

#endif
