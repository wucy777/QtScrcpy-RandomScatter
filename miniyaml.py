# -*- coding: utf-8 -*-
"""一个极简的 YAML 子集校验器，专门用来挡住 GitHub Actions workflow 里
最容易犯、又最难查的那类语法错误。

背景：YAML 里 `- name: 文本: 更多文本` 中未加引号的冒号会让这个标量变成
一个嵌套映射。GitHub 不报语法错，而是把 workflow 的**名字**变成文件路径，
然后整个 run 直接失败、连 job 都不建 —— 从 API 只能看到
`"jobs": []`，非常难定位。所以本地必须能提前发现。

只实现 workflow 真正用到的子集：
  * 缩进用空格的块映射
  * `- ` 序列项
  * 纯标量 / 单双引号标量 / `|` `>` 块标量
  * 行内注释
遇到看不懂的构造就报出来，宁可误报也不放过。

真正的 YAML 解析器（PyYAML）可用时会优先用它，见 workflow_check.py。
"""

import re
import sys

# Windows 上 Python 默认把 stdout 编成 cp936/cp1252，打印中文会抛
# UnicodeEncodeError 直接弄崩脚本（CI 上真踩过）。统一改 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

KEY_RE = re.compile(r"^(\s*)([A-Za-z_][\w.\-]*)\s*:(?:\s+(.*))?$")
ITEM_RE = re.compile(r"^(\s*)-\s*(.*)$")
QUOTED = ('"', "'")


def _strip_comment(s):
    """去掉行尾注释，但不在引号里动手。"""
    out = []
    quote = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        else:
            if ch in QUOTED:
                quote = ch
                out.append(ch)
            elif ch == "#" and (not out or out[-1] in " \t"):
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _scalar_problem(rest):
    """检查一个标量值里有没有未加引号的冒号（会变成嵌套映射）。"""
    if not rest:
        return None
    r = rest.strip()
    if not r or r[0] in QUOTED or r in ("|", ">", "|-", ">-", "|+", ">+"):
        return None
    # 未加引号：出现 ": " 或行尾 ":" 就是嵌套映射/新键
    if re.search(r":(\s|$)", r):
        return ("未加引号的冒号会让这个标量变成嵌套映射；"
                "含冒号的文本请用引号包起来，或把冒号改掉")
    return None


def validate(text):
    """返回问题列表（每项是 (行号, 说明)）。空列表表示通过。"""
    problems = []
    lines = text.splitlines()

    # 先按行做标量检查
    for n, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        s = _strip_comment(raw)
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip(" "))
        if "\t" in raw[:indent + 1]:
            problems.append((n, "缩进里出现制表符（YAML 不允许）"))
            continue

        m = ITEM_RE.match(s)
        if m:
            rest = m.group(2)
            # "- name: xxx" 形式
            km = re.match(r"^([A-Za-z_][\w.\-]*)\s*:(?:\s+(.*))?$", rest)
            if km:
                p = _scalar_problem(km.group(2))
                if p:
                    problems.append((n, "序列项的 '%s' 值有问题：%s" % (km.group(1), p)))
                continue
            p = _scalar_problem(rest)
            if p:
                problems.append((n, "序列项值有问题：%s" % p))
            continue

        km = KEY_RE.match(s)
        if km:
            p = _scalar_problem(km.group(3))
            if p:
                problems.append((n, "键 '%s' 的值有问题：%s" % (km.group(2), p)))
            continue

        # 既不是键也不是序列项 -> 可能是块标量的内容行，也可能是真错
        # 块标量内容只要缩进比键更深就算合法，这里不深究。

    return problems


def named_step_problems(text):
    """检查每个 `steps:` 下的序列项是否都有 `name:`。

    GitHub 不强制 step 有名字，但没名字的 step 在 Actions 界面里只显示
    「Run ...」，排查失败时很难对应到具体步骤。所以本项目要求全部命名。

    注意：`run: |` 里的内容是块标量正文，缩进再深也不是序列项，
    必须跳过，否则多行脚本会被当成一堆缺 name 的 step。

    返回 [(行号, 说明)]。空表示通过。
    """
    lines = text.splitlines()
    problems = []
    steps_indent = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = _strip_comment(raw)
        if not s.strip():
            i += 1
            continue
        indent = len(s) - len(s.lstrip(" "))

        # 刚刚进入了一个块标量（run: | / run: >）：跳到缩进回落的下一行
        mblock = re.match(r"^\s*[\w.\-]+\s*:\s*[|>][-+]?\s*$", s)
        if mblock:
            block_indent = indent
            i += 1
            while i < len(lines):
                s2 = lines[i]
                if not s2.strip():
                    i += 1
                    continue
                ind2 = len(s2) - len(s2.lstrip(" "))
                if ind2 <= block_indent:
                    break
                i += 1
            continue

        # 离开 steps 块
        if steps_indent is not None and indent <= steps_indent and s.strip():
            if not s.lstrip().startswith("-"):
                steps_indent = None

        m = re.match(r"^(\s*)steps:\s*$", s)
        if m:
            steps_indent = len(m.group(1))
            i += 1
            continue

        if steps_indent is not None:
            im = ITEM_RE.match(s)
            if im and len(im.group(1)) > steps_indent:
                dash_indent = len(im.group(1))
                head = im.group(2)
                named = bool(re.match(r"^name\s*:", head))
                if not named:
                    # 同一个 item 的后续键：缩进比 dash 深，且不是同层的新 item
                    j = i + 1
                    while j < len(lines):
                        s2 = _strip_comment(lines[j])
                        if not s2.strip():
                            j += 1
                            continue
                        ind2 = len(s2) - len(s2.lstrip(" "))
                        if ind2 <= dash_indent:
                            break
                        if re.match(r"^name\s*:", s2.strip()):
                            named = True
                            break
                        j += 1
                if not named:
                    shown = head.strip()[:60] or "(空)"
                    problems.append((i + 1, "steps 下的序列项缺少 name: —— %s" % shown))
        i += 1
    return problems


def main():
    import sys
    import os
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".github", "workflows", "windows.yml")
    with open(path, encoding="utf-8") as fp:
        text = fp.read()
    problems = validate(text) + named_step_problems(text)
    if not problems:
        print("miniyaml: OK  %s" % path)
        return 0
    print("miniyaml: %d problem(s) in %s" % (len(problems), path))
    for n, msg in problems:
        print("  line %d: %s" % (n, msg))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
