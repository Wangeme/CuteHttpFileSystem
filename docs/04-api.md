# HTTP API（v1）

所有 JSON 响应均为 UTF-8。认证使用 `Authorization: Bearer <token>`；未提供令牌时按访客权限处理。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 无 | 响应式浏览器文件管理器。 |
| GET | `/api/health` | 无 | 健康检查。 |
| GET | `/api/v1/session` | 无 | 返回当前访客或令牌主体与权限。 |
| POST | `/api/v1/session` | 无 | JSON：`username`、`password`。 |
| DELETE | `/api/v1/session` | 登录 | 注销当前令牌。 |
| GET | `/api/v1/files?space=shared&path=` | read | 列出目录；受信任设备可使用 `space=computer`。 |
| GET | `/api/v1/content?space=shared&path=` | read | 下载文件。 |
| GET | `/api/v1/archive?space=shared&path=目录` | read | 将一个或多个重复 `path` 参数实时打包为 ZIP 下载。 |
| PUT | `/api/v1/content?space=shared&path=&overwrite=false` | write | 请求体为原始文件字节。 |
| POST | `/api/v1/uploads` | write | 创建或按恢复键恢复分块上传会话。 |
| PATCH | `/api/v1/uploads/{upload_id}?offset=` | write | 按精确偏移追加分块；可携带严格校验摘要。 |
| POST | `/api/v1/uploads/{upload_id}/complete` | write | 校验总长度、同步写盘并原子提交。 |
| DELETE | `/api/v1/uploads/{upload_id}` | write | 取消上传并清理临时文件。 |
| POST | `/api/v1/directories` | write | JSON：`path`。 |
| POST | `/api/v1/file-operations` | read+write | JSON：`space`、`operation`、`sources`、`destination`；移动还需 delete。 |
| DELETE | `/api/v1/files?path=&recursive=false` | delete | 删除文件或目录。 |

错误响应格式：

```json
{"error":{"code":"permission_denied","message":"当前身份没有执行此操作的权限"},"request_id":"..."}
```

登录还会分别签发路径限制为 `/api/v1/content` 和 `/api/v1/archive` 的 HttpOnly Cookie，仅供浏览器原生 GET 下载使用；
上传、删除和新建目录始终要求 Bearer 令牌，Cookie 不能授权写操作。

## 分块上传协议

创建会话：

```json
{"path":"video.iso","size":8589934592,"overwrite":false,"resume_key":"浏览器生成的随机键"}
```

服务端返回 `upload_id`、已确认的 `offset` 和建议 `chunk_size`。默认网页采用快速模式，
按精确偏移直接发送分块，不在浏览器逐块计算 SHA-256：

```http
PATCH /api/v1/uploads/{upload_id}?offset=134217728
Content-Type: application/octet-stream
```

严格客户端仍可发送 `X-CHFS-Chunk-SHA256`，并在完成时发送 `manifest_sha256`；服务端会
执行原有分块与清单校验。无论哪种模式，服务端都检查偏移和总长度、流式计算整文件
SHA-256、执行 `fsync` 并原子发布。偏移不一致返回 HTTP 409。

`GET /api/v1/content` 支持 `Range: bytes=start-end`，成功的部分响应为 HTTP 206。
`GET /api/v1/archive` 使用 ZIP_STORED 和 ZIP64 边生成边发送，不在服务器落地临时归档，也不会把完整 ZIP 装入内存。

## 共享文本

```http
GET /api/v1/shared-text
PUT /api/v1/shared-text
Content-Type: application/json

{"text":"手机和电脑之间同步的文本"}
```

读取需要 `read` 权限，更新需要 `write` 权限。服务端限制 UTF-8 文本不超过 1 MiB，
并返回 `revision`、`updated_at` 和 `max_bytes`；网页在本地没有未保存编辑时每 3 秒自动刷新。
