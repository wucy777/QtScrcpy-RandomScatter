#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把随机散布补丁套用到一份干净的 QtScrcpy 源码上。

用法：
    python apply_patch.py <上游源码根目录>
    python apply_patch.py <上游源码根目录> --core <QtScrcpyCore 目录>
    python apply_patch.py . --emit-diff random-scatter.patch   # 开发用：重新生成 diff
    python apply_patch.py <上游源码根目录> --check             # 只检查能否套用

默认按 `random-scatter.patch`（unified diff）打补丁，**不依赖 git**，
纯 Python 实现，因此 CI 里不需要装 git 也能跑。

为什么要用 diff 而不是直接放改好的文件：本仓库不包含任何 QtScrcpy 源码，
只有这份补丁文本。CI 在构建时现场拉官方 v4.2.1 再打补丁，
既避免分发第三方源码，也保证补丁始终对着官方版本生效。

开发时若本地存在 files/（改好的完整文件），可用 --from-files 直接覆盖，
用 --emit-diff 从 files/ 重新生成 diff。
"""

import argparse
import difflib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")
DEFAULT_DIFF = os.path.join(HERE, "random-scatter.patch")

# 相对 QtScrcpyCore 仓库根的路径
TARGETS = [
    "src/device/controller/inputconvert/keymap/keymap.h",
    "src/device/controller/inputconvert/keymap/keymap.cpp",
    "src/device/controller/inputconvert/inputconvertgame.h",
    "src/device/controller/inputconvert/inputconvertgame.cpp",
]


def read(path):
    with open(path, "r", encoding="utf-8") as fp:
        return fp.read()


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fp:
        fp.write(text)


# ---------------------------------------------------------------- diff 生成
def make_diff(core):
    """从 files/ 对着 core 生成 unified diff。"""
    out = []
    for rel in TARGETS:
        src = os.path.join(FILES, *rel.split("/"))
        dst = os.path.join(core, *rel.split("/"))
        diff = list(difflib.unified_diff(
            read(dst).splitlines(keepends=True),
            read(src).splitlines(keepends=True),
            fromfile="a/" + rel, tofile="b/" + rel))
        out.extend(diff)
    return "".join(out)


# ---------------------------------------------------------------- diff 解析
def parse_diff(text):
    """把 unified diff 解析成 [(rel, [hunks]), ...]，保持出现顺序。"""
    files = []
    cur = None
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            old = line[4:].strip()
            new = lines[i + 1][4:].strip() if i + 1 < len(lines) else ""
            rel = new[2:] if new.startswith("b/") else new
            cur = {"rel": rel, "old": old, "hunks": []}
            files.append(cur)
            i += 2
            continue
        if line.startswith("@@") and cur is not None:
            # 解析 hunk 头 @@ -a,b +c,d @@
            head = line.strip()
            try:
                spec = head.split("@@")[1].strip()
                minus, plus = spec.split(" ")[:2]
                o_start = int(minus[1:].split(",")[0])
                n_start = int(plus[1:].split(",")[0])
            except Exception:
                raise ValueError("无法解析 hunk 头: %r" % head)
            body = []
            i += 1
            while i < len(lines):
                l2 = lines[i]
                if l2.startswith("@@") or l2.startswith("--- ") or l2.startswith("diff "):
                    break
                if l2.startswith("\\"):      # "\ No newline at end of file"
                    i += 1
                    continue
                if l2[:1] in (" ", "+", "-"):
                    body.append(l2)
                    i += 1
                    continue
                if l2.strip() == "":
                    # 空行在上下文里表现为 " " 丢失了结尾空格，补回来
                    body.append(" \n" if l2 == "\n" else l2)
                    i += 1
                    continue
                break
            cur["hunks"].append({"o_start": o_start, "n_start": n_start, "body": body})
            continue
        i += 1
    return files


def apply_hunks(original_lines, hunks, rel):
    """把 hunks 套到原始行列表上；返回新行列表。"""
    out = []
    pos = 0          # 已写入 out 的原始行数
    for h in hunks:
        target = h["o_start"] - 1        # diff 行号从 1 开始
        if target < pos:
            raise ValueError("%s: hunk 重叠或乱序 (@@ -%d)" % (rel, h["o_start"]))
        out.extend(original_lines[pos:target])
        pos = target
        for l2 in h["body"]:
            tag, content = l2[0], l2[1:]
            if tag == " ":
                if pos >= len(original_lines) or original_lines[pos] != content:
                    got = original_lines[pos].rstrip("\n") if pos < len(original_lines) else "<EOF>"
                    raise ValueError("%s: 上下文不匹配，期望 %r 实际 %r"
                                     % (rel, content.rstrip("\n"), got))
                out.append(content)
                pos += 1
            elif tag == "-":
                if pos >= len(original_lines) or original_lines[pos] != content:
                    got = original_lines[pos].rstrip("\n") if pos < len(original_lines) else "<EOF>"
                    raise ValueError("%s: 待删除行不匹配，期望 %r 实际 %r"
                                     % (rel, content.rstrip("\n"), got))
                pos += 1
            elif tag == "+":
                out.append(content)
    out.extend(original_lines[pos:])
    return out


def already_applied(current_text, hunks):
    """判断这份文件是不是已经打过补丁了。

    diff 本身没法重复套用（套过一次后上下文就变了），所以先用一个便宜且
    可靠的探针：补丁要新增的那些行，是不是都已经在了。

    只在「套用失败」和「--check」两条路径上用得到，不影响正常补丁流程。
    """
    probes = []
    for h in hunks:
        for l2 in h["body"]:
            if l2.startswith("+") and l2[1:].strip():
                probes.append(l2[1:].rstrip("\n"))
    if not probes:
        return False
    lines = set(l.rstrip("\n") for l in current_text.splitlines())
    hits = sum(1 for p in probes if p in lines)
    # 九成以上的新增行都在，就认定已经打过了
    return hits >= max(1, int(len(probes) * 0.9))


def apply_diff(core, diff_path, check_only=False):
    if not os.path.exists(diff_path):
        print("[ERROR] 补丁文件不存在: %s" % diff_path, file=sys.stderr)
        return 2
    files = parse_diff(read(diff_path))
    if not files:
        print("[ERROR] 补丁文件是空的: %s" % diff_path, file=sys.stderr)
        return 2

    by_rel = {f["rel"]: f for f in files}
    missing = [t for t in TARGETS if t not in by_rel]
    if missing:
        print("[ERROR] 补丁未覆盖这些文件: %s" % missing, file=sys.stderr)
        return 2

    changed = 0
    for rel in TARGETS:
        f = by_rel[rel]
        dst = os.path.join(core, *rel.split("/"))
        if not os.path.exists(dst):
            print("[ERROR] 上游文件缺失: %s" % dst, file=sys.stderr)
            return 2
        current_text = read(dst)
        if already_applied(current_text, f["hunks"]):
            print("[skip] %s 已经是补丁后版本" % rel)
            continue

        original = current_text.splitlines(keepends=True)
        try:
            new_lines = apply_hunks(original, f["hunks"], rel)
        except ValueError as e:
            print("[ERROR] %s" % e, file=sys.stderr)
            print("[hint] 上游版本可能不是 v4.2.1 / 该 commit，补丁无法套用",
                  file=sys.stderr)
            return 3
        new_text = "".join(new_lines)
        if new_text == current_text:
            print("[skip] %s 已经是补丁后版本" % rel)
            continue
        added = sum(1 for h in f["hunks"] for l in h["body"] if l.startswith("+"))
        removed = sum(1 for h in f["hunks"] for l in h["body"] if l.startswith("-"))
        print("[patch] %s  (+%d -%d)" % (rel, added, removed))
        if not check_only:
            write(dst, new_text)
        changed += 1

    print("[check] %d 个文件需要打补丁" % changed if check_only
          else "[done] 已更新 %d 个文件" % changed)
    return 0


def copy_from_files(core, check_only=False):
    changed = 0
    for rel in TARGETS:
        src = os.path.join(FILES, *rel.split("/"))
        dst = os.path.join(core, *rel.split("/"))
        if not os.path.exists(src):
            print("[ERROR] 补丁文件缺失: %s" % src, file=sys.stderr)
            return 2
        if not os.path.exists(dst):
            print("[ERROR] 上游文件缺失: %s" % dst, file=sys.stderr)
            return 2
        new, old = read(src), read(dst)
        if old == new:
            print("[skip] %s 已经是补丁后版本" % rel)
            continue
        diff = list(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="a/" + rel, tofile="b/" + rel))
        print("[patch] %s  (+%d -%d)"
              % (rel,
                 sum(1 for l in diff if l.startswith("+") and not l.startswith("+++")),
                 sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))))
        if not check_only:
            shutil.copyfile(src, dst)
        changed += 1
    print("[check] %d 个文件需要打补丁" % changed if check_only
          else "[done] 已更新 %d 个文件" % changed)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("upstream", help="上游源码根目录（含 QtScrcpy/ 子目录）")
    ap.add_argument("--core", default=None,
                    help="QtScrcpyCore 目录；默认 <upstream>/QtScrcpy/QtScrcpyCore")
    ap.add_argument("--diff", default=DEFAULT_DIFF, help="补丁文件路径")
    ap.add_argument("--from-files", action="store_true",
                    help="开发用：直接从 files/ 覆盖，而不是套 diff")
    ap.add_argument("--emit-diff", default=None,
                    help="开发用：从 files/ 重新生成 diff（不改任何源码）")
    ap.add_argument("--check", action="store_true", help="只检查补丁能否套用")
    args = ap.parse_args()

    core = args.core or os.path.join(args.upstream, "QtScrcpy", "QtScrcpyCore")
    if not os.path.isdir(core):
        print("[ERROR] QtScrcpyCore 目录不存在: %s" % core, file=sys.stderr)
        return 2

    if args.emit_diff:
        if not os.path.isdir(FILES):
            print("[ERROR] --emit-diff 需要本地存在 files/: %s" % FILES, file=sys.stderr)
            return 2
        write(args.emit_diff, make_diff(core))
        print("[diff] 已写出 %s" % args.emit_diff)
        return 0

    if args.from_files:
        if not os.path.isdir(FILES):
            print("[ERROR] --from-files 需要本地存在 files/: %s" % FILES, file=sys.stderr)
            return 2
        return copy_from_files(core, args.check)

    return apply_diff(core, args.diff, args.check)


if __name__ == "__main__":
    sys.exit(main())
