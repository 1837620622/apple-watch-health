# Apple Watch Health Skill (`awh`)

![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)
![Platform: macOS | Windows](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-informational)
![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab)
![Free & Open Source](https://img.shields.io/badge/Cost-100%25%20Free%20%26%20Open%20Source-brightgreen)

**跨平台（macOS & Windows）极简本地 USB iAP2 工具链与 AI Agent Skill** —— 面向零开发环境新手，通过本地 USB 模拟 MFi 外部 GPS 配件，在未开放地区（如中国大陆）对本人 Apple Watch（Series 9–12 / Ultra 2–4）进行地区限制健康功能（如 **高血压通知**）的兼容性测试、诊断与开通，完成后恢复真实定位并一键清理环境。

适配 **Claude Code、Codex、Cursor、Antigravity、WorkBuddy** 等本地 AI Agent，也支持纯命令行直接使用。

---

> ### ⚖️ 开源免费声明 & 法律免责声明（使用前必读）
>
> 1. **100% 开源免费，绝无任何费用**：本项目遵循 [GPL-3.0-only](LICENSE) 完全开源，**永久免费、无任何收费/会员/代开服务**。请警惕任何第三方收费倒卖或付费代开。
> 2. **使用即默认同意并自担全部责任**：下载、安装、运行或参考本项目代码与文档，**即默认视为你已完整阅读并无条件同意 [完整法律免责声明](references/legal-and-security.md)**。使用者须自行承担全部法律、监管合规、设备状态、保修及健康风险，作者及贡献者不承担任何直接或间接责任。
> 3. **非医疗设备声明**：Apple Watch 高血压通知、睡眠呼吸暂停提示等仅作趋势筛查参考，**不能测出具体血压数值，不能替代血压计或专业医生诊断**；身体不适请及时就医。
> 4. **零内置密钥**：本仓库**不包含、不分发任何 MFi 证书、私钥或 APK 文件**，仅限设备所有人在本人自有设备上用于技术研究与兼容性测试。本项目与 Apple Inc.、DiPlay、Carlinkit 等无任何关联。

---

## 为什么重写这个 Skill？（核心优化一览）

相比早期粗糙的 Shell 脚本包装，v2.0 彻底重构为**原生跨平台 `uv` Python 包 + 极简 Skill**：

| 维度 | 旧版脚本痛点 | v2.0 重构方案 |
|---|---|---|
| **iOS + 手表自动检测** | `device_info.sh` 同步调异步 `pymobiledevice3 v9+` 必报 `AttributeError`，且无法读取手表 | 全链路 `async/await` 适配 `pymobiledevice3>=9.40`，并通过 `CompanionProxyService` **自动识别配对的 Apple Watch 型号与 watchOS 版本**（手表关机/断联时自动回退为人工确认） |
| **身份提取速度** | 声称「8 段并行」实为串行下载 31.3 MB APK，国内耗时 15–20 分钟 | **HTTP Range Remote-ZIP 极速提取**，仅拉取 ~68 KB（实测 **3.6 秒**）并执行双文件 SHA-256 锁定 + P-256 密钥校验；保留真 8 线程 `--full-apk` 与本地 `--apk`/`--cert --key` |
| **跨平台与稳定性** | 硬编码 macOS `sh`、无 Windows 支持、切飞行模式断连即退出 | **macOS（真机实测通过）+ Windows（CI 打包兼容）** 双平台支持；`send` 内置**断线自动重连** + 单实例 PID 锁 + `SIGINT/SIGTERM` 安全释放 + Seatbelt 断网沙箱 |
| **新手零配置** | 依赖手工装 Python/pip，在 Homebrew 环境易撞 PEP 668 | `awh.sh` 自动检测或免 sudo 自动安装独立版 `uv`，自动管理 Python 3.11+ 与隔离 `.venv`；用完 `./awh.sh clean --all` 彻底清理 |
| **为什么只改定位不行** | 缺少机制说明与日志诊断，只能盲试 | 内置 `awh monitor` 实时监听脱敏后的 `GPRMC Screening` / `CurrentCountryIsSupported` / `countryCode` 状态变化，精准排查 `CN → HK` |
| **安全性与收尾** | 无单实例锁、无 `SIGTERM` 清理、沙箱包裹 `uv` 易报错 | 单实例 PID 锁 + `SIGINT/SIGTERM` 嵌套安全释放 + macOS Seatbelt 仅包裹纯 Python 运行时（断绝一切 IP 联网） |

---

## 中国大陆 Apple Watch 功能现状（2026-11 · watchOS 27 / S12）

> 很多攻略已经过时！截至 **2026 年 11 月**，大部分健康功能在大陆已经官方开放，**不要盲目改定位**：

| 功能 | 大陆原生状态 | 是否需要本工具 | 说明 |
|---|---|---|---|
| **高血压通知** | ❌ 审批中未开放 | ✅ **需要**（iOS 27.2.1 + S12 实测通过） | 需 S9–S12 / Ultra 2–4（不含 SE）、iPhone 11+、22岁+未怀孕未确诊 |
| **睡眠呼吸暂停提示** | ✅ **2026-10-14 已大陆上线** | 旧系统/未灰度时可用；新系统直接开 | S9+ / Ultra 2+ / SE 3，18岁+未确诊 |
| **心电图 (ECG) / 心律不齐 / 房颤历史 / 血氧** | ✅ 大陆全部官方开放 | ❌ 无需改定位 | 直接在健康 App / Watch App 开启 |
| **准备指数 / 生命体征 / 恢复 HRV / 睡眠评分** | ✅ 大陆 S12 原生支持 | ❌ 无需改定位 | watchOS 27 自带 |
| **卫星联络 / 国行快充 / FaceTime Audio** | ❌ 硬件或系统限制 | ❌ 改定位无效 | 非定位门控功能 |

完整矩阵与官方依据见 [references/feature-matrix.md](references/feature-matrix.md) 或运行 `./awh.sh features`。

---

## 快速开始（小白 3 分钟上手）

### 方式 A：作为 AI Agent Skill 使用（推荐小白）

将本仓库克隆或下载 ZIP 解压到你的 Agent Skills 目录（如 `~/.claude/skills/apple-watch-health`、`~/.codex/skills/apple-watch-health`、`~/.gemini/config/skills/apple-watch-health` 等），新开对话直接发：

> 使用 apple-watch-health，检查我的 USB iPhone 与环境，帮我在合规前提下测试并开启 Apple Watch 高血压通知，完成后恢复真实定位并清理缓存。

### 方式 B：命令行直接使用

**前置条件**：macOS 或 Windows（Windows 需装微软商店版「Apple 设备」：`winget install --id 9NP83LWLPZ9K`），一根 **USB 数据线**，iPhone 解锁屏幕并点「信任此电脑」。

#### macOS / Linux
```bash
git clone https://github.com/1837620622/apple-watch-health.git
cd apple-watch-health

# 1. 环境体检 & 查看已连接的 iPhone（会自动准备好 uv 与隔离环境）
./awh.sh doctor

# 2. 极速提取并校验本地 MFi 身份（仅 ~68 KB，1-2 秒；国内可加 AWH_PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple）
./awh.sh setup

# 3. 握手测试（确认出现 authentication_succeeded）
./awh.sh probe --duration 15

# 4. 启动香港中环坐标推送（默认 20 分钟自动结束；macOS 自动运行在断网沙箱内）
./awh.sh send --preset hk
```

#### Windows (CMD / PowerShell) — 避坑必看
> **为什么很多 Windows 用户说弄不了？90% 都卡在 Apple USB 驱动没装好或被抢占！按下面 3 条检查包通：**
> 1. **必须装 Apple USB 驱动**：管理员终端执行 `winget install --id 9NP83LWLPZ9K`（微软商店版「Apple 设备」，装完建议重启一次；**不要和官网 exe 版旧 iTunes 同时装**，会冲突）。
> 2. **插主板后置 USB 口**：优先插台式机背后主板 USB 口或笔记本原生口，避开供电不足的前置口/廉价扩展坞；插线后打开「Apple 设备」确认已点「信任」识别成功，然后**把「Apple 设备」窗口关掉**（避免抢占 USB 通道）。
> 3. **中文终端防乱码已内置**：直接运行 `awh.cmd`（会自动免配置装好 `uv` 和 UTF-8 环境），先跑 `awh.cmd doctor` 看到 `[OK] USB 服务: Apple Mobile Device Service (127.0.0.1:27015) 运行中` 且读出你的 iPhone + Watch 型号即可继续！

```cmd
git clone https://github.com/1837620622/apple-watch-health.git
cd apple-watch-health

awh.cmd doctor
awh.cmd setup
awh.cmd probe --duration 15
awh.cmd send --preset hk
```

### 手机端关键配合步骤（macOS 真机实测通过 · 缺一不可！）

> **已实测通过环境**：**macOS** + **iPhone 16 Pro（iOS 27.2.1，国行 CH/A）** + **Apple Watch Series 12（watchOS 27.2.1，国行 CH/B）**，成功在健康 App 弹出并开启 **高血压通知**，停止并恢复真实定位后保持「通知已打开」。
> Windows 端已做完整兼容与 GitHub Actions 自动化测试，真机流程与下方一致（需先装微软商店版「Apple 设备」USB 驱动）。

保持 **USB 数据线始终连接** 且 `awh send --preset hk` 运行的同时，在 **iPhone** 上严格按顺序操作：

1. **开启飞行模式 + 关闭 Wi-Fi + 关闭蜂窝流量**（**最关键！** 千万不要连国内 Wi-Fi 或开流量，仅保留**蓝牙开启**即可；iOS 内置离线国界数据即可识别香港，连国内 Wi-Fi 或流量会把国家码拉回 `CN` 导致失败）。
2. **核对系统开关**：
   - `设置 → 隐私与安全性 → 定位服务 → 系统服务 → 系统自定义` → **必须开启**
   - `设置 → 隐私与安全性 → 定位服务 → 地图` → 设为 **使用 App 期间** + 开启 **精确位置**
3. **重置定位缓存（必做！）**：保持 `send` 运行，进入 `设置 → 隐私与安全性 → 定位服务`，**关闭定位服务 → 默数至少 10 秒 → 重新开启定位服务**。
4. **触发订阅并开通功能**：
   - 打开 **Apple 地图**点一下右上角定位箭头（离线底图空白属正常，此步用于触发手机发出 `location_requested` 订阅并接收香港坐标）。
   - 彻底划掉并重开 **健康 App → 右上角头像 → 健康核对清单 → 高血压通知**，本人如实回答问卷并完成设置，确认详情页显示绿色 **通知已打开**。
   - **⚠️ 新手高频误区：「移动脉率房颤迹象记录软件（房颤历史）」为什么开不了？**
     - 健康 App 里有两个名字几乎一样、但苹果强制**二选一互斥**的功能（两项在中国大陆都**已官方开放**，不需要改定位）：
       1. **移动脉率房颤提示软件（心律不齐通知）**：给**没确诊过房颤的普通人**日常预警用。
       2. **移动脉率房颤迹象记录软件（房颤历史）**：仅给**医生已确诊房颤的患者**统计每周房颤占比用。
     - **一句话记住**：开了其中一个，就不能再同时开另一个！普通人保留「心律不齐通知」就行，没有房颤病史就不要去点「房颤历史」；如果确实要换开另一个，先在健康 App 关闭当前那个，再开启另一个（如遇旧系统地区限制再重跑本 Skill 即可）。
   - 若地图已触发但健康仍提示不可用：尝试**先将 Apple Watch 关机**（或关闭手表 Wi-Fi）后重开健康 App，并可在另一终端运行 `./awh.sh monitor` 实时查看脱敏日志。
5. **收尾恢复真实定位**：
   ```bash
   ./awh.sh stop          # 停止发送器
   ```
   关闭飞行模式（恢复 Wi-Fi/流量）、拔掉 USB 线（若地图仍停在香港，再关闭定位服务 10 秒后重开），在地图确认真实位置已恢复，再次进健康 App 确认 **通知已打开** 依然保留。
6. **一键清理环境**（用完不留垃圾）：
   ```bash
   ./awh.sh clean         # 清理旧缓存与临时文件（保留身份）
   ./awh.sh clean --all   # 彻底删除本地身份、缓存与 .venv 虚拟环境
   ```

---

## 命令一览

| 命令 | 说明 |
|---|---|
| `doctor` | 一键体检：平台、USB 驱动、MFi 身份、残留进程、已连接 iPhone 型号与 iOS 版本 |
| `devices` | 列出所有 USB/网络 iOS 设备（支持 `--all`、`--full-udid`） |
| `features` | 查看 2026-11 watchOS 27 中国大陆 Apple Watch 全功能矩阵 |
| `presets` | 查看内置坐标预设（`hk`, `hk-tst`, `mo`, `us-sf`, `us-ny`, `jp`, `sg`, `tw`） |
| `setup` | 极速 Range 提取 MFi 身份（支持 `--full-apk`、`--apk <path>`、`--cert`/`--key`、`--force`） |
| `probe` | 仅做 USB iAP2 识别与 MFi-v3 握手测试，不发送坐标 |
| `send` | 有时限 USB iAP2 坐标推送（支持 `--preset`、`--lat/--lon`、`--udid`、`--duration`、`--no-sandbox`） |
| `monitor` | 实时监听脱敏后的 iPhone 定位筛查（`GPRMC`）与健康地区代码（`CN → HK`）诊断日志 |
| `stop` | 通过 PID 锁安全停止后台发送进程 |
| `clean` | 清理冗余与临时缓存；加 `--all` 彻底删除本地身份与 `.venv` |

---

## 项目结构

```text
apple-watch-health/
├── SKILL.md                      # AI Agent Skill 规范入口
├── README.md                     # 项目说明与新手文档
├── LICENSE                       # GNU GPL-3.0-only
├── pyproject.toml                # uv / hatchling 跨平台包配置
├── awh.sh / awh.cmd              # macOS/Linux 与 Windows 零配置入口脚本
├── sandboxes/local-only.sb       # macOS Seatbelt 断网沙箱配置
├── references/                   # 功能矩阵、深度排障、法律与安全说明
├── src/apple_watch_health/       # 模块化纯 Python 3.11+ 核心实现
└── tests/                        # 单元测试套件（pytest + ruff）
```

## 致谢与上游参考

- 协议与设计参考：[aur3l14no/accessory-location](https://github.com/aur3l14no/accessory-location)、[xpqball/iap2-location-bridge-windows](https://github.com/xpqball/iap2-location-bridge-windows)、[youcci/playport](https://github.com/youcci/playport)、[flute/apple-watch-hypertension-skill](https://github.com/flute/apple-watch-hypertension-skill) 及社区实测反馈。
- 实验性共享配件身份来源说明：[shihabal3amri/DiPlay THIRD_PARTY_NOTICES](https://github.com/shihabal3amri/DiPlay/blob/main/docs/THIRD_PARTY_NOTICES.md#experimental-authentication-data)（本仓库不附带任何第三方身份材料）。
