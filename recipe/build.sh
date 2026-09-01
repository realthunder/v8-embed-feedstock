#!/usr/bin/env bash
set -euxo pipefail

# Nothing here builds node.  The source is node's because that is where a
# buildable V8 lives -- node vendors deps/v8 and drives it with ordinary GYP
# files and an ordinary toolchain, where upstream V8 needs depot_tools, gclient
# and a downloaded clang.  We configure node's build and then ask ninja for one
# target out of it.  See README.md.

# gyp puts its own -std= on every V8 compile; conda's is different, and two of
# them on one command line is a conflict rather than a choice.
export CXXFLAGS=$(echo ${CXXFLAGS:-} | sed -E 's@\-std=[^ ]*@@g')

if [[ "$target_platform" == osx-* ]]; then
    # The deployment target is set by the build, not by clang's baked-in CPPFLAGS.
    export CPPFLAGS="$(echo ${CPPFLAGS:-} | sed -E 's@\-mmacosx\-version\-min=[^ ]*@@g')"
    export CPPFLAGS="${CPPFLAGS} -D_DARWIN_C_SOURCE"
else
    # clock_gettime; V8 uses it and glibc before 2.17 keeps it in librt.
    export LDFLAGS="$LDFLAGS -lrt"
fi

if [[ "${CONDA_BUILD_CROSS_COMPILATION:-0}" == "1" ]]; then
    case $ARCH in
        64)             DEST_ARCH=x64 ;;
        arm64|aarch64)  DEST_ARCH=arm64 ;;
        ppc64le)        DEST_ARCH=ppc64 ;;
        riscv64)        DEST_ARCH=riscv64 ;;
        *)              echo "unknown architecture for cross"; exit 1 ;;
    esac
    case $target_platform in
        linux-*)  DEST_OS=linux ;;
        osx-*)    DEST_OS=mac ;;
        *)        echo "unknown os for cross"; exit 1 ;;
    esac
    EXTRA_ARGS="--cross-compiling --dest-os=$DEST_OS --dest-cpu=$DEST_ARCH"

    export CC_host=$CC_FOR_BUILD
    export CXX_host=$CXX_FOR_BUILD
    export AR_host=$($CC_FOR_BUILD -print-prog-name=ar)
    export LDFLAGS_host="$(echo $LDFLAGS | sed s@${PREFIX}@${BUILD_PREFIX}@g)"
fi

# What makes the `v8` target a shared library rather than an empty aggregate;
# see patches/0100-Let-a-build-ask-gyp-for-a-shared-V8.patch.
export NODE_GYP_COMPONENT=shared_library
# V8 promises no ABI across any two versions, so the SONAME is the whole
# version.  A consumer that was built against 14.6.202.34 must not silently
# load 14.6.203.1.
export GYP_DEFINES="soname_version=${PKG_VERSION}"

# --shared is node's switch for "this is going into a shared library", and it
# is the reason to pass it here even though no libnode is built: it defines
# V8_TLS_USED_IN_LIBRARY, which puts V8's thread-locals in a model that stays
# correct when the library is dlopen'd.  A consumer reached through a Python
# extension module is loaded exactly that way.
#
# --with-intl=system-icu takes ICU from this prefix instead of node's bundled
# copy, so there is one ICU in the process and no data file to ship.  Windows
# cannot do this; see bld.bat.
./configure \
    --ninja \
    --verbose \
    --prefix=${PREFIX} \
    --shared \
    --without-node-snapshot \
    --with-intl=system-icu \
    ${EXTRA_ARGS:-}

if [[ "${target_platform}" != "${build_platform}" ]]; then
    # The host-toolchain half of a cross build only has to run mksnapshot and
    # torque once; optimizing it is time spent on nothing that ships.
    for ninja_build in $(find out/Release/obj.host/ -name '*.ninja'); do
        sed -ie 's/-O3/-O1/g' ${ninja_build}
    done
fi

# One target, not `all`: node, npm, openssl, the fuzzers and the test binaries
# are all in this build graph and none of them are wanted.
#
# The default matters: bare `ninja` runs cores+2 jobs, and V8's compiler and
# maglev translation units take a gigabyte or two each, so an unbounded build
# on a wide machine exhausts memory and gcc reports it as an internal compiler
# error in a different file every run.
ninja -C out/Release -j${CPU_COUNT:-4} v8

# Everything past here is the same job on every platform; see install.py.
${PYTHON:-python} "${RECIPE_DIR}/install.py" \
    --source-dir . \
    --build-dir out/Release \
    --prefix "${PREFIX}" \
    --version "${PKG_VERSION}" \
    --target-platform "${target_platform}"
