# PRT-Preview 设计说明

本文说明这套工具的**内部原理**与**为什么这么做**，供后续维护者参考。日常使用看 `README.md` 即可。

---

## 一、整体链路

```
.prt 文件
   │
   │ ① NX 自带 step214ug.exe（AP214 优先，AP203 兜底）/ iges.exe
   ▼
扁平 STEP（保留颜色与几何）
   │
   │ ② semantic_group.py —— 语义分组（OCP/OpenCascade）
   │    ├─ 提取每个 solid 的 bbox / 体积 / 面积 / 颜色 / 薄板比
   │    ├─ 几何指纹聚类 → 组
   │    ├─ 产品类型推断 → 决定用柜类还是中性角色名
   │    ├─ 组级角色分类（可解释规则 + 置信度）
   │    └─ 写入 XCAF 装配树 + NamedData 尺寸卡片
   ▼
语义装配 STEP（3 层树 + 中文节点名 + 尺寸 UDA）
   │
   │ ③ mayo.exe
   ▼
轻量看图程序
```

失败回退链：**语义分组失败 → 空间分组 → 扁平 STEP**。任何一级失败都不会让用户看不到模型。

---

## 二、为什么用 STEP 转换而不是别的

- NX 2.0 自带 `step214ug.exe` / `step203ug.exe` / `iges.exe`，是官方导出器，**几何与颜色保真度最高**，且不需要自己实现几何内核。
- AP214 比 AP203 更完整地保留颜色、装配结构与属性，因此作首选；AP203 作为老版本兜底。
- IGES 只保留几何，且老 CAM 生态仍有需求，所以保留但作为次要选项。

### 已知坑：ANSI 程序拒绝非 ASCII 路径

NX 2.0 的转换器是 2002 年的 ANSI 程序，对非 ASCII 路径直接报 `Invalid file name`。空格无害。

**解法**：`sanitize_input()` 检测到非 ASCII 路径时，先复制到缓存目录下的临时 ASCII 路径再转，转完清理。缓存 key 仍按原始路径计算，所以下次命中逻辑正确。

---

## 三、语义分组：核心设计

### 3.1 为什么不按空间切块

早期版本（`group_step.py`）沿最长轴递归剖分空间，得到的是「这一坨几何在那个位置」——**没有语义**。用户真正想说的是「这个是外壳」「这个是门」「里面那些是五金」。空间块无法回答「整体隐藏外壳看内部」这种需求，因为外壳被切碎在多个块里。

### 3.2 三层决策

**第一层：产品类型推断**

判据基于几何指纹与结构证据，把模型归入 6 类之一：

| 类型 | 特征 |
|---|---|
| 立式双门柜体 / 方舱 | 高瘦比 + 竖直大薄板成对 + 多层水平板 |
| 立式多层柜体 | 高瘦比 + 水平板层数多 |
| 多层箱体 / 机架 | 扁平箱体 + 内部框架 |
| 板类 / 薄壁结构 | 主导尺寸极大且薄 |
| 卧式箱体 / 底座 | 扁平 + 大面积水平板 |
| 通用装配体 | 都不像 → 中性角色名 |

**第二层：几何指纹聚类**

主键 = bbox 三边按尺度自适应量化；体积作二级拆分。

**第三层：组级角色分类**

输出 `SHELL / DOOR / PANEL / FRAME / SHELF / REPEAT / HARDWARE / INTERNAL / UNKNOWN`，每个带**可解释的判定理由**与**置信度**。

### 3.3 尺度无关 —— 这是兼容性的核心

早期版本用固定阈值（`round(v, 2)`、`// 200` 分桶、体积容差 `+100`），结果：

- 14 件法兰在 mm 数值下是 5 组 4 类
- 同样的模型换成米级数值 → **坍缩成 1 组，全部归入「小型重复件」**

根因是**绝对量阈值**。修复方案：

| 参数 | 原方案（错） | 现方案（对） |
|---|---|---|
| 指纹量化 | `round(v)` 固定小数位 | `q = S / 2000`，`S = max(全局尺寸)` |
| 体积容差 | `+100`（绝对体积） | `base * 1.25 + S³ * 1e-12` |
| Z 分桶 | `// 200`（绝对长度） | `S / 10` |
| 角色阈值 | 固定 mm 值 | 全部改为 `S` 的比例 |
| 数据精度 | `round(v, 2)` 抹掉小数 | 保留原始 float，输出时才 `.4g` |

**回归验证**：464 件柜体整体 ÷10 → `0/464` 变化；mm 数值与米级数值的法兰 → 结果完全一致。

**推论**：方案不绑定当前文件，换任何模型、任何单位、任何尺度都能工作。

### 3.4 XCAF 装配树怎么写

关键点：`XCAFDoc_DocumentTool.ShapeTool_s(doc)` 的 `GetComponents_s()` 返回的是**组件实例 label**（默认名 `=>[0:1:1:2]`），不是形状定义。要拿真实定义节点必须解引用：

```python
ref = TDF_Label()
if st.GetReferredShape_s(component_label, ref):
    walk(ref, depth + 2)
```

**中文命名双保险**：同时给形状定义 label 和组件实例 label 写 `TDataStd_Name`，且用 `TCollection_ExtendedString(s, True)` 显式声明按 UTF-8 多字节解析。否则中文树名变乱码。

### 3.5 尺寸卡片：NamedData + ASCII 取舍

每个叶节点写 `TDataStd_NamedData`：

| 字段 | 含义 |
|---|---|
| `Role` | 语义角色 |
| `Layer` | EXTERIOR / INTERIOR / UNKNOWN |
| `EntityCount` | 该组件数 |
| `Size_XYZ` | 长宽高 |
| `Volume` | 体积 |
| `SurfaceArea` | 表面积 |
| `Rule` | 判定规则名 |
| `Confidence` | 置信度 |

**字段值故意用 ASCII**（角色用英文枚举），因为实测中文属性值经 STEP 往返后会变成 `ä¸»ä½å¤å£³`（编码被转换器按 latin-1 处理）。中文语义保留在**树节点名称**里 —— 那条路径经验证是好的。

Mayo 官方 `XCaf_DocumentTreeNodePropertiesProvider` 会自动读取 XCAF `TDataStd_NamedData` 并放入 MetaData 分组，所以无需改 Mayo 源码就能显示。

---

## 四、缓存与一致性

### 4.1 缓存判据

```python
def _fresh(out, *deps):
    if not (os.path.isfile(out) and os.path.getsize(out) > 1024):
        return False
    om = os.path.getmtime(out)
    for d in deps:
        if os.path.isfile(d) and om < os.path.getmtime(d):
            return False
    return True
```

**关键**：`deps` 里必须带分组器脚本自身的 mtime。否则改了规则代码，旧缓存仍被认为有效，用户永远看到旧树，还以为「改了没生效」。

依赖关系：
- 语义成品 ← 源 STEP + `semantic_group.py`
- 空间成品 ← 源 STEP + `group_step.py`

### 4.2 人工标注的三时序

`semantic_map.json` 里的 `overrides` 可人工编辑（借鉴 SegCAD 的外部标签文件思路）。判定「是否需要重建」**不能靠 map 文件的 mtime** —— 因为重跑时 map 会被重写，mtime 永远比成品新，会每次白跑 70 秒；而且分组失败时成品不更新，用 mtime 会陷入无限重试。

正确做法是**内容签名**：

```
ov_sig = md5(json.dumps(overrides, sort_keys=True))
```

存于 `<成品>.ovstamp`。判定：

```python
force = (ov_sig != old_sig) or (bool(ov_sig) and not os.path.isfile(out))
```

三种时序都要覆盖（`test_e2e.py` 5/6/7）：

| 时序 | 现象 | 修法 |
|---|---|---|
| 首次标注 | 标注应生效 | 签名变化 → 带 `--map` 重建 |
| 失败重试 | 成品上次没生成，标注不能丢 | `ov_sig` 非空但成品不存在 → 仍带 `--map` |
| 清空标注 | 应恢复自动分类 | 签名变空 → 重建无标注树，且 stamp **写空串** |

**最后一条最容易漏**：不写空签名的话，`old_sig` 永远非空，每次打开都误判「签名又变了」白跑一次重建。

---

## 五、可靠性设计

| 机制 | 实现 |
|---|---|
| 并发保护 | 缓存目录 `.lock` 文件（`O_CREAT|O_EXCL`），>10 分钟视为死锁自动接管 |
| 许可自愈 | 转换前检查 `lmstat` 确认 `uglmd: UP`；不通则静默拉起 `lmgrd` 等 60s |
| 许可自愈看门狗 | Windows 计划任务每 30 分钟幂等检查一次 |
| 转换重试 | 首次失败 → 重启许可服务 → 再试一轮；每轮内 AP214→AP203 逐链降级 |
| 转换超时 | 单链 600s，分组 1800s |
| 日志截断 | 超 1MB 自动保留最近 200KB |
| 无控制台 | 走 `pyw.exe`；子进程带 `CREATE_NO_WINDOW` |
| 依赖缺失 | 不静默失败，弹窗给出「填 config.json 哪个字段」的可操作提示 |

### 许可自愈为何默认关闭（发布版）

同事机器上的 NX 通常连公司统一许可服务器。本机自动拉起 `lmgrd` **可能与正式服务冲突**，反而搞坏他们的签出。所以发布版默认 `license_autostart: false`，仅在明确「本机用本地 `.lic` 许可」时才开启。

---

## 六、配置与可移植性

路径来源唯一：`prt_config.py`。优先级从高到低：

```
命令行 --config  >  PRT_PREVIEW_CONFIG 环境变量  >  脚本同目录 config.json
  >  PRT_PREVIEW_* 单项环境变量  >  自动探测  >  内置默认
```

自动探测只读：
- **注册表** `HKLM/HKCU\SOFTWARE\UGS\*` 的 `UGII_BASE_DIR` / `UGII_ROOT_DIR`
- **常见安装位置** `C:\Program Files\UGS`、`D:\UGS`、`F:/G:/H:\UGS` 等
- **环境变量** `UGII_BASE_DIR`
- **Mayo**：`Program Files\Fougue\Mayo`、PATH
- **OCP Python**（逐级探测，每个候选都过版本兼容校验）：
  1. 显式配置 / `PRT_PREVIEW_OCP_PYTHON`
  2. 包内 `.venv`（`install.ps1` 建的）
  3. PATH 上的 `python` / `python3`
  4. **NX 安装根旁边的 OCP 环境**（`ocpenv`、`python` venv）

探测全部失败时返回空，由调用方弹可读提示，而不是抛栈。

### OCP 版本兼容校验：为什么不能只测 `import OCP`

`import OCP` 成功 ≠ 语义分组能跑。老版本 OCP（7.7 及以前）没有 `OCP.collections`
模块，而 `semantic_group.py` / `group_step.py` 的 `from OCP.collections import
Sequence_TDF_Label` 会直接 `ModuleNotFoundError`。

失败的表现很隐蔽：转换成功、文件能打开，但**没有智能分组**，静默退化成
「所有零件平铺在树里」。用户以为程序坏了。

因此 `_has_ocp()` 同时验证：

```python
import OCP, OCP.collections, OCP.STEPCAFControl
from OCP.TCollection import TCollection_ExtendedString
```

任一缺失即判定该解释器不可用，继续往下一个候选找。

### 安装策略：先探测，后安装

`cadquery-ocp` 约 200MB，是安装链路里唯一耗时的一步。很多同事机器上已经有
可用环境（自己建的 venv、NX 旁边的 `ocpenv`、公司统一镜像的 Python 库），
重复下载既慢又占空间。

`install.ps1` 因此先调 `prt_config.py --print-ocp` 拿探测结果，命中就复用；
探测不到才建虚拟环境拉包。`--print-ocp` 只往 stdout 打一行路径，不掺日志，
避免 PowerShell 侧解析歧义。

`disable_ocp` 与「探测不到」是两个不同语义：
- 探测不到 → 继续自动找下一个候选
- `disable_ocp: true` → 用户明确不要用，直接放弃（`-SkipOcp` 会置位）

---

## 七、回归测试

| 脚本 | 覆盖 |
|---|---|
| `test_scale.py` | 尺度无关性（缩放 / 压扁变体，断言角色不变） |
| `test_compat.py` | 中文空格路径、缓存随规则失效、失败回退链、标注回灌、Python 3.14 |
| `test_e2e.py` | 右键命令全链路（拦截 Mayo 启动与文件交付）+ 标注三时序 |
| `test_path_matrix.py` | ASCII/空格/中文路径矩阵 |
| `test_ap214.py` / `test_iges.py` | 两种导出格式真实转换 |
| `make_release.py` | 发布包自检（文件名合规、内容敏感扫描、干净目录编译导入） |

测试**自生成样本、自清理**，不依赖本机特定文件，除了 `test_e2e.py` 的真 `.prt` 端到端段（该段可用 mock 替代）。

---

## 八、发布安全边界

**禁止进入发布包**：

- 许可绕过 / 期限校验规避工具（`patch_uglmd*.py`、`pe_*.py`、`patch_time.py`）
- `D:\UGNX2_Lic` 全部内容（`.lic`、lmgrd、uglmd）
- NX 商业二进制
- `.prt` / `.stp` / `.igs` 成品
- 截图、缓存、日志、探测结果
- 本机绝对路径、内部资料库 ID、内部零件编号、内部账号

`make_release.py` 用**白名单拷贝**（不是整目录复制）保证不夹带，再用**两层扫描**兜底：

1. 文件名 / 扩展名黑名单
2. 文件内容正则扫描（本机路径、许可标识、内部编号）

任何一层命中即判定发布包不合格，不允许打包成功。
