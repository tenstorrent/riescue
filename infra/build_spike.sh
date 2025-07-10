#! /usr/bin/env bash
# Assumes spike isn't cloned and built. Using hardcoded SHAs for both public riscv-isa-sim and TT-fork.

script_dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
container_run=$script_dir/container-run

# Hardcoded SHAs and remotes
spike_remote="https://github.com/riscv-software-src/riscv-isa-sim.git"
spike_sha="4703ad98bf4c247a0841a6d7254357b14a97ff29"

tt_spike_remote="https://github.com/tenstorrent/spike.git"
tt_spike_sha="344b9ef3951fc9318caae4ce41da8ede5d6085fc"

build_spike() {
    local repo_url="$1"
    local sha="$2"
    local build_dir="$3"
    shift 3

    mkdir -p $build_dir
    pushd $build_dir
    git init &&
    git remote add origin "$repo_url" &&
    git fetch --depth 1 origin "$sha" &&
    git checkout FETCH_HEAD
    ./configure $@
    make -j 12
    make install
    popd
}

set -e
set -o pipefail

echo $(command -v singularity)
if ! [ -x "$(command -v singularity)" ] || [ "$NO_SINGULARITY" ];  then
    echo "Assuming already in singularity container or NO_SINGULARITY set"

    echo "Building spike"
    # Install spike as spike
    build_spike "$spike_remote" "$spike_sha" "spike" --enable-dual-endian --with-isa=RV64IMAFDCV_ZBA_ZBB_ZBC_ZBS --with-priv=MSU
    rm -rf spike

    # Install spike as tt_spike
    echo "Building tt_spike"
    install_dir=/usr/local/tt_spike
    build_spike "$tt_spike_remote" "$tt_spike_sha" "tt_spike" --enable-tt-stop-if-tohost-nonzero --enable-tt-table-walk-debug --enable-tt-expanded-dram-address-range --enable-dual-endian --with-isa=RV64IMAFDCV_ZBA_ZBB_ZBC_ZBS --with-priv=MSU --prefix=$install_dir
    cp $install_dir/bin/spike /usr/local/bin/tt_spike
    rm -rf $install_dir
    rm -rf tt_spike


else
    echo "Not in container"
    $container_run bash -c "
        echo 'Building spike'
        build_spike '$spike_remote' '$spike_sha' 'spike' --enable-dual-endian --with-isa=RV64IMAFDCV_ZBA_ZBB_ZBC_ZBS --with-priv=MSU
        rm -rf spike

        echo 'Building tt_spike'
        install_dir=/usr/local/tt_spike
        build_spike '$tt_spike_remote' '$tt_spike_sha' 'tt_spike' --enable-tt-stop-if-tohost-nonzero --enable-tt-table-walk-debug --enable-tt-expanded-dram-address-range --enable-dual-endian --with-isa=RV64IMAFDCV_ZBA_ZBB_ZBC_ZBS --with-priv=MSU --prefix=\$install_dir
        cp \$install_dir/bin/spike /usr/local/bin/tt_spike
        rm -rf \$install_dir
        rm -rf tt_spike
    "
fi