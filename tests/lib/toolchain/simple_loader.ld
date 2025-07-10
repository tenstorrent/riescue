ENTRY(_start)
SECTIONS
{
    tohost = 0x1000;
    /* Set the entry point */
    . = 0x80000000;

    /* .text section loaded at 0x80000000 */
    .text : {
        *(.text)
    }


    /* .data section loaded immediately after .text */
    /*
    .data : {
        *(.data)
    }
    */

    /* .bss section loaded immediately after .data */
    /*
    .bss : {
        *(.bss)
    }
    */
}