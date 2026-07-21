# CHFS

CHFS（Convenient HTTP File Server）是一款面向局域网临时共享场景的 HTTP 文件传输服务器。
项目采用“领域内核—HTTP 适配器—桌面 GUI”分层设计：先保证文件边界、权限、会话和审计可靠，
再在同一内核之上构建易用的图形界面。

> 当前版本包含内核、HTTP API、浏览器文件管理器和桌面服务端控制台。

## 已实现能力

- 安全浏览共享目录，阻止 `..`、绝对路径及符号链接越界。
- 原子上传、文件下载、新建目录、删除文件或目录。
- 访客与账户两级权限，支持读、写、删除、管理四类权限。
- PBKDF2 密码散列、随机不透明会话令牌、会话过期和主动退出。
- IPv4/IPv6 CIDR 允许/拒绝规则，拒绝规则优先。
- JSON Lines 审计日志，记录主体、来源地址、动作和结果。
- JSON 配置文件、命令行启动与配置检查。
- 响应式浏览器文件管理器，默认免登录传输，支持拖放上传和目录导航。
- 可选账户受控模式；不配置账户时，其他局域网机器无需安装软件或登录。
- 原生桌面管理器，提供运行概览、共享、网络、账户、安全和日志页面。
- 可选 HTTPS，证书与私钥在启动前成对校验。
- 8 MiB 分块断点续传，网络闪断后可从服务端确认的偏移继续。
- 每块 SHA-256、分块清单 SHA-256 与整文件 SHA-256 三层完整性校验。
- 临时文件写入、`fsync` 和原子替换，未完整上传的文件不会出现在共享目录中。
- 标准 HTTP Range 下载，可由浏览器或下载器恢复中断的大文件下载。

## 快速开始

```powershell
Copy-Item config.example.json config.json
python -m chfs.cli check-config --config config.json
python -m chfs.cli serve --config config.json
```

默认地址为 `http://127.0.0.1:8080`。若未安装为包，可在 PowerShell 中先设置：

```powershell
$env:PYTHONPATH = "src"
```

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
