<div align="center">

# Apple Watch Health (`awh`)

**不越狱 · 免 Root · 永久免费开源**

一根 USB 数据线，在 **macOS & Windows** 上一站式搞定两件刚需：<br>
**① 对标爱思助手的 iPhone 全局虚拟定位**（微信 / 地图开 Wi-Fi 照样生效，支持高德百度纠偏、GPX 轨迹、自然微幅漂移，用完一键恢复）<br>
**② 解锁国行 Apple Watch 地区受限健康功能**（USB 外置 MFi 硬件通道过健康校验，自动探测手表型号与 watchOS 版本）

[![Release](https://img.shields.io/github/v/release/1837620622/apple-watch-health?style=flat-square&color=2563eb)](https://github.com/1837620622/apple-watch-health/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/1837620622/apple-watch-health/ci.yml?style=flat-square&label=macOS%20%26%20Windows%20CI)](https://github.com/1837620622/apple-watch-health/actions)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0--only-0f172a.svg?style=flat-square)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-0284c7.svg?style=flat-square)](#)
[![AI Agent Skill](https://img.shields.io/badge/AI%20Agent-Skill%20Ready-7c3aed.svg?style=flat-square)](SKILL.md)
[![100% Free](https://img.shields.io/badge/cost-100%25%20Free%20%26%20Open%20Source-16a34a.svg?style=flat-square)](references/legal-and-security.md)

[30 秒极速上手](#30-秒极速上手) · [iPhone 全局改定位与轨迹模拟](#场景一iphone-全局改定位与-gpx-轨迹模拟) · [解锁 Watch 高血压通知](#场景二解锁-apple-watch-高血压通知) · [Windows 避坑](#windows-避坑指南) · [命令速查](#命令速查)

</div>

---

## 为什么选择 `awh`？

| 痛点 | 市面收费软件 / 远程代开 | **Apple Watch Health (`awh`)** |
| --- | --- | --- |
| **费用与隐私** | 几十到上百元买激活码/代开，闭源不透明 | **GPL-3.0 永久免费开源**，纯本地 USB 运行，零云端上传 |
| **高德 / 微信偏移 500 米** | 直接塞 WGS-84 或写反经纬度导致大偏移 | 内置 **GCJ-02 / BD-09 → WGS-84 亚米级纠偏**，自动识别高德/百度 `经度,纬度` |
| **改完定位几秒跳回原处** | iOS 17+ 隧道一断就失效 | **后台守护保持 + 自动重连 + 自动挂载 DDI 并刷新隧道**，稳如原生 |
| **静止死坐标 / 跑路线** | 只支持单点静止坐标 | 支持 **`--jitter` 自然微幅 GPS 漂移** 与 **`--gpx` 路线轨迹回放**（倍速/循环） |
| **Apple Watch 健康不认** | 普通软件模拟被 `healthd` 拦截 | **双引擎分工**：`send` 走 USB MFi 硬件 GPS (`isProducedByAccessory`)，实测过健康校验 |
| **环境折腾 / 留垃圾** | 要装 Xcode、配 Python 或改系统设置 | **零前置开箱即用**（自动拉独立 `uv`），**不改 Apple ID / 时区 / 描述文件**，`clean --all` 一键清场 |
| **AI 助手联动** | 需手动一步步折腾 | 原生 **AI Agent Skill**（Claude Code / Codex / Cursor / Antigravity / WorkBuddy 通用） |

---

## 双引擎工作原理（各司其职）

iOS 17–27 对「普通 App 定位」与「健康 App 地区校验」走了完全不同的系统通道，本工具内置**双引擎**：

| 引擎 | 命令 | 适用场景 | 是否需要开发者模式 | 开 Wi-Fi / 5G 流量 |
| --- | --- | --- | --- | --- |
| **官方免 Root 定位引擎** | `locate`（默认） | **微信 / 高德 / 苹果地图等全部 App 全局改定位 + GPX 轨迹**（iOS 17+ 走 `UserspaceRsdTunnel` + DVT；iOS <= 16 走 `DtSimulateLocation`） | iOS 16+ 需要（免 Root/管理员） | **开着 Wi-Fi / 5G 照样秒生效** |
| **iAP2 外置 MFi 配件引擎** | `send` | **开通 Apple Watch 高血压通知**（走 `isProducedByAccessory`，通过 `healthd` 地区校验） | **不需要** | 需开飞行模式 + 关 Wi-Fi/流量 |

```text
  Mac / Windows (awh)                              iPhone (USB 信任)                  Apple Watch
  ────────────────────                             ─────────────────                  ───────────
  • [体检] companion_proxy ────USB───────────────► lockdownd ───────────────────────► 自动读手表型号 & watchOS
  • [全局] UserspaceRsdTunnel / DtSimulateLocation► DVT LocationSimulation (自动DDI) ► 微信/地图/GPX 轨迹全生效
  • [健康] iAP2 + MFi-v3 ◄────0xAA00/0xFFFB──────► com.apple.carkit.service ────────► healthd 解锁高血压通知
  • [恢复] awh restore ──────────────────────────► 停守护进程 + stopLocationSimulation► 零副作用秒回真实 GPS
```

- **真机实测**：**macOS** + **iPhone 16 Pro (`iOS 27.2.1`, CH/A)** + **Apple Watch Series 12 (`watchOS 27.2.1`, CH/B)** 已验证 `iAP2` 开通高血压通知与 `DVT` 免 Root 改定位/一键恢复；**macOS & Windows** 双平台已通过完整 CI 单测与启动器验证。
- **快且安全**：MFi 实验身份经 HTTP Range 仅拉取 ~68 KB（实测约 3 秒），双文件 SHA-256 锁定 + 防 Deflate 炸弹限流，仓库**零内置密钥/APK**；macOS 上 `iAP2` 自动进 Seatbelt 断网沙箱。

---

## 30 秒极速上手

### 方式一：装给 AI 助手当 Skill 用（最省心）

在 [Releases](https://github.com/1837620622/apple-watch-health/releases/latest) 下载 `apple-watch-health-skill.zip`，解压放到你的 Agent 技能目录（Claude Code / Codex / Cursor / Antigravity / WorkBuddy 通用），插上 USB 线后直接说人话：

> 「帮我把手机定位改到纽约时代广场，用后台保持模式」<br>
> 「把高德坐标 `116.3974,39.9092` 用 gcj02 纠偏定位过去，加 5 米自然漂移」<br>
> 「帮我检查手机和手表，开启 Apple Watch 高血压通知，弄完恢复真实定位并清理缓存」

### 方式二：终端命令行直接用（零 Python / Xcode 前置）

插上 **USB 数据线**（别用纯充电线），iPhone 解锁并点「信任此电脑」：

```bash
git clone https://github.com/1837620622/apple-watch-health.git
cd apple-watch-health

# macOS / Linux 用 ./awh.sh，Windows 把 ./awh.sh 换成 awh.cmd
./awh.sh doctor                  # 1. 自动识别 iPhone + Apple Watch 型号、系统与驱动状态
```

> 国内首次同步依赖如果慢，在命令前加清华镜像即可：<br>
> `AWH_PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple ./awh.sh doctor`

---

## 场景一：iPhone 全局改定位与 GPX 轨迹模拟

开着 Wi-Fi 与 5G 流量，让 **微信、苹果地图、高德地图等全部 App** 精准定位到世界任意位置（内置 **75+ 国内外热门城市离线秒解析** + 在线地名解析），免 `sudo` / 免管理员权限：

```bash
# 0) 首次使用（iOS 16+）：检查/显示「开发者模式」开关
./awh.sh devmode                 # 查看状态
./awh.sh devmode --reveal        # iPhone 设置 → 隐私与安全性 → 开发者模式 → 打开 → 重启 → 点【打开】

# 1) 任意城市 / 地标定位（前台保持：Ctrl+C 立即恢复真实定位）
./awh.sh locate --place "纽约"
./awh.sh locate --place "东京" --detach                # 后台保持（命令立即返回，可关闭终端）
./awh.sh locate --place "Apple Park" --hold 600        # 保持 600 秒后自动恢复

# 2) 高德 / 腾讯 / 微信 / 百度坐标精准不偏移（支持直接粘贴高德/百度「经度,纬度」！）
./awh.sh locate --place "116.3974,39.9092" --coord-type gcj02 --detach   # 自动识别经度,纬度 + GCJ-02 纠偏
./awh.sh locate --lat 39.9151 --lon 116.4039 --coord-type bd09 --detach  # 百度地图 BD-09 自动纠偏

# 3) 自然微幅 GPS 漂移（防静止死坐标，每次刷新在 ±5 米内自然微动）
./awh.sh locate --place "上海" --jitter 5 --detach

# 4) 导入 GPX 轨迹模拟步行/驾车路线（兼容爱思助手 / 标准 GPX 1.0 & 1.1）
./awh.sh locate --gpx route.gpx --speed 1.5 --loop --detach
./awh.sh locate --gpx amap_route.gpx --coord-type gcj02 --jitter 3       # 支持 GPX 同步纠偏与漂移

# 5) 随时查看状态 & 零副作用一键恢复真实定位
./awh.sh status                  # 查看当前定位目标、GPX 进度、已运行时长
./awh.sh restore                 # 停止后台保持并发送官方 stopLocationSimulation，秒回真实 GPS
```

> **提示**：首次在未连过 Xcode 的手机（特别是 Windows 电脑）上运行 `locate` 时，工具会自动联网下载并挂载对应版本的 Developer Disk Image (DDI) 并重建隧道刷新服务表，保持手机解锁与电脑联网即可。

---

## 场景二：解锁 Apple Watch 高血压通知

### 2026-11 中国大陆功能现状（别白折腾）

截至 **2026 年 11 月**，国行 Apple Watch 大部分健康功能已官方原生开放，仅仍在审批中的功能需要临时地区坐标：

| 功能 | 中国大陆现状 | 需要本工具吗 | 说明 |
| --- | --- | --- | --- |
| **高血压通知** | 未开放（审批中） | **需要** | S9–S12 / Ultra 2–4（**不含 SE**）、iPhone 11+、22 岁+ 未怀孕未确诊 |
| **睡眠呼吸暂停提示** | **2026-10-14 已开放** | 旧系统才需要 | 新系统直接开；S9+ / Ultra 2+ / SE 3，18 岁+ 未确诊 |
| **心电图 (ECG) / 血氧 / 心律不齐 / 房颤历史** | 已全部官方开放 | 不需要 | 正常联网直接在健康 App / Watch App 开 |
| **准备指数 / 生命体征 / 睡眠评分** | S12 原生支持 | 不需要 | watchOS 27 自带 |
| **卫星联络 / 国行快充 / FaceTime Audio** | 硬件或系统级限制 | **改定位无效** | 非地区定位开关 |

完整条件见 [references/feature-matrix.md](references/feature-matrix.md)，或终端运行 `./awh.sh features`。

### 4 行命令 + 手机端 4 步（缺一不可）

```bash
./awh.sh doctor                  # 1. 识别 iPhone + Apple Watch 型号与兼容性
./awh.sh setup                   # 2. 极速提取本地 MFi 身份（~68 KB，约 3 秒）
./awh.sh probe --duration 15     # 3. 握手测试，看到 authentication_succeeded 即通过
./awh.sh send --preset hk        # 4. 推送香港中环硬件 GPS 坐标（默认 20 分钟自动停，断线自动重连）
```

保持 **USB 线插着** 且 `send --preset hk` 运行中，在 **iPhone** 上按顺序完成 4 步：
1. **开飞行模式 + 关 Wi-Fi + 关蜂窝流量**（仅留蓝牙开；切勿连国内 Wi-Fi 或基站，iOS 内置离线国界即可识别 `HK`）。
2. **确认定位开关**：`设置 → 隐私与安全性 → 定位服务 → 系统服务 → 系统自定义` 保持打开；`地图` 权限为 **使用 App 期间 + 精确位置**。
3. **重置定位缓存（关键）**：进 `定位服务` → **关闭 → 默数 10 秒 → 重新打开** → 打开 **Apple 地图**点定位箭头触发拉取坐标（离线地图空白属正常）。
4. **进健康 App 开通**：彻底划掉并重开 **健康 App → 右上角头像 → 健康核对清单 → 高血压通知**，按真实情况填写并确认显示绿色 **通知已打开**。仍灰显时先将 **Apple Watch 关机**再划掉重进健康 App，或另开终端跑 `./awh.sh monitor` 看实时脱敏诊断日志。

> **关于「移动脉率房颤迹象记录软件（房颤历史）」**：大陆已官方开放，但与「心律不齐通知」**严格二选一互斥**。未确诊房颤的普通用户保留「心律不齐通知」即可，切勿误开「房颤历史」。

### 开通后收尾与清场

```bash
./awh.sh restore                 # 停止定位推送并恢复真实定位
./awh.sh clean                   # 清理临时缓存（保留 MFi 身份方便复测）
./awh.sh clean --all             # 彻底删除本地身份、缓存与 .venv，不留痕迹
```

然后关闭飞行模式、拔掉 USB 线（若地图仍停在香港，开关一次定位服务 10 秒），复查健康 App 中 **通知已打开** 依然保留（前 30 天为基线采集期，无提醒属正常，**请勿手动关闭开关**）。

---

## Windows 避坑指南

Windows 端已通过 GitHub Actions `windows-latest` 完整单测与 `awh.cmd` 冒烟验证。绝大部分卡点来自 USB 驱动而非代码：

1. **安装微软商店版「Apple 设备」驱动**：管理员终端运行 `winget install --id 9NP83LWLPZ9K`，装完重启电脑。**不要同时安装旧版 exe iTunes**，驱动会冲突。
2. **插主板原生 USB 口**：优先插机箱背后主板口，识别并点「信任」后**关掉「Apple 设备」窗口**避免占着 USB。
3. 先跑 `awh.cmd doctor`，确认 `[OK] USB 服务: Apple Mobile Device Service (127.0.0.1:27015) 运行中` 且读出设备信息。
4. **改定位前先跑 `awh.cmd devmode --reveal`**：Windows 上从未连过 Xcode 的 iPhone 默认隐藏「开发者模式」开关；显示并打开重启后即可直接 `awh.cmd locate`。
5. 若国内自动下载 DDI 较慢，保持网络畅通稍等即可；iOS 17–26 在 RSD 挂载失败时会自动回退高速 USB 通道挂载。

更多排障见 [references/troubleshooting.md](references/troubleshooting.md)。

---

## 命令速查

| 命令 | 作用 |
| --- | --- |
| `doctor` | 全面体检：系统、USB 驱动、MFi 身份、后台进程、自动识别 iPhone & Apple Watch |
| `status` | 秒看当前 DVT 定位保持 / GPX 轨迹进度 / iAP2 发送器与 MFi 就绪状态 |
| `devices` | 列出已连接设备与配对手表（`--all` 含网络设备，`--full-udid` 显示完整 UDID） |
| `devmode` | 查看开发者模式；`--reveal` 显示设置开关，`--enable` 直接开启（无锁屏密码） |
| `locate` | 全局改定位与 GPX 轨迹模拟（支持 `--place`、`--lat/--lon`、`--coord-type wgs84\|gcj02\|bd09`、`--jitter`、`--gpx`、`--speed`、`--loop`、`--detach`、`--hold`、`--engine iap2`） |
| `restore` | 一键停止所有模拟进程并发送官方 `stopLocationSimulation`，零副作用恢复真实 GPS |
| `setup` | 极速提取本地 MFi 身份（`--full-apk`、`--apk`、`--cert/--key`、`--force`） |
| `probe` | 仅做 USB iAP2 识别与 MFi-v3 握手测试，不发坐标 |
| `send` | iAP2 硬件 GPS 坐标推送（专用于开通健康功能，支持 `--preset`、`--place`、`--coord-type`） |
| `monitor` | 实时查看脱敏后的 iPhone 定位筛查（`GPRMC`）与健康地区状态日志 |
| `features` | 查看 2026-11 大陆 Apple Watch 功能支持矩阵 |
| `presets` | 查看内置坐标预设（`hk`, `hk-tst`, `mo`, `us-sf`, `us-ny`, `jp`, `sg`, `tw`） |
| `stop` | 优雅停止后台 iAP2 发送进程并清理子进程与锁 |
| `clean` | 清理缓存与残留；加 `--all` 彻底删除身份文件与 `.venv` |

---

## 法律与免责声明

1. **完全开源免费，零费用**：本项目以 [GNU GPL-3.0-only](LICENSE) 协议开源，**永久免费，不收任何费用**，没有付费版、激活码或代开服务。任何渠道的收费倒卖均与作者无关。
2. **使用即默认同意并自担全部责任**：下载、安装、运行或参考本项目的任何代码与文档，即默认你已阅读并同意 [references/legal-and-security.md](references/legal-and-security.md) 的全部条款，**自行承担由此产生的一切法律、监管合规、设备保修、账号及健康风险**，作者与贡献者不承担任何直接或间接责任。
3. **非医疗器械**：Apple Watch 高血压通知、睡眠呼吸暂停提示等仅作健康趋势参考，**测不出具体血压数值，不能替代血压计或医生诊断**；身体不适请立刻就医。
4. **零内置密钥与研究边界**：本仓库**不包含、不分发任何 MFi 证书、私钥或 APK 文件**，仅限设备所有人在本人自有设备上做技术研究与兼容性测试，严禁用于商业代开或批量操作。本项目与 Apple Inc.、DiPlay、Carlinkit 等无任何关联。

<details>
<summary>致谢与上游参考</summary>

- 协议与实现参考：[aur3l14no/accessory-location](https://github.com/aur3l14no/accessory-location)、[xpqball/iap2-location-bridge-windows](https://github.com/xpqball/iap2-location-bridge-windows)、[youcci/playport](https://github.com/youcci/playport)、[flute/apple-watch-hypertension-skill](https://github.com/flute/apple-watch-hypertension-skill)、[doronz88/pymobiledevice3](https://github.com/doronz88/pymobiledevice3)
- 第三方实验凭据来源披露：[shihabal3amri/DiPlay THIRD_PARTY_NOTICES](https://github.com/shihabal3amri/DiPlay/blob/main/docs/THIRD_PARTY_NOTICES.md#experimental-authentication-data)

</details>
