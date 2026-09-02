"""When the Windows ninja build fails in a V8 translation unit, find out why.

Runs from out/Release after `ninja v8` failed.  For the object given, it reads
the response file gyp wrote for it and compiles the file three more ways, so
one failed CI round answers what would otherwise take several:

- without the precompiled-header flags (/Yu, /FI, /Fp): does the same source
  compile on its own?  If so, the PCH is what hides the definitions.
- preprocessed only (/E): does torque-defined-classes-tq.inc get entered, and
  does a class definition from it survive to the output?
- the generated .inc itself: size and first lines, in case torque wrote
  something other than what it writes on unix.

Diagnostic only; nothing here changes the build.
"""

import os
import re
import subprocess
import sys


def main():
    obj = sys.argv[1]
    marker = sys.argv[2]  # a class the error called incomplete
    rsp = obj + ".rsp"
    if not os.path.exists(rsp):
        print("diag: no %s" % rsp)
        return
    args = open(rsp).read().split()
    src = next((a for a in args if a.endswith(".cc")), None)
    cmd = subprocess.check_output(
        ["ninja", "-t", "commands", obj], text=True
    ).strip().splitlines()[-1]
    m = re.search(r'-- "([^"]+)"', cmd)
    clang_cl = m.group(1) if m else "clang-cl"
    m = re.search(r"/c (\S+)", cmd)
    src = m.group(1) if m else src
    print("diag: compiler %s, source %s, %d flags in %s" % (clang_cl, src, len(args), rsp))
    pch = [a for a in args if a.startswith(("/Yu", "/FI", "/Fp", "/Yc"))]
    print("diag: pch flags: %s" % " ".join(pch))

    def run(name, extra, strip_pch, capture=None):
        flags = [a for a in args if not (strip_pch and a.startswith(("/Yu", "/FI", "/Fp", "/Yc")))]
        argv = [clang_cl, "-m64", "/nologo"] + flags + extra + ["/c", src]
        with open("diag_%s.rsp" % name, "w") as fh:
            fh.write(" ".join(flags + extra))
        p = subprocess.run(
            [clang_cl, "-m64", "/nologo", "@diag_%s.rsp" % name, "/c", src],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace",
        )
        errors = [l for l in p.stderr.splitlines() if "error:" in l]
        print("diag: %s -> exit %d, %d errors" % (name, p.returncode, len(errors)))
        for l in errors[:3]:
            print("      " + l[:200])
        return p

    run("with_pch", ["/Fodiag_with_pch.obj"], strip_pch=False)
    run("no_pch", ["/Fodiag_no_pch.obj"], strip_pch=True)
    p = run("preprocess", ["/E", "/Fidiag_pre.i"], strip_pch=False)
    pre = p.stdout
    if not pre and os.path.exists("diag_pre.i"):
        pre = open("diag_pre.i", errors="replace").read()
    print("diag: preprocessed %d bytes; 'torque-defined-classes-tq.inc' mentioned %d times; "
          "'class %s :' defined %d times; 'class %s;' forward-declared %d times"
          % (len(pre), pre.count("torque-defined-classes-tq.inc"),
             marker, pre.count("class %s :" % marker),
             marker, pre.count("class %s;" % marker)))
    # The torque action: how long its one cmd.exe line is, and whether the
    # source that owns the torque-defined classes is still on it.
    import glob
    for rsp_path in glob.glob(os.path.join("obj", "tools", "v8_gypfiles",
                                           "run_torque*.rsp")):
        line = open(rsp_path).read()
        print("diag: %s: %d chars; torque-defined-classes.tq at offset %d; "
              "last entry: %s" % (rsp_path, len(line),
                                  line.find("torque-defined-classes.tq"),
                                  line.split()[-1] if line.split() else "-"))
    gen = os.path.join("obj", "gen", "torque-generated", "src", "objects")
    if os.path.isdir(gen):
        sizes = sorted((os.path.getsize(os.path.join(gen, n)), n)
                       for n in os.listdir(gen))
        print("diag: %d generated files in %s; smallest: %s" % (
            len(sizes), gen, ", ".join("%s=%d" % (n, z) for z, n in sizes[:6])))

    inc = os.path.join("obj", "gen", "torque-generated", "src", "objects",
                       "torque-defined-classes-tq.inc")
    if os.path.exists(inc):
        text = open(inc, errors="replace").read()
        print("diag: %s: %d bytes, %d lines, 'class %s :' %d times" % (
            inc, len(text), text.count("\n"), marker, text.count("class %s :" % marker)))
        print("\n".join(text.splitlines()[:5]))
    else:
        print("diag: %s MISSING" % inc)


if __name__ == "__main__":
    main()
