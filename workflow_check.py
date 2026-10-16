# -*- coding: utf-8 -*-
"""Workflow / 补丁包自检。

不依赖 PyYAML，用最小缩进解析确认结构正确，并检查 workflow 里引用的
每个本地文件都真实存在 —— 这是 CI 最容易踩的坑。
"""

import os
import re
import sys

# Windows 上 Python 默认把 stdout 编成 cp936/cp1252，打印中文会抛
# UnicodeEncodeError 直接弄崩脚本（CI 上真踩过）。统一改 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

    # ---------------------------------------------------------- YAML 合法性
    # 这一步很关键：YAML 里未加引号的 `name: 文本: 更多文本` 会被解析成
    # 嵌套映射，GitHub 侧的报错是「workflow 名字变成文件路径」然后整个 run
    # 直接失败、连 job 都不建，极难查。有 PyYAML 就真解析，没有就用自带的
    # miniyaml 做针对性校验。
    parsed = False
    try:
        import yaml  # type: ignore
        try:
            doc = yaml.safe_load(text)
            check(isinstance(doc, dict), "workflow parses as a YAML mapping")
            trig = doc.get("on", doc.get(True))
            check(isinstance(trig, dict) and bool(trig),
                  "workflow has a non-empty 'on:' trigger")
            jobs_doc = doc.get("jobs") or {}
            check(isinstance(jobs_doc, dict) and bool(jobs_doc), "workflow has jobs")
            for jname, job in (jobs_doc or {}).items():
                if not isinstance(job, dict):
                    continue
                for si, s in enumerate(job.get("steps", []) or []):
                    if not (isinstance(s, dict) and isinstance(s.get("name"), str)):
                        keys = list(s.keys()) if isinstance(s, dict) else type(s).__name__
                        check(False,
                              f"{jname}: step #{si + 1} is missing a 'name:' "
                              f"(keys={keys})")
                check(all(isinstance(s, dict) and isinstance(s.get("name"), str)
                          for s in (job.get("steps") or [])),
                      f"{jname}: every step has a plain string name")
            print("      (validated with PyYAML)")
            parsed = True
        except Exception as e:
            check(False, f"workflow YAML parses ({type(e).__name__}: {e})")
            parsed = True
    except ImportError:
        pass

    if not parsed:
        sys.path.insert(0, HERE)
        import miniyaml  # noqa: E402
        probs = miniyaml.validate(text)
        for n, msg in probs:
            print(f"      line {n}: {msg}")
        check(not probs,
              "no YAML scalar traps (e.g. unquoted colon in a step name)"
              if not probs else f"{len(probs)} YAML scalar problem(s)")
        # 正向确认：序列项确实被识别成了「键: 值」而不是嵌套映射
        step_names = re.findall(r"^\s*-\s*name:\s*(.+)$", text, re.M)
        check(len(step_names) >= 8,
              f"miniyaml saw {len(step_names)} step names (expected >= 8)")
        check(all(": " not in n for n in step_names),
              "no step name contains ': '")
        print("      (validated with miniyaml; PyYAML unavailable)")

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
        "package_win.ps1",
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
    # 打包刻意不用上游的 publish_for_win.bat：它是 UTF-8 但被 cmd.exe 按 ANSI
    # 代码页读取，中文注释会变成乱码并报语法错误；它还依赖 Unix 的 cp。
    # 只看「实际调用」，注释里提到它不算。
    code_only = "\n".join(l for l in lines if not l.strip().startswith("#"))
    invoked = re.search(r"(call|cmd\.exe\s*/c|&|\./)\s*[\"']?[^\n\"']*publish_for_win\.bat",
                        code_only)
    check(invoked is None,
          "does NOT invoke upstream publish_for_win.bat (encoding + cp breakage)"
          if invoked is None else
          f"still invokes publish_for_win.bat: {invoked.group(0)[:60]}")
    check("package_win.ps1" in text, "uses our own PowerShell packaging script")
    check("working-directory: src" in text, "build runs inside the source tree")

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

    # ---------------------------------------------------------- 控制台编码
    # CI 上真踩过：Windows runner 的 Python 把 stdout 编成 cp1252，脚本里
    # 打印中文的 print() 直接抛 UnicodeEncodeError 把构建搞挂。
    # 所有会打印非 ASCII 的脚本都必须自己把 stdout 改成 UTF-8。
    for name in ("apply_patch.py", "static_check.py", "workflow_check.py",
                 "miniyaml.py", "ci_sim.py"):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        has_non_ascii = any(ord(c) > 127 for c in src)
        if not has_non_ascii:
            continue
        guard = re.search(
            r"for\s+_\w+\s+in\s+\(sys\.stdout,\s*sys\.stderr\)\s*:.*?reconfigure\(\s*encoding",
            src, re.S)
        if guard is None:
            # 也接受逐个流 reconfigure 的写法
            guard = re.search(r"sys\.stdout\.reconfigure\(\s*encoding", src)
        check(guard is not None,
              f"{name}: forces UTF-8 stdout (prints non-ASCII, cp1252 runner would crash)"
              if guard else
              f"{name}: prints non-ASCII but does NOT reconfigure stdout to UTF-8")

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
