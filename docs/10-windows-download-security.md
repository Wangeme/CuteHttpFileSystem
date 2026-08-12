# Windows 下载与安全校验

## 官方附件

只从项目的 [GitHub Releases](https://github.com/Wangeme/CuteHttpFileSystem/releases/latest) 下载。每个正式版本应包含：

- `CHFS.exe`：Windows x64 便携式单文件程序；
- `SHA256SUMS.txt`：对应程序的 SHA-256 摘要。

在 PowerShell 中校验：

```powershell
Get-FileHash .\CHFS.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

两处 64 位十六进制摘要必须完全一致。摘要只能证明文件与发布者上传的附件一致，不能替代杀毒扫描或代码审计。

## Windows 为什么可能拦截

CHFS 使用 PyInstaller 打包为未签名的单文件网络服务程序。单文件程序启动时会解包运行，而且 CHFS 会监听端口并读写共享文件；这种组合可能触发 SmartScreen 信誉提示或 Defender 的机器学习启发式检测。提示本身不等于已经确认感染，也不能据此直接断言一定是误报。

历史 `v0.3.1` 附件曾被 Defender 识别为 `Trojan:Win32/Sabsik.TE.A!ml`。核查时，GitHub 附件的 SHA-256 与同一源码在本机重新构建的文件完全一致，Defender 同时记录 `DidThreatExecute=False`、`IsActive=False`。这些证据排除了附件被下载链路替换的情况，更符合启发式误报特征，但最终结论仍以 Microsoft 样本分析为准。

如果系统再次报警：

1. 不要关闭 Defender，也不要直接加入排除项；
2. 核对 Release 中的 SHA-256；
3. 将文件提交到 [Microsoft Security Intelligence 文件分析](https://www.microsoft.com/wdsi/filesubmission)；
4. 在分析结果出来前，可审查源码后于隔离环境自行构建。

项目后续如采用可信代码签名证书，应在 Release 说明中同时公布签名主体和校验方法。
