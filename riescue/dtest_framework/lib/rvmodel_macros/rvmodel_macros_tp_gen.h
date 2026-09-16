// SPDX-FileCopyrightText: (c) 2026 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0
//
// tp_gen default rvmodel header. RVMODEL_BOOT brings up APLIC/IMSIC so
// hypervisor interrupt tests do not depend on EnableInterrupts programming
// the controller.

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

// ACLINT (SiFive CLINT-compatible):
//   0x42180000 : MSIP     (4B per hart, hart 0 @ +0x0)
//   0x42188000 : MTIMECMP (8B per hart, hart 0 @ +0x8000)
//   0x4218BFF8 : MTIME    (shared, 8B)
// Total ACLINT region: [0x42180000, 0x4218C000) -- size 0xC000.
#define CLINT_BASE_ADDRESS 0x42180000
#define MSIP_ADDRESS       (CLINT_BASE_ADDRESS + 0x0)
#define MTIMECMP_ADDRESS   (CLINT_BASE_ADDRESS + 0x8000)
#define MTIME_ADDRESS      (CLINT_BASE_ADDRESS + 0xBFF8)

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

// APLIC MMIO from whisper_aplic_config.json (maplic/saplic bases).
#define RVMODEL_MAPLIC_BASE      0x10800000
#define RVMODEL_SAPLIC_BASE      0x20800000
#define RVMODEL_APLIC_NUM_SRC    128
#define RVMODEL_IMSIC_EIE_TOP    (0xc0 + (RVMODEL_APLIC_NUM_SRC >> 4))

// Bring up IMSIC (eidelivery/eithreshold/eie) and APLIC (domaincfg, sources,
// MSI targets) plus guest files 1/2 for VSEI/SGEI. Runs from M-mode at loader
// start. Scratch: t0-t3.
#define RVMODEL_BOOT \
  li t0, RVMODEL_MAPLIC_BASE + 0x1bc0; \
  li t1, 0x40000; \
  sw t1, 0(t0); \
  li t0, RVMODEL_MAPLIC_BASE + 0x1bc4; \
  li t1, 0x601000; \
  sw t1, 0(t0); \
  li t0, RVMODEL_MAPLIC_BASE + 0x1bc8; \
  li t1, 0x44000; \
  sw t1, 0(t0); \
  li t0, RVMODEL_MAPLIC_BASE + 0x1bcc; \
  li t1, 0x600000; \
  sw t1, 0(t0); \
  li t0, 0x70; \
  csrw miselect, t0; \
  csrw siselect, t0; \
  li t0, 1; \
  csrw mireg, t0; \
  csrw sireg, t0; \
  li t0, 0x72; \
  csrw miselect, t0; \
  csrw siselect, t0; \
  csrw mireg, zero; \
  csrw sireg, zero; \
  li t0, 0xc0; \
  li t1, -1; \
  li t2, RVMODEL_IMSIC_EIE_TOP; \
4: \
  csrw miselect, t0; \
  csrw mireg, t1; \
  csrw siselect, t0; \
  csrw sireg, t1; \
  addi t0, t0, 2; \
  bne t0, t2, 4b; \
  li t0, HSTATUS_VGEIN_MASK; \
  csrc hstatus, t0; \
  li t0, (1 << HSTATUS_VGEIN_SHIFT); \
  csrs hstatus, t0; \
  li t0, 0x70; \
  csrw vsiselect, t0; \
  li t0, 1; \
  csrw vsireg, t0; \
  li t0, 0x72; \
  csrw vsiselect, t0; \
  csrw vsireg, zero; \
  li t0, 0xc0; \
  li t1, -1; \
  li t2, RVMODEL_IMSIC_EIE_TOP; \
5: \
  csrw vsiselect, t0; \
  csrw vsireg, t1; \
  addi t0, t0, 2; \
  bne t0, t2, 5b; \
  li t0, HSTATUS_VGEIN_MASK; \
  csrc hstatus, t0; \
  li t0, (2 << HSTATUS_VGEIN_SHIFT); \
  csrs hstatus, t0; \
  li t0, 0x70; \
  csrw vsiselect, t0; \
  li t0, 1; \
  csrw vsireg, t0; \
  li t0, 0x72; \
  csrw vsiselect, t0; \
  csrw vsireg, zero; \
  li t0, 0xc0; \
  li t1, -1; \
  li t2, RVMODEL_IMSIC_EIE_TOP; \
6: \
  csrw vsiselect, t0; \
  csrw vsireg, t1; \
  addi t0, t0, 2; \
  bne t0, t2, 6b; \
  li t1, 0x80000104; \
  li t0, RVMODEL_MAPLIC_BASE; \
  sw t1, 0(t0); \
  li t0, RVMODEL_SAPLIC_BASE; \
  sw t1, 0(t0); \
  li t1, 0x4; \
  li t2, RVMODEL_MAPLIC_BASE + 4; \
  li t3, RVMODEL_SAPLIC_BASE + 4; \
  li t0, RVMODEL_MAPLIC_BASE + (RVMODEL_APLIC_NUM_SRC * 4); \
7: \
  sw t1, 0(t2); \
  sw t1, 0(t3); \
  addi t2, t2, 4; \
  addi t3, t3, 4; \
  bne t2, t0, 7b; \
  li t1, 1; \
  li t2, RVMODEL_MAPLIC_BASE + 0x3004; \
  li t3, RVMODEL_SAPLIC_BASE + 0x3004; \
  li t0, RVMODEL_APLIC_NUM_SRC; \
8: \
  sw t1, 0(t2); \
  sw t1, 0(t3); \
  addi t2, t2, 4; \
  addi t3, t3, 4; \
  addi t1, t1, 1; \
  bne t1, t0, 8b; \
  li t0, (1 << 1); \
  csrs mvien, t0;

#define RVMODEL_SET_MEXT_INT(_R1, _R2) \
  GET_MHART_ID _R1; \
  slli _R1, _R1, IMSIC_STRIDE_LOG2; \
  li _R2, MIMSIC_BASE; \
  add _R2, _R2, _R1; \
  li _R1, MEI_TEST_ID; \
  sw _R1, IMSIC_SETEIPNUM_OFF(_R2);

#define RVMODEL_CLR_MEXT_INT(_R1, _R2) \
  csrrw _R1, mtopei, zero;

// MSIP is per-hart, 4 bytes per hart starting at MSIP_ADDRESS.
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
  csrrw _R1, stopei, zero;

// Raise hgeip[_GUEST_FILE]. _GUEST_FILE is the guest interrupt number; this
// target drives it through the per-hart IMSIC guest interrupt files (sbase=
// 0x44000000, sstride=0x40000, file N at sbase + hart*sstride + N*4KiB), by
// writing GEI_TEST_ID to that file's seteipnum_le. Another target may raise
// hgeip by any other mechanism. Pure MMIO here, so it works from VU. Requires
// the guest interrupt files to have been brought up first (EnableInterrupts
// with mode=v) so eidelivery/eie are in place.
#define RVMODEL_SET_HGEI_INT(_R1, _R2, _GUEST_FILE) \
  GET_MHART_ID _R1; \
  slli _R1, _R1, IMSIC_STRIDE_LOG2; \
  li _R2, SIMSIC_BASE; \
  add _R2, _R2, _R1; \
  slli _R1, _GUEST_FILE, IMSIC_GUEST_FILE_LOG2; \
  add _R2, _R2, _R1; \
  li _R1, GEI_TEST_ID; \
  sw _R1, IMSIC_SETEIPNUM_OFF(_R2);

// Clear hgeip[_GUEST_FILE]. On this target that means pointing hstatus.VGEIN at
// that guest interrupt file and claiming via vstopei, then restoring VGEIN to
// its previous value. Writes hstatus, so this is HS-mode only — callers running
// in VS/VU wrap it in a SupervisorCode (HS) block. (A VS-mode VSEI claim instead
// uses `stopei`, which with V=1 routes to the VGEIN-selected file without
// touching hstatus.)
#define RVMODEL_CLR_HGEI_INT(_R1, _R2, _GUEST_FILE) \
  csrr _R2, hstatus; \
  li _R1, HSTATUS_VGEIN_MASK; csrc hstatus, _R1; \
  slli _R1, _GUEST_FILE, HSTATUS_VGEIN_SHIFT; csrs hstatus, _R1; \
  csrrw _R1, vstopei, zero; \
  csrw hstatus, _R2;

#endif
