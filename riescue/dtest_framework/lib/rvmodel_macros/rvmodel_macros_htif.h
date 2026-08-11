// SPDX-FileCopyrightText: (c) 2026 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0

#ifndef _RVMODEL_MACROS_H
#define _RVMODEL_MACROS_H


##### IO #####

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


#endif
