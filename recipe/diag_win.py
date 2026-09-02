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
    # The torque action: its one command line, how the .tq sources are
    # spelled on it, and -- the direct test -- what torque writes for the
    # torque-defined classes when run with those exact arguments versus
    # the same arguments with every backslash turned into a slash.
    import glob
    import shutil
    import tempfile
    # ninja deletes an action's response file once the edge succeeds, so
    # the command is read back from the rspfile_content gyp wrote into
    # the target's .ninja file ($-continued lines, "$ " escapes spaces).
    lines = []
    ninja_path = os.path.join("obj", "tools", "v8_gypfiles", "run_torque.ninja")
    if os.path.exists(ninja_path):
        text = open(ninja_path, errors="replace").read()
        for m in re.finditer(r"rspfile_content = (.*?)(?<!\$)\n", text, re.S):
            lines.append(m.group(1).replace("$\n", "").replace("$ ", " "))
    for rsp_path, line in [("run_torque.ninja#%d" % i, l) for i, l in enumerate(lines)]:
        toks = line.split()
        tq = [t.strip('"') for t in toks if t.strip('"').endswith(".tq")]
        print("diag: %s: %d chars, %d .tq args; first %s; torque-defined-classes "
              "spelled %s" % (rsp_path, len(line), len(tq), tq[:1],
                              [t for t in tq if "torque-defined-classes" in t]))
        print("diag: head: " + line[:300].replace("\n", " "))
        exe = toks[0].strip('"')
        if not os.path.isfile(exe):
            continue
        real_out = os.path.join("obj", "gen", "torque-generated")
        for label, fix in (("as-is", lambda t: t), ("slashes", lambda t: t.replace("\\", "/"))):
            out_dir = tempfile.mkdtemp(prefix="diag_torque_")
            # torque does not create output directories; ninja pre-creates
            # the ones it declares.  Mirror the real tree's directories.
            for root, dirs, _ in os.walk(real_out):
                for d in dirs:
                    os.makedirs(os.path.join(out_dir, os.path.relpath(os.path.join(root, d), real_out)), exist_ok=True)
            argv = [exe]
            skip = False
            for t in toks[1:]:
                t = t.strip('"')
                if skip:
                    argv.append(out_dir)
                    skip = False
                    continue
                if t == "-o":
                    argv.append(t)
                    skip = True
                    continue
                argv.append(fix(t) if t.endswith(".tq") else t)
            p = subprocess.run(argv, cwd=os.path.join("..", "..", "tools", "v8_gypfiles"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, errors="replace")
            target = os.path.join(out_dir, "src", "objects", "torque-defined-classes-tq.inc")
            size = os.path.getsize(target) if os.path.exists(target) else -1
            print("diag: torque re-run (%s): exit %d, torque-defined-classes-tq.inc = %d bytes%s"
                  % (label, p.returncode, size,
                     ("; stderr: " + p.stderr[:200]) if p.stderr.strip() else ""))
            shutil.rmtree(out_dir, ignore_errors=True)
    gen = os.path.join("obj", "gen", "torque-generated", "src", "objects")
    if os.path.isdir(gen):
        sizes = sorted((os.path.getsize(os.path.join(gen, n)), n)
                       for n in os.listdir(gen))
        print("diag: %d generated files in %s; smallest: %s" % (
            len(sizes), gen, ", ".join("%s=%d" % (n, z) for z, n in sizes[:6])))

    inc = os.path.join("obj", "gen", "torque-generated", "src", "objects",
                       "torque-defined-classes-tq.inc")
    # Which edge owns the file, and whether anything wrote it after torque.
    q = subprocess.run(["ninja", "-t", "query", inc], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, errors="replace")
    print("diag: ninja -t query: " + " | ".join(q.stdout.split("\n")[:6]))
    for path in (inc, os.path.join("torque.exe"),
                 os.path.join("obj", "tools", "v8_gypfiles", "run_torque.actions_rules_copies.stamp")):
        if os.path.exists(path):
            print("diag: mtime %s = %.0f" % (path, os.path.getmtime(path)))
    if os.path.exists(inc):
        text = open(inc, errors="replace").read()
        print("diag: %s: %d bytes, %d lines, 'class %s :' %d times" % (
            inc, len(text), text.count("\n"), marker, text.count("class %s :" % marker)))
        print("\n".join(text.splitlines()[:5]))
    else:
        print("diag: %s MISSING" % inc)


if __name__ == "__main__":
    main()
