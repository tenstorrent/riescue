#! /usr/bin/env bash
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# Builds whisper for both development and public environments
# Internal dev environment uses internal submodule, public repo uses clone.

script_dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
container_run=$script_dir/container-run
build_cmd="make -j 20 BOOST_LIB_DIR=/usr/lib64/ BOOST_INC=/usr/include/boost/ STATIC_LINK=0 SOFT_FLOAT=1"

whisper_remote="https://github.com/tenstorrent/whisper.git"
whisper_sha="b24e30f238d462d3930744cb084d74129d0873a8"

set -e
set -o pipefail

# Check if whisper submodule exists (development environment)
if [ -d "whisper" ]; then
    echo "Found whisper submodule, building in development mode"
    cd whisper
    rm -rf build-Linux || echo "build-Linux not found"
    rm -f whisper || echo "whisper not found"
    make clean

    if ! [ -x "$(command -v singularity)" ] || [ "$NO_SINGULARITY" ]; then
        echo "Building whisper directly"
        $build_cmd
    else
        echo "Building whisper in container"
        $container_run $build_cmd
    fi

    mv build-Linux/whisper whisper || echo "whisper not found in build-Linux"

else
    echo "No whisper submodule found, cloning from remote"

    # Determine target directory based on environment
    if [ "$NO_SINGULARITY" ]; then
        # Public/CI environment - use $HOME/whisper or current directory
        whisper_dir="${HOME}/whisper"
        mkdir -p "$whisper_dir"
    else
        # Container environment - use /usr/local/whisper
        whisper_dir="/usr/local/whisper"
        mkdir -p "$whisper_dir"
    fi

    echo "Building Whisper in $whisper_dir"
    pushd "$whisper_dir"

    # Clean any existing state
    rm -rf .git build-Linux whisper || true

    # Clone and build
    git init &&
    git remote add origin "$whisper_remote" &&
    git fetch --depth 1 origin "$whisper_sha" &&
    git checkout FETCH_HEAD

    $build_cmd
    mv build-Linux/whisper whisper

    # Copy to system location if in container
    if [ -z "$NO_SINGULARITY" ]; then
        cp whisper /usr/bin/whisper
    fi

    popd
fi

echo "Whisper build completed"