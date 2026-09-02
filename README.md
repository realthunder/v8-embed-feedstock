# v8-embed-feedstock

A conda package for **V8 built to be embedded**: one shared library holding
the JavaScript engine, its default platform and its startup snapshot, plus
the public headers. No `d8`, no `node`, no JavaScript command-line tool --
an embedder does not need one.

## Why it is packaged

Because a bare V8 cannot reach the machine, and that is the point.

An engine on its own is ECMAScript and WebAssembly. It has no `process`, no
`fs`, no `net`, no `child_process` and no module loader, because every one
of those is something a *host* adds on top -- in node's case, in C++. So an
embedder that never adds them has a guest that cannot get out, not as a
matter of policy that has to be audited and re-audited, but because the
primitives are not present to be found. FreeCAD wants that for a desktop
Pyodide: expression and spreadsheet Python coming out of a downloaded
document, running with none of the application's reach.

The alternative was measured and rejected. Embedding **libnode** instead
gives the engine plus node's whole environment, and node's environment
cannot be taken back by subtraction: after `delete globalThis.process`, a
dynamic `import("node:fs")` reached through the `Function` constructor still
returned a working filesystem module with read *and* write. `import()` rides
node's loader, which is not reachable from JavaScript to remove. `node
--permission` is not an answer either -- it cannot even load Pyodide
(`process.binding`), and it is per-process, so it cannot separate host from
guest.

There is no usable V8 on conda-forge to fall back on: their `v8` package
rotted at 8.9.83 in 2021.

## What this package is, and what it is not

**Engine only.** What ships is the library, the headers, the snapshot and
the build metadata that ties them together. There is deliberately no
convenience API and no ready-made context: the embedder writes the twenty
lines that create the platform, the isolate and the ArrayBuffer allocator,
and the embedder decides what goes on the global object.

That is a security decision, not laziness. The moment a package hands back a
context with a default environment in it, the confinement above stops being
a property of the artifact and becomes a promise about its defaults. So the
package hands back a bare isolate and an empty global, and anything a guest
can see is something the consumer put there on purpose.

The Web-platform shim a real guest needs -- `TextDecoder`, `URL`,
`performance.now`, `crypto.getRandomValues`, a byte reader scoped to one
directory -- is therefore *not* here. It belongs to the consumer, and
writing it is the mechanism: you provide those and you do not provide a
filesystem. `WebAssembly` comes free with the engine, which is what loads
the wheels.

## Why it is built out of node's source tree

Upstream V8's own build wants `depot_tools`, `gclient`, GN and a downloaded
clang -- a build system that does not sit inside a conda recipe, and the
reason conda-forge's `v8` has not moved since 2021.

Node vendors V8 at `deps/v8` and builds it with ordinary GYP files and an
ordinary toolchain, and keeps it current: node 26.6.0 carries V8 14.6. So
this feedstock is a fork of
[conda-forge/nodejs-feedstock](https://github.com/conda-forge/nodejs-feedstock),
which buys the V8 pin, node's V8 patches, and their cross-build for free,
and stays cheap to follow forward.

**Nothing forks node's source.** The changes are recipe-level:

- `tools/v8_gypfiles/v8.gyp` already describes the whole engine as one
  shared library. With `component=="shared_library"` the `v8` target becomes
  a `shared_library` over `v8_snapshot`, `v8_base`, `v8_compiler`,
  `v8_libbase` and `v8_libplatform`, and gyp's ninja generator
  whole-archives static dependencies of a shared library -- so the result is
  the complete engine with a SONAME. No monolith target had to be written.
- The one patch that is ours,
  `0100-Let-a-build-ask-gyp-for-a-shared-V8.patch`, makes that mode
  reachable. `tools/gyp_node.py` appends `-Dcomponent=static_library` after
  everything else, which also puts it out of reach of `GYP_DEFINES`, since a
  later `-D` wins. The patch reads an environment variable instead and keeps
  the static default.
- The build asks ninja for the `v8` target alone. node, npm, openssl and the
  test binaries are all in the same graph and none of them are built.

Six of nodejs-feedstock's eight patches are carried unchanged, so a node
bump can take their set as it stands. The two that only touch code this
build never compiles -- system abseil, and a `stdlib.h` include for the
`fmt` vendored under `deps/LIEF` -- are dropped rather than kept dormant.
abseil, highway, simdutf and zlib come from node's bundled copies, and gyp
whole-archives a shared library's static dependencies, so all four are
linked *into* `libv8.so` and the package has no runtime dependency on any
of them.

Keeping node's bundled abseil rather than conda's is deliberate. conda's
`libabseil` is built for C++17 and V8 compiles as C++20, and abseil's ABI
changes with the standard level -- whether `absl::string_view` is
`std::string_view` -- so the two are not interchangeable. It also keeps the
package off abseil's repinning treadmill. The cost of vendoring is the
usual one: a CVE in any of these means rebuilding `v8-embed` rather than
updating a package.

The second patch that is ours,
`0101-Give-abseil-the-build-configuration-V8-targets-get.patch`, is what
makes that safe. `v8.gyp` opens with `'includes': ['toolchain.gypi',
'features.gypi']` and a `target_defaults` that compiles every V8 target
with `-fvisibility=hidden`. `abseil.gyp` includes only `toolchain.gypi`
and inherits neither, so abseil alone was built with a different
configuration from the V8 code including its headers. That cost two things:

- **`NDEBUG`**, which lives in `features.gypi`. abseil's `mutex.h` puts
  `Mutex::Dtor` inline under `NDEBUG` and out of line in `mutex.cc`
  without it, so the symbol was defined twice. MSVC rejects that
  (`LNK2005`); ELF links it anyway, which is worse -- the ODR violation
  was silently present in every unix build.
- **Visibility.** abseil was the one part of the library at default
  visibility: 973 exported `absl::` symbols, against 0 for highway and
  simdutf. A process that already has an abseil (conda's `libprotobuf`
  pulls one in) would then have two implementations under identical
  mangled names, resolved by load order.

The same thing happens a third time in `tools/icu/icu-generic.gyp`, fixed
by `0103-Hide-the-bundled-ICU-s-symbols.patch`: with the bundled ICU
compiled in, 9824 `icu_78::` symbols were exported. That one is worse than
abseil's, because ICU's `icu_78` namespace guards against mixing two
*versions* and does nothing about two copies of the *same* version -- which
is what an embedder gets when the host already links ICU 78, as anything
using Qt does -- and because ICU carries process-global state.

With all of them the library exports `v8::`, `cppgc::`, `v8_inspector::`
and weak `std::` template instantiations, and nothing else: 2743 symbols.

**If you change what gets linked in, re-measure.** The ICU leak appeared
only when small-icu started compiling ICU into the library:

```
nm -DC --defined-only lib/libv8.so.* | awk '{$1="";$2="";print}' \
  | grep -oE '^[A-Za-z_][A-Za-z0-9_]*::' | sort | uniq -c | sort -rn
```

### Why the vendored libraries are not unbundled

Asked and answered, so it does not have to be re-litigated. None of node's
`--shared-*` switches reach these: they apply to `deps/zlib` and
`deps/simdutf`, which are separate trees from `deps/v8/third_party/`, and
`v8.gyp` has no `node_shared_*` conditionals at all. Unbundling any of them
means writing the patch here.

| | V8 pins | conda-forge has | |
|---|---|---|---|
| abseil | git rev `6d8e1a5c`, no tag, +5 local patches | `libabseil`, `cxx17` builds only | possible, but wrong |
| highway | git rev `8295336d`, no tag | `libhwy` 1.4.0 | untagged revision |
| simdutf | 7.7.0 | 9.0.0 | version mismatch |
| zlib | Chromium's fork, renamed `Cr_z_*` | zlib 1.3.x | renaming is the design |

abseil is the one that is demonstrably possible -- nodejs-feedstock replaces
`abseil.gyp` with an empty target and adds `-labsl_*` link flags -- and the
one where doing it would hurt most:

- V8's copy carries five local patches, among them
  `0001-Turn-on-hardened-mode.patch`. Hardened mode is a security build
  option, and swapping in stock abseil turns it off silently, in a package
  whose whole purpose is running untrusted code.
- conda-forge builds abseil as C++17 and V8 compiles as C++20. Abseil does
  not support mixing standard versions across its boundary; the `cxx17` in
  the build string is that ABI variant, not a note.

The benefit would have been security updates without a rebuild, and it is
mostly illusory here: `run_exports` pins consumers to an exact `v8-embed`,
so any update cascades a rebuild anyway; V8 is the real attack surface and
a node bump rebuilds it along with refreshed copies of all four.

What was worth doing instead is making the private copies invisible, which
patch 0101 does. V8's public headers name none of `absl::`, `hwy::` or
`simdutf::`, so with their symbols hidden a consumer cannot tell they are
there.

## Using it

```cmake
find_package(v8-embed CONFIG REQUIRED)
target_link_libraries(myapp PRIVATE v8-embed::v8)
```

or `pkg-config --cflags --libs v8-embed`. `recipe/test_consumer/` is a
complete working embedder, and it is run as part of the package test.

On Windows the DLL is installed to `Library/bin/v8.dll` and its import
library to `Library/lib/v8.lib`; the CMake target knows about both.

### `v8-gn.h`, and why you must not skip it

V8's public headers are not self-describing. `v8config.h`, `v8-internal.h`
and the inline code in `v8-local-handle.h` read a handful of macros
recording how the engine was compiled -- pointer compression, the sandbox,
the target OS, whether the engine is in a shared library -- and get object
layout wrong, silently, if your idea of them differs from the library's.

A GN build of V8 hands embedders those macros in a generated `v8-gn.h`, and
`v8config.h` includes it when `V8_GN_HEADER` is defined. Node's GYP port has
no equivalent: it passes them on the command line to every V8 target, which
is enough for code inside node's build and nothing else. So this recipe
reconstructs that header (`recipe/emit_v8_gn_header.py`) from the flags the
build actually used, intersected with the macros the public headers actually
test, and ships it.

**Compile with `-DV8_GN_HEADER`.** The CMake target and the `.pc` file both
carry it; if you build by hand, you must add it. Without it your translation
units describe a different engine than the one they link against.

One thing the header cannot carry: V8 reads `DEBUG` in its public headers,
and the packaged library is built without it. Do not compile an embedder
with `-DDEBUG`.

### ICU, and what "small" means

Every platform builds node's bundled ICU into the library with
`--with-intl=small-icu`, which is why the package has **no runtime
dependencies at all** beyond libc and the C++ runtime. `icudt78_dat` is
embedded in the library and `DT_NEEDED` names no ICU.

**"small" here is about node's source tree, not the locale data.** node
ships `deps/icu-small/source/data/in/icudt78l.dat.bz2`, 11.3 MB
compressed, and uses it whole; the `icu_locales` filter is not applied to
a prebuilt `.dat`. Measured on a build of this recipe, every one of the
twenty locales tested is supported with real data rather than a fallback:

| | |
|---|---|
| `Intl.DateTimeFormat('th' / 'hi' / 'ja')` | `มกราคม` / `जनवरी` / `1月` |
| `Intl.NumberFormat('ar-EG')` | `١٬٢٣٤٫٥` |
| `Intl.DisplayNames(['fr']).of('de')` | `allemand` |
| `Collator('sv')` vs `Collator('de')` sorting `['z','ä']` | `zä` vs `äz` -- genuinely different collations |
| `timeZoneName: 'long'` for `Asia/Tokyo` | `Japan Standard Time` |

Strings are not an ICU question at all: V8 stores UTF-16 internally, and
UTF-8 at the embedder boundary, `JSON`, codepoint iteration and ordering
are core V8. `normalize()` and `/\p{Script=Han}/u` work too, from the
Unicode character database. The cost of embedding the data is about 10 MB
of library size.

`system-icu` was rejected: it works only through `pkg-config`, so it cannot
be used on Windows at all, and it puts an `icu` pin on the package --
conda-forge bumps ICU roughly yearly, and because `run_exports` pins
consumers to an exact `v8-embed`, every bump would cascade a rebuild into
them.

## ABI

There is none between versions. V8 makes no compatibility promise across any
two of its versions, not even patch levels, and the headers inline code over
layouts the engine fixes at compile time. So:

- the SONAME is the whole version (`libv8.so.14.6.202.34`),
- `run_exports` pins consumers to that exact version,
- the shipped CMake version file reports compatible only on an exact match.

Rebuilding consumers on every bump is the honest cost of embedding V8.

## Platforms

Built from source on `linux-64`, `linux-aarch64`, `osx-64`, `osx-arm64` and
`win-64`.

**Windows is the one platform with no inherited build.** nodejs-feedstock
does not compile anything there: its Windows source is the official binary
zip and its build script is six `COPY` lines, and a binary zip has no V8
static libraries to link a shared library out of. Node itself builds from
source on Windows, so `bld.bat` is written against that -- the same
`configure.py`, the same GYP files. Nothing about the shared-V8 mechanism
is unix-specific; `v8.gyp` carries the Windows half of it, and a component
build is how Chromium builds V8 there.

**The compiler is clang-cl, not MSVC.** V8 dropped MSVC at 13.0 and node
followed at 24 ("ClangCL is required to compile on Windows"); the headers
no longer compile with `cl.exe` -- `FLEXIBLE_ARRAY_MEMBER` is a zero-length
array in a base class, C2503. Where the clang-cl comes from is the
`v8_win_toolchain` variant, and for now both are built:

- `conda-clang-cl`: conda-forge's pinned `clang-cl` package layered on the
  `vs2022` activation (which stays for INCLUDE, LIB and `link.exe`), through
  ninja like every other platform. Two things had to be fixed for that,
  because the ninja generator on Windows is a path node itself never runs
  -- node's Windows build is `vcbuild.bat`, which is MSBuild. `configure.py
  --clang-cl` only set gyp's `clang` variable, which the ninja generator
  ignores; patch 0104 makes it emit the `make_global_settings` the
  generator does read, and adds compiler-rt's builtins to the link. And gyp
  wrote a Python `map` repr into the precompiled-header compile's flags
  (nodejs/node#57633, fixed upstream in gyp-next#355 after node 26.6.0's
  copy); patch 0105 is that fix.
- `vs-clang-cl`: the clang-cl that ships inside Visual Studio on the CI
  image, through MSBuild and the ClangCL platform toolset -- node's own
  build. Unpinned, since it is whatever the runner image installed.

One more thing differs on Windows, in `bld.bat`: **no SONAME.** `v8.gyp`
turns `soname_version` into a product extension without checking the OS,
which on Windows would name the DLL `v8.so.14.6.202.34`. A Windows consumer
gets its version guarantee from the package pin instead. Everything else --
the flags, the ICU mode, the install step -- is the same on all five.

`win-arm64` is left out for now: a cross build of a compiler-heavy target
with nowhere to run its own tests.

## Licensing

V8's own code is **BSD-3-Clause** (`deps/v8/LICENSE.v8`, "Copyright the V8
project authors"). The library is not only that, and the difference is worth
knowing before you ship it:

- V8 compiles in its own copy of glibc's `sin`/`cos`
  (`deps/v8/third_party/glibc`), which is **LGPL-2.1-or-later**. Every V8 and
  every Chrome carries this. Shipping as a *shared* library, with the source
  URL in the recipe, is what satisfies it.
- The vendored abseil, highway and wasm-api headers are Apache-2.0, fp16 and
  simdutf MIT-ish, zlib Zlib, fdlibm and strongtalk permissive.

Every license file for code that is linked in is reproduced in the package.
Node itself is MIT and none of it ships here. All of it is compatible with
linking into an LGPL-2.1+ application, which is what FreeCAD is.
