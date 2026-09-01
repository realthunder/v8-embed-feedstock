@echo on
setlocal enabledelayedexpansion

:: Windows is the one platform nodejs-feedstock does not compile: its Windows
:: source is the official binary zip and its build script is six COPY lines.
:: So there is nothing to inherit here and this is written from node's own
:: Windows build instead -- which does exist, and drives the same configure.py
:: and the same GYP files through MSVC.  Nothing about the shared-V8 mechanism
:: is unix-specific: v8.gyp has the Windows half of it too (v8dll-main.cc, the
:: CRT conditions in toolchain.gypi), and a component build is how Chromium
:: itself builds V8 on Windows.
::
:: vcbuild.bat is deliberately not used.  Its job is finding Python and Visual
:: Studio, and conda's compiler activation has already put both on PATH and
:: filled in INCLUDE and LIB.

:: What makes the `v8` target a shared library rather than an empty aggregate;
:: see patches/0100-Let-a-build-ask-gyp-for-a-shared-V8.patch.
set "NODE_GYP_COMPONENT=shared_library"

:: No soname_version here, unlike build.sh.  v8.gyp turns it into a product
:: extension without checking the OS, so setting it would name the DLL
:: v8.so.14.6.202.34.  A Windows consumer gets its version guarantee from the
:: package pin instead.
set "GYP_DEFINES="

if "%target_platform%"=="win-arm64" (
    set "DEST_CPU=arm64"
) else (
    set "DEST_CPU=x64"
)

:: --with-intl=small-icu, where the unix build uses system-icu.  node can only
:: find a system ICU through pkg-config, which is not something to rely on
:: under MSVC with conda's paths; small-icu builds deps/icu-small into the
:: library and needs nothing external.  The C++ ABI is identical either way --
:: V8_INTL_SUPPORT is not one of the macros the public headers read -- so this
:: is a difference in JavaScript locale data and nothing else.
::
:: --without-ssl, and not --openssl-no-asm, which it is incompatible with.
:: No openssl is built here -- only the `v8` target -- and configuring it is
:: what breaks on Windows: configure_openssl reads OPENSSL_VERSION_NUMBER out
:: of the bundled headers by running the compiler as `cc -dM -E -x c`, which
:: are GCC's flags.  conda's MSVC activation sets CC=cl.exe, cl rejects them,
:: the version comes back None and configure dies subscripting it.  Skipping
:: openssl skips the whole question.
python configure.py ^
    --ninja ^
    --verbose ^
    --dest-cpu=%DEST_CPU% ^
    --shared ^
    --without-node-snapshot ^
    --without-ssl ^
    --with-intl=small-icu
if errorlevel 1 exit 1

:: One target, not `all`: node, npm, openssl and the test binaries are all in
:: this build graph and none of them are wanted.
::
:: The default matters: bare `ninja` runs cores+2 jobs, and V8's compiler and
:: maglev translation units take a gigabyte or two each, so an unbounded build
:: on a wide machine exhausts memory.
if not defined CPU_COUNT set "CPU_COUNT=4"
ninja -C out/Release -j%CPU_COUNT% v8
if errorlevel 1 exit 1

:: Everything past here is the same job on every platform; see install.py.
python "%RECIPE_DIR%\install.py" ^
    --source-dir . ^
    --build-dir out/Release ^
    --prefix "%LIBRARY_PREFIX%" ^
    --version "%PKG_VERSION%" ^
    --target-platform "%target_platform%"
if errorlevel 1 exit 1
