// SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
// SPDX-License-Identifier: Apache-2.0
#define MAP_ADDR    (0x90000000)
#define MAP_SIZE    (0x1000)
#define MAP_MAGIC_1 (0x3150414dU)
#define MAP_MAGIC_2 (0x3250414dU)

struct ipc_struct {
    unsigned int ipc_flag;
};

int
c_func_0(unsigned int *addr1)
{
    volatile struct ipc_struct *sbuf;

    unsigned int *addr2 = (unsigned int *)((unsigned char *)addr1 + 0x1000);
    unsigned int *addr3 = (unsigned int *)((unsigned char *)addr1 + 0x2000);

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
c_func_1(unsigned int *addr1)
{
    volatile struct ipc_struct *sbuf;

    unsigned int *addr2 = (unsigned int *)((unsigned char *)addr1 + 0x1000);
    unsigned int *addr3 = (unsigned int *)((unsigned char *)addr1 + 0x2000);

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
