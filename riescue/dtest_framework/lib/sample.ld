/*======================================================================*/
/* Proxy kernel linker script                                           */
/*======================================================================*/
/* This is the linker script used when building the proxy kernel. */

/*----------------------------------------------------------------------*/
/* Setup                                                                */
/*----------------------------------------------------------------------*/

/* The OUTPUT_ARCH command specifies the machine architecture where the
   argument is one of the names used in the BFD library. More
   specifically one of the entires in bfd/cpu-mips.c */

OUTPUT_ARCH( "riscv" )
ENTRY(_start)

/*----------------------------------------------------------------------*/
/* Sections                                                             */
/*----------------------------------------------------------------------*/

SECTIONS
{

  . = 0x80000000;
  .text : { *(.text) }

  . = 0x90000000;
  /* data segment */
  .data : { *(.data) }

  . = 0xa0000000;
  /* data segment */
  .data1 : { *(.data1) }

  . = 0xb000000;
  /* loader segment */
  .loader : { *(.loader) }

  . = 0xb100000;
  /* os segment */
  .os : { *(.os) }

  . = 0xc0000000;
  /* data segment */
  .os_data : { *(.os_data) }

}
