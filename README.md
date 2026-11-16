# PRT-Preview —— UG NX `.prt` 智能看图 / 转换工具

把 NX `.prt` 文件**一键转成 STEP 并用轻量看图程序打开**，自动识别装配结构并建立**语义组件树**（外壳 / 门板 / 层板 / 框架 / 标准件），可以整体隐藏大组件、点选看每个零件的基础尺寸。

> 为不熟悉 CAD 的使用者设计：不需要打开 NX，不需要懂 UG 操作，右键一下就有结果。

---

## 一、它能做什么

| 场景 | 怎么用 | 得到什么 |
|---|---|---|
| 只想看看这个零件长什么样 | 右键 → **用 Mayo 查看** | 轻量看图程序秒开，不启动 NX（NX 启动要 1 分钟以上，Mayo 约 3 秒） |
| 想**按结构**看，而不是一堆散件 | 右键 → **智能分组查看** | 自动识别产品类型 → 建立语义装配树，可整组隐藏/显示 |
| 要把文件给 CAM / 别的软件 | 右键 → **转为 STEP (AP214)** | 在 `.prt` 同目录生成同名 `.stp`，保留颜色与几何细节 |
| 老 CAM 只认 IGES | 右键 → **转为 IGES** | 同上，生成 `.igs` |
| 拖拽打开 | 把 `.prt` 拖到 `prt-preview.bat` | 等同于「用 Mayo 查看」 |

**智能分组查看**是核心。它不是简单按空间切块，而是：

1. **先认出这是什么** —— 通过尺寸指纹与结构证据判断产品类型（立式双门柜体 / 立式多层柜 / 多层箱体机架 / 板类薄壁结构 / 卧式箱体 / 通用装配体）
2. **再按语义角色分组** —— 主体外壳、门板组件、侧顶底板、框架立柱、层板托盘、同型组件、标准件五金、其它内部件
3. **每个零件带尺寸卡片** —— 鼠标点选后属性面板直接显示：`Size_XYZ`（长宽高）、`Volume`、`SurfaceArea`、`Role`（角色）、`Layer`（外壳/内部）、`EntityCount`（件数）、`Confidence`（判定置信度）

非柜类模型（如法兰、轴、支架装配）会自动切换成中性角色名（主体大件 / 竖直大板 / 细长杆件 …），不会硬把「门板」「层板」套上去。

---

## 二、三十秒安装

### 前置条件

| 需要 | 说明 | 缺了会怎样 |
|---|---|---|
| **Python 3.9+** | [python.org](https://www.python.org/downloads/) 下载安装，**务必勾选 "Add python.exe to PATH"** | 安装脚本会直接报错退出 |
| **NX 2.0 已安装** | 同事电脑上本来就有，能正常打开 NX 即可 | 无法转换，只能看已有 STEP |
| **Mayo 看图程序** | 免费开源 CAD 查看器 → [GitHub 发布页](https://github.com/Fougue/Mayo/releases) 下载解压 | 无法看图，但「转 STEP/IGES」照常可用 |

### 安装步骤

1. 把整个文件夹解压到任意位置（建议 `C:\PRT-Preview`，或直接跑安装脚本让它自动放好）
2. **Windows Terminal / PowerShell** 里执行：

   ```powershell
   cd C:\PRT-Preview
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   脚本会自动：建立 Python 虚拟环境 → 安装 `cadquery-ocp` → 写配置文件 → 注册右键菜单 → 跑自检。

3. **重启资源管理器**（或按 `F5`），之后在任意 `.prt` 文件上右键，就能看到 4 个新菜单项。

### 安装参数（可选）

```powershell
# 指定安装位置
powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir "D:\Tools\PRT-Preview"

# 提前告知 NX 安装位置（不填则自动探测）
powershell -ExecutionPolicy Bypass -File install.ps1 -NxBase "D:\UGS\NX2.0"

# 提前告知 Mayo 位置（不填则自动探测）
powershell -ExecutionPolicy Bypass -File install.ps1 -Mayo "C:\Program Files\Fougue\Mayo\mayo.exe"

# 公司有统一 NX 许可服务器
powershell -ExecutionPolicy Bypass -File install.ps1 -LicenseServer "27000@license.corp.local"

# 不装 OCP（跳过「智能分组查看」，其余功能正常，包体小很多）
powershell -ExecutionPolicy Bypass -File install.ps1 -SkipOcp
```

---

## 三、配置

配置文件：`<安装目录>\config.json`

```json
{
  "nx_base": "D:\\UGS\\NX2.0",
  "mayo": "C:\\Program Files\\Fougue\\Mayo\\mayo.exe",
  "ocp_python": "C:\\PRT-Preview\\.venv\\Scripts\\python.exe",
  "cache_dir": "",
  "license_server": "27000@localhost",
  "license_dir": "",
  "license_autostart": false,
  "register_menu": true
}
```

- 留空 = 自动探测（注册表 → 常见安装位置 → PATH）。
- 也可以用环境变量覆盖：`PRT_PREVIEW_NX_BASE`、`PRT_PREVIEW_MAYO`、`PRT_PREVIEW_OCP_PYTHON`、`PRT_PREVIEW_CACHE_DIR`、`PRT_PREVIEW_LICENSE_SERVER`。

### ⚠️ 关于 `license_autostart`

**默认关闭，且不建议打开。**

这个选项的作用是：NX 许可服务没响应时，自动在本机拉起 `lmgrd` 守护进程。同事电脑上 NX 通常连的是公司统一许可服务器，本机拉起一个本地服务**可能与正式服务冲突**，反而搞坏他们的签出。

只有一种情况该开：你的机器确实用本地 `ugnx2.lic` 文件做许可，且确认没有其它 `lmgrd` 在跑。

---

## 四、日常使用

### 右键菜单

在 `.prt` 文件上右键：

- **转为 STEP (AP214)** —— 静默转换，输出到 `.prt` 同目录。首次约 1 分钟，之后走缓存秒开。
- **转为 IGES** —— 同上，输出 `.igs`。
- **用 Mayo 查看** —— 直接打开看图程序，不做结构分析，最快。
- **智能分组查看** —— 做结构分析后打开，组件树可按语义分组折叠/隐藏。

### 在 Mayo 里怎么用

打开「智能分组查看」后：

- **左侧组件树** —— 顶层是产品类型（比如「立式双门柜体 / 方舱」），第二层是语义分组（主体外壳 / 门板组件 / 层板托盘 …），点箭头折叠。
- **隐藏大组件** —— 选中「主体外壳」这一组，右键 → Hide，内部结构立刻露出来。这是这套工具最常用的操作。
- **看某个零件的尺寸** —— 点选零件，右侧属性面板会显示 `Size_XYZ`、`Volume`、`SurfaceArea`、`Role`、`Layer`、`Confidence`。
- **测量** —— Mayo 自带测量工具（工具栏的测量图标），支持长度、角度、直径、面积等。

### 缓存

转换结果缓存在 `cache_dir`（默认 `%LOCALAPPDATA%\PRTPreview\cache`），**同一个文件第二次打开是秒开**，不会重复转换。

缓存会自动失效重建，触发条件：
- 源 `.prt` 被修改过
- 分组规则代码升级过（所以不会「改了规则还是旧树」）

---

## 五、目录结构

```
PRT-Preview/
├─ prt2mayo.py          主程序：转换 / 缓存 / 许可 / 启动 Mayo
├─ prt_config.py        配置与自动探测（唯一路径来源）
├─ semantic_group.py    语义分组器（核心：产品类型推断 + 角色分类 + XCAF 装配树）
├─ group_step.py        空间分组器（语义分组失败时的兜底）
├─ reg_menu.py          右键菜单注册 / 注销
├─ gen_test_part.py     生成测试用装配体（回归测试用）
├─ prt-preview.bat      拖拽查看器
├─ install.ps1          一键安装
├─ uninstall.ps1        卸载
├─ config.example.json  配置模板
├─ requirements.txt     依赖
├─ tests/               回归测试
└─ docs/                原理与设计说明
```

---

## 六、故障排查

**右键菜单没出现**
重启资源管理器（任务管理器 → 找到「Windows 资源管理器」→ 重新启动），或按 `F5`。

**提示「未找到 NX 转换器」**
NX 安装路径没探测到。打开 `config.json` 填 `nx_base`，要指向**含 `UGII` 子目录的那一层**（例：`D:\UGS\NX2.0`，不是 `D:\UGS\NX2.0\UGII`）。

**提示「找不到 Mayo 看图程序」**
没装 Mayo，或路径不对。装 Mayo 后填 `config.json` 的 `mayo`。

**转换失败 / 许可报错**
- 先跑自检看依赖状态：
  ```powershell
  python <安装目录>\prt_config.py
  ```
- 看详细日志：`<cache_dir>\prt2mayo.log`
- 许可问题：确认 `license_server` 填的是你们实际的许可服务器地址。

**中文路径报错**
NX 2.0 的转换器是 2002 年的 ANSI 程序，**拒绝非 ASCII 路径**。本工具已自动处理：遇到中文路径会先复制到临时 ASCII 路径再转，转换完自动清理。你不需要做任何事，但要知道日志里会有「非 ASCII 路径，复制到临时路径转换」这一行 —— 那是正常的。

**智能分组很慢**
首次分析一个大模型（约 500 个零件）需 1～3 分钟，之后走缓存秒开。进度看 `<cache_dir>\prt2mayo.log`。

**想改分组结果**
编辑缓存目录里的 `*.semantic_map.json` 里的 `overrides` 字段（`{"零件序号": "SHELF"}`，角色名见 `ROLES` 定义），保存后重新打开文件即可生效，标注不会被覆盖。

---

## 七、卸载

```powershell
powershell -ExecutionPolicy Bypass -File <安装目录>\uninstall.ps1
# 连缓存一起清
powershell -ExecutionPolicy Bypass -File <安装目录>\uninstall.ps1 -PurgeCache
```

---

## 八、边界与合规

- 本工具**不修改** `.prt` 原文件，不改动 NX 的默认打开方式（不动 `ugs_router`），只在自己的 `HKCU` 下加右键动词。
- 转换调用的是 **NX 自带的官方导出器**（`step214ug.exe` / `iges.exe`），不重新实现几何内核。
- 许可服务自愈默认关闭（见上文）。
- 输出格式为 STEP AP214 / IGES，可被绝大多数 CAD/CAM 读取。
- 仓库内**不含** NX 二进制文件、许可文件、任何本机路径或缓存产物。

---

## 九、许可与致谢

- 本工具代码以 MIT 协议发布，见 `LICENSE`。
- **Mayo** —— 开源 CAD 查看器，Apache-2.0，作者 Fougue。安装脚本不捆绑其二进制，请自行从官方发布页下载。
- **cadquery-ocp** —— OpenCascade 的 Python 绑定，由 cadquery 组织维护。
- **Siemens NX** —— 本工具调用 NX 自带导出器，NX 属 Siemens 商标，此项目与 Siemens 无从属关系。NX 的使用须遵守贵司既有的许可协议。
