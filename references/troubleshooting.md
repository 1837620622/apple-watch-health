# 原理剖析与深度排障手册（macOS & Windows · iOS 26/27）

## 一、为什么「单独改定位」经常失败？底层机制全解析

很多用户用 Xcode GPX、普通开发者模式 `simulate-location` 或免费改定位工具把地图改到了香港，但**健康 App 依然显示「所在地区不可用」**。原因在于 Apple 在 iOS 26/27 与 watchOS 26/27 上同时叠加了四层校验：

1. **位置来源标记校验（核心差异）**
   - CoreLocation 的 `CLLocationSourceInformation` 会明确区分：
     - `isSimulatedBySoftware = true`（Xcode / DVT `simulate-location` / 普通开发者模拟）→ **`healthd` 直接拒收**。
     - `isProducedByAccessory = true`（USB/蓝牙 iAP2 外部 MFi GPS 配件，经由 `com.apple.carkit.service` 注入 NMEA `GPGGA`/`GPRMC`）→ 与 CarPlay 车机/外置蓝牙 GPS 同级，**`locationd` 与 `healthd` 视为可信硬件定位**。
2. **NMEA 一致性筛查（`GPRMC NMEA Screening failed`）**
   - iOS 27.2+ 的 `locationd` 会把外置配件传入的 NMEA 坐标与手机当前基站 MCC（中国大陆 `460`）、周边 Wi-Fi 802.11d 国家码（`CN`）及缓存定位交叉比对；若差异过大，日志会出现 `GPRMC NMEA Screening failed` 并丢弃报文。
   - **解法**：开启 **飞行模式**（切断蜂窝 MCC 与 Wi-Fi 802.11d 干扰），并在 `send` 运行期间 **关闭「定位服务」10 秒再重新开启**，刷新 `locationd` 基线缓存。
3. **系统自定义开关（高频漏项）**
   - 必须开启 `设置 → 隐私与安全性 → 定位服务 → 系统服务 → 系统自定义`（`System Customization`）；同时将 `地图` 定位权限设为 **使用 App 期间 + 精确位置**（避免小组件跨设备同步真实定位）。
4. **手机 + 手表双端地区门控**
   - `healthd` 内部同时校验两个条件：
     - `CurrentCountryIsSupportedOnLocalDevice`（iPhone 侧）
     - `CurrentCountryIsSupportedOnActiveRemoteDevice`（已配对 Apple Watch 侧）
   - 若 Apple Watch 自己连着大陆 Wi-Fi 或缓存了 `CN`，即使 iPhone 地图已在香港，健康核对清单依然灰显。
   - **解法**：保持 iPhone 飞行模式（可单开蓝牙）+ `send` 运行，尝试**先将 Apple Watch 关机（或关闭手表 Wi-Fi）**，彻底划掉并重开健康 App；若进入设置页时系统提示需要连接手表，再开启手表（保持手表 Wi-Fi 关闭）仅通过蓝牙与 iPhone 同步。

---

## 二、用 `awh monitor` 实时看真相（告别盲猜）

怀疑坐标没被采用或 `healthd` 仍是 `CN` 时，在第二个终端运行（仅输出脱敏后的关键日志行，不含个人健康数据）：

```bash
./awh.sh monitor --duration 120
```

重点观察三类信号：
- `GPRMC NMEA Screening failed`：说明被一致性筛查拦截 → 确认飞行模式已开，并重新「关定位 10 秒 → 开定位」。
- `CurrentCountryIsSupportedOnLocalDevice` / `countryCode = HK`：确认健康服务的地区估计已从 `CN` 翻转为 `HK`。
- `Enabled = 1`：确认开通记录已写入本机健康配置。

---

## 三、常见问题速查表

| 阶段 | 现象 | 排查与解决 |
|---|---|---|
| **环境** | 国内安装依赖较慢 | 运行前设置镜像：`AWH_PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple ./awh.sh doctor` |
| **环境 (Win)** | `doctor` 提示未检测到 Apple USB 服务 (`127.0.0.1:27015`) | 用管理员终端安装微软商店版 Apple 设备：`winget install --id 9NP83LWLPZ9K`（或安装官方 iTunes），重启后重插 USB |
| **身份提取** | `awh setup` 报错 GitHub API 频控 (HTTP 403/429) | 可设置环境变量 `GITHUB_TOKEN=<任意只读PAT>` 提速至 5000 次/小时，或手动下载 DiPlay v0.2.7 APK 后执行 `./awh.sh setup --apk ~/Downloads/DiPlay-0.2.7.apk` |
| **USB 连接** | `doctor`/`devices` 找不到 iPhone | ① 更换支持数据传输的 **USB 数据线**（纯充电线不行）② 保持 iPhone **屏幕解锁** ③ 首次连接弹窗点 **信任** 并输锁屏密码 ④ 多台 iOS 设备同时接入时用 `--udid <UDID>` 指定目标机 |
| **MFi 握手** | `probe` 出现 `0xAA04` 认证失败 | 共享实验 MFi 证书可能已被 Apple 吊销或被新系统策略阻断；检查上游 `aur3l14no/accessory-location` / `DiPlay` 动态，或使用 `--cert`/`--key` 导入自有兼容证书 |
| **订阅** | `authentication_succeeded` 但一直没有 `location_requested` | 打开 iPhone **Apple 地图**并点击右上角定位箭头触发位置订阅 |
| **地图不移动** | 已出现 `location_link_acknowledged` 但蓝点仍在原地 | 保持 `send` 运行 → 开启飞行模式 → 进 `设置 → 隐私与安全性 → 定位服务` **关闭等 10 秒再打开** → 回地图重点定位；同时核对 **系统自定义** 已打开 |
| **健康仍不可用** | 地图蓝点已在香港，但健康核对清单仍灰显 | ① 确认已开飞行模式 ② 彻底划掉健康 App 重开 ③ **把 Apple Watch 关机（或关闭手表 Wi-Fi）**后重开健康 App ④ 换 `--preset us-sf` 重试 ⑤ 用 `./awh.sh monitor` 检查 `countryCode` |
| **收到 `location_stopped`** | 从地图切到健康 App 后终端打印 `location_stopped` | 正常现象（地图退后台暂停了定位订阅），不必立刻重启 `send`，先看健康 App 页面是否已刷新为可设置 |
| **恢复真实位置** | 停止后地图仍停留在香港 | 执行 `./awh.sh stop` → 关闭飞行模式 → 拔掉 USB 线 → 开关一次「定位服务」（间隔 10 秒）→ 回地图刷新；最后再进健康 App 确认「通知已打开」仍保留 |

---

## 四、事件流含义速查

```text
device_connected           已通过 USB 连接到已信任 iPhone
link_ready                 iAP2 链路层握手完成（控制会话 1）
identification_accepted    配件身份描述 (0x1D01) 被手机接受 (0x1D02)
authentication_succeeded   MFi-v3 挑战签名通过 (0xAA05) —— 核心节点
location_requested         手机订阅 NMEA 坐标 (0xFFFA)
location_link_acknowledged NMEA 报文获链路层 ACK（仅代表送达，不代表被采用）
location_stopped           手机暂停当前订阅 (0xFFFC)
finished                   会话结束并释放锁与资源
```

---

## 五、DVT `locate` 全局改定位排障（2.3.0）

| 现象 | 原因 | 解决 |
|---|---|---|
| 定位改过去几秒后又跳回真实位置 | iOS 17+ 模拟定位只在 DVT 会话存活期间有效；2.2.0 注入后立即断开会话 | 升级 2.3.0：`locate` 默认前台保持，或用 `--detach` 后台保持；不要关闭前台窗口 |
| Windows 上 `locate` 卡住 / 报 SSL、`NotImplementedError`、超时 | Windows 默认 Proactor 事件循环与 DTX strip-SSL、用户态隧道不兼容 | 2.3.0 已强制 Selector 事件循环；仍失败请加 `--debug` 收集堆栈 |
| 提示「未开启开发者模式」 / Windows 设置里找不到开关 | 没连过 Xcode 的 iPhone 默认隐藏该开关 | `awh devmode --reveal` → 设置 → 隐私与安全性 → 开发者模式 → 打开 → 重启 → 解锁后点【打开】 |
| `UserspaceTunnelUnavailableError` | iOS 17.0–17.3 没有 CoreDeviceProxy，需要同一 Wi-Fi 走 RemotePairing | 升级到 iOS 17.4+，或让电脑和手机连同一 Wi-Fi |
| 后台 `--detach` 失败 | 后台进程启动后无法连接手机 | 命令会打印后台日志末尾；完整日志在数据缓存目录的 `dvt.log` |
| `restore` 后地图仍停在模拟位置 | 未回执或会话已断 | 再执行 `awh restore --force-dvt`；兜底重启 iPhone 一定恢复 |
| `awh.cmd` 中文乱码 / 参数里的 `!` 消失 | 批处理为 LF 换行且开启了延迟变量扩展 | 2.3.0 已改为 CRLF 并移除 `enabledelayedexpansion`（`.gitattributes` 保证 git 检出不再变回 LF） |
| 中国大陆定位偏差 300–600 米 | 高德/腾讯/微信用 GCJ-02，百度用 BD-09，而 iPhone 底层只认 WGS-84 | 从高德/腾讯/微信复制的坐标加 `--coord-type gcj02`，百度加 `--coord-type bd09` 自动精准纠偏 |
| Windows 首次 `locate` 报 `InvalidServiceError` | 手机未挂载 Developer Disk Image (DDI) 且旧 RSD 缓存无 `dtservicehub` | 2.3.0 会自动联网挂载 DDI 后重建隧道刷新服务表；确保已开开发者模式、屏幕解锁且电脑能访问 GitHub/Apple |
| Windows `awh stop` 后仍有残留 `python` 子进程 | Windows 上 `os.kill(SIGTERM)` 直接强杀跳过清理 | 2.3.0 改为停止文件优雅关闭 + 超时 `taskkill /T /F` 整树清理，彻底避免 MFi 助手残留 |
