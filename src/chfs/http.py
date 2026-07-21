"""Starlette HTTP 适配器。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .audit import AuditLogger
from .config import AppConfig
from .errors import AuthenticationError, CHFSError, InvalidPathError
from .models import Principal
from .paths import SafePathResolver
from .security import NetworkPolicy, SessionManager
from .services import FileService
from .transfers import TransferRegistry
from .uploads import ResumableUploadManager

LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).with_name("web")


@dataclass(slots=True)
class Runtime:
    config: AppConfig
    files: FileService
    sessions: SessionManager
    network: NetworkPolicy
    audit: AuditLogger
    uploads: ResumableUploadManager
    transfers: TransferRegistry


class TrackedFileResponse(FileResponse):
    """在不改变 FileResponse/Range 行为的前提下统计实际发送字节。"""

    def __init__(
        self,
        path: Path,
        *,
        registry: TransferRegistry,
        public_path: str,
        owner: str,
        source: str,
    ) -> None:
        super().__init__(path, filename=path.name)
        self.registry = registry
        self.public_path = public_path
        self.owner = owner
        self.source = source

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        transfer_id: str | None = None
        completed = False

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal transfer_id, completed
            if message["type"] == "http.response.start" and int(message.get("status", 0)) in {200, 206}:
                headers = {key.lower(): value for key, value in message.get("headers", [])}
                try:
                    total = int(headers.get(b"content-length", b"0"))
                except ValueError:
                    total = 0
                transfer_id = self.registry.start_download(
                    self.public_path,
                    self.owner,
                    self.source,
                    total,
                )
            elif message["type"] == "http.response.body" and transfer_id is not None:
                self.registry.advance(transfer_id, len(message.get("body", b"")))
                if not message.get("more_body", False):
                    completed = True
                    self.registry.finish(transfer_id)
            await send(message)

        try:
            await super().__call__(scope, receive, tracked_send)
        finally:
            if transfer_id is not None and not completed:
                self.registry.finish(transfer_id, failed=True)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """在协议边界统一处理请求标识、来源过滤和错误格式。"""

    def __init__(self, app: Any, runtime: Runtime) -> None:
        super().__init__(app)
        self.runtime = runtime

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
        request.state.request_id = request_id
        source = request.client.host if request.client else "unknown"
        if not self.runtime.network.permits(source):
            self.runtime.audit.record("network.reject", actor="anonymous", source=source, success=False)
            return _error("network_denied", "来源地址不在允许范围内", 403, request_id)
        try:
            response = await call_next(request)
        except CHFSError as exc:
            response = _domain_error(exc, request_id)
        except Exception:
            LOGGER.exception("未处理的请求异常 request_id=%s", request_id)
            response = _error("internal_error", "服务器内部错误", 500, request_id)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response


def create_app(config: AppConfig) -> Starlette:
    """装配应用依赖；测试和 GUI 都可据此创建独立服务实例。"""

    resolver = SafePathResolver(config.share_root)
    runtime = Runtime(
        config=config,
        files=FileService(resolver, config.max_upload_bytes),
        sessions=SessionManager(config.accounts, config.guest_permissions, config.session_ttl_seconds),
        network=NetworkPolicy(config.allow_networks, config.deny_networks),
        audit=AuditLogger(config.audit_log),
        uploads=ResumableUploadManager(resolver, config.max_upload_bytes),
        transfers=TransferRegistry(),
    )
    routes = [
        Route("/", web_index, methods=["GET"]),
        Route("/api/health", health, methods=["GET"]),
        Route("/api/v1/session", session_endpoint, methods=["GET", "POST"]),
        Route("/api/v1/session", delete_session, methods=["DELETE"]),
        Route("/api/v1/files", list_or_delete_files, methods=["GET", "DELETE"]),
        Route("/api/v1/content", content, methods=["GET", "PUT"]),
        Route("/api/v1/directories", create_directory, methods=["POST"]),
        Route("/api/v1/uploads", create_resumable_upload, methods=["POST"]),
        Route("/api/v1/uploads/{upload_id}", upload_chunk, methods=["PATCH", "DELETE"]),
        Route("/api/v1/uploads/{upload_id}/complete", complete_resumable_upload, methods=["POST"]),
        Mount("/assets", app=StaticFiles(directory=WEB_ROOT), name="assets"),
    ]
    app = Starlette(debug=False, routes=routes)
    app.state.runtime = runtime
    app.add_middleware(RequestContextMiddleware, runtime=runtime)
    return app


async def web_index(request: Request) -> FileResponse:
    """返回浏览器文件管理器入口；静态资源不具备任何磁盘访问能力。"""

    return FileResponse(WEB_ROOT / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.0"})


async def session_endpoint(request: Request) -> JSONResponse:
    """查询当前身份或创建登录会话。"""

    if request.method == "GET":
        principal = _principal(request)
        return JSONResponse(
            {
                "principal": _principal_dict(principal),
                "authentication_available": bool(_runtime(request).config.accounts),
            }
        )
    runtime = _runtime(request)
    source = _source(request)
    try:
        payload = await _json_object(request)
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise AuthenticationError("用户名或密码错误")
        session = runtime.sessions.login(username, password)
    except CHFSError:
        runtime.audit.record("session.login", actor=str(locals().get("username", "anonymous")), source=source, success=False)
        raise
    runtime.audit.record("session.login", actor=session.principal.name, source=source, success=True)
    response = JSONResponse(
        {
            "token": session.token,
            "expires_at": session.expires_at,
            "principal": _principal_dict(session.principal),
        }
    )
    # 该 HttpOnly Cookie 仅用于 GET 文件下载，路径被限制在 content 端点；
    # 所有写操作仍必须使用 Authorization Bearer，避免 Cookie 认证导致 CSRF。
    response.set_cookie(
        "chfs_download_session",
        session.token,
        max_age=runtime.config.session_ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/api/v1/content",
    )
    return response


async def delete_session(request: Request) -> Response:
    runtime = _runtime(request)
    token = _bearer_token(request)
    if not token:
        raise AuthenticationError("需要有效会话")
    principal = runtime.sessions.resolve(token)
    runtime.sessions.logout(token)
    runtime.audit.record("session.logout", actor=principal.name, source=_source(request), success=True)
    response = Response(status_code=204)
    response.delete_cookie("chfs_download_session", path="/api/v1/content")
    return response


async def list_or_delete_files(request: Request) -> Response:
    runtime = _runtime(request)
    principal = _principal(request)
    user_path = request.query_params.get("path", "")
    if request.method == "GET":
        entries = runtime.files.list_directory(principal, user_path)
        return JSONResponse(
            {"path": user_path.replace("\\", "/"), "entries": [item.as_dict() for item in entries]}
        )
    recursive = _boolean_query(request, "recursive", False)
    runtime.files.delete(principal, user_path, recursive=recursive)
    runtime.audit.record(
        "file.delete", actor=principal.name, source=_source(request), success=True, path=user_path, recursive=recursive
    )
    return Response(status_code=204)


async def content(request: Request) -> Response:
    runtime = _runtime(request)
    principal = _principal(request)
    user_path = request.query_params.get("path", "")
    if not user_path:
        raise InvalidPathError("必须提供文件路径")
    if request.method == "GET":
        # 浏览器原生下载不能附加 Authorization 请求头，故仅 GET 下载允许读取
        # 登录时签发的窄路径 HttpOnly Cookie；PUT 上传绝不接受 Cookie 身份。
        if not principal.authenticated:
            cookie_token = request.cookies.get("chfs_download_session")
            if cookie_token:
                principal = runtime.sessions.resolve(cookie_token)
        path = runtime.files.open_download(principal, user_path)
        runtime.audit.record("file.download", actor=principal.name, source=_source(request), success=True, path=user_path)
        return TrackedFileResponse(
            path,
            registry=runtime.transfers,
            public_path=user_path.replace("\\", "/"),
            owner=principal.name,
            source=_source(request),
        )
    overwrite = _boolean_query(request, "overwrite", False)
    length_text = request.headers.get("content-length")
    if length_text:
        try:
            if int(length_text) > runtime.config.max_upload_bytes:
                from .errors import UploadTooLargeError

                raise UploadTooLargeError("上传文件超过配置上限")
        except ValueError as exc:
            raise InvalidPathError("Content-Length 格式无效") from exc
    entry = await runtime.files.upload(principal, user_path, request.stream(), overwrite=overwrite)
    runtime.audit.record(
        "file.upload", actor=principal.name, source=_source(request), success=True, path=user_path, size=entry.size
    )
    return JSONResponse(entry.as_dict(), status_code=201)


async def create_directory(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    principal = _principal(request)
    payload = await _json_object(request)
    user_path = payload.get("path")
    if not isinstance(user_path, str) or not user_path:
        raise InvalidPathError("必须提供目录路径")
    entry = runtime.files.create_directory(principal, user_path)
    runtime.audit.record("directory.create", actor=principal.name, source=_source(request), success=True, path=user_path)
    return JSONResponse(entry.as_dict(), status_code=201)


async def create_resumable_upload(request: Request) -> JSONResponse:
    """创建新上传事务，或按续传标识返回现有偏移。"""

    runtime = _runtime(request)
    principal = _principal(request)
    payload = await _json_object(request)
    user_path = payload.get("path")
    expected_size = payload.get("size")
    resume_key = payload.get("resume_key")
    overwrite = payload.get("overwrite", False)
    if not isinstance(user_path, str) or not user_path:
        raise InvalidPathError("必须提供文件路径")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int):
        raise InvalidPathError("size 必须是非负整数")
    if not isinstance(resume_key, str):
        raise InvalidPathError("resume_key 必须是字符串")
    if not isinstance(overwrite, bool):
        raise InvalidPathError("overwrite 必须是布尔值")
    session = runtime.uploads.create(
        principal,
        user_path,
        expected_size,
        resume_key,
        overwrite=overwrite,
        source=_source(request),
    )
    runtime.audit.record(
        "upload.create",
        actor=principal.name,
        source=_source(request),
        success=True,
        path=user_path,
        size=expected_size,
        offset=session.offset,
    )
    return JSONResponse(runtime.uploads.status_dict(session), status_code=201)


async def upload_chunk(request: Request) -> Response:
    runtime = _runtime(request)
    principal = _principal(request)
    upload_id = request.path_params["upload_id"]
    if request.method == "DELETE":
        runtime.uploads.cancel(principal, upload_id)
        runtime.audit.record(
            "upload.cancel", actor=principal.name, source=_source(request), success=True, upload_id=upload_id
        )
        return Response(status_code=204)
    offset_text = request.query_params.get("offset")
    try:
        offset = int(offset_text or "")
    except ValueError as exc:
        raise InvalidPathError("offset 必须是非负整数") from exc
    if offset < 0:
        raise InvalidPathError("offset 必须是非负整数")
    declared_sha256 = request.headers.get("x-chfs-chunk-sha256", "")
    session = await runtime.uploads.append(
        principal,
        upload_id,
        offset,
        declared_sha256,
        request.stream(),
    )
    return JSONResponse(runtime.uploads.status_dict(session))


async def complete_resumable_upload(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    principal = _principal(request)
    upload_id = request.path_params["upload_id"]
    payload = await _json_object(request)
    manifest = payload.get("manifest_sha256")
    if not isinstance(manifest, str) or len(manifest) != 64:
        raise InvalidPathError("manifest_sha256 格式无效")
    entry, file_sha256, manifest_sha256 = runtime.uploads.complete(principal, upload_id, manifest)
    runtime.audit.record(
        "file.upload",
        actor=principal.name,
        source=_source(request),
        success=True,
        path=entry.path,
        size=entry.size,
        sha256=file_sha256,
        resumable=True,
    )
    result = entry.as_dict()
    result.update({"sha256": file_sha256, "manifest_sha256": manifest_sha256})
    return JSONResponse(result, status_code=201)


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise InvalidPathError("请求体必须是有效 JSON") from exc
    if not isinstance(value, dict):
        raise InvalidPathError("请求体必须是 JSON 对象")
    return value


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _source(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    if not value:
        return None
    scheme, separator, token = value.partition(" ")
    if separator and scheme.casefold() == "bearer" and token.strip():
        return token.strip()
    raise AuthenticationError("Authorization 请求头格式无效")


def _principal(request: Request) -> Principal:
    return _runtime(request).sessions.resolve(_bearer_token(request))


def _boolean_query(request: Request, name: str, default: bool) -> bool:
    value = request.query_params.get(name)
    if value is None:
        return default
    normalized = value.casefold()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise InvalidPathError(f"{name} 必须是 true 或 false")


def _principal_dict(principal: Principal) -> dict[str, object]:
    return {
        "name": principal.name,
        "authenticated": principal.authenticated,
        "permissions": sorted(item.value for item in principal.permissions),
    }


def _domain_error(exc: CHFSError, request_id: str) -> JSONResponse:
    status = {
        "invalid_path": 400,
        "authentication_failed": 401,
        "permission_denied": 403,
        "not_found": 404,
        "conflict": 409,
        "upload_too_large": 413,
        "integrity_mismatch": 422,
        "invalid_configuration": 500,
    }.get(exc.code, 500)
    return _error(exc.code, exc.message, status, request_id)


def _error(code: str, message: str, status: int, request_id: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}, "request_id": request_id}, status_code=status
    )
