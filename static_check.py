# -*- coding: utf-8 -*-
"""补丁一致性静态检查。

不做完整编译（本地/CI 早期都没有 Qt），但会检查最容易出错的地方：
  1. 每个成员函数的声明与定义签名是否匹配
  2. 每个成员函数调用点的实参个数是否与声明一致
  3. 补丁是否只用了上游已存在的符号（避免引入未定义引用）
  4. 原版 keymap JSON 的每个字段仍被解析（向后兼容性）
  5. 概率分布相关字段/分支是否齐全，且缺省必须回落 uniform
  6. 括号是否配平

检查对象（按优先级自动选择）：
  * 打过补丁的真实源码树（用 --tree <目录> 指定，或自动在 ../_cache/ 里找）
  * 本地开发用的 files/（改好的完整文件）

CI 里用打过补丁的树检查，所以本仓库不必携带任何第三方源码。

任何一项不通过都会以非 0 退出，供 CI 早期拦截。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 相对 QtScrcpyCore 根的路径
REL = {
    "game_h": "src/device/controller/inputconvert/inputconvertgame.h",
    "game_cpp": "src/device/controller/inputconvert/inputconvertgame.cpp",
    "key_h": "src/device/controller/inputconvert/keymap/keymap.h",
    "key_cpp": "src/device/controller/inputconvert/keymap/keymap.cpp",
}


def _resolve_tree():
    """找到一份「补丁后的」源码目录。

    1. 命令行 --tree
    2. CI: src/QtScrcpy/QtScrcpyCore（工作目录）
    3. 本地: ../_cache/ci_sim/QtScrcpy/QtScrcpyCore
    4. 本地: ../_cache/diff_test/QtScrcpy/QtScrcpyCore
    5. 兜底: files/（开发用，里面就是改好的完整文件）
    """
    cands = []
    for i, a in enumerate(sys.argv):
        if a == "--tree" and i + 1 < len(sys.argv):
            cands.append(sys.argv[i + 1])
        elif a.startswith("--tree="):
            cands.append(a.split("=", 1)[1])
    cands += [
        os.path.join(os.getcwd(), "src", "QtScrcpy", "QtScrcpyCore"),
        os.path.join(os.getcwd(), "QtScrcpy", "QtScrcpyCore"),
        os.path.join(HERE, "..", "_cache", "ci_sim", "QtScrcpy", "QtScrcpyCore"),
        os.path.join(HERE, "..", "_cache", "diff_test", "QtScrcpy", "QtScrcpyCore"),
        os.path.join(HERE, "..", "_cache", "verify_tree", "QtScrcpy", "QtScrcpyCore"),
    ]
    for c in cands:
        c = os.path.abspath(c)
        if all(os.path.exists(os.path.join(c, *REL[k].split("/"))) for k in REL):
            return c, "patched tree"
    f = os.path.join(HERE, "files")
    if all(os.path.exists(os.path.join(f, *REL[k].split("/"))) for k in REL):
        return f, "files/ (local dev copy)"
    return None, None


TREE, TREE_KIND = _resolve_tree()
if TREE is None:
    print("[ERROR] 找不到补丁后的源码树，也无法回退到 files/", file=sys.stderr)
    sys.exit(2)

GAME_H = os.path.join(TREE, *REL["game_h"].split("/"))
GAME_CPP = os.path.join(TREE, *REL["game_cpp"].split("/"))
KEY_H = os.path.join(TREE, *REL["key_h"].split("/"))
KEY_CPP = os.path.join(TREE, *REL["key_cpp"].split("/"))

FAIL = []


def check(ok, what):
    print(("[ OK ] " if ok else "[FAIL] ") + what)
    if not ok:
        FAIL.append(what)


def read(p):
    with open(p, "r", encoding="utf-8") as fp:
        return fp.read()


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


# ---------------------------------------------------------------- 1. README
def test_backward_compat_fields():
    """原版 keymap 的字段必须全都还在解析，否则老脚本会失效。"""
    kc = read(KEY_CPP)
    for field in ("switchKey", "mouseMoveMap", "speedRatio", "speedRatioX",
                  "speedRatioY", "startPos", "smallEyes", "keyMapNodes",
                  "switchMap", "androidKey", "clickNodes", "delay",
                  "leftKey", "rightKey", "upKey", "downKey",
                  "leftOffset", "rightOffset", "upOffset", "downOffset",
                  "centerPos", "endPos", "startDelay", "dragSpeed"):
        if field in ("delay",):
            ok = f'"{field}"' in kc
        else:
            ok = f'"{field}"' in kc
        check(ok, f"upstream field still parsed: {field}")


# ---------------------------------------------------------------- 2. 新字段
def test_new_field_optional():
    """randomPos 必须只走可选路径：缺失时行为等价于原版。"""
    kc = read(KEY_CPP)
    check('getItemRandomPos' in kc, "keymap.cpp defines getItemRandomPos")
    check('"randomPos"' in kc, "keymap.cpp reads randomPos")
    check('"startRandomPos"' in kc, "keymap.cpp reads mouseMoveMap.startRandomPos")
    # 缺失时不能写入错误并中断
    m = re.search(r"RandomPos KeyMap::getItemRandomPos.*?\n\}", kc, re.S)
    print("      ---- getItemRandomPos body ----")
    print("\n".join("      " + l for l in m.group(0).splitlines()))
    check(m is not None, "getItemRandomPos definition found")
    body = m.group(0) if m else ""
    check("return result;" in body, "getItemRandomPos returns default (RS_NONE) on missing field")
    check("goto parseError" not in body, "a bad randomPos never aborts parsing")


# ---------------------------------------------------------------- 2b. 分布
def test_distribution_support():
    """非均匀分布：均匀随机本身就是统计指纹，必须支持高斯。"""
    kh = read(KEY_H)
    check("RD_GAUSS" in kh, "keymap.h declares a gauss distribution mode")
    check("RD_UNIFORM" in kh, "keymap.h keeps the uniform mode")
    check("Dist dist" in kh or "dist = RD_UNIFORM" in kh,
          "RandomPos carries a dist field, defaulting to uniform (back-compat)")
    check("double sigma" in kh, "RandomPos carries sigma")
    check("double outlier" in kh, "RandomPos carries an outlier probability")

    kc = read(KEY_CPP)
    check('"dist"' in kc, "keymap.cpp parses randomPos.dist")
    check('"gauss"' in kc, "keymap.cpp accepts dist=gauss")
    check('"sigma"' in kc, "keymap.cpp parses randomPos.sigma")
    check('"outlier"' in kc, "keymap.cpp parses randomPos.outlier")

    gc = read(GAME_CPP)
    check("nextGauss" in gc, "inputconvertgame.cpp implements a normal RNG")
    check("std::log" in gc and "std::cos" in gc, "nextGauss uses Box-Muller")
    check("RD_GAUSS" in gc, "scatterPos branches on the gauss mode")
    check("forceUniform" in gc, "the outlier path falls back to uniform")

    # 缺省必须是 uniform：老 JSON 的行为不能变
    body = re.search(r"RandomPos KeyMap::getItemRandomPos.*?\n\}", kc, re.S)
    check(body is not None, "getItemRandomPos definition found for dist parsing")
    if body:
        src = body.group(0)
        guard = src.find('checkItemString(obj, "dist")')
        first_set = src.find("result.dist =")
        check(guard != -1 and first_set > guard,
              "dist is only assigned inside the explicit 'dist present' guard")
        check(src.count("result.dist =") == 2,
              "dist has exactly two assignments (gauss / uniform)")
        # 结构体默认值必须是 uniform，否则老 JSON 的行为就变了
        check(re.search(r"Dist dist = RD_UNIFORM", kh) is not None,
              "RandomPos defaults to RD_UNIFORM (old JSON keeps old behaviour)")

    # 高斯必须把落点夹在区域内，不能放出去
    sp = re.search(r"QPointF InputConvertGame::scatterPos.*?\n\}", gc, re.S)
    check(sp is not None, "scatterPos definition found")
    if sp:
        src = sp.group(0)
        check("radiusPx * radiusPx" in src,
              "circle gauss uses rejection sampling inside the radius")
        check("accepted" in src, "circle gauss has a bounded rejection loop")
        check("qBound(x1, gx, x2)" in src, "rect gauss clamps back into the rect")


# ---------------------------------------------------------------- 3. 签名
def parse_decls(header, cls):
    """从类声明里抽出成员函数名 -> 参数个数。"""
    text = strip_comments(read(header))
    m = re.search(rf"class {cls}\b.*?\n\{{(.*?)\n\}};", text, re.S)
    if not m:
        return None, text
    body = m.group(1)
    decls = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        mm = re.match(r"^(?:virtual\s+)?(?:static\s+)?[\w:<>,\s\*&]+?(\w+)\s*\((.*?)\)\s*(?:const)?\s*;?$", line)
        if mm:
            name = mm.group(1)
            params = mm.group(2).strip()
            n = 0 if params in ("", "void") else len(split_params(params))
            decls[name] = n
    return decls, body


def split_params(params):
    depth = 0
    cur = ""
    out = []
    for ch in params:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def test_signatures():
    decls, _ = parse_decls(GAME_H, "InputConvertGame")
    check(decls is not None, "parsed InputConvertGame declaration")
    cpp = strip_comments(read(GAME_CPP))

    # 定义与声明参数个数必须一致
    for name, n in decls.items():
        # 只检查本补丁关心的几个，避免误判
        if name not in ("scatterPos", "processKeyClick", "processKeyDrag",
                        "processKeyClickMulti", "processKeySteerWheel"):
            continue
        mm = re.search(rf"InputConvertGame::{name}\s*\((.*?)\)\s*\n?\{{", cpp, re.S)
        if not mm:
            # 有的定义带返回类型在同一行
            mm = re.search(rf"InputConvertGame::{name}\s*\(([^;{{]*?)\)\s*(?:const\s*)?\{{", cpp, re.S)
        check(mm is not None, f"definition found for {name}")
        if mm:
            got = len(split_params(mm.group(1).strip()))
            check(got == n, f"{name}: declaration has {n} params, definition has {got}")

    # 调用点参数个数
    for name in ("processKeyClick", "processKeyDrag"):
        n = decls.get(name)
        if n is None:
            continue
        for mm in re.finditer(rf"(?<![\w:]){name}\s*\((.*?)\)\s*;", cpp, re.S):
            got = len(split_params(mm.group(1).strip()))
            if got == 0:
                continue
            check(got == n, f"call site {name}(...) passes {got} args (expected {n})")


# ---------------------------------------------------------------- 4. 调用点
def test_call_sites_use_random():
    cpp = read(GAME_CPP)
    # KMT_CLICK / KMT_CLICK_TWICE 必须把 random 传进去
    check(cpp.count("node.data.click.keyNode.random") >= 2,
          "KMT_CLICK call sites pass random config")
    check("node.data.clickTwice.keyNode.random" in cpp,
          "KMT_CLICK_TWICE call site passes random config")
    check("node.data.drag.keyNode.random" in cpp,
          "KMT_DRAG call site passes random config")
    check("nodes[i].random" in cpp, "KMT_CLICK_MULTI scatters each click node")
    check("mouseMove.smallEyes.random" in cpp, "smallEyes honours random config")
    check("mouseMove.startRandom" in cpp, "mouse-move start honours random config")
    check("m_scatterCache" in cpp, "scatter cache used so down/up share one point")
    check("std::sqrt" in cpp, "circle radius computed in pixel space (true circle)")


# ---------------------------------------------------------------- 5. 成员
def test_header_members():
    h = read(GAME_H)
    check("#include <QHash>" in h, "inputconvertgame.h includes QHash for the cache")
    check("m_scatterCache" in h, "scatter cache member declared")
    check("scatterPos" in h, "scatterPos declared")
    kh = read(KEY_H)
    check("struct RandomPos" in kh, "keymap.h declares RandomPos")
    check("RS_CIRCLE" in kh and "RS_RECT" in kh, "RandomPos has circle and rect shapes")
    check("RandomPos random;" in kh, "KeyNode carries a RandomPos")
    check("startRandom" in kh, "mouseMove carries startRandom")
    check("bool isValid()" in kh, "RandomPos::isValid available")


# ---------------------------------------------------------------- 6. 括号平衡
def test_brace_balance():
    """编辑补丁文件最容易犯的错就是括号不配对。这里逐文件核对。

    先剥掉注释和字符串/字符字面量，避免把注释里的括号算进去。
    """
    for path in (KEY_H, KEY_CPP, GAME_H, GAME_CPP):
        raw = read(path)
        text = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        text = re.sub(r"//[^\n]*", "", text)
        # 去掉字符串与字符字面量（含转义）
        text = re.sub(r'"(\\.|[^"\\])*"', '""', text)
        text = re.sub(r"'(\\.|[^'\\])*'", "''", text)

        pairs = {"{": "}", "(": ")", "[": "]"}
        stack = []
        bad = None
        for i, ch in enumerate(text):
            if ch in pairs:
                stack.append((ch, i))
            elif ch in pairs.values():
                if not stack:
                    bad = f"unmatched closing {ch!r}"
                    break
                op, _ = stack.pop()
                if pairs[op] != ch:
                    bad = f"{op!r} closed by {ch!r}"
                    break
        if not bad and stack:
            bad = f"{len(stack)} unclosed {stack[0][0]!r}"
        name = os.path.basename(path)
        check(bad is None, f"{name}: braces/brackets balanced" if not bad
              else f"{name}: {bad}")


# ---------------------------------------------------------------- 7. 无残留
def test_no_garbage():
    for path, names in ((KEY_H, ["RS_INVALID", "randomShapeName", "checkItemRandomPos"]),
                        (KEY_CPP, ["randomShapeName", "checkRandomPosObject"]),
                        (GAME_CPP, ["TODO", "FIXME", "XXX"])):
        text = read(path)
        for n in names:
            check(n not in text, f"{os.path.basename(path)}: no leftover '{n}'")


print("检查对象：%s  (%s)" % (TREE, TREE_KIND))
print()
for fn in (test_backward_compat_fields, test_new_field_optional,
           test_distribution_support, test_signatures,
           test_call_sites_use_random, test_header_members,
           test_brace_balance, test_no_garbage):
    print("--- " + fn.__name__ + " ---")
    fn()

print()
if FAIL:
    print(f"STATIC CHECK FAILED: {len(FAIL)}")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("STATIC CHECK PASSED")
