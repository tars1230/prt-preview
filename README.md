# PRT-Preview · 开箱即用

> **一句话**：UG NX 的 `.prt` 文件，右键一下就能看，不用开 NX、不用懂 UG 操作。
> 自动转成 STEP，用轻量看图程序 Mayo 打开，还能把散件按结构自动分组（外壳 / 门板 / 层板 / 框架 / 标准件）。

这份文档包含你需要的全部内容：**三步安装 → 日常使用 → 故障排查 → 原理速览**。看这一份就够。

---

## 一、先准备三样东西

| 需要 | 说明 | 没有会怎样 |
|---|---|---|
| **Python 3.9+** | [python.org](https://www.python.org/downloads/) 下载安装，**安装时务必勾选 "Add python.exe to PATH"** | 安装脚本直接报错退出 |
| **NX 2.0** | 你电脑上本来就有，能正常打开 NX 就行 | 无法转换，只能看已有的 STEP |
| **Mayo 看图程序** | 免费开源 CAD 查看器 → [GitHub 下载](https://github.com/Fougue/Mayo/releases) 解压即用 | 无法看图，但「转 STEP/IGES」照常能用 |

> Mayo 开源免费，不用申请、不用登录、不用交钱。

---

## 二、安装（三步，通常 10 秒）

### 第 1 步：解压

把 `PRT-Preview-v1.0.0.zip` 解压到任意位置，比如 `C:\PRT-Preview`。

### 第 2 步：跑安装脚本

在解压目录里打开 **PowerShell**，执行：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

脚本会自动做四件事：

1. **探测本机已有的 OCP 环境并复用**（没有才新建虚拟环境装 `cadquery-ocp`，约 200MB）
2. 写好配置文件 `config.json`
3. 注册 `.prt` 右键菜单
4. 跑一次自检，把结果打给你看

> **装完多久？** 通常 **10 秒内**。脚本先找机器上已经装好 `cadquery-ocp` 的 Python 直接复用；
> 确实没有才下载安装（走公司网络大概几分钟）。
> 嫌慢可以加 `-SkipOcp`：跳过智能分组，其余三个菜单照常可用。

**看到「核心依赖齐备」就是成功了。**

### 第 3 步：重启资源管理器

任务管理器 → 找到「Windows 资源管理器」→ 重新启动，或者随便开个文件夹按 `F5`。
之后在任意 `.prt` 文件上右键，就能看到 4 个新菜单项。

### 可选安装参数

```powershell
# 指定安装位置
install.ps1 -InstallDir "D:\Tools\PRT-Preview"

# 提前告知 NX 安装位置（不填则自动探测）
install.ps1 -NxBase "D:\UGS\NX2.0"

# 提前告知 Mayo 位置（不填则自动探测）
install.ps1 -Mayo "C:\Program Files\Fougue\Mayo\mayo.exe"

# 公司有统一 NX 许可服务器
install.ps1 -LicenseServer "27000@license.corp.local"

# 不装 OCP（无智能分组，其余功能正常）
install.ps1 -SkipOcp

# 强制重装 OCP（不复用已有环境）
install.ps1 -ForceOcp
```

（上面每行都补全为 `powershell -ExecutionPolicy Bypass -File <路径>\install.ps1 ...`，在 PowerShell 里可直接用。）

---

## 三、用起来

### 右键菜单（4 个）

在 `.prt` 文件上右键：

| 菜单 | 效果 | 什么时候用 |
|---|---|---|
| **用 Mayo 查看** | 直接打开看图程序，最快（约 3 秒） | 只想看看长什么样 |
| **智能分组查看** | 先分析结构再打开，组件树按语义分组 | 想按结构看、想整组隐藏 |
| **转为 STEP (AP214)** | 同目录生成同名 `.stp`，保留颜色和几何 | 要给 CAM / 别的软件 |
| **转为 IGES** | 同上，生成 `.igs` | 老 CAM 只认 IGES |

也可以把 `.prt` 拖到 `prt-preview.bat` 上打开，等同「用 Mayo 查看」。

### 「智能分组查看」怎么用

打开后左侧是一棵组件树：

- **顶层**是产品类型（比如「立式双门柜体 / 方舱」）
- **第二层**是语义分组（主体外壳 / 门板组件 / 层板托盘 …），点箭头折叠
- **隐藏大组件**：选中「主体外壳」这一组 → 右键 → Hide，内部结构立刻露出来。**这是最常用的操作**
- **看某个零件的尺寸**：点选零件，右侧属性面板显示 `Size_XYZ`（长宽高）、`Volume`、`SurfaceArea`、`Role`、`Layer`、`Confidence`
- **测量**：Mayo 工具栏的测量图标，支持长度、角度、直径、面积等

非柜类模型（法兰、轴、支架）会自动换成中性角色名（主体大件 / 竖直大板 / 细长杆件 …），不会硬套「门板」「层板」。

### 缓存

转换结果缓存在 `%LOCALAPPDATA%\PRTPreview\cache`，**同一个文件第二次打开是秒开**，不会重复转换。
源文件被改、或分组规则升级时，缓存自动失效重建——不会出现「改了规则还是旧树」。

---

## 四、遇到问题

| 现象 | 怎么办 |
|---|---|
| **右键菜单没出现** | 重启资源管理器，或按 `F5` |
| **提示「未找到 NX 转换器」** | 打开 `config.json` 填 `nx_base`，指向**含 `UGII` 子目录的那一层**（例 `D:\UGS\NX2.0`，不是 `D:\UGS\NX2.0\UGII`） |
| **提示「找不到 Mayo 看图程序」** | 装好 Mayo 后在 `config.json` 填 `mayo` 路径 |
| **转换失败 / 许可报错** | 跑 `python <安装目录>\prt_config.py` 看自检；详细日志在 `%LOCALAPPDATA%\PRTPreview\cache\prt2mayo.log`；许可问题确认 `license_server` 是你们实际地址 |
| **中文路径报错** | 已自动处理：NX 2.0 转换器是 2002 年 ANSI 程序，拒绝非 ASCII 路径，工具会先复制到临时 ASCII 路径再转。日志里出现「非 ASCII 路径，复制到临时路径转换」是**正常的** |
| **智能分组很慢** | 首次分析大模型（约 500 零件）要 1～3 分钟，之后走缓存秒开。进度看 `prt2mayo.log` |
| **想改分组结果** | 编辑缓存目录里的 `*.semantic_map.json`，在 `overrides` 里写 `{"零件序号": "SHELF"}`，保存后重开即生效，标注不会被覆盖 |

---

## 五、配置（一般用不到，全留空即可）

配置文件：`<安装目录>\config.json`

```json
{
  "nx_base": "",
  "mayo": "",
  "ocp_python": "",
  "disable_ocp": false,
  "cache_dir": "",
  "license_server": "27000@localhost",
  "license_dir": "",
  "license_autostart": false,
  "register_menu": true
}
```

- **留空 = 自动探测**（注册表 → 常见安装位置 → PATH → NX 旁边的 `ocpenv`）
- 也可以用环境变量覆盖：`PRT_PREVIEW_NX_BASE`、`PRT_PREVIEW_MAYO`、`PRT_PREVIEW_OCP_PYTHON`、`PRT_PREVIEW_CACHE_DIR`、`PRT_PREVIEW_LICENSE_SERVER`
- `disable_ocp: true` 是明确「不要用 OCP」，和「探测不到」是两回事（探测不到还会继续找）

> ⚠️ **`license_autostart` 默认关闭，不建议打开。** 它会在许可服务没响应时自动在本机拉起 `lmgrd`。
> 同事机器通常连公司统一许可服务器，本机拉起一个可能**冲突**，反而搞坏签出。
> 只有你确实用本地 `ugnx2.lic` 且确认没有其它 `lmgrd` 在跑时才开。

---

## 六、原理速览（想知道为什么这么设计）

### 整体链路

```
.prt ──NX官方导出器(step214ug)──> STEP AP214 ──OCP读取──> 几何特征
                                                    │
                                          产品类型推断 + 语义分组
                                                    │
                                    XCAF 语义装配树 ──> Mayo 打开
```

转换调用 **NX 自带的官方导出器**（`step214ug.exe` / `iges.exe`），不重新实现几何内核。输出 STEP AP214 / IGES，绝大多数 CAD/CAM 都能读。
`.prt` 原文件**不会被修改**，NX 默认打开方式也**不动**，只在自己的 `HKCU` 下加右键动词。

### 智能分组怎么判

1. **先认出这是什么** —— 通过尺寸指纹与结构证据判断产品类型（立式双门柜体 / 立式多层柜 / 多层箱体机架 / 板类薄壁结构 / 卧式箱体 / 通用装配体）
2. **再按语义角色分组** —— 主体外壳、门板组件、侧顶底板、框架立柱、层板托盘、同型组件、标准件五金、其它内部件
3. **尺度无关** —— 所有判据都是相对比例（相对整体尺寸 S），指纹量化 `q = S/2000`。换模型、换单位（mm / 英寸 / 米）结论一致
4. **方向自适应** —— 不再假定 STEP 的 Z 轴就是产品竖直方向，会根据层板/立板证据在 X/Y/Z 中选择竖直轴；旋转坐标系后仍按同一结构理解
5. **更多输入形态** —— 多 free-shape STEP 不漏掉后续产品；纯壳体/曲面模型没有 solid 时也会进入低置信度的可查看分组，而不是直接报坏文件

### 两个关键的坑（已解决）

- **老版 OCP 的静默失败**：OCP 7.7 及以前没有 `OCP.collections` 模块，`import OCP` 会成功但语义分组一跑就崩，表现是「能打开但没有智能分组」，很容易以为程序坏了。安装脚本会同时校验 `OCP.collections` 和 `OCP.STEPCAFControl`，不可用就换下一个 Python 环境。
- **人工标注的时序**：标注签名变化、或输出文件不存在时才会重建语义树；失败重试不丢标注，清空标注会恢复自动分类。这些都是自动的。
- **同尺寸不同结构不再强行合并**：指纹除了 bbox 三边和体积，还加入填充率/表面积密度的粗特征，带孔件、空腔件和实心件可以分开；仍保留人工 `overrides` 兜底。
- **失败不会破坏旧缓存**：语义 STEP 先写到临时文件，成功后才替换正式缓存；中途被杀、磁盘满或 OCP 崩溃时，旧结果仍在，下一次可重试。

### 可靠性

- **缓存新鲜度**判据里包含分组器脚本的修改时间——改了规则一定会重建
- **失败逐级回退**：语义分组失败 → 空间分组 → 扁平文件，不会出现「点了没反应」
- 发布包经过敏感扫描，不含 NX 二进制、许可文件、任何本机路径或缓存产物

---

## 七、卸载

```powershell
powershell -ExecutionPolicy Bypass -File <安装目录>\uninstall.ps1
# 连缓存一起清
powershell -ExecutionPolicy Bypass -File <安装目录>\uninstall.ps1 -PurgeCache
```

---

## 八、许可与致谢

- 本工具代码以 **MIT** 协议发布，见 `LICENSE`
- **Mayo** —— 开源 CAD 查看器，Apache-2.0，作者 Fougue。安装脚本不捆绑其二进制，请自行从官方发布页下载
- **cadquery-ocp** —— OpenCascade 的 Python 绑定，由 cadquery 组织维护
- **Siemens NX** —— 本工具调用 NX 自带导出器，NX 属 Siemens 商标，此项目与 Siemens 无从属关系。NX 的使用须遵守贵司既有许可协议
