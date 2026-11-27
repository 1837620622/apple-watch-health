# Changelog

## 2.3.0 — 2026-11-27

### 核心修复：macOS & Windows USB 全局改定位（参考爱思助手完整链路）
- **DVT 会话保持（根治秒弹回）**：iOS 17+ 的 `LocationSimulation` 仅在设置它的 DVT 会话存活期间有效。旧版 `locate` 注入后立即关闭隧道导致手机几秒内回弹真实 GPS。现默认**前台保持**直到 Ctrl+C / `awh restore`，`--hold N` 到期自动恢复；新增 `--detach` 后台独立保持进程，命令立即返回、可关闭终端，重复 `locate` 自动替换旧保持进程。
- **修复未挂载 DDI 首次改定位必败（关键真机 Bug）**：未连过 Xcode 的 iPhone（尤其是 Windows 设备）首次建立 `UserspaceRsdTunnel` 时，`RSD.peer_info` 尚未包含 `dtservicehub`。此前代码调用 `auto_mount(rsd)` 后继续复用旧 RSD 连接会再次触发 `InvalidServiceError`。现挂载 DDI 后自动关闭旧隧道并重建新隧道以刷新 `peer_info`，真正实现零 Xcode 一键改定位。
- **iOS 16 及以下切换爱思助手同款 `DtSimulateLocation`**：iOS <= 16 自动改走原生 `com.apple.dt.simulatelocation`（并按需自动挂载 DDI），替代易受 `strip_ssl` 影响的旧 DVT 通道。
- **新增中国大陆坐标系纠偏（`--coord-type wgs84|gcj02|bd09`）**：与爱思助手高德/百度选点纠偏原理一致，粘贴高德/腾讯/微信坐标时传 `--coord-type gcj02`、百度坐标传 `--coord-type bd09`，自动高精度反算为 iOS 底层原生 WGS-84 坐标（中国大陆误差 < 1 厘米，港澳台与海外零偏移），彻底解决国内地图 300–600 米偏移问题。
- **强制 USB 优先与优雅退出恢复**：`locate` 与 `devmode` 默认限定 USB 传输避免被同局域网 Wi-Fi 设备干扰；修复 `CancelledError`/Ctrl+C 下 `stopLocationSimulation` 未发出即关隧道的竞态；重连退避期即时响应停止文件；`awh restore` 核验后台进程确实已发清场指令，若旧进程系断线退出则自动补连手机发送 `stopLocationSimulation`。

### 核心修复：Windows 全链路与 Apple Watch iAP2 稳定性
- **Windows 事件循环根因修复**：`asyncio.run()` 在 Windows 默认使用 Proactor 循环，与 `pymobiledevice3` DTX/用户态隧道存在兼容性问题。新增 `aio.run()`，DVT / `devices` / `doctor` / `monitor` / `devmode` 在 Windows 统一使用 `SelectorEventLoop`；iAP2 会话继续使用默认循环以支持 MFi 认证子进程。
- **修复 Windows `awh stop` / `awh restore` 泄露 MFi 认证子进程**：Windows 上 `os.kill(pid, SIGTERM)` 会直接强杀进程跳过 `finally` 导致持有私钥的 `mfi` 子进程残留。现为 iAP2 发送器加入 PID 定向停止文件 `sender.stop` 与 `SIGBREAK` 优雅退出机制，超时则使用 `taskkill /T /F` 整树清理；POSIX 超时补发 `SIGKILL`。
- **新增 `awh devmode`**：查看开发者模式状态，`--reveal` 让 iPhone 设置显示「开发者模式」开关（Windows 未连过 Xcode 时默认隐藏），`--enable` 直接开启（无锁屏密码）并在重启重连后正确关闭新旧 lockdown 连接。
- **修复 `awh.cmd` 与 `awh.sh` 启动器**：`awh.cmd` 固定 CRLF 换行并移除吞掉 `!` 的延迟变量扩展，补充 `.cargo\bin\uv.exe` 与 PATH 探测；`awh.sh` 补齐 `--quiet` 避免同步依赖日志污染 JSONL stdout；新增 `.gitattributes` 锁定跨平台换行符。
- **Windows 进程存活、原子写入与日志文件锁修复**：`is_pid_alive` 改用 `GetExitCodeProcess == STILL_ACTIVE`；状态/锁文件统一经带重试的原子替换写入；后台日志文件防占用自动回退并拒绝符号链接；`active_dvt_holder` 清理过期状态文件。
- **USB 服务检测本地化兼容**：兼容 `Apple Mobile Device Service` / `AppleMobileDeviceService`，按数字状态码判断，不依赖英文输出。

### 安全漏洞修复与工程加固
- **修复 ZIP Deflate 炸弹防护失效**：`extract_entry_from_local_bytes` 将误用的 `zlib.decompress(..., bufsize)` 改为 `zlib.decompressobj(...).decompress(..., MAX_IDENTITY_BYTES + 1)` 强制内存上限。
- **修复无界读取与网络校验缺陷**：限制本地 `--apk` / 证书 / 私钥与在线地名解析、HTTP Range 响应读取上限防 OOM；校验 `Content-Range` 起止偏移与 HTTPS 重定向；`GITHUB_TOKEN` 仅发送给 `api.github.com`；`safe_destination` 递归校验所有父路径禁止符号链接；Windows 写入凭据严格校验 `icacls` 返回值。
- **修复参数注入与边界校验**：`--detach` 与 macOS 沙箱重拉统一采用 `--flag=value` 语法，消除负经纬度及 `-` 开头地名的参数解析失败；修复 `setup --full-apk` / `--key` 被静默跳过、`--altitude nan`、`monitor --duration <= 0` 与 `sanitize_log_message` 漏脱敏 40 位 UDID 及坐标等问题。
- **加固 GitHub Actions CI & Skill 打包**：遵循最小权限原则、使用 `uv sync --frozen`，打包 `apple-watch-health-skill.zip` 时强制校验完整性、零敏感文件、`awh.sh` 可执行权限与 LF/CRLF 换行符，并附带 `apple-watch-health-skill.zip.sha256`。
