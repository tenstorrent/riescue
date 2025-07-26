#! /usr/bin/env bash
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

yum --enablerepo=crb -y install \
    bc                              \
    perl                            \
    libnsl                          \
    dtc                             \
    make                            \
    which                           \
    openssl-devel                   \
    bzip2-devel                     \
    sqlite-devel                    \
    libffi-devel                    \
    gcc-toolset-12                  \
    zlib-devel                      \
    boost-regex                     \
    numactl-libs                    \
    dtc                             \
    boost                           \
    boost-static                    \
    boost-devel                     \
    boost-program-options           \
    libstdc++-static                \
    wget                            \
    gdbm-devel                      \
    readline-devel                  \
    uuid-devel                      \
    xz                              \
    xz-devel                        \
    sudo                            \
    python3-pip                     \
    python3-devel                   \
    python-sphinx                   \
    zstd                          \
    git

