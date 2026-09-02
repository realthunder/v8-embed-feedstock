"""Write the v8-gn.h that an embedder has to compile against.

V8's public headers are not self-describing.  v8config.h, v8-internal.h and
the inline code in v8-local-handle.h read a handful of macros that record how
the engine was configured -- pointer compression, the sandbox, the target OS,
whether the engine lives in a shared library -- and get the object layout
wrong, silently, if the embedder's idea of them differs from the library's.

A GN build of V8 hands those macros to embedders in a generated v8-gn.h, and
v8config.h pulls it in when V8_GN_HEADER is defined.  Node's GYP port has no
equivalent: it passes the macros on the command line to every V8 target,
which is enough for code inside the node build and nothing else.  This
reconstructs the header GN would have written, from the flags the build
actually used, so the package can ship a configuration its consumers cannot
get wrong.

Both inputs are read from the build tree rather than hard-coded, so a node
version bump that turns pointer compression on shows up in the shipped header
without anyone remembering to look.  The flags come from whichever files gyp
generated: a target's .ninja file, or its MSBuild project.
"""

import argparse
import os
import re
import shlex

# Read as `#define name` / `#define name value` on the library side; these are
# what a consumer must be told.  Anything the headers do not test is build
# noise and stays out, so the header says what it means.
_COND = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b(.*)$", re.MULTILINE)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NOT_A_CONFIG = frozenset(
    (
        "defined",
        "__has_attribute",
        "__has_builtin",
        "__has_cpp_attribute",
        "__has_feature",
        "__has_include",
        # The consumer's own build decides these, and V8 does not record them.
        "DEBUG",
        "NDEBUG",
    )
)
# The library is compiled as the thing being built; a consumer is compiled as
# the thing using it.  Same configuration, opposite side.
_BUILDING_TO_USING = {
    "BUILDING_V8_SHARED": "USING_V8_SHARED",
    "BUILDING_V8_PLATFORM_SHARED": "USING_V8_PLATFORM_SHARED",
}


def defines_from_ninja(path):
    """The -D flags gyp handed the compiler for one target."""
    with open(path) as fh:
        text = fh.read()
    # `defines = -DA -DB $\n    -DC ...`, continued with a trailing dollar.
    match = re.search(r"^defines = (.*?)(?<!\$)$", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise SystemExit("no `defines =` line in %s" % path)
    line = match.group(1).replace("$\n", " ")
    out = {}
    for token in shlex.split(line):
        if not token.startswith("-D"):
            continue
        name, sep, value = token[2:].partition("=")
        # `-DFOO` on a command line means `#define FOO 1`, not an empty
        # definition, and the difference is not cosmetic: V8's headers use
        # these as values -- `#if V8_TARGET_ARCH_X64`, `defined(
        # BUILDING_V8_SHARED) || USING_V8_SHARED` -- so an empty define is a
        # preprocessor syntax error rather than a wrong answer.
        out[name] = value if sep else "1"
    return out


def defines_from_vcxproj(path, configuration="Release|x64"):
    """The preprocessor definitions gyp's MSBuild generator gave one target.

    gyp writes them as `A;B=1;...;%(PreprocessorDefinitions)` under the
    ClCompile item definition of each configuration.  A name without a value
    is `/D A`, which cl.exe and clang-cl both read as `#define A 1`, the same
    convention as -D on the ninja side.
    """
    import xml.etree.ElementTree as ET

    ns = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    root = ET.parse(path).getroot()
    wanted = "=='%s'" % configuration
    for group in root.findall("m:ItemDefinitionGroup", ns):
        condition = group.get("Condition", "").replace(" ", "")
        if not condition.endswith(wanted):
            continue
        node = group.find("m:ClCompile/m:PreprocessorDefinitions", ns)
        if node is None or not node.text:
            continue
        out = {}
        for token in node.text.split(";"):
            token = token.strip()
            if not token or token.startswith("%("):
                continue
            name, sep, value = token.partition("=")
            out[name] = value if sep else "1"
        return out
    raise SystemExit("no PreprocessorDefinitions for %s in %s" % (configuration, path))


def macros_tested_by(include_dir):
    """Every identifier the public headers branch on."""
    tested = set()
    for root, _, files in os.walk(include_dir):
        for name in files:
            if not name.endswith(".h"):
                continue
            with open(os.path.join(root, name), errors="replace") as fh:
                # v8config.h spreads its target-OS checks over continued
                # lines; a condition is the whole of it, not its first line.
                text = fh.read().replace("\\\n", " ")
                for condition in _COND.findall(text):
                    tested.update(_IDENT.findall(condition))
    return tested - _NOT_A_CONFIG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ninja", help="the V8 target's .ninja file")
    source.add_argument("--vcxproj", help="the V8 target's MSBuild project file")
    parser.add_argument("--include-dir", required=True, help="V8's public headers")
    parser.add_argument("--out", required=True, help="where to write v8-gn.h")
    args = parser.parse_args()

    if args.ninja:
        built = defines_from_ninja(args.ninja)
    else:
        built = defines_from_vcxproj(args.vcxproj)
    tested = macros_tested_by(args.include_dir)

    emitted = {}
    for name, value in built.items():
        name = _BUILDING_TO_USING.get(name, name)
        if name in tested or name in _BUILDING_TO_USING.values():
            emitted[name] = value

    lines = [
        "// Generated by the v8-embed recipe; do not edit.",
        "//",
        "// The configuration this copy of V8 was compiled with, limited to the",
        "// macros its public headers read.  Compile with -DV8_GN_HEADER and this",
        "// on the include path, or the headers describe a different engine than",
        "// the one you link against.",
        "",
        "#ifndef V8_EMBED_V8_GN_H_",
        "#define V8_EMBED_V8_GN_H_",
        "",
    ]
    for name in sorted(emitted):
        lines.append("#define %s %s" % (name, emitted[name]))
    lines += ["", "#endif  // V8_EMBED_V8_GN_H_", ""]

    with open(args.out, "w") as fh:
        fh.write("\n".join(lines))
    print("wrote %s with %d macros: %s" % (args.out, len(emitted), " ".join(sorted(emitted))))


if __name__ == "__main__":
    main()
