# CHFS 本轮界面设计验收

## 验收证据

- 源视觉真值：
  - `C:/Users/Administrator/AppData/Local/Temp/codex-clipboard-265e51c6-6dd1-4d75-8d66-1ae78cab77d7.png`
  - `C:/Users/Administrator/AppData/Local/Temp/codex-clipboard-aaa6656e-19df-4749-8c44-8e9aa336434e.png`
- 实现截图：
  - `artifacts/gui-overview-qr-final.png`
  - `artifacts/gui-overview-running.png`
  - `artifacts/gui-transfers-active.png`
- 同屏对照：`artifacts/change-comparison.png`
- 桌面视口：1120 × 720 客户区。
- 状态：已关闭、运行中，以及由真实 HTTP 请求创建的 4 GiB 等待续传会话。

## Findings

- 桌面端没有剩余 P0/P1/P2 可见问题。地址区拥有明确滚动条，长 IPv6 地址换行，局域网
  IPv4 优先，并且二维码未被裁切。传输会话表的方向、文件、来源、进度、速度和状态均
  在 1120 × 720 内完整可读。
- 状态区已消除互相矛盾的组合：已关闭对应绿色“启动服务”，运行中对应红色“停止服务”；
  正在启动与正在停止阶段使用同色禁用按钮，避免重复操作。
- [P2 验证缺口] 手机端 390 × 844 动态截图未取得。应用内浏览器第一次连接本机服务失败后，
  后续导航被 URL 安全策略阻止；遵守策略未改用其他浏览器自动化绕过。HTML、CSS 和 JS
  语法及组件契约已经过静态检查，但这不能代替真机视觉验收。

## Required fidelity surfaces

- 字体与排版：沿用现有 Microsoft YaHei UI / Cascadia Mono 层级；状态、表头和地址无异常换行。
- 间距与布局：地址列表与二维码保持双栏；传输表格使用现有 24/18/12 px 节奏，无持久控件溢出。
- 颜色与令牌：继续使用既有深色令牌；启动为青绿色，停止为危险红色，语义一致。
- 图像质量：二维码由 `qrcode` 标准矩阵生成并以整数倍像素绘制，边缘清晰，不是装饰性占位图。
- 文案与内容：使用“运行中 / 已关闭 / 等待续传 / 下载中”等直接状态词，来源地址和进度为真实数据。

## Comparison history

1. 初始 P2：IPv6 最后一行被容器裁切、没有二维码。修复为可滚动地址列表、局域网地址优先和
   固定二维码面板；证据为 `artifacts/change-comparison.png` 上半部分。
2. 初始 P1：停止过程中出现“正在停止”与绿色“启动服务”交叉。修复为线程安全四态生命周期，
   停止等待移出 GUI 主线程；证据为 `artifacts/change-comparison.png` 下半部分。
3. 初始 P2：二维码说明文字在卡片底部裁切。移除非必要的重复说明，最终二维码完整可见；证据为
   `artifacts/gui-overview-qr-final.png`。

## Implementation checklist

- [x] 桌面停止/运行状态视觉与逻辑一致。
- [x] 地址 IPv4/IPv6 滚动与二维码预览。
- [x] 真实传输会话数据状态。
- [x] 手机多选、当前/总进度与实时速度代码及静态检查。
- [ ] 在真实手机或允许访问本机服务的 390 × 844 浏览器完成动态视觉复验。

final result: blocked
