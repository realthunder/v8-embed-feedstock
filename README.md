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
abseil, highway, simdutf and zlib come from node's bundled copies and are
linked *into* the library, so the package has no runtime dependency on them.

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
source on Windows perfectly well, so `bld.bat` is written against that --
the same `configure.py`, the same GYP files, MSVC instead of gcc. Nothing
about the shared-V8 mechanism is unix-specific; `v8.gyp` carries the
Windows half of it, and a component build is how Chromium builds V8 there.

Two things differ on Windows, both in `bld.bat`:

- **ICU.** node can only find a system ICU through `pkg-config`, which is
  not something to rely on under MSVC with conda's paths, so Windows builds
  node's bundled `small-icu` into the library. The C++ ABI is identical --
  `V8_INTL_SUPPORT` is not one of the macros the public headers read -- so
  this is a difference in JavaScript locale data and nothing else.
- **No SONAME.** `v8.gyp` turns `soname_version` into a product extension
  without checking the OS, which on Windows would name the DLL
  `v8.so.14.6.202.34`. A Windows consumer gets its version guarantee from
  the package pin instead.

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
