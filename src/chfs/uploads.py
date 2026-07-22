"""高吞吐、可续传的分块上传管理器。"""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import time
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from .errors import (
    IntegrityMismatchError,
    ResourceConflictError,
    ResourceNotFoundError,
    UploadTooLargeError,
)
from .models import FileEntry, Permission, Principal
from .paths import FullDiskPathResolver, SafePathResolver
from .security import require

DEFAULT_CHUNK_SIZE = 16 * 1024 * 1024
MAX_CHUNK_SIZE = 32 * 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60


@dataclass(slots=True)
class UploadSession:
    """一个尚未原子提交的上传事务。"""

    upload_id: str
    resume_key: str
    owner: str
    public_path: str
    target: Path
    temporary: Path
    expected_size: int
    overwrite: bool
    source: str = "unknown"
    offset: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    receiving: bool = False
    full_hasher: Any = field(default_factory=hashlib.sha256, repr=False)
    manifest_hasher: Any = field(default_factory=hashlib.sha256, repr=False)


class ResumableUploadManager:
    """以固定内存占用接收、校验并原子提交大文件。

    默认快速模式按精确偏移顺序写入并流式计算整文件 SHA-256；严格 API 客户端
    仍可为分块提供 SHA-256。全部字节到齐并刷新磁盘后使用 ``os.replace`` 原子
    发布，因此共享目录中不会出现可见的半文件。
    """

    def __init__(self, resolver: SafePathResolver | FullDiskPathResolver, max_upload_bytes: int) -> None:
        self.resolver = resolver
        self.max_upload_bytes = max_upload_bytes
        self._sessions: dict[str, UploadSession] = {}
        self._resume_index: dict[str, str] = {}
        self._lock = RLock()

    def create(
        self,
        principal: Principal,
        user_path: str,
        expected_size: int,
        resume_key: str,
        *,
        overwrite: bool = False,
        source: str = "unknown",
    ) -> UploadSession:
        require(principal, Permission.WRITE)
        if expected_size < 0:
            raise ResourceConflictError("文件大小不能为负数")
        if expected_size > self.max_upload_bytes:
            raise UploadTooLargeError("上传文件超过配置上限")
        if not resume_key or len(resume_key) > 160:
            raise ResourceConflictError("续传标识格式无效")
        target = self.resolver.resolve(user_path)
        if not target.parent.exists() or not target.parent.is_dir():
            raise ResourceNotFoundError("父目录不存在")

        index_key = self._index_key(principal.name, resume_key)
        with self._lock:
            self._purge_expired()
            existing_id = self._resume_index.get(index_key)
            existing = self._sessions.get(existing_id or "")
            if existing is not None:
                if existing.target != target or existing.expected_size != expected_size:
                    raise ResourceConflictError("续传标识已用于其他文件")
                existing.updated_at = time.time()
                return existing
            if target.exists() and not overwrite:
                raise ResourceConflictError("目标文件已存在")
            if target.exists() and target.is_dir():
                raise ResourceConflictError("目标是目录")
            descriptor, temporary_name = tempfile.mkstemp(prefix=".chfs-resume-", dir=target.parent)
            os.close(descriptor)
            session = UploadSession(
                upload_id=secrets.token_urlsafe(32),
                resume_key=resume_key,
                owner=principal.name,
                public_path=self.resolver.relative(target),
                target=target,
                temporary=Path(temporary_name),
                expected_size=expected_size,
                overwrite=overwrite,
                source=source,
            )
            self._sessions[session.upload_id] = session
            self._resume_index[index_key] = session.upload_id
            return session

    async def append(
        self,
        principal: Principal,
        upload_id: str,
        offset: int,
        declared_sha256: str | None,
        chunks: AsyncIterable[bytes],
    ) -> UploadSession:
        require(principal, Permission.WRITE)
        session = self._get(principal, upload_id)
        if offset != session.offset:
            raise ResourceConflictError(f"上传偏移不匹配，当前偏移为 {session.offset}")
        if declared_sha256 is not None and len(declared_sha256) != 64:
            raise IntegrityMismatchError("分块 SHA-256 格式无效")

        with self._lock:
            session.receiving = True
            session.updated_at = time.time()
        try:
            # 单次请求最多只保留 MAX_CHUNK_SIZE，文件总大小不影响进程内存占用。
            buffer = bytearray()
            async for chunk in chunks:
                buffer.extend(chunk)
                if len(buffer) > MAX_CHUNK_SIZE:
                    raise UploadTooLargeError("单个上传分块超过 32 MiB")
            if not buffer and session.expected_size != 0:
                raise ResourceConflictError("上传分块不能为空")
            if session.offset + len(buffer) > session.expected_size:
                raise UploadTooLargeError("收到的数据超过声明的文件大小")

            actual_digest = None
            if declared_sha256 is not None:
                actual_digest = hashlib.sha256(buffer).digest()
                if not secrets.compare_digest(actual_digest.hex(), declared_sha256.casefold()):
                    raise IntegrityMismatchError("分块完整性校验失败，请重传该分块")

            # append 由偏移检查保证顺序；写入成功后才推进会话状态。
            with self._lock:
                if offset != session.offset:
                    raise ResourceConflictError(f"上传偏移不匹配，当前偏移为 {session.offset}")
                with session.temporary.open("ab", buffering=0) as stream:
                    stream.write(buffer)
                session.full_hasher.update(buffer)
                if actual_digest is not None:
                    session.manifest_hasher.update(actual_digest)
                session.offset += len(buffer)
                session.updated_at = time.time()
            return session
        finally:
            with self._lock:
                session.receiving = False

    def complete(
        self,
        principal: Principal,
        upload_id: str,
        declared_manifest_sha256: str | None,
    ) -> tuple[FileEntry, str, str]:
        require(principal, Permission.WRITE)
        session = self._get(principal, upload_id)
        if session.offset != session.expected_size:
            raise ResourceConflictError(
                f"文件尚未上传完成：{session.offset}/{session.expected_size} 字节"
            )
        manifest = session.manifest_hasher.hexdigest()
        if declared_manifest_sha256 is not None and not secrets.compare_digest(
            manifest, declared_manifest_sha256.casefold()
        ):
            self.cancel(principal, upload_id)
            raise IntegrityMismatchError("文件分块清单校验失败，临时数据已清理")

        try:
            # Windows 不允许对只读句柄执行 fsync，因此这里显式使用读写句柄。
            with session.temporary.open("r+b", buffering=0) as stream:
                os.fsync(stream.fileno())
            if session.target.exists() and not session.overwrite:
                raise ResourceConflictError("目标文件已存在")
            os.replace(session.temporary, session.target)
            stat = session.target.stat()
        except BaseException:
            # 冲突时保留临时文件和会话，用户仍可选择覆盖后重试；其他失败由取消接口清理。
            raise
        self._remove_session(session)
        entry = FileEntry(
            session.target.name,
            self.resolver.relative(session.target),
            False,
            stat.st_size,
            stat.st_mtime_ns,
        )
        return entry, session.full_hasher.hexdigest(), manifest

    def cancel(self, principal: Principal, upload_id: str) -> None:
        require(principal, Permission.WRITE)
        session = self._get(principal, upload_id)
        session.temporary.unlink(missing_ok=True)
        self._remove_session(session)

    def status_dict(self, session: UploadSession) -> dict[str, object]:
        return {
            "upload_id": session.upload_id,
            "path": session.public_path,
            "size": session.expected_size,
            "offset": session.offset,
            "chunk_size": DEFAULT_CHUNK_SIZE,
            "prefix_manifest_sha256": session.manifest_hasher.copy().hexdigest(),
        }

    def snapshots(self) -> list[dict[str, object]]:
        """返回所有尚未提交的上传会话，供服务端控制台展示。"""

        with self._lock:
            self._purge_expired()
            sessions = list(self._sessions.values())
        result: list[dict[str, object]] = []
        for session in sessions:
            elapsed = max(session.updated_at - session.started_at, 0.001)
            result.append(
                {
                    "id": session.upload_id,
                    "direction": "upload",
                    "path": session.public_path,
                    "owner": session.owner,
                    "source": session.source,
                    "transferred_bytes": session.offset,
                    "total_bytes": session.expected_size,
                    "bytes_per_second": session.offset / elapsed,
                    "status": "uploading" if session.receiving else "waiting",
                    "updated_at": session.updated_at,
                }
            )
        return sorted(result, key=lambda item: float(item["updated_at"]), reverse=True)

    def _get(self, principal: Principal, upload_id: str) -> UploadSession:
        with self._lock:
            session = self._sessions.get(upload_id)
        if session is None:
            raise ResourceNotFoundError("上传会话不存在或已过期")
        if session.owner != principal.name:
            # 不泄露一个随机会话是否属于其他主体。
            raise ResourceNotFoundError("上传会话不存在或已过期")
        return session

    def _remove_session(self, session: UploadSession) -> None:
        with self._lock:
            self._sessions.pop(session.upload_id, None)
            self._resume_index.pop(self._index_key(session.owner, session.resume_key), None)

    def _purge_expired(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        expired = [item for item in self._sessions.values() if item.updated_at < cutoff]
        for session in expired:
            session.temporary.unlink(missing_ok=True)
            self._remove_session(session)

    @staticmethod
    def _index_key(owner: str, resume_key: str) -> str:
        return f"{owner}\0{resume_key}"
