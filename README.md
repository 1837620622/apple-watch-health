<div align="center">

# Apple Watch Health (`awh`)

**Local USB iAP2 Accessory-Location Toolkit & AI Agent Skill**

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0--only-0f172a.svg?style=flat-square)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/1837620622/apple-watch-health/ci.yml?style=flat-square&label=macOS%20%26%20Windows%20CI)](https://github.com/1837620622/apple-watch-health/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-2563eb.svg?style=flat-square)](#)
[![Zero Cost](https://img.shields.io/badge/cost-100%25%20Free%20%26%20Open%20Source-16a34a.svg?style=flat-square)](references/legal-and-security.md)

通过本地 USB 模拟外置 MFi GPS 配件（iAP2 / NMEA），把 iPhone 临时定位到香港等支持地区，<br>
帮中国大陆或未开放地区的 Apple Watch 开启地区受限健康功能，开完恢复真实定位并一键清场。

[3 分钟上手](#快速开始) · [手机端必做步骤](#手机端操作缺一不可) · [Windows 避坑](#windows-避坑) · [功能清单](#2026-11-大陆功能现状) · [免责声明](#法律与免责声明)

</div>

---

## 工作原理

为什么用 Xcode 或普通改定位工具把地图改到香港，**健康 App 还是提示「所在地区不可用」**？

Apple 在 iOS 26/27 里给位置打了来源标签：`isSimulatedBySoftware`（开发者模拟）会被健康服务直接拒收；而外置硬件 GPS（如 CarPlay 车机、蓝牙/USB MFi GPS）走的是 `isProducedByAccessory`，系统将其视为可信硬件定位。

```text
  Mac / Windows (awh)                         iPhone (USB 信任)                 Apple Watch
  ────────────────────                        ─────────────────                 ───────────
  1. pymobiledevice3 (async) ──USB──────────► lockdownd / companion_proxy ────► 自动读手表型号 & watchOS
  2. MFi-v3 本地签名 (P-256) ◄─0xAA00/0xAA02─► com.apple.carkit.service (iAP2)
  3. 注入香港 GPGGA / GPRMC  ──0xFFFB───────► locationd (离线国界 → HK) ──────► healthd 解锁开通入口
     (macOS 走 Seatbelt 断网沙箱)              (开飞行模式 + 关 Wi-Fi/流量)       (开完停推恢复真实定位)
```

- **实测验证环境**：**macOS** + **iPhone 16 Pro (`iOS 27.2.1`, 国行 CH/A)** + **Apple Watch Series 12 (`watchOS 27.2.1`, 国行 CH/B)** 已完整跑通，成功开通高血压通知，停推恢复真实定位后开关保持开启。
- **不装开发环境也能用**：`awh.sh` / `awh.cmd` 会自动检测或安装独立版 `uv` 与隔离 Python 环境，不碰系统全局 Python；用完一条 `clean --all` 删干净。
- **速度与安全**：身份文件通过 HTTP Range Remote-ZIP 只拉取 ~68 KB（实测约 **3.6 秒**，替代旧方案 31 MB 二十分钟龟速下载），双文件 SHA-256 校验，仓库内**零内置证书/私钥/APK**；macOS 运行 `probe`/`send` 时自动切入 Seatbelt 内核沙箱断绝全部 IP 联网。

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
| `send` | 推送坐标（支持 `--preset`、`--lat/--lon`、`--udid`、`--duration`、`--no-sandbox`） |
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
