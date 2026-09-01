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
# copy, so there is one ICU in the process and no data file to ship.
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
ninja -C out/Release -j${CPU_COUNT} v8

if [[ "$target_platform" == osx-* ]]; then
    SOLIB="libv8.${PKG_VERSION}.dylib"
    LINKNAME="libv8.dylib"
else
    SOLIB="libv8.so.${PKG_VERSION}"
    LINKNAME="libv8.so"
fi

mkdir -p "${PREFIX}/lib" "${PREFIX}/include"
cp "out/Release/lib/${SOLIB}" "${PREFIX}/lib/${SOLIB}"
ln -s "${SOLIB}" "${PREFIX}/lib/${LINKNAME}"

if [[ "$target_platform" == osx-* ]]; then
    # conda relocates by rpath; an install name that is anything else follows
    # the build machine into the package.
    install_name_tool -id "@rpath/${SOLIB}" "${PREFIX}/lib/${SOLIB}"
fi

# The public headers, and nothing else in that directory -- it also holds
# OWNERS, DEPS, the inspector protocol JSON and V8's own API notes.
( cd deps/v8/include && find . -name '*.h' -exec install -D -m 644 '{}' "${PREFIX}/include/{}" \; )

# The configuration those headers read.  Without it an embedder compiles
# against V8's defaults and links against ours, and the two disagree about
# object layout without saying so.
${PYTHON:-python} "${RECIPE_DIR}/emit_v8_gn_header.py" \
    --ninja out/Release/obj/tools/v8_gypfiles/v8_base_without_compiler.ninja \
    --include-dir deps/v8/include \
    --out "${PREFIX}/include/v8-gn.h"

# Build metadata, not an embedding API: the include path, the library, and the
# -DV8_GN_HEADER that makes v8config.h read the file above.
mkdir -p "${PREFIX}/lib/pkgconfig" "${PREFIX}/lib/cmake/v8-embed"

cat > "${PREFIX}/lib/pkgconfig/v8-embed.pc" <<EOF
prefix=${PREFIX}
libdir=\${prefix}/lib
includedir=\${prefix}/include

Name: v8-embed
Description: The V8 JavaScript engine, built to be embedded
Version: ${PKG_VERSION}
Libs: -L\${libdir} -lv8
Cflags: -I\${includedir} -DV8_GN_HEADER
EOF

cat > "${PREFIX}/lib/cmake/v8-embed/v8-embed-config.cmake" <<EOF
# find_package(v8-embed CONFIG) -> the imported target v8-embed::v8.
#
# V8_GN_HEADER is not optional decoration: it is what makes v8config.h read
# the shipped v8-gn.h and so agree with the library about how V8 was built.
include(CMakeFindDependencyMacro)

get_filename_component(_v8_embed_prefix "\${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

set(V8_EMBED_VERSION "${PKG_VERSION}")
set(V8_EMBED_INCLUDE_DIRS "\${_v8_embed_prefix}/include")
find_library(V8_EMBED_LIBRARY NAMES v8 HINTS "\${_v8_embed_prefix}/lib" NO_DEFAULT_PATH)

if(NOT TARGET v8-embed::v8)
  add_library(v8-embed::v8 SHARED IMPORTED)
  set_target_properties(v8-embed::v8 PROPERTIES
    IMPORTED_LOCATION "\${V8_EMBED_LIBRARY}"
    INTERFACE_INCLUDE_DIRECTORIES "\${V8_EMBED_INCLUDE_DIRS}"
    INTERFACE_COMPILE_DEFINITIONS "V8_GN_HEADER"
    INTERFACE_COMPILE_FEATURES "cxx_std_20")
endif()

set(V8_EMBED_LIBRARIES v8-embed::v8)
set(v8-embed_FOUND TRUE)
EOF

cat > "${PREFIX}/lib/cmake/v8-embed/v8-embed-config-version.cmake" <<EOF
set(PACKAGE_VERSION "${PKG_VERSION}")
# V8 keeps no ABI compatibility between versions, so nothing but this version
# is compatible with this version.
if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  set(PACKAGE_VERSION_EXACT TRUE)
else()
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
endif()
EOF
