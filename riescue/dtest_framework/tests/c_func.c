// SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0
#include <stdio.h>
#include <stdint.h>
#include <strings.h>

#define MAP_ADDR    (0x90000000)
#define MAP_SIZE    (0x1000)
#define MAP_MAGIC_1 (0x3150414d)
#define MAP_MAGIC_2 (0x3250414d)

struct ipc_struct {
    uint32_t ipc_flag;
};

int
c_func_0(uint32_t *addr1)
{
    volatile struct ipc_struct *sbuf;

    uint32_t *addr2 = (uint32_t *)((uint8_t *)addr1 + 0x1000);
    uint32_t *addr3 = (uint32_t *)((uint8_t *)addr1 + 0x2000);

    if (*addr1 != 0) for(;;);

    *addr1 = MAP_MAGIC_1;
    *addr2 = MAP_MAGIC_1 + 1;

    sbuf = (struct ipc_struct *)addr3;

    sbuf->ipc_flag = MAP_MAGIC_1;

    while (sbuf->ipc_flag != MAP_MAGIC_2);

    if ((*addr1 != MAP_MAGIC_1) || (*addr2 != (MAP_MAGIC_1+1))) {
        return 1;
    }

    return 0;
}

int
c_func_1(uint32_t *addr1)
{
    volatile struct ipc_struct *sbuf;

    uint32_t *addr2 = (uint32_t *)((uint8_t *)addr1 + 0x1000);
    uint32_t *addr3 = (uint32_t *)((uint8_t *)addr1 + 0x2000);

    if (*addr1 != 0) for(;;);

    *addr1 = MAP_MAGIC_2;
    *addr2 = MAP_MAGIC_2 + 1;

    sbuf = (struct ipc_struct *)addr3;

    while (sbuf->ipc_flag != MAP_MAGIC_1);
    sbuf->ipc_flag = MAP_MAGIC_2;

    if ((*addr1 != MAP_MAGIC_2) || (*addr2 != (MAP_MAGIC_2+1))) {
        return 1;
    }

    return 0;
}
