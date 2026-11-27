<div align="center">

# Apple Watch Health (`awh`)

**Local USB iAP2 Accessory-Location Toolkit & AI Agent Skill**

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0--only-0f172a.svg?style=flat-square)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/1837620622/apple-watch-health/ci.yml?style=flat-square&label=macOS%20%26%20Windows%20CI)](https://github.com/1837620622/apple-watch-health/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-2563eb.svg?style=flat-square)](#)
[![Zero Cost](https://img.shields.io/badge/cost-100%25%20Free%20%26%20Open%20Source-16a34a.svg?style=flat-square)](references/legal-and-security.md)

通过本地 USB 模拟外置 MFi GPS 配件（iAP2 / NMEA），**无副作用**将 iPhone 临时定位到地球上任意地点并随时一键恢复；<br>
同时支持自动探测 Apple Watch 型号与 watchOS 版本，协助开启地区受限健康功能，用完一键清场。

[3 分钟上手](#快速开始) · [任意地点定位与恢复](#任意地点定位与恢复零副作用) · [手机端必做步骤](#手机端操作缺一不可) · [Windows 避坑](#windows-避坑) · [免责声明](#法律与免责声明)

</div>

---

## 双引擎工作原理（各司其职）

为什么很多改定位工具要么「健康 App 不认」，要么「开了 Wi-Fi 微信就不动」？因为 iOS 26/27 对不同场景走了两条完全不同的通道，本工具内置**双引擎**：

| 引擎 | 命令 | 适用场景 | 是否需要开发者模式 | 开 Wi-Fi / 5G 流量 |
| --- | --- | --- | --- | --- |
| **iAP2 外置 MFi 配件** | `send` | **开通 Apple Watch 高血压通知**（走 `isProducedByAccessory`，过健康地区校验） | **不需要** | 需开飞行模式 + 关 Wi-Fi/流量 |
| **DVT 官方免 Root 隧道** | `locate`（默认） | **微信 / 苹果地图 / 高德等全部 App 全局改任意地点**（走官方 `LocationSimulation`） | 需要（免 sudo/Root） | **开着 Wi-Fi / 5G 照样秒生效** |

```text
  Mac / Windows (awh)                         iPhone (USB 信任)                 Apple Watch
  ────────────────────                        ─────────────────                 ───────────
  • [体检] companion_proxy ──USB────────────► lockdownd ──────────────────────► 自动读手表型号 & watchOS
  • [健康] iAP2 + MFi-v3 ◄─0xAA00/0xFFFB────► com.apple.carkit.service ───────► healthd 解锁高血压通知
  • [全局] UserspaceRsdTunnel (免 Root) ────► DVT LocationSimulation ─────────► 微信/地图/全部 App 秒改定位
  • [恢复] awh restore ─────────────────────► 停 iAP2 + stopLocationSimulation ► 零副作用秒回真实 GPS
```

- **真机实测通过**：**macOS** + **iPhone 16 Pro (`iOS 27.2.1`, CH/A)** + **Apple Watch Series 12 (`watchOS 27.2.1`, CH/B)** 双引擎均已实测跑通——`iAP2` 成功开通高血压通知，`DVT` 免 Root 秒切纽约时代广场并一键恢复。
- **零开发环境上手**：`awh.sh` / `awh.cmd` 自动准备独立版 `uv` 与隔离 Python 环境，不动系统全局 Python；用完 `clean --all` 删干净。
- **快且克制**：MFi 身份通过 HTTP Range Remote-ZIP 只拉 ~68 KB（实测 **3.6 秒**），双文件 SHA-256 锁定，仓库**零内置密钥/APK**；macOS 上 `iAP2` 会话自动进 Seatbelt 断网沙箱。

---

## 2026-11 大陆功能现状

别白折腾——截至 **2026 年 11 月**，国行 Apple Watch 大部分健康功能已经官方开放，只有还在审批里的需要临时切地区：

| 功能 | 中国大陆现状 | 需要本工具吗 | 说明 |
| --- | --- | --- | --- |
| **高血压通知** | 未开放（审批中） | **需要** | S9–S12 / Ultra 2–4（**不含 SE**）、iPhone 11+、22 岁+ 未怀孕未确诊 |
| **睡眠呼吸暂停提示** | **2026-10-14 已开放** | 旧系统才需要 | 新系统直接开；S9+ / Ultra 2+ / SE 3，18 岁+ 未确诊 |
| **心电图 (ECG) / 血氧 / 心律不齐 / 房颤历史** | 已全部官方开放 | 不需要 | 正常联网直接在健康 App / Watch App 开 |
| **准备指数 / 生命体征 / 睡眠评分** | S12 原生支持 | 不需要 | watchOS 27 自带 |
| **卫星联络 / 国行快充 / FaceTime Audio** | 硬件或系统级限制 | **改定位无效** | 非地区定位开关 |

完整条件见 [references/feature-matrix.md](references/feature-matrix.md)，或终端运行 `./awh.sh features`。

---

## 快速开始

### 方式一：给 AI 助手当 Skill 用（最省心）

在 [Releases](https://github.com/1837620622/apple-watch-health/releases/latest) 下载 `apple-watch-health-skill.zip`，解压后放到你的 Agent 技能目录（Claude Code / Codex / Cursor / Antigravity / WorkBuddy 通用），新开对话直接说：

> 用 apple-watch-health 检查我的手机和手表，帮我开启 Apple Watch 高血压通知，弄完恢复真实定位并清理缓存。

### 方式二：自己敲 4 行命令

插上 **USB 数据线**（别用纯充电线），iPhone 解锁并点「信任此电脑」：

```bash
# macOS / Linux（Windows 把 ./awh.sh 换成 awh.cmd）
git clone https://github.com/1837620622/apple-watch-health.git
cd apple-watch-health

./awh.sh doctor               # 1. 自动识别 iPhone + Apple Watch 型号、系统与兼容性
./awh.sh setup                # 2. 极速提取本地 MFi 身份（~68 KB，约 3 秒）
./awh.sh probe --duration 15  # 3. 测握手，看到 authentication_succeeded 即通过
./awh.sh send --preset hk     # 4. 开始推送香港中环坐标（默认 20 分钟自动停，支持断线自动重连）
```

> 国内首次同步 Python 依赖如果慢，在命令前加镜像即可：
> `AWH_PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple ./awh.sh doctor`

---

## 任意地点全局改定位与恢复（微信 / 地图全生效 · 零副作用）

参考爱思助手原生开发者定位方案（**iOS 17+ 免 Root `UserspaceRsdTunnel` + DVT `LocationSimulation` + 自动挂载 DDI；iOS 16 及以下自动切 `DtSimulateLocation`**），日常把 **微信、苹果地图、高德等全部 App** 定位到世界任意角落（开着 Wi-Fi 和 5G 流量照样生效），免 `sudo` / 免管理员权限，macOS & Windows 通用：

```bash
# 0) 首次使用：确认/打开开发者模式（Windows 没装过 Xcode 时设置里默认看不到这个开关）
./awh.sh devmode               # 查看状态
./awh.sh devmode --reveal      # 在 iPhone「设置 → 隐私与安全性」里显示「开发者模式」开关 → 打开 → 重启 → 点【打开】

# 1) 前台保持（推荐）：窗口开着就一直生效，Ctrl+C 立即恢复真实定位
./awh.sh locate --place "纽约"
./awh.sh locate --lat 51.5074 --lon -0.1278            # Windows 把 ./awh.sh 换成 awh.cmd

# 2) 高德 / 腾讯 / 微信 / 百度选点不偏移（与爱思助手同款中国大陆火星/百度坐标纠偏）
./awh.sh locate --lat 39.9087 --lon 116.3975 --coord-type gcj02   # 高德 / 腾讯 / 微信坐标自动转 WGS-84
./awh.sh locate --lat 39.9151 --lon 116.4039 --coord-type bd09    # 百度地图坐标自动转 WGS-84

# 3) 后台保持：命令立刻返回，可关闭终端，用 restore 结束
./awh.sh locate --place "东京" --detach

# 4) 定时：保持 600 秒后自动恢复
./awh.sh locate --place "Apple Park" --hold 600

# 一键恢复真实定位（停后台保持进程 + 发官方 stopLocationSimulation，零系统副作用）
./awh.sh restore
```

> **为什么要「保持」？** iOS 17+ 的 DVT 模拟定位只在设置它的会话存活期间有效，会话一断手机就会回到真实 GPS。2.3.0 起 `locate` 会一直保持会话（每 10 秒刷新一次坐标、USB 抖动自动重连；首次未挂载 DDI 会自动挂载并刷新隧道），旧版「注入后立即退出」会导致定位几秒内就跳回原处。

> 给 AI 助手直接说人话就行，例如：「帮我把手机定位改到美国纽约」「把这个高德坐标用 gcj02 纠偏定位过去」「用完了，帮我恢复真实定位」。

---

## 手机端操作（缺一不可）

很多人只推了坐标却没成功，问题都出在下面 4 步没做全。保持 **USB 线插着** 且 `send --preset hk` 运行中：

1. **开飞行模式 + 关 Wi-Fi + 关流量**
   - 千万别连国内 Wi-Fi 或开蜂窝流量（国内路由器和基站会把地区强行拉回 `CN`）。
   - iOS 内置离线国界数据，完全离线就能认出香港；**蓝牙可以留着开**。
2. **确认两个定位开关**
   - `设置 → 隐私与安全性 → 定位服务 → 系统服务 → 系统自定义`：**必须打开**。
   - `设置 → 隐私与安全性 → 定位服务 → 地图`：**使用 App 期间 + 精确位置**。
3. **重置定位缓存（关键一步）**
   - 保持 `send` 运行，进 `定位服务` → **关闭 → 默数 10 秒 → 重新打开**。
   - 打开 **Apple 地图**点一下右上角定位箭头（离线底图空白没事，目的是触发手机拉取坐标）。
4. **进健康 App 开通**
   - 彻底划掉并重开 **健康 App → 右上角头像 → 健康核对清单 → 高血压通知**，按真实情况点完，确认详情页显示绿色 **通知已打开**。
   - 还是灰显？先把 **Apple Watch 关机**（清掉手表侧地区缓存）再划掉重进健康 App；也可以另开终端跑 `./awh.sh monitor` 实时看脱敏诊断日志。

### 关于「移动脉率房颤迹象记录软件」为什么开不了？

健康 App 里有两个名字几乎一样、但苹果强制**二选一互斥**的功能（两项大陆都已官方开放，恢复联网直接能开）：

- **移动脉率房颤提示软件（心律不齐通知）**：给**没确诊过房颤的普通人**日常预警用。
- **移动脉率房颤迹象记录软件（房颤历史）**：只给**医生已确诊房颤的患者**统计每周房颤占比用。

**一句话记住**：开了其中一个，另一个就不会让你同时开。没有房颤病史就保留「心律不齐通知」，别去开「房颤历史」（开了反而会把心律不齐通知关掉）。如果你确实要切换成另一个，先在健康 App 里关掉当前那个，再开启另一个（如遇旧系统地区受限再重跑本工具即可）。

### 开通后的收尾与清场

```bash
./awh.sh stop          # 停止定位推送
```

然后关闭手机飞行模式、拔掉 USB 线（如果地图还停在香港，再关定位等 10 秒重开），确认地图回到真实位置、健康里的 **通知已打开** 依然保留（开通后前 30 天是基线采集期，没有通知属正常，**不要手动关闭开关**）。

用完不想留任何文件，一行彻底清干净：

```bash
./awh.sh clean         # 只清临时缓存，保留身份方便以后复测
./awh.sh clean --all   # 删光本地身份、缓存与 .venv 虚拟环境
```

---

## Windows 避坑

Windows 端代码已通过 GitHub Actions `windows-latest` 完整单测与 `awh.cmd` 冒烟验证。大部分 Windows 用户卡住不是代码问题，而是 USB 驱动：

1. **装微软商店版「Apple 设备」驱动**：管理员终端运行 `winget install --id 9NP83LWLPZ9K`，装完重启一次电脑。**别同时装旧版官网 exe 安装包版 iTunes**，驱动会打架。
2. **插主板原生 USB 口**：优先插机箱背后主板口，别用供电不稳的前置口或廉价扩展坞；在「Apple 设备」里点完「信任」识别成功后，**把「Apple 设备」窗口关掉**，别让它占着 USB。
3. 先跑 `awh.cmd doctor`，看到 `[OK] USB 服务: Apple Mobile Device Service (127.0.0.1:27015) 运行中` 且读出你的手机和手表型号，就可以按上面的步骤走。
4. **DVT 改定位前先跑 `awh.cmd devmode --reveal`**：Windows 上从没连过 Xcode 的 iPhone，设置里不会出现「开发者模式」开关；这条命令让它显示出来，打开并重启后 `doctor` 会显示「开发者模式: 已开启」。首次改定位时若手机尚未挂载 DDI，工具会自动联网下载挂载并刷新隧道，请保持网络通畅与屏幕解锁。
5. 2.3.0 已彻底修复 Windows 上 DVT 定位失败/卡住（强制 Selector 事件循环、DDI 挂载后重连刷新 RSD 服务表、iOS 16 及以下切原生 `DtSimulateLocation`）、Windows `awh stop` 泄露 MFi 子进程，以及 `awh.cmd` 换行/参数 `!` 与负坐标解析问题。

更多排障见 [references/troubleshooting.md](references/troubleshooting.md)。

---

## 命令速查

| 命令 | 作用 |
| --- | --- |
| `doctor` | 体检：系统、USB 驱动、MFi 身份、残留进程、自动识别 iPhone & Apple Watch |
| `devices` | 列出已连接设备与配对手表（`--all` 含网络设备，`--full-udid` 显示完整 UDID） |
| `features` | 查看 2026-11 大陆 Apple Watch 功能支持矩阵 |
| `presets` | 查看内置坐标预设（`hk`, `hk-tst`, `mo`, `us-sf`, `us-ny`, `jp`, `sg`, `tw`） |
| `setup` | 极速提取本地 MFi 身份（支持 `--full-apk`、`--apk`、`--cert/--key`、`--force`） |
| `probe` | 仅做 USB iAP2 识别与 MFi-v3 握手测试，不发坐标 |
| `send` | iAP2 推送坐标（开健康功能用；`--place`、`--preset`、`--lat/--lon`、`--udid`、`--duration`） |
| `locate` | 全局改定位（默认官方免 Root DVT / iOS<=16 `DtSimulateLocation`；支持 `--detach`、`--hold N`、`--coord-type wgs84\|gcj02\|bd09`、`--engine iap2`） |
| `devmode` | 查看开发者模式；`--reveal` 显示设置开关，`--enable` 直接开启（仅限无锁屏密码） |
| `restore` | 停止 iAP2 推送与 DVT 保持进程，无副作用恢复 iPhone 真实定位 |
| `monitor` | 实时查看脱敏后的 iPhone 定位筛查（`GPRMC`）与健康地区状态日志 |
| `stop` | 停止后台发送进程并释放 PID 锁 |
| `clean` | 清理缓存与残留；加 `--all` 彻底删除身份文件与 `.venv` |

---

## 目录结构

```text
apple-watch-health/
├── SKILL.md            # AI Agent Skill 规范入口
├── README.md           # 说明文档
├── LICENSE             # GPL-3.0-only
├── pyproject.toml      # uv 跨平台依赖配置
├── awh.sh / awh.cmd    # macOS/Linux & Windows 免配置入口
├── references/         # 功能矩阵、深度排障、法律与安全边界
├── src/                # 核心实现（含内置 macOS Seatbelt 断网沙箱）
└── tests/              # 单元测试（pytest + ruff）
```

---

## 法律与免责声明

1. **完全开源免费，零费用**：本项目以 [GNU GPL-3.0-only](LICENSE) 协议开源，**永久免费，不收任何费用**，没有付费版、激活码或代开服务。任何渠道的收费倒卖均与作者无关。
2. **使用即默认同意并自担全部责任**：下载、安装、运行或参考本项目的任何代码与文档，即默认你已阅读并同意 [references/legal-and-security.md](references/legal-and-security.md) 的全部条款，**自行承担由此产生的一切法律、监管合规、设备保修、账号及健康风险**，作者与贡献者不承担任何直接或间接责任。
3. **非医疗器械**：Apple Watch 高血压通知、睡眠呼吸暂停提示等仅作健康趋势参考，**测不出具体血压数值，不能替代血压计或医生诊断**；身体不适请立刻就医。
4. **零内置密钥与研究边界**：本仓库**不包含、不分发任何 MFi 证书、私钥或 APK 文件**，仅限设备所有人在本人自有设备上做技术研究与兼容性测试，严禁用于商业代开或批量操作。本项目与 Apple Inc.、DiPlay、Carlinkit 等无任何关联。

<details>
<summary>致谢与上游参考</summary>

- 协议与实现参考：[aur3l14no/accessory-location](https://github.com/aur3l14no/accessory-location)、[xpqball/iap2-location-bridge-windows](https://github.com/xpqball/iap2-location-bridge-windows)、[youcci/playport](https://github.com/youcci/playport)、[flute/apple-watch-hypertension-skill](https://github.com/flute/apple-watch-hypertension-skill)
- 第三方实验凭据来源披露：[shihabal3amri/DiPlay THIRD_PARTY_NOTICES](https://github.com/shihabal3amri/DiPlay/blob/main/docs/THIRD_PARTY_NOTICES.md#experimental-authentication-data)

</details>
