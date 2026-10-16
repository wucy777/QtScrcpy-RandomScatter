# QtScrcpy 随机散布补丁

给 [barry-ran/QtScrcpy](https://github.com/barry-ran/QtScrcpy) **v4.2.1** 加上
「按键落点在指定范围内随机」的能力。

**本仓库不含上游源码。** 只有一份补丁文本 + 校验脚本。
CI 会现场拉取官方代码 + 它锁定的 QtScrcpyCore 提交，套用补丁，再用 Qt 5.15.2 编译。

| 文件 | 作用 |
|---|---|
| `random-scatter.patch` | 补丁本体（unified diff，4 个文件） |
| `apply_patch.py` | 纯 Python 打补丁 / 生成 diff，**不需要 git** |
| `static_check.py` | 补丁一致性检查，直接跑在打过补丁的树上 |
| `test_scatter.cpp` | 散布数学与卡方检验的独立测试（纯 std，可单独编译） |
| `build_test.bat` | 编译并运行上面的测试 |
| `workflow_check.py` | workflow 结构与仓库洁净度检查 |
| `ci_sim.py` | 本地模拟 CI 套补丁（需要 `_cache/src` 有上游源码） |
| `keymap/` | 随构建一起发布的调教好的键位 |

---

## 为什么

原版每个 `KMT_CLICK` 都把触点发到 JSON 里写死的 `pos`，一个像素不差。
本补丁让落点在圆内或矩形内随机，同时**完全兼容原有 keymap JSON**。

顺带说：v4.2.1 里只有方向盘和拖拽带一点内置抖动
（`±0.002` 和 `±0.0005` 的相对偏移），`KMT_CLICK` 是**没有任何随机**的。

---

## 效果

| 官方 4.2.1 | 本补丁 |
|---|---|
| `KMT_CLICK` 固定点 | 可选圆内 / 矩形内随机 |
| — | 概率分布可选：高斯（像真人手指）或均匀 |
| — | 可配 `sigma` 控制集中程度、`outlier` 加一条重尾 |
| 方向盘 `±0.002` 抖动 | 保留 |
| 拖拽 `±0.0005` 抖动 | 保留 |
| — | `KMT_CLICK_TWICE`、`KMT_CLICK_MULTI`、`KMT_DRAG` 起点、视角起点也可随机 |

---

## JSON 用法

固定坐标（与原版完全一致，无需改动旧脚本）：

```json
{
  "comment": "跳",
  "type": "KMT_CLICK",
  "key": "Key_Space",
  "pos": { "x": 0.929, "y": 0.701 },
  "switchMap": false
}
```

加随机 —— 圆形（`p1` 圆心，`p2` 圆周上一点）：

```json
{
  "comment": "开火",
  "type": "KMT_CLICK",
  "key": "LeftButton",
  "pos": { "x": 0.845, "y": 0.744 },
  "switchMap": false,
  "randomPos": {
    "type": "circle",
    "p1": { "x": 0.845,   "y": 0.744 },
    "p2": { "x": 0.85474, "y": 0.744 }
  }
}
```

加随机 —— 矩形（`p1` 左上，`p2` 右下）：

```json
"randomPos": {
  "type": "rect",
  "p1": { "x": 0.596, "y": 0.676 },
  "p2": { "x": 0.640, "y": 0.724 }
}
```

### ⚠️ 区域必须是按钮可点区域的子集

超出按钮的那些点击不会生效（丢键）。区域宁小勿大。

### 概率分布：别用均匀随机

```json
"randomPos": {
  "type": "circle",
  "p1": { "x": 0.845, "y": 0.745 },
  "p2": { "x": 0.8562, "y": 0.745 },
  "dist": "gauss",
  "sigma": 0.45,
  "outlier": 0.08
}
```

| 字段 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `dist` | `gauss` / `uniform` | `uniform` | 落点概率分布 |
| `sigma` | 0.05 ~ 2.0 | `0.4` | 高斯标准差占半径 / 半宽的比例，越小越集中 |
| `outlier` | 0.0 ~ 1.0 | `0` | 以此概率整片区域均匀取点，制造偶发大幅偏移 |

**为什么默认仍是 `uniform`、但强烈建议写 `gauss`：**

`dist` 缺失时按 `uniform` 处理，是为了让早期导出的 JSON 行为不变（向后兼容）。
但**均匀随机的统计特征本身就是一种指纹**：把落点按等面积同心环或等分格计数，
各项近似相等 —— 用卡方检验一眼就能判定「这不是人点的」。

真人按按钮时手指往按钮中间落，中心密、边缘稀，呈钟形分布。

本仓库的 `test_scatter.cpp` 里有现成的对照实验（每项 40 万次采样、8 个等面积同心环）：

```
uniform ring shares: 0.015 0.047 0.078 0.109 0.141 0.172 0.204 0.234
uniform chi2 = 2.61        (df=7, 临界值 24.32)   <- 无法拒绝「均匀」

gauss   ring shares: 0.050 0.136 0.187 0.195 0.169 0.128 0.086 0.050
gauss   chi2 = 276619.65                          <- 压倒性拒绝「均匀」
```

高斯那行的各环占比与解析解
`(1-exp(-t²/2σ²))`（σ=0.4，圆内归一化）逐项吻合到小数点后三位，
所以这不是「加点噪声」，是真的钟形。

`sigma=0.4` 时约 39% 的落点落在半径 40% 以内（面积占比只有 16%），
`sigma` 越小越集中。

`outlier` 用来补一条重尾：真人偶尔会点偏，纯高斯在长序列上仍然偏「太干净」。
`outlier=1.0` 会退化成均匀分布（测试里有这一项）。

### 注意事项

* `pos` 仍然必填，建议写**区域中心**。官方版不认 `randomPos`，
  会照 `pos` 点 —— 所以同一份 JSON 在官方版上也能用，只是不随机。
* 圆的半径按**屏幕像素**计算。相对坐标 x/y 分开按宽高归一化，
  直接画圆会变椭圆（2670×1200 的画面会扁 2.225 倍）。
  高斯采样同样在像素空间做，所以画出来仍是正圆。
* 一次点击的 down / up 用同一个随机点，不会被识别成滑动。
* 支持挂载位置：`KMT_CLICK`、`KMT_CLICK_TWICE`、`KMT_CLICK_MULTI` 的每个
  `clickNodes[i]`、`KMT_DRAG`、`mouseMoveMap.startRandomPos`、`smallEyes.randomPos`。

---

## 编译

### GitHub Actions（推荐）

推上去即可，`.github/workflows/windows.yml` 会自动：

1. 跑静态检查（Ubuntu，不需要 Qt，快速失败）
2. 拉 `QtScrcpy v4.2.1` + `QtScrcpyCore 9e388b8a…`
3. 套补丁
4. 用 Qt 5.15.2 + MSVC 编译 **x64** 和 **x86**
5. 验证产物里有 `QtScrcpy.exe` / `adb.exe` / `scrcpy-server` / `keymap` / `config`
6. 上传 artifact

也可以手动触发（Actions → Run workflow）。

### 本地

```bat
python apply_patch.py <上游源码根目录>
```

纯 Python 实现，不用 git，也不需要网络。目录结构要求：

```
<上游源码根目录>/
└── QtScrcpy/
    ├── CMakeLists.txt
    ├── QtScrcpy/
    └── QtScrcpyCore/       ← 补丁打在这里
```

只检查能否套用（不改文件）：

```bat
python apply_patch.py <上游源码根目录> --check
```

开发时如果要改补丁内容，本地流程是：

1. 准备一份干净的上游 `QtScrcpyCore`；
2. `python apply_patch.py <根目录>` 套上现有补丁；
3. 手工改那 4 个文件；
4. 把改好的文件放到 `files/`（该目录被 gitignore，只用于开发）；
5. `python apply_patch.py <根目录> --from-files --emit-diff random-scatter.patch`
   重新生成补丁文本。

`files/` **绝不会**被提交 —— `workflow_check.py` 和 CI 都会拒绝
仓库里出现第三方 C++ 源码。

也可以直接 `git apply random-scatter.patch`（在有 git 的环境里）。

---

## 验证

```bat
python static_check.py --tree <打过补丁的 QtScrcpyCore>   # 补丁一致性
python workflow_check.py                                  # workflow + 仓库洁净度
python ci_sim.py                                          # 模拟 CI（需要 _cache/src）
build_test.bat                                            # 散布数学 + 卡方检验
```

`static_check.py` 不指定 `--tree` 时会自动在 `_cache/` 里找一份打过补丁的树，
找不到才回退到本地 `files/`。

`static_check.py` 检查的重点：

* 原版 keymap 的 24 个字段是否**全都还在解析**（向后兼容）
* `randomPos` 是否只走**可选路径**（缺失时行为等同原版）
* 成员函数的声明 / 定义 / **调用点参数个数是否一致**
* `dist` / `sigma` / `outlier` 是否齐全，且**缺省必须回落 uniform**
* 四个文件的括号是否配平、有没有残留废弃符号

`test_scatter.cpp` 额外做了**卡方检验**，证明均匀分布会被统计识别、
高斯分布不会（见下文）。

---

## 改了哪四个文件

全部位于 QtScrcpyCore（补丁文本里一一对应）：

| 文件 | 改动 |
|---|---|
| `keymap/keymap.h` | 新增 `struct RandomPos`（含 `shape` / `dist` / `sigma` / `outlier`）；`KeyNode` / `DelayClickNode` / `mouseMove` 各加一个 RandomPos 成员；声明 `getItemRandomPos()` |
| `keymap/keymap.cpp` | 实现 `getItemRandomPos()`；6 处解析点读取 `randomPos`，并解析 `dist` / `sigma` / `outlier` |
| `inputconvertgame.h` | `#include <QHash>`；声明 `scatterPos()` 与 `m_scatterCache`；`processKeyClick()` / `processKeyDrag()` 增加 `RandomPos` 形参 |
| `inputconvertgame.cpp` | `#include <cmath>`；`nextGauss()`（Box-Muller）；实现 `scatterPos()`（均匀 / 高斯两路，高斯用拒绝采样裁进区域）；所有点击 / 拖拽 / 视角起点改为先散布再发送；缓存 down 时的点供 up 复用 |

补丁以 unified diff 形式提供（`random-scatter.patch`），路径与 QtScrcpyCore
仓库一致，因此 `apply_patch.py` 或 `git apply` 都能直接套用。

---

## 上游版本固定

| 组件 | 版本 |
|---|---|
| QtScrcpy | `v4.2.1` |
| QtScrcpyCore | `9e388b8aa48e4e1c2cfdef408ef00c4c6b45c921` |
| Qt | `5.15.2`（msvc2019 / msvc2019_64）|

QtScrcpyCore 的 SHA 取自 v4.2.1 的 gitlink，保证和官方二进制一致。
