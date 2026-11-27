# Changelog

## 2.4.0 — 2026-11-27

### 新增功能（对标爱思助手完整定位体验）
- **GPX 路线轨迹模拟（`--gpx`）**：`awh locate --gpx route.gpx [--speed 1.5] [--loop] [--coord-type gcj02] [--detach]`，采用防 XXE 的 `defusedxml` 解析标准 GPX 1.0/1.1（`<trkpt>`/`<rtept>`/`<wpt>`），支持倍速、循环、断线断点续播与优雅恢复真实 GPS。
- **自然 GPS 微幅漂移（`--jitter <米>`）**：支持每次刷新在指定半径内施加真实户外级别的微小随机漂移，避免静止死坐标。
- **新增 `awh status` 命令**：无需重复枚举 USB 即可秒查当前 DVT 定位保持目标、GPX 轨迹进度、iAP2 发送器状态、已运行时长与 MFi 身份状态。
- **智能识别高德 / 百度 `经度,纬度` 顺序 + 75+ 离线热门城市库**：直接粘贴高德/百度坐标拾取器输出的 `116.3974,39.9092` 时自动翻转识别为 `(纬度, 经度)`；扩充全国直辖市/省会/热门城市及全球主要地标至 75+，零网络毫秒级解析。

### 深度漏洞修复与底层稳定性加固
- **修复 `awh monitor` 1 秒静默即提前退出与连接泄露（真机关键 Bug）**：`stream_region_diagnostics` 原先用 `asyncio.wait_for(stream.__anext__(), 1.0)` 轮询超时，触发 `TimeoutError` 会取消并永久终结 Python 异步生成器且未关闭 `OsTraceService` socket；现改用 `asyncio.timeout(duration)` 包裹完整流并在 `finally` 规范关闭流、`OsTraceService` 与 `lockdown`。
- **修复 iOS 17–26 经 RSD 挂载 DDI 偶发失败**：`_open_rsd_dvt_channel` 在 iOS 17–26 经用户态隧道挂载 DDI 失败时，自动回退原生 USB `lockdown` 挂载 `PersonalizedDMG` 后重建隧道刷新 `peer_info`。
- **修复 `discover_usb_devices` 去重顺序 Bug**：`awh devices --all` 在 `Network` 条目先于 `USB` 出现时不再重复列举同一台设备。
- **修复 iAP2 协议层两处隐藏 Bug**：`Link.read()` 遇到非超时异常时立即清理失败的 `_reader_task`；`Link.handshake()` 拒绝 `size < 16`（修复 `size == 10` 时 `chunk_limit == 0` 触发 `ValueError`）；`wire.py` 补齐 16-bit 长度溢出校验并统一抛 `ProtocolError`。
- **增强 `probe` / `send` 中文排障提示**：USB/配对/驱动异常时在事件与 stderr 同步附带可操作中文提示。

---

## 2.3.0 — 2026-11-27

### 核心修复：macOS & Windows USB 全局改定位（参考爱思助手完整链路）
- **DVT 会话保持（根治秒弹回）**：iOS 17+ 的 `LocationSimulation` 仅在设置它的 DVT 会话存活期间有效。现默认**前台保持**直到 Ctrl+C / `awh restore`，`--hold N` 到期自动恢复；新增 `--detach` 后台独立保持进程，命令立即返回、可关闭终端，重复 `locate` 自动替换旧保持进程。
- **修复未挂载 DDI 首次改定位必败**：挂载 DDI 后自动关闭旧隧道并重建新 `UserspaceRsdTunnel` 刷新 `RSD.peer_info`。
- **iOS 16 及以下切换爱思助手同款 `DtSimulateLocation`**：iOS <= 16 自动改走原生 `com.apple.dt.simulatelocation`（并按需自动挂载 DDI）。
- **中国大陆坐标系纠偏（`--coord-type wgs84|gcj02|bd09`）**：高德/腾讯/微信（GCJ-02）与百度（BD-09）高精度反算为 iOS 原生 WGS-84。
- **强制 USB 优先与优雅退出恢复**：修复 `CancelledError`/Ctrl+C 下未发 `stopLocationSimulation` 即关隧道的竞态；重连退避即时响应停止文件；`awh restore` 核验后台进程确已清场，否则自动补连发送恢复指令。

### 核心修复：Windows 全链路与安全加固
- **Windows 事件循环与进程清理**：新增 `aio.run()` 强制 `SelectorEventLoop`；iAP2 发送器引入 `sender.stop` 优雅停止与超时 `taskkill /T /F` 整树清理防 MFi 子进程泄露；新增 `awh devmode`；修复 `awh.cmd` CRLF / `!` 与 `awh.sh --quiet`；`is_pid_alive` 改用 `GetExitCodeProcess == STILL_ACTIVE`；USB 服务检测兼容中文本地化输出。
- **安全漏洞修复与 CI 审计**：`extract_entry_from_local_bytes` 改用 `zlib.decompressobj` 强制内存上限防御 ZIP Deflate 炸弹；限制本地文件与 HTTP 响应读取上限；校验 `Content-Range` 偏移与 HTTPS 重定向；`GITHUB_TOKEN` 仅发 `api.github.com`；递归拒绝父目录符号链接；`--detach` 与沙箱重拉统一使用 `--flag=value` 语法；脱敏 40 位 UDID 与坐标；CI 打包自动审计 ZIP 完整性、权限位、换行符与零凭据并生成 SHA-256。
