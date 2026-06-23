// SPDX-FileCopyrightText: (c) 2026 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0

#ifndef _RVMODEL_MACROS_H
#define _RVMODEL_MACROS_H

##### IO #####

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

##### Interrupt Latency #####

#define RVMODEL_INTERRUPT_LATENCY 10

##### Machine Interrupts #####

#define CLINT_BASE_ADDRESS 0x02000000
#define MSIP_ADDRESS (CLINT_BASE_ADDRESS + 0x0)

#define RVMODEL_SET_MEXT_INT(_R1, _R2)

#define RVMODEL_CLR_MEXT_INT(_R1, _R2)

#define RVMODEL_SET_MSW_INT(_R1, _R2) \
  li _R1, 1; \
  li _R2, MSIP_ADDRESS; \
  sw _R1, 0(_R2);

#define RVMODEL_CLR_MSW_INT(_R1, _R2) \
  li _R2, MSIP_ADDRESS; \
  sw zero, 0(_R2);

#define RVMODEL_SET_MTIMER_INT(_R1, _R2)
#define RVMODEL_CLR_MTIMER_INT(_R1, _R2)

// No MMIO mtimer on this target (MTIMER set/clear are no-ops), so no-op.
#define RVMODEL_SET_MTIMER_INT_SOON(_R1, _R2)


##### Supervisor Interrupts #####

#define CVW_SSIP_ADDRESS (CLINT_BASE_ADDRESS + 0xC000)

#define RVMODEL_SET_SEXT_INT(_R1, _R2)

#define RVMODEL_CLR_SEXT_INT(_R1, _R2)

// No IMSIC guest interrupt files on this target, so guest external interrupts
// (VSEI/SGEI) cannot be driven here — no-ops, like MEXT/SEXT above. _GUEST_FILE
// is the guest interrupt file index expected by the TT/whisper variants.
#define RVMODEL_SET_HGEI_INT(_R1, _R2, _GUEST_FILE)
#define RVMODEL_CLR_HGEI_INT(_R1, _R2, _GUEST_FILE)

#define RVMODEL_SET_SSW_INT(_R1, _R2) \
  li _R1, 1; \
  li _R2, CVW_SSIP_ADDRESS; \
  sw _R1, 0(_R2);

#define RVMODEL_CLR_SSW_INT(_R1, _R2) \
  li _R2, CVW_SSIP_ADDRESS; \
  sw zero, 0(_R2);

#define RVMODEL_SET_STIMER_INT(_R1, _R2) \
  csrwi stimecmp, 0;

#define RVMODEL_CLR_STIMER_INT(_R1, _R2) \
  li _R1, 0xffffffff; \
  csrw stimecmp, _R1;

#define RVMODEL_SET_VSTIMER_INT(_R1, _R2) \
  csrwi vstimecmp, 0;

#define RVMODEL_CLR_VSTIMER_INT(_R1, _R2) \
  li _R1, 0xffffffff; \
  csrw vstimecmp, _R1;

#define RVMODEL_SET_STIMER_INT_SOON(_R1, _R2) \
  rdtime _R1; \
  addi _R1, _R1, 5000; \
  csrw stimecmp, _R1;
  


#endif
