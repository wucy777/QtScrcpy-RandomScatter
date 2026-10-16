# -*- coding: utf-8 -*-
"""Workflow / 补丁包自检。

不依赖 PyYAML，用最小缩进解析确认结构正确，并检查 workflow 里引用的
每个本地文件都真实存在 —— 这是 CI 最容易踩的坑。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, ".github", "workflows", "windows.yml")
FAIL = []


def check(ok, what):
    print(("[ OK ] " if ok else "[FAIL] ") + what)
    if not ok:
        FAIL.append(what)


def main():
    text = open(WF, encoding="utf-8").read()
    lines = text.splitlines()

    # ---------------------------------------------------------- 基本结构
    check(lines[0].startswith("name:"), "workflow starts with name:")
    check(re.search(r"^on:", text, re.M) is not None, "has 'on:' trigger block")
    check("workflow_dispatch:" in text, "manual trigger available")
    check(re.search(r"^jobs:", text, re.M) is not None, "has jobs:")
    check("preflight:" in text, "preflight job present")
    check(re.search(r"^  build:", text, re.M) is not None, "build job present")
    check("needs: preflight" in text, "build waits for preflight")

    # ---------------------------------------------------------- 缩进一致性
    bad_indent = 0
    for i, l in enumerate(lines, 1):
        if not l.strip() or l.strip().startswith("#"):
            continue
        if "\t" in l:
            print(f"      tab at line {i}")
            bad_indent += 1
    check(bad_indent == 0, "no tabs in workflow (YAML forbids tabs)")

    # 每个 step 都要有 name 和 uses/run
    steps = re.findall(r"^      - name: (.+)$", text, re.M)
    uses = len(re.findall(r"^        uses: ", text, re.M))
    runs = len(re.findall(r"^        run: ", text, re.M))
    check(len(steps) >= 8, f"at least 8 named steps (found {len(steps)})")
    check(uses + runs >= len(steps), f"every step has uses or run ({uses}+{runs} vs {len(steps)})")

    # ---------------------------------------------------------- 引用的文件
    # CI 里被显式调用的本地文件（仓库本身只应有这些 + 补丁文本）
    referenced = [
        "apply_patch.py",
        "static_check.py",
        "test_scatter.cpp",
        "workflow_check.py",
        "random-scatter.patch",
    ]
    for r in referenced:
        p = os.path.join(HERE, r)
        check(os.path.exists(p), f"workflow-referenced file exists: {r}")

    # 仓库必须是 patch-only：不得携带第三方 C++ 源码。
    # 注意排除：
    #   .git / __pycache__          非源码
    #   files/                      本地开发用的改好的完整文件（已 gitignore）
    #   src/                        CI 现场拉下来的上游源码（由 apply_patch 生成）
    SKIP_DIRS = {".git", "__pycache__", "files", "src", "stubs", "_cache", "build"}
    vendored = []
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith((".cpp", ".h")) and fn != "test_scatter.cpp":
                vendored.append(os.path.relpath(os.path.join(root, fn), HERE))
    check(not vendored,
          "repo is patch-only (no vendored third-party sources)" if not vendored
          else f"vendored sources present: {vendored}")

    # files/ 若存在（本地开发用），必须包含四个目标文件且会被 git 忽略
    if os.path.isdir(os.path.join(HERE, "files")):
        targets = [
            "src/device/controller/inputconvert/keymap/keymap.h",
            "src/device/controller/inputconvert/keymap/keymap.cpp",
            "src/device/controller/inputconvert/inputconvertgame.h",
            "src/device/controller/inputconvert/inputconvertgame.cpp",
        ]
        for t in targets:
            p = os.path.join(HERE, "files", *t.split("/"))
            check(os.path.exists(p), f"local files/ copy present: {t}")
        gi = open(os.path.join(HERE, ".gitignore"), encoding="utf-8").read()
        check("/files/" in gi or "files/" in gi,
              "files/ is git-ignored so it never reaches GitHub")

    # 补丁文本本身
    pf = os.path.join(HERE, "random-scatter.patch")
    if os.path.exists(pf):
        ptxt = open(pf, encoding="utf-8", errors="replace").read()
        for t in ("keymap.h", "keymap.cpp", "inputconvertgame.h", "inputconvertgame.cpp"):
            check(t in ptxt, f"random-scatter.patch covers {t}")
        check("gauss" in ptxt, "random-scatter.patch carries the gauss distribution")

    # ---------------------------------------------------------- 路径逻辑
    check("python apply_patch.py src" in text or "apply_patch.py src" in text,
          "apply_patch invoked with the CI layout")
    check("ci\\win\\build_for_win.bat" in text, "uses upstream build script")
    check("ci\\win\\publish_for_win.bat" in text, "uses upstream publish script")
    check("working-directory: src" in text, "build/publish run inside the source tree")

    # ---------------------------------------------------------- 上游固定
    sha = re.search(r"QTSCRCPYCORE_SHA:\s*([0-9a-f]{40})", text)
    check(sha is not None, "QtScrcpyCore commit is pinned to a full SHA")
    check("UPSTREAM_TAG: v4.2.1" in text, "upstream QtScrcpy tag pinned to v4.2.1")

    # ---------------------------------------------------------- 产物检查
    check("upload-artifact@v4" in text, "uploads artifacts")
    check("if-no-files-found: error" in text, "fails loudly if the artifact is empty")
    check("QtScrcpy.exe" in text, "verifies the produced exe")

    # ---------------------------------------------------------- 中文路径
    check("keymap/" in text and "tuned" in text.lower(),
          "ships the tuned keymap as a separate step")

    print()
    if FAIL:
        print(f"WORKFLOW CHECK FAILED: {len(FAIL)}")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("WORKFLOW CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
