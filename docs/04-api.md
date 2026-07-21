# HTTP API（v1）

所有 JSON 响应均为 UTF-8。认证使用 `Authorization: Bearer <token>`；未提供令牌时按访客权限处理。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 无 | 响应式浏览器文件管理器。 |
| GET | `/api/health` | 无 | 健康检查。 |
| GET | `/api/v1/session` | 无 | 返回当前访客或令牌主体与权限。 |
| POST | `/api/v1/session` | 无 | JSON：`username`、`password`。 |
| DELETE | `/api/v1/session` | 登录 | 注销当前令牌。 |
| GET | `/api/v1/files?path=` | read | 列出目录。 |
| GET | `/api/v1/content?path=` | read | 下载文件。 |
| PUT | `/api/v1/content?path=&overwrite=false` | write | 请求体为原始文件字节。 |
| POST | `/api/v1/uploads` | write | 创建或按恢复键恢复分块上传会话。 |
| PATCH | `/api/v1/uploads/{upload_id}?offset=` | write | 追加一个已校验 SHA-256 的分块。 |
| POST | `/api/v1/uploads/{upload_id}/complete` | write | 校验分块清单并原子提交。 |
| DELETE | `/api/v1/uploads/{upload_id}` | write | 取消上传并清理临时文件。 |
| POST | `/api/v1/directories` | write | JSON：`path`。 |
| DELETE | `/api/v1/files?path=&recursive=false` | delete | 删除文件或目录。 |

错误响应格式：

```json
{"error":{"code":"permission_denied","message":"当前身份没有执行此操作的权限"},"request_id":"..."}
```

登录还会签发路径限制为 `/api/v1/content` 的 HttpOnly Cookie，仅供浏览器原生 GET 下载使用；
上传、删除和新建目录始终要求 Bearer 令牌，Cookie 不能授权写操作。

## 分块上传协议

创建会话：

```json
{"path":"video.iso","size":8589934592,"overwrite":false,"resume_key":"浏览器生成的随机键"}
```

服务端返回 `upload_id`、已确认的 `offset`、建议 `chunk_size` 和
`prefix_manifest_sha256`。浏览器按建议大小切块，对每块计算 SHA-256，随后发送：

```http
PATCH /api/v1/uploads/{upload_id}?offset=8388608
X-Chunk-SHA256: <64位十六进制摘要>
Content-Type: application/octet-stream
```

完成请求的 `manifest_sha256` 是“按顺序连接每个分块的 32 字节原始摘要后，再做一次
SHA-256”的结果。完成响应同时返回标准整文件 `sha256`。摘要错误返回 HTTP 422，偏移
不一致返回 HTTP 409；两种情况都不会把错误数据发布为最终文件。

`GET /api/v1/content` 支持 `Range: bytes=start-end`，成功的部分响应为 HTTP 206。
