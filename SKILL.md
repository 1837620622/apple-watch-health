---
name: apple-watch-health
description: >
  跨平台（macOS & Windows）本地 USB iAP2 定位修改与 Apple Watch 健康功能工具链（AI Agent Skill）：
  支持将 iPhone 无副作用临时定位到地球上任意地点（内置全球城市 / 地名解析 / 任意经纬度）并随时一键恢复真实定位；
  同时支持自动探测配对的 Apple Watch 型号与 watchOS 版本，协助中国大陆及未开放地区用户在本人设备上开启地区受限健康功能（如高血压通知），
  完成后恢复真实定位并一键清理临时环境。
  触发词：Apple Watch 高血压通知、睡眠呼吸暂停、地区不可用、健康核对清单、修改定位、虚拟定位、定位到香港、定位到东京、恢复定位、
  apple-watch-health、awh、开启手表健康功能。
  不适用于血压数值测量、医疗诊断或商业批量代开。
license: GPL-3.0-only
metadata:
  version: "2.1.0"
  platforms: "macOS, Windows"
---

# Apple Watch Health Skill (`awh`)

面向 AI 编程助手（Claude Code、Codex、Cursor、Antigravity、WorkBuddy 等）的极简跨平台 Skill。所有命令均通过本目录的 `./awh.sh`（macOS/Linux）或 `awh.cmd`（Windows）执行——无需用户预装 Python、Git 或 Homebrew，`uv` 自动隔离管理运行环境，用完可一条命令清理干净。

> **开源免费 & 法律免责**：本项目遵循 GNU GPL-3.0 完全开源免费，**不收取任何费用**。运行或使用本 Skill 即视为操作者已阅读并同意 [legal-and-security.md](references/legal-and-security.md) 中的免责声明与合规边界，自担全部法律、设备与健康风险。

---

## 核心原则

1. **三层状态绝不混淆**：`authentication_succeeded`（认证通过）≠ `location_link_acknowledged`（报文送达）≠ 地图蓝点移动 ≠ 健康 App 显示可设置。必须逐层核实。
2. **先查功能矩阵，再决定是否改定位**：截至 2026-11，**睡眠呼吸暂停提示（2026-10-14 上线）、心电图 ECG、心律不齐、房颤历史、血氧、生命体征、准备指数**在中国大陆已官方原生开放，无需改定位；仅**高血压通知**等仍处大陆审批中的功能需要临时地区坐标。详见 [feature-matrix.md](references/feature-matrix.md)。
3. **只改必要项，不动无关设置**：不修改 Apple ID 地区、系统时间、描述文件或开发者模式；macOS 上 `probe`/`send` 自动运行在 Seatbelt 断网沙箱 (`src/apple_watch_health/local-only.sb`) 内。
4. **用完必须收尾**：开通或中止后，必须执行 `stop` 停掉发送器、引导用户拔线恢复真实定位、复查健康 App 开关状态，并按需运行 `clean --all` 清理临时缓存。

---

## 标准执行流程（5 步极简版）

在 Skill 根目录执行命令（macOS 用 `./awh.sh`，Windows 用 `awh.cmd`）：

### Step 1：体检与功能核对（自动探测 iPhone + Apple Watch）
```bash
./awh.sh doctor
./awh.sh features
```
- `doctor` 会通过 `CompanionProxyService` 自动读出 USB iPhone 与配对 Apple Watch 的型号、iOS/watchOS 版本与地区，并自动判断硬件/系统兼容性；若手表当前关机或蓝牙断开导致无法探测，则直接询问用户手表型号与 watchOS 版本。
- 若 `doctor` 提示未配置 MFi 身份，进入 Step 2。

### Step 2：极速准备本地 MFi 身份（约 68 KB，1–2 秒）
```bash
./awh.sh setup
```
- 默认通过 HTTP Range 仅拉取 DiPlay v0.2.7 APK 中的两份身份资产（~68 KB），执行双文件 SHA-256 锁定 + ECDSA P-256 密钥匹配校验，存入系统用户缓存目录（权限 `0700`/`0600` 或 Windows 仅当前用户 SID ACL），**绝不写入仓库**。
- 可选：`--full-apk`（8 线程并发下载 31MB 整包并核验整包 SHA-256）、`--apk <本地文件>`、`--cert <p7b> --key <pk8>`（使用自有身份）。

### Step 3：握手测试（不发送坐标）
```bash
./awh.sh probe --duration 15
```
- 通过标准：输出依次出现 `device_connected` → `link_ready` → `identification_accepted` → `authentication_succeeded`（退出码 `0`）。
- 若出现 `0xAA04`，说明该共享凭证可能失效或策略变化，查看 [troubleshooting.md](references/troubleshooting.md)。

### Step 4：启动香港定位推送 + 手机端操作
先提醒用户记住当前真实位置，然后在后台启动有时限的香港中环坐标会话（默认 20 分钟自动停止）：
```bash
./awh.sh send --preset hk --duration 1200
```
并同步指导用户在 **iPhone** 上按顺序完成以下操作（缺一不可，macOS + iOS 27.2.1 + S12 实测通过）：
1. **保持 USB 线连接，并开启飞行模式 + 关闭 Wi-Fi + 关闭蜂窝流量**（仅保留蓝牙开；切勿连国内 Wi-Fi 或蜂窝流量，iOS 内置离线国界即可判定 `HK`）。
2. **开启系统自定义**：`设置 → 隐私与安全性 → 定位服务 → 系统服务 → 系统自定义` 保持开启；`地图` 定位权限设为 **使用 App 期间 + 精确位置**。
3. **重置定位缓存（必做）**：保持 `send` 运行，进入 `设置 → 隐私与安全性 → 定位服务` **关闭 → 等满 10 秒 → 重新开启**，然后打开 **Apple 地图**点定位箭头触发 `location_requested`（离线底图空白属正常）。
4. **开通健康功能**：彻底划掉并重开 **健康 App** → 右上角头像 → **健康核对清单** → **高血压通知**，由用户本人如实填写年龄与健康问卷完成设置，确认详情页显示绿色 **通知已打开**。
   - **防误操作提示**：「移动脉率房颤提示软件（心律不齐）」与「移动脉率房颤迹象记录软件（房颤历史）」大陆均已官方开放且**严格互斥**，未确诊房颤用户保留心律不齐即可，切勿误开房颤历史。
   - 若健康仍提示不可用：尝试**先将 Apple Watch 关机（或关闭手表 Wi-Fi）**后重开健康 App，并可用 `./awh.sh monitor` 实时查看脱敏日志。

### 场景 B：通用任意地点定位与无副作用恢复（macOS & Windows）

如果用户只是想把 iPhone 临时定位到任意城市/地标/经纬度（不涉及健康开通），直接运行：
```bash
# 按中英文地名（内置 30+ 热门城市秒级离线解析，其余地标自动在线解析后再进沙箱）
./awh.sh locate --place "东京"
./awh.sh locate --place "Apple Park"
./awh.sh locate --place "35.6595,139.7004"

# 或指定经纬度 / 预设（Windows 把 ./awh.sh 换成 awh.cmd）
./awh.sh locate --lat 48.8566 --lon 2.3522 --duration 600
```
- 手机端操作：保持 USB 连接，打开 Apple 地图点定位箭头（若原地不动，关闭「定位服务」10 秒再开即可生效）。
- **无副作用恢复真实定位**（随时执行）：
```bash
./awh.sh restore
```
会立即终止后台发送器、释放 PID 锁，不留任何系统配置改动；拔掉 USB 线（必要时开关一次定位服务 10 秒）即可恢复真实 GPS。

---

### Step 5：停止发送、恢复真实定位与清理环境
```bash
./awh.sh restore
```
1. 指导用户关闭飞行模式、拔掉 USB 数据线（若地图仍停在香港，开关一次定位服务 10 秒），在地图确认恢复真实位置。
2. 重新打开 **健康 App → 高血压通知详情页**，确认状态仍为 **通知已打开**（开通后前 30 天为基线采集期，无通知属正常；请勿手动关闭开关）。
3. 如用户不再需要本地缓存，执行清理：
   - `./awh.sh clean`：清理临时文件与旧版冗余缓存（保留身份以便复测）。
   - `./awh.sh clean --all`：彻底删除本地身份文件、缓存目录与 `.venv` 虚拟环境，不留痕迹。

---

## 参考文档

- [references/feature-matrix.md](references/feature-matrix.md) — 2026-11 watchOS 27 / iOS 27 中国大陆 Apple Watch 全功能支持与限制对照表
- [references/troubleshooting.md](references/troubleshooting.md) — 原理剖析（`isProducedByAccessory`、`GPRMC Screening`、`countryd`）、iOS 27.2.1 排障、Windows USB 驱动
- [references/legal-and-security.md](references/legal-and-security.md) — 法律免责声明、开源免费声明、MFi 凭证来源披露与安全边界
