@echo on
setlocal enabledelayedexpansion

:: Windows is the one platform nodejs-feedstock does not compile: its Windows
:: source is the official binary zip and its build script is six COPY lines.
:: So there is nothing to inherit here and this is written from node's own
:: Windows build instead -- which does exist, and drives the same configure.py
:: and the same GYP files.  Nothing about the shared-V8 mechanism is
:: unix-specific: v8.gyp has the Windows half of it too (v8dll-main.cc, the
:: CRT conditions in toolchain.gypi), and a component build is how Chromium
:: itself builds V8 on Windows.
::
:: The compiler is clang-cl, and it has to be.  V8 dropped MSVC at 13.0 and
:: node followed at 24: its headers no longer compile with cl.exe
:: (FLEXIBLE_ARRAY_MEMBER is a zero-length array in a base class, which MSVC
:: rejects as C2503).  Where the clang-cl comes from is the V8_WIN_TOOLCHAIN
:: variant, set from the recipe:
::
::   conda-clang-cl  conda-forge's clang-cl package, pinned, on top of the
::                   vs2022 activation for headers, libraries and link.exe;
::                   built with ninja, the way every other platform is.
::   vs-clang-cl     the clang-cl that ships as a Visual Studio component,
::                   driven by MSBuild through the ClangCL platform toolset --
::                   node's own Windows build, and the only one node tests.
::
:: vcbuild.bat is deliberately not used in either case.  Its job is finding
:: Python and Visual Studio, and conda's compiler activation has already put
:: both on PATH and filled in INCLUDE and LIB.

if "%V8_WIN_TOOLCHAIN%"=="" (
    echo V8_WIN_TOOLCHAIN is not set; the recipe should have set it
    exit 1
)

:: gyp's ninja generator appends CFLAGS, CXXFLAGS and LDFLAGS from the
:: environment to its own, after them.  conda's clang-cl activation puts
:: /std:c++17 in CXXFLAGS, which then outranks the /std:c++20 V8 needs (the
:: same conflict build.sh strips -std= for), plus -fuse-ld=lld, which is a
:: link option and only draws an unused-argument warning on a compile line.
:: Its LDFLAGS is `-Xlinker /DEFAULTLIB:<compiler-rt builtins>` in the
:: clang driver's syntax; the link here is a bare link.exe, which does not
:: know -Xlinker, and patches/0104 already puts that library on the link
:: line in a form it does know.  So: those flags go, the rest stay.
for /f "usebackq delims=" %%i in (`python -c "import os; [print(v + '=' + ' '.join(t for t in os.environ.get(v, '').split() if not t.startswith(('/std:', '-std', '-fuse-ld=')))) for v in ('CFLAGS', 'CXXFLAGS')]"`) do set "%%i"
set "LDFLAGS="
echo CFLAGS=%CFLAGS%
echo CXXFLAGS=%CXXFLAGS%

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

:: Tell gyp which Visual Studio to use instead of letting it hunt.  Its
:: autodetection walks the registry and came up empty on the CI image even
:: though vcvarsall had already set the environment up, which fails the whole
:: build with "Could not locate Visual Studio installation".  Setting both of
:: these short-circuits the search entirely: gyp takes the version and path it
:: is given and trusts that the environment is configured, which after conda's
:: compiler activation it is.
if not defined GYP_MSVS_VERSION if "%VisualStudioVersion%"=="18.0" set "GYP_MSVS_VERSION=2026"
if not defined GYP_MSVS_VERSION if "%VisualStudioVersion%"=="17.0" set "GYP_MSVS_VERSION=2022"
if not defined GYP_MSVS_VERSION if "%VisualStudioVersion%"=="16.0" set "GYP_MSVS_VERSION=2019"
if defined GYP_MSVS_VERSION if not defined GYP_MSVS_OVERRIDE_PATH if defined VSINSTALLDIR set "GYP_MSVS_OVERRIDE_PATH=%VSINSTALLDIR%"
echo GYP_MSVS_VERSION=%GYP_MSVS_VERSION% GYP_MSVS_OVERRIDE_PATH=%GYP_MSVS_OVERRIDE_PATH%

:: Which clang-cl, and its version: configure.py wants --clang-cl=<version>,
:: the same way vcbuild.bat passes it.  `clang --version` starts with
:: "clang version X.Y.Z", and the third word is the number.
if "%V8_WIN_TOOLCHAIN%"=="conda-clang-cl" (
    set "CLANG_EXE=clang.exe"
    set "GENERATOR=--ninja"
) else if "%V8_WIN_TOOLCHAIN%"=="vs-clang-cl" (
    set "CLANG_EXE=%VCINSTALLDIR%\Tools\Llvm\x64\bin\clang.exe"
    set "GENERATOR="
) else (
    echo unknown V8_WIN_TOOLCHAIN "%V8_WIN_TOOLCHAIN%"
    exit 1
)
set "CLANG_VERSION="
for /F "tokens=3" %%i in ('"%CLANG_EXE%" --version') do (
    if not defined CLANG_VERSION set "CLANG_VERSION=%%i"
)
if not defined CLANG_VERSION (
    echo could not get a version from "%CLANG_EXE%" --version
    exit 1
)
echo clang-cl %CLANG_VERSION% from "%CLANG_EXE%"

:: --with-intl=small-icu, the same as build.sh; see the note there.
::
:: --without-ssl, and not --openssl-no-asm, which it is incompatible with.
:: No openssl is built here -- only the `v8` target -- and configuring it is
:: what breaks on Windows: configure_openssl reads OPENSSL_VERSION_NUMBER out
:: of the bundled headers by running the compiler as `cc -dM -E -x c`, which
:: are GCC's flags.  conda's MSVC activation sets CC=cl.exe, cl rejects them,
:: the version comes back None and configure dies subscripting it.  Skipping
:: openssl skips the whole question.
::
:: --clang-cl sets gyp's `clang` variable.  With --ninja that alone is not
:: enough -- see patches/0104, which is what makes the ninja generator run
:: clang-cl rather than the cl.exe on PATH.  Without --ninja, gyp writes
:: MSBuild projects with the ClangCL platform toolset, node's own path.
python configure.py ^
    %GENERATOR% ^
    --verbose ^
    --dest-cpu=%DEST_CPU% ^
    --shared ^
    --without-node-snapshot ^
    --without-ssl ^
    --v8-disable-temporal-support ^
    --with-intl=small-icu ^
    --clang-cl=%CLANG_VERSION%
if errorlevel 1 exit 1

:: One target, not `all`: node, npm, openssl and the test binaries are all in
:: this build graph and none of them are wanted.
::
:: The default matters: bare `ninja` runs cores+2 jobs, and V8's compiler and
:: maglev translation units take a gigabyte or two each, so an unbounded build
:: on a wide machine exhausts memory.
if not defined CPU_COUNT set "CPU_COUNT=4"
::
:: With MSBuild, the project rather than the solution: MSBuild builds a
:: project's references along with it, and addressing one project inside
:: node.sln would mean knowing the solution folder gyp filed it under.
:: common.gypi sends the output to out/<Configuration>/, the same place
:: ninja puts it.
if "%V8_WIN_TOOLCHAIN%"=="conda-clang-cl" (
    ninja -C out/Release -j%CPU_COUNT% v8
    if errorlevel 1 exit 1
) else (
    msbuild tools\v8_gypfiles\v8.vcxproj /m:%CPU_COUNT% /p:Configuration=Release /p:Platform=%DEST_CPU% /clp:NoItemAndPropertyList;Verbosity=minimal /nologo
    if errorlevel 1 exit 1
)

:: Everything past here is the same job on every platform; see install.py.
python "%RECIPE_DIR%\install.py" ^
    --source-dir . ^
    --build-dir out/Release ^
    --prefix "%LIBRARY_PREFIX%" ^
    --version "%PKG_VERSION%" ^
    --target-platform "%target_platform%"
if errorlevel 1 exit 1
