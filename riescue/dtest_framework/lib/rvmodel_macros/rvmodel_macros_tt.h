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

// ACLINT layout per riescue/dtest_framework/lib/whisper_config.json:
//   "aclint": { base=0x42180000, size=0xC000,
//               time_offset=0x0, timer_offset=0x8000 }
// Resulting map (no sw_offset -> no MSWI/MSIP block at this base):
//   0x42180000 .. 0x42187FFF : MTIME (shared, 8B at +0x0)
//   0x42188000 .. 0x4218FFFF : MTIMECMP (8B per hart, hart 0 @ +0x8000)
// Total ACLINT region: [0x42180000, 0x4218C000) -- size 0xC000.
//
// NOTE: whisper_config.json does NOT define an MSWI region for this ACLINT,
// so MSIP cannot be triggered via MMIO at this base. MSI must be asserted
// some other way (e.g. mip.MSIP write from M-mode, or a separate MSWI
// device if added to the config).
#define CLINT_BASE_ADDRESS 0x42180000
#define MTIME_ADDRESS      (CLINT_BASE_ADDRESS + 0x0)
#define MTIMECMP_ADDRESS   (CLINT_BASE_ADDRESS + 0x8000)
#define MSIP_ADDRESS       (CLINT_BASE_ADDRESS + 0x0)  // unmapped: no sw_offset in aclint config

// Machine external (MEI) is driven via the per-hart M-mode IMSIC interrupt
// file. Per whisper_aplic_config.json:
//   mbase=0x40000000, mstride=0x40000  (i.e. 1 << 18)
// SET writes MEI_TEST_ID to seteipnum_le (offset 0) of THIS hart's M-IMSIC
// page; CLR reads mtopei to atomically claim and clear the highest-priority
// pending external interrupt.
#define MIMSIC_BASE              0x40000000
#define SIMSIC_BASE              0x44000000
#define IMSIC_STRIDE_LOG2        18
#define IMSIC_SETEIPNUM_OFF      0x0
#define MEI_TEST_ID              1
#define SEI_TEST_ID              1
#define IMSIC_GUEST_FILE_LOG2    12        /* each IMSIC guest interrupt file is 4 KiB */
#define GEI_TEST_ID              1         /* identity poked into a guest file's seteipnum_le */
#define HSTATUS_VGEIN_SHIFT      12             /* hstatus.VGEIN field LSB (bits 17:12) */
#define HSTATUS_VGEIN_MASK       (0x3f << HSTATUS_VGEIN_SHIFT)   /* hstatus.VGEIN field (bits 17:12) */

#define RVMODEL_SET_MEXT_INT(_R1, _R2) \
  GET_MHART_ID _R1; \
  slli _R1, _R1, IMSIC_STRIDE_LOG2; \
  li _R2, MIMSIC_BASE; \
  add _R2, _R2, _R1; \
  li _R1, MEI_TEST_ID; \
  sw _R1, IMSIC_SETEIPNUM_OFF(_R2);

#define RVMODEL_CLR_MEXT_INT(_R1, _R2) \
  csrr _R1, mtopei;

// MSW is not supported on TT when IMSIC is provided.
#define RVMODEL_SET_MSW_INT(_R1, _R2)
#define RVMODEL_CLR_MSW_INT(_R1, _R2) 

#define RVMODEL_SET_MTIMER_INT(_R1, _R2) \
  li _R1, 0; \
  li _R2, MTIMECMP_ADDRESS; \
  sd _R1, 0(_R2);

#define RVMODEL_CLR_MTIMER_INT(_R1, _R2) \
  li _R1, 0xffffffff; \
  li _R2, MTIMECMP_ADDRESS; \
  sd _R1, 0(_R2);

#define RVMODEL_SET_MTIMER_INT_SOON(_R1, _R2) \
  rdtime _R1; \
  addi _R1, _R1, 5000; \
  li _R2, MTIMECMP_ADDRESS; \
  sd _R1, 0(_R2);

##### Supervisor Interrupts #####


// Supervisor external (SEI) is driven via the per-hart S-mode IMSIC interrupt
// file (sbase=0x44000000, sstride=0x40000). SET writes SEI_TEST_ID to
// seteipnum_le of THIS hart's S-IMSIC page; CLR reads stopei to atomically
// claim and clear the highest-priority pending external interrupt.
#define RVMODEL_SET_SEXT_INT(_R1, _R2) \
  GET_MHART_ID _R1; \
  slli _R1, _R1, IMSIC_STRIDE_LOG2; \
  li _R2, SIMSIC_BASE; \
  add _R2, _R2, _R1; \
  li _R1, SEI_TEST_ID; \
  sw _R1, IMSIC_SETEIPNUM_OFF(_R2);

#define RVMODEL_CLR_SEXT_INT(_R1, _R2) \
  csrr _R1, stopei;

// Hypervisor guest external interrupt (HGEI) delivery via the per-hart IMSIC
// guest interrupt files. Per whisper config: sbase=0x44000000, sstride=0x40000,
// guest interrupt file N at sbase + hart*sstride + N*4KiB. _GUEST_FILE holds the
// guest interrupt file index to target; SET writes GEI_TEST_ID to that file's
// seteipnum_le. Guest file 1 is the VGEIN home and drives VSEIP/vsip[9]; other
// files (e.g. 2) raise hgeip[N] -> hip[12]=SGEIP per hgeie. Pure MMIO poke,
// works from VU. Requires the guest IMSIC to have been brought up first
// (EnableInterrupts with mode=v) so eidelivery/eie (and hstatus.VGEIN for file 1)
// are in place.
#define RVMODEL_SET_HGEI_INT(_R1, _R2, _GUEST_FILE) \
  GET_MHART_ID _R1; \
  slli _R1, _R1, IMSIC_STRIDE_LOG2; \
  li _R2, SIMSIC_BASE; \
  add _R2, _R2, _R1; \
  slli _R1, _GUEST_FILE, IMSIC_GUEST_FILE_LOG2; \
  add _R2, _R2, _R1; \
  li _R1, GEI_TEST_ID; \
  sw _R1, IMSIC_SETEIPNUM_OFF(_R2);

// Hypervisor guest external interrupt (HGEI) claim/clear. _GUEST_FILE holds the
// guest interrupt file index whose pending bit should be cleared. Point
// hstatus.VGEIN at that file, claim via vstopei (atomically clears the file's top
// pending bit), then restore VGEIN to the home file 1 (the VSEI/VGEIN delivery
// file). Guest file 1 clears VSEIP/vsip[9]; file 2 clears hgeip[2] -> hip[12]=SGEIP
// (the loader sets hgeie[2]=1, hgeie[1]=0 so file 2 raises SGEIP without disturbing
// VSEIP). Writes hstatus, so this is HS-mode only — callers running in VS/VU wrap
// it in a SupervisorCode (HS) block. (A VS-mode VSEI claim instead uses `stopei`,
// which with V=1 routes to the VGEIN-selected guest file without hstatus access.)
#define RVMODEL_CLR_HGEI_INT(_R1, _R2, _GUEST_FILE) \
  li _R1, HSTATUS_VGEIN_MASK; csrc hstatus, _R1; \
  slli _R1, _GUEST_FILE, HSTATUS_VGEIN_SHIFT; csrs hstatus, _R1; \
  csrrw _R1, vstopei, zero; \
  li _R1, HSTATUS_VGEIN_MASK; csrc hstatus, _R1; \
  li _R1, (1 << HSTATUS_VGEIN_SHIFT); csrs hstatus, _R1;

// Note that this is a super tricky way to do branching of mip/sip bits in super mode.
// Calculate what PC to jump to based on privilege mode, then jump to it.
// PRIV_MODE_* are mutually-exclusive presence flags (1 when the test runs at that
// privilege, else 0). The branch index is !PRIV_MODE_MACHINE: machine (index 0)
// jumps to the mip instruction and harmlessly falls through into the sip one (M can
// access both), while everything below machine (HS/VS and U/VU, index 1) jumps
// straight to the sip instruction only — mip is M-only and faults from S/VS.
#define RVMODEL_SET_SSW_INT(_R1, _R2) \
  li _R1, !PRIV_MODE_MACHINE; \
  auipc _R2, 0; \
  slli _R1, _R1, 2; \
  addi _R1, _R1, 20; \
  add _R2, _R2, _R1; \
  jalr zero, 0(_R2); \
  csrsi mip, (1 << 1); \
  csrsi sip, (1 << 1);

#define RVMODEL_CLR_SSW_INT(_R1, _R2) \
  li _R1, !PRIV_MODE_MACHINE; \
  auipc _R2, 0; \
  slli _R1, _R1, 2; \
  addi _R1, _R1, 20; \
  add _R2, _R2, _R1; \
  jalr zero, 0(_R2); \
  csrci mip, (1 << 1); \
  csrci sip, (1 << 1);

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
