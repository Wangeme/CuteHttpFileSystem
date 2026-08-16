# CHFS

CHFS（Convenient HTTP File Server）是一款面向局域网临时共享场景的 HTTP 文件传输服务器。
项目采用“领域内核—HTTP 适配器—桌面 GUI”分层设计：先保证文件边界、权限、会话和审计可靠，
再在同一内核之上构建易用的图形界面。

> 当前版本包含内核、HTTP API、浏览器文件管理器和桌面服务端控制台。

## 下载 Windows 版

从 [GitHub Releases](https://github.com/Wangeme/CuteHttpFileSystem/releases/latest) 下载 `CHFS.exe` 和同一版本的 `SHA256SUMS.txt`。程序为便携式单文件，无需安装。当前发布文件尚未进行商业代码签名，Windows 可能显示信誉提示；请先核对 SHA-256，不要从第三方转载地址下载。详见 [Windows 下载与安全校验](docs/10-windows-download-security.md)。

## 已实现能力

- 安全浏览共享目录，阻止 `..`、绝对路径及符号链接越界。
- 原子上传、文件下载、新建目录、删除文件或目录。
- 访客与账户两级权限，支持读、写、删除、管理四类权限。
- PBKDF2 密码散列、随机不透明会话令牌、会话过期和主动退出。
- IPv4/IPv6 CIDR 允许/拒绝规则，拒绝规则优先。
- JSON Lines 审计日志，记录主体、来源地址、动作、结果和文件操作的完整公开路径。
- JSON 配置文件、命令行启动与配置检查。
- 响应式浏览器文件管理器，默认免登录传输，支持拖放上传、上一级导航、批量选择、复制、剪切、粘贴与删除。
- 可选账户受控模式；不配置账户时，其他局域网机器无需安装软件或登录。
- 原生桌面管理器，提供运行概览、共享、网络、账户、安全和日志页面。
- 可选 HTTPS，证书与私钥在启动前成对校验。
- 128 MiB Blob 分块断点续传，浏览器不再复制为 `ArrayBuffer`，网络闪断后可从服务端确认的偏移继续。
- 分块请求体边接收边写入临时文件并在线程池增量哈希，不聚合完整请求体；失败请求自动截回已确认偏移。
- 内置持久化共享文本，手机网页提供复制、粘贴、清空、刷新四个按钮，编辑后自动同步；电脑端直接集成在运行概览页。
- 共享目录始终保留；可为指定 MAC 开放附加“此电脑”空间及主目录、下载、文档快捷入口，也可显式开启高风险的全盘访客访问。
- 默认快速模式使用 TCP 校验、严格偏移与总长度、服务端整文件 SHA-256、`fsync` 和原子发布；API 仍支持可选分块 SHA-256 严格校验。
- 临时文件写入、`fsync` 和原子替换，未完整上传的文件不会出现在共享目录中。
- 标准 HTTP Range 下载，可由浏览器或下载器恢复中断的大文件下载。
- 文件夹与多选项目使用 ZIP64 流式打包下载，不生成同等大小的服务器临时文件。
- 手机端支持系统文件多选、当前文件/全部文件双进度和实时上传速度。
- 手机端进度事件持续累计字节，但界面最多约每 100 ms 重绘一次，降低浏览器主线程开销。
- 服务端“传输会话”页实时显示上传、等待续传、下载、来源设备、进度和速度。
- 访问地址支持滚动查看 IPv4/IPv6，悬停即可显示可扫码的真实二维码。
- 桌面启动按钮采用明确四态：正在启动、运行中、正在停止、已关闭。

## 快速开始

```powershell
Copy-Item config.example.json config.json
python -m chfs.cli check-config --config config.json
python -m chfs.cli serve --config config.json
```

桌面版默认监听 `0.0.0.0:8080`，共享目录为当前用户“下载”目录下的 `CHFShare`，
配置保存到 `%LOCALAPPDATA%\CHFS\config.json`。若未安装为包，可在 PowerShell 中先设置：

```powershell
$env:PYTHONPATH = "src"
```

桌面管理器中的共享、权限、网络、账户与 TLS 设置会在编辑停止约 0.7 秒后自动校验并原子写入配置文件。服务运行时会自动应用最新配置，无需先停止服务或点击保存。

启动桌面管理器：

```powershell
$env:PYTHONPATH = "src"
python -m chfs.cli gui --config config.json
```

运行测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## 文档

- [需求规格](docs/01-requirements.md)
- [架构设计](docs/02-architecture.md)
- [安全威胁模型](docs/03-threat-model.md)
- [HTTP API](docs/04-api.md)
- [用户指南](docs/05-user-guide.md)
- [测试与性能报告](docs/06-test-report.md)
- [界面设计验收](docs/07-design-qa.md)
- [发布检查清单](docs/08-release-checklist.md)
- [Windows 下载与安全校验](docs/10-windows-download-security.md)
- [v0.3.7 发布说明](docs/releases/v0.3.7.md)
- [v0.3.6 发布说明](docs/releases/v0.3.6.md)
- [v0.3.4 发布说明](docs/releases/v0.3.4.md)
- [v0.3.3 发布说明](docs/releases/v0.3.3.md)
