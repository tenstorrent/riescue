#! /usr/bin/env bash
# Assumes whisper is already cloned and builds

script_dir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
container_run=$script_dir/container-run
build_whisper="make -j 20 BOOST_LIB_DIR=/usr/lib64/ BOOST_INC=/usr/include/boost/ STATIC_LINK=0 SOFT_FLOAT=1"


cd whisper
rm -rf build-Linux || echo "build-Linux not found"
rm whisper || echo "whisper not found"
make clean

set -e
set -o pipefail

echo $(command -v singularity)
if ! [ -x "$(command -v singularity)" ] || [ "$NO_SINGULARITY" ];  then
    echo "Assuming already in singularity container or NO_SINGULARITY set"
    $build_whisper
else
    echo "Not in container"
    $container_run $build_whisper
fi
mv build-Linux/whisper whisper || echo "whisper not found in build-Linux"
