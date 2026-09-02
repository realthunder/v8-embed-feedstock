"""Install the built engine into a conda prefix, on any of the platforms.

Everything after `ninja v8` is the same job on unix and on Windows -- put the
library where the platform keeps libraries, copy the public headers, write the
configuration header those headers need, and leave behind the two files that
tell a consumer's build system about all of it.  Only the file names differ.
Doing it here rather than once in build.sh and again in bld.bat means the
Windows path cannot quietly drift away from the one that gets tested most.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def library_names(target_platform, version):
    """(built name, installed name, the symlink or import library beside it)."""
    if target_platform.startswith("osx"):
        return "libv8.%s.dylib" % version, "libv8.%s.dylib" % version, "libv8.dylib"
    if target_platform.startswith("win"):
        # gyp names a Windows shared library after the target, with the import
        # library beside it; neither carries the version.  The import library
        # is v8.dll.lib from the ninja generator and v8.lib from MSBuild;
        # main() accepts either and installs it as v8.lib.
        return "v8.dll", "v8.dll", "v8.lib"
    return ("libv8.so.%s" % version,) * 2 + ("libv8.so",)


def install_headers(include_src, include_dst):
    """The public headers, and nothing else in that directory.

    deps/v8/include also holds OWNERS, DEPS, the inspector protocol JSON and
    V8's own API notes, none of which belong in a prefix.
    """
    count = 0
    for root, _, files in os.walk(include_src):
        for name in files:
            if not name.endswith(".h"):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(include_dst, os.path.relpath(src, include_src))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            count += 1
    return count


def write_pkgconfig(path, prefix, version):
    # -DV8_GN_HEADER is not decoration: it is what makes v8config.h read the
    # v8-gn.h written below, and so agree with the library about how V8 was
    # built.
    with open(path, "w") as fh:
        fh.write(
            "prefix=%s\n"
            "libdir=${prefix}/lib\n"
            "includedir=${prefix}/include\n"
            "\n"
            "Name: v8-embed\n"
            "Description: The V8 JavaScript engine, built to be embedded\n"
            "Version: %s\n"
            "Libs: -L${libdir} -lv8\n"
            "Cflags: -I${includedir} -DV8_GN_HEADER\n" % (prefix, version)
        )


def write_cmake_config(directory, version):
    with open(os.path.join(directory, "v8-embed-config.cmake"), "w") as fh:
        fh.write(
            '# find_package(v8-embed CONFIG) -> the imported target v8-embed::v8.\n'
            '#\n'
            '# V8_GN_HEADER is not optional decoration: it is what makes v8config.h\n'
            '# read the shipped v8-gn.h and so agree with the library about how V8\n'
            '# was built.\n'
            'get_filename_component(_v8_embed_prefix\n'
            '                       "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)\n'
            '\n'
            'set(V8_EMBED_VERSION "%s")\n'
            'set(V8_EMBED_INCLUDE_DIRS "${_v8_embed_prefix}/include")\n'
            'find_library(V8_EMBED_LIBRARY NAMES v8\n'
            '             HINTS "${_v8_embed_prefix}/lib" NO_DEFAULT_PATH)\n'
            '\n'
            'if(NOT TARGET v8-embed::v8)\n'
            '  add_library(v8-embed::v8 SHARED IMPORTED)\n'
            '  set_target_properties(v8-embed::v8 PROPERTIES\n'
            '    INTERFACE_INCLUDE_DIRECTORIES "${V8_EMBED_INCLUDE_DIRS}"\n'
            '    INTERFACE_COMPILE_DEFINITIONS "V8_GN_HEADER"\n'
            '    INTERFACE_COMPILE_FEATURES "cxx_std_20")\n'
            '  if(WIN32)\n'
            '    # On Windows a consumer links the import library and loads the DLL.\n'
            '    set_target_properties(v8-embed::v8 PROPERTIES\n'
            '      IMPORTED_IMPLIB "${V8_EMBED_LIBRARY}"\n'
            '      IMPORTED_LOCATION "${_v8_embed_prefix}/bin/v8.dll")\n'
            '  else()\n'
            '    set_target_properties(v8-embed::v8 PROPERTIES\n'
            '      IMPORTED_LOCATION "${V8_EMBED_LIBRARY}")\n'
            '  endif()\n'
            'endif()\n'
            '\n'
            'set(V8_EMBED_LIBRARIES v8-embed::v8)\n'
            'set(v8-embed_FOUND TRUE)\n' % version
        )
    with open(os.path.join(directory, "v8-embed-config-version.cmake"), "w") as fh:
        fh.write(
            'set(PACKAGE_VERSION "%s")\n'
            '# V8 keeps no ABI compatibility between versions, so nothing but this\n'
            '# version is compatible with this version.\n'
            'if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)\n'
            '  set(PACKAGE_VERSION_COMPATIBLE TRUE)\n'
            '  set(PACKAGE_VERSION_EXACT TRUE)\n'
            'else()\n'
            '  set(PACKAGE_VERSION_COMPATIBLE FALSE)\n'
            'endif()\n' % version
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, help="the unpacked node tree")
    parser.add_argument("--build-dir", required=True, help="out/Release")
    parser.add_argument("--prefix", required=True, help="where to install")
    parser.add_argument("--version", required=True)
    parser.add_argument("--target-platform", required=True)
    args = parser.parse_args()

    windows = args.target_platform.startswith("win")
    # A conda prefix on Windows keeps a DLL in bin/ and its import library in
    # lib/; everywhere else the library is one file in lib/.
    libdir = os.path.join(args.prefix, "lib")
    bindir = os.path.join(args.prefix, "bin")
    incdir = os.path.join(args.prefix, "include")
    for directory in (libdir, incdir) + ((bindir,) if windows else ()):
        os.makedirs(directory, exist_ok=True)

    built, installed, companion = library_names(args.target_platform, args.version)

    # gyp puts a shared library under out/Release/lib on unix and beside the
    # other outputs on Windows.
    candidates = [
        os.path.join(args.build_dir, "lib", built),
        os.path.join(args.build_dir, built),
    ]
    src = next((path for path in candidates if os.path.exists(path)), None)
    if src is None:
        raise SystemExit("no %s in %s" % (built, " or ".join(candidates)))

    if windows:
        shutil.copyfile(src, os.path.join(bindir, installed))
        implibs = [os.path.join(os.path.dirname(src), name)
                   for name in (built + ".lib", companion)]
        implib = next((path for path in implibs if os.path.exists(path)), None)
        if implib is None:
            raise SystemExit("no import library at %s" % " or ".join(implibs))
        shutil.copyfile(implib, os.path.join(libdir, companion))
    else:
        shutil.copyfile(src, os.path.join(libdir, installed))
        shutil.copymode(src, os.path.join(libdir, installed))
        link = os.path.join(libdir, companion)
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(installed, link)
        if args.target_platform.startswith("osx"):
            # conda relocates by rpath; an install name that is anything else
            # follows the build machine into the package.
            subprocess.check_call(
                ["install_name_tool", "-id", "@rpath/" + installed,
                 os.path.join(libdir, installed)]
            )

    include_src = os.path.join(args.source_dir, "deps", "v8", "include")
    print("installed %d headers" % install_headers(include_src, incdir))

    # The configuration those headers read.  Without it an embedder compiles
    # against V8's defaults and links against ours, and the two disagree about
    # object layout without saying so.  The flags are read back from whichever
    # build files gyp wrote: ninja's, or MSBuild's project file, which gyp
    # puts beside the .gyp it came from.
    ninja_file = os.path.join(args.build_dir, "obj", "tools", "v8_gypfiles",
                              "v8_base_without_compiler.ninja")
    vcxproj = os.path.join(args.source_dir, "tools", "v8_gypfiles",
                           "v8_base_without_compiler.vcxproj")
    if os.path.exists(ninja_file):
        build_files = ["--ninja", ninja_file]
    elif os.path.exists(vcxproj):
        build_files = ["--vcxproj", vcxproj]
    else:
        raise SystemExit("neither %s nor %s exists" % (ninja_file, vcxproj))
    subprocess.check_call(
        [sys.executable, os.path.join(HERE, "emit_v8_gn_header.py")]
        + build_files
        + ["--include-dir", include_src,
           "--out", os.path.join(incdir, "v8-gn.h")]
    )

    # Build metadata, not an embedding API: the include path, the library, and
    # the -DV8_GN_HEADER that ties them together.
    pkgconfig = os.path.join(libdir, "pkgconfig")
    cmakedir = os.path.join(libdir, "cmake", "v8-embed")
    os.makedirs(pkgconfig, exist_ok=True)
    os.makedirs(cmakedir, exist_ok=True)
    write_pkgconfig(os.path.join(pkgconfig, "v8-embed.pc"),
                    args.prefix.replace("\\", "/"), args.version)
    write_cmake_config(cmakedir, args.version)


if __name__ == "__main__":
    main()
