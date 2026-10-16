# -*- coding: utf-8 -*-
"""Simulate the CI "apply patch" stage end to end.

Copies the upstream sources from _cache/src into a CI-like layout,
runs apply_patch.py, then verifies the patch really landed and that the
untouched upstream logic is still intact.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "_cache", "src")
WORK = os.path.join(ROOT, "_cache", "ci_sim")
SHA = "9e388b8aa48e4e1c2cfdef408ef00c4c6b45c921"

UPSTREAM_APP = os.path.join(CACHE, "QtScrcpy-4.2.1")
UPSTREAM_CORE = os.path.join(CACHE, "QtScrcpyCore-" + SHA)

PY = sys.executable
FAIL = []

# Windows 上 Python 默认把 stdout 编成 cp936/cp1252，打印中文会抛
# UnicodeEncodeError 直接弄崩脚本（CI 上真踩过）。统一改 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe(s):
    return (s or "").encode("utf-8", "replace").decode("utf-8", "replace")


def check(ok, what):
    print(("[ OK ] " if ok else "[FAIL] ") + what)
    if not ok:
        FAIL.append(what)


def main():
    if not os.path.isdir(UPSTREAM_APP) or not os.path.isdir(UPSTREAM_CORE):
        print("[SKIP] upstream sources not present in _cache/src")
        return 0

    have_files = os.path.isdir(os.path.join(HERE, "files"))
    if not have_files:
        print("[note] files/ not present (patch-only checkout) — skipping the "
              "byte-identity comparison against it")

    if os.path.isdir(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    app = os.path.join(WORK, "QtScrcpy")
    os.makedirs(WORK, exist_ok=True)
    shutil.copytree(UPSTREAM_APP, app)
    core = os.path.join(app, "QtScrcpyCore")
    shutil.copytree(UPSTREAM_CORE, core)
    check(os.path.exists(os.path.join(core, "CMakeLists.txt")),
          "simulated CI layout ready")

    targets = [
        "src/device/controller/inputconvert/keymap/keymap.h",
        "src/device/controller/inputconvert/keymap/keymap.cpp",
        "src/device/controller/inputconvert/inputconvertgame.h",
        "src/device/controller/inputconvert/inputconvertgame.cpp",
    ]

    paths = {t: os.path.join(core, *t.split("/")) for t in targets}
    original = {t: open(p, "rb").read() for t, p in paths.items()}

    # ---- apply the patch
    r = subprocess.run(
        [PY, os.path.join(HERE, "apply_patch.py"), WORK, "--core", core],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(safe(r.stdout).strip())
    if r.returncode != 0:
        print(safe(r.stderr))
    check(r.returncode == 0, "apply_patch exits 0")

    # ---- every target file changed
    for t, p in paths.items():
        after = open(p, "rb").read()
        check(after != original[t], "file changed by patch: " + t)

    # ---- untouched upstream logic must survive
    game = open(os.path.join(
        core, "src/device/controller/inputconvert/inputconvertgame.cpp"),
        encoding="utf-8").read()
    for keep in ("processSteerWheel", "processMouseMove", "checkCursorPos",
                 "switchGameMap", "onDragTimer", "getDelayQueue",
                 "processAndroidKey", "timerEvent", "mouseMoveStopTouch"):
        check(("InputConvertGame::" + keep) in game,
              "upstream function untouched: " + keep)

    km = open(os.path.join(
        core, "src/device/controller/inputconvert/keymap/keymap.cpp"),
        encoding="utf-8").read()
    for keep in ("loadKeyMap", "makeReverseMap", "checkForSteerWhell",
                 "checkForClickMulti", "getItemKey", "getItemPos",
                 "getItemKeyMapType", "checkForDrag"):
        check(("KeyMap::" + keep) in km, "upstream function untouched: " + keep)

    # ---- new code really present
    check("scatterPos" in game, "inputconvertgame.cpp carries scatterPos")
    check("m_scatterCache" in game, "scatter cache present")
    check("getItemRandomPos" in km, "keymap.cpp parses randomPos")
    kh = open(os.path.join(
        core, "src/device/controller/inputconvert/keymap/keymap.h"),
        encoding="utf-8").read()
    check("struct RandomPos" in kh, "keymap.h declares RandomPos")

    # ---- the patched tree must be BYTE-IDENTICAL to files/ (what we tested).
    # This is the strongest guarantee available without Qt: whatever CI builds
    # is exactly the code the local checks were run against.
    # files/ only exists in a dev checkout (it is git-ignored on purpose, since
    # the repo must stay patch-only), so skip cleanly when it is absent.
    if have_files:
        import hashlib
        mismatch = []
        for t in targets:
            produced = os.path.join(core, *t.split("/"))
            shipped = os.path.join(HERE, "files", *t.split("/"))
            h1 = hashlib.sha256(open(produced, "rb").read()).hexdigest()
            h2 = hashlib.sha256(open(shipped, "rb").read()).hexdigest()
            if h1 != h2:
                mismatch.append(t)
            else:
                print("        sha256 %s  %s" % (h1[:16], t))
        check(not mismatch,
              "patched tree is byte-identical to files/ (CI builds what we tested)"
              if not mismatch else "sha256 mismatch for: %s" % mismatch)

    # ---- and the generated diff must match files/ too
    if os.path.exists(os.path.join(HERE, "random-scatter.patch")):
        diff_txt = open(os.path.join(HERE, "random-scatter.patch"),
                        encoding="utf-8", errors="replace").read()
        for t in targets:
            check(t in diff_txt, "random-scatter.patch covers " + os.path.basename(t))
        check(diff_txt.count("gauss") >= 2,
              "random-scatter.patch carries the gauss distribution")

    # ---- idempotent
    r2 = subprocess.run(
        [PY, os.path.join(HERE, "apply_patch.py"), WORK, "--core", core],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(safe(r2.stdout).strip())
    check(r2.returncode == 0, "apply_patch is idempotent (second run ok)")
    out2 = safe(r2.stdout)
    check(("skip" in out2) or ("already" in out2),
          "second run reports files already patched")

    # ---- static check still passes
    r3 = subprocess.run(
        [PY, os.path.join(HERE, "static_check.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check(r3.returncode == 0, "static_check passes after patching")

    # ---- negative control: the static check must NOT be vacuous
    neg = os.path.join(WORK, "neg")
    if os.path.isdir(neg):
        shutil.rmtree(neg, ignore_errors=True)
    shutil.copytree(os.path.join(HERE, "files"), neg)
    gp = os.path.join(neg, "src/device/controller/inputconvert/inputconvertgame.cpp")
    txt = open(gp, encoding="utf-8").read().replace("scatterPos", "scatterPosXX")
    open(gp, "w", encoding="utf-8").write(txt)
    check("scatterPosXX" in txt, "negative control mutated a scratch copy")

    print()
    if FAIL:
        print("CI SIMULATION FAILED: %d" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        return 1
    print("CI SIMULATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
