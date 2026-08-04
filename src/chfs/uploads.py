"""高吞吐、可续传的分块上传管理器。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import tempfile
import time
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO

from .errors import (
    IntegrityMismatchError,
    ResourceConflictError,
    ResourceNotFoundError,
    UploadTooLargeError,
)
from .models import FileEntry, Permission, Principal
from .paths import FullDiskPathResolver, SafePathResolver
from .security import require

DEFAULT_CHUNK_SIZE = 128 * 1024 * 1024
MAX_CHUNK_SIZE = 128 * 1024 * 1024
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
    receiving_bytes: int = 0
    full_hasher: Any = field(default_factory=hashlib.sha256, repr=False)
    manifest_hasher: Any = field(default_factory=hashlib.sha256, repr=False)
    append_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def _write_and_hash(
    stream: BinaryIO,
    data: bytes,
    full_hasher: Any,
    chunk_hasher: Any | None,
) -> int:
    """在线程池中完整写入一段网络数据，并同步推进对应哈希状态。"""

    view = memoryview(data)
    written = 0
    while written < len(view):
        count = stream.write(view[written:])
        if not count:
            raise OSError("临时文件写入未取得进展")
        written += count
    full_hasher.update(data)
    if chunk_hasher is not None:
        chunk_hasher.update(data)
    return written


def _truncate_file(path: Path, offset: int) -> None:
    """把失败请求已经写入的尾部裁掉，恢复到服务端确认偏移。"""

    with path.open("r+b", buffering=0) as stream:
        stream.truncate(offset)


async def _write_and_hash_without_orphaning(
    stream: BinaryIO,
    data: bytes,
    full_hasher: Any,
    chunk_hasher: Any | None,
) -> int:
    """任务取消时也等待已进入线程池的写操作结束，避免写入与回滚竞态。"""

    write_task = asyncio.create_task(
        asyncio.to_thread(_write_and_hash, stream, data, full_hasher, chunk_hasher)
    )
    try:
        return await asyncio.shield(write_task)
    except asyncio.CancelledError:
        # to_thread 的底层线程不能被强制取消。必须先等它退出，外层才能安全
        # truncate；否则断线回滚可能与尚未结束的写操作同时修改临时文件。
        try:
            await write_task
        except BaseException:
            pass
        raise


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
        # 上传会在服务器上创建或修改文件，所以调用者必须拥有写权限。
        require(principal, Permission.WRITE)
        # 文件大小来自客户端声明，负数没有业务意义。
        if expected_size < 0:
            raise ResourceConflictError("文件大小不能为负数")
        # 在创建临时文件之前拒绝超过管理员配置上限的上传。
        if expected_size > self.max_upload_bytes:
            raise UploadTooLargeError("上传文件超过配置上限")
        # resume_key 用于找回断点；限制长度可以避免无界占用索引内存。
        if not resume_key or len(resume_key) > 160:
            raise ResourceConflictError("续传标识格式无效")
        # 把用户可见路径解析成经过安全约束的本地绝对路径。
        target = self.resolver.resolve(user_path)
        # 上传只负责创建文件，不会自动递归创建缺失的父目录。
        if not target.parent.exists() or not target.parent.is_dir():
            raise ResourceNotFoundError("父目录不存在")

        # 把用户身份纳入索引，避免不同用户的相同 resume_key 互相恢复。
        index_key = self._index_key(principal.name, resume_key)
        # 会话字典和续传索引是共享可变状态，访问它们时必须持有锁。
        with self._lock:
            # 创建新会话前顺便清理超过有效期的旧会话。
            self._purge_expired()
            # 先根据“用户 + 续传标识”查找已有上传 ID。
            existing_id = self._resume_index.get(index_key)
            # 再根据上传 ID 取得真正的会话；找不到时得到 None。
            existing = self._sessions.get(existing_id or "")
            # 找到会话意味着客户端可能正在恢复一次中断的上传。
            if existing is not None:
                # 同一续传标识只能对应同一路径、同一文件大小，防止串文件。
                if existing.target != target or existing.expected_size != expected_size:
                    raise ResourceConflictError("续传标识已用于其他文件")
                # 目标可能在上传期间被另一个请求创建。用户随后明确选择“覆盖”时，
                # 应升级原会话的策略，使已经传完的临时文件可以直接重新提交。
                if overwrite:
                    existing.overwrite = True
                # 刷新活跃时间，避免正在恢复的会话被过期清理。
                existing.updated_at = time.time()
                # 返回旧会话；其中 offset 告诉客户端应从哪里继续发送。
                return existing
            # 默认不覆盖目标文件，避免同名文件被静默破坏。
            if target.exists() and not overwrite:
                raise ResourceConflictError("目标文件已存在")
            # 即使允许覆盖，也不能把一个目录当作普通文件替换。
            if target.exists() and target.is_dir():
                raise ResourceConflictError("目标是目录")
            # 在目标目录创建零字节临时文件；同一文件系统内才能可靠地原子替换。
            descriptor, temporary_name = tempfile.mkstemp(prefix=".chfs-resume-", dir=target.parent)
            # mkstemp 返回一个已打开的底层描述符，这里关闭它，后面按分块重新打开。
            os.close(descriptor)
            # 构造只存在于服务端内存中的上传会话状态。
            session = UploadSession(
                # 生成不可预测的公开上传 ID，后续 PATCH 请求用它定位会话。
                upload_id=secrets.token_urlsafe(32),
                # 保存客户端续传标识，供索引和清理使用。
                resume_key=resume_key,
                # 记录会话所有者，防止其他账户接管上传。
                owner=principal.name,
                # 保存经过解析器规范化后的公开路径。
                public_path=self.resolver.relative(target),
                # 最终文件成功提交后的目标路径。
                target=target,
                # 上传期间真正写入数据的隐藏临时文件。
                temporary=Path(temporary_name),
                # 客户端在创建会话时声明的完整文件字节数。
                expected_size=expected_size,
                # 保存是否允许覆盖现有目标的策略。
                overwrite=overwrite,
                # 保存客户端来源地址，供状态展示或审计使用。
                source=source,
            )
            # 用上传 ID 注册会话，使后续分块请求能够找到它。
            self._sessions[session.upload_id] = session
            # 建立续传键到上传 ID 的反向索引。
            self._resume_index[index_key] = session.upload_id
            # 把新会话返回给 HTTP 层，最终序列化出 upload_id、offset 和 chunk_size。
            return session

    async def append(
        self,
        principal: Principal,
        upload_id: str,
        offset: int,
        declared_sha256: str | None,
        chunks: AsyncIterable[bytes],
    ) -> UploadSession:
        # 每一个分块请求都重新鉴权，不能只相信“创建会话”阶段的权限。
        require(principal, Permission.WRITE)
        # 按上传 ID 取回会话，同时校验会话是否存在且属于当前用户。
        session = self._get(principal, upload_id)
        # SHA-256 的十六进制文本固定为 64 个字符；None 表示快速模式不校验分块。
        if declared_sha256 is not None and len(declared_sha256) != 64:
            raise IntegrityMismatchError("分块 SHA-256 格式无效")

        # 每个会话只允许一个 PATCH 修改临时文件；等待锁的并发请求会在取得锁后
        # 重新检查 offset，因此重试和乱序请求不会交叉写入。
        async with session.append_lock:
            if offset != session.offset:
                raise ResourceConflictError(f"上传偏移不匹配，当前偏移为 {session.offset}")

            with self._lock:
                session.receiving = True
                session.updated_at = time.time()

            # hashlib 状态可复制。若请求中断、过大或摘要不匹配，临时文件和完整
            # 文件哈希都能恢复到本请求开始前，客户端可从原 offset 安全重试。
            full_hasher_before = session.full_hasher.copy()
            chunk_hasher = hashlib.sha256() if declared_sha256 is not None else None
            received = 0
            try:
                # 不把完整请求拼成 bytearray。request.stream() 每产出
                # 一段 bytes，就在线程池写盘并增量哈希，使内存规模由 ASGI 小块决定。
                with session.temporary.open("r+b", buffering=0) as stream:
                    stream.seek(offset)
                    async for chunk in chunks:
                        if not chunk:
                            continue
                        next_received = received + len(chunk)
                        if next_received > MAX_CHUNK_SIZE:
                            raise UploadTooLargeError("单个上传分块超过 128 MiB")
                        if offset + next_received > session.expected_size:
                            raise UploadTooLargeError("收到的数据超过声明的文件大小")
                        written = await _write_and_hash_without_orphaning(
                            stream,
                            chunk,
                            session.full_hasher,
                            chunk_hasher,
                        )
                        received += written
                        # offset 是协议确认点，只有完整 PATCH 成功后才能推进；另用
                        # receiving_bytes 暴露请求内的实时进度，避免 128 MiB 分块
                        # 完成前桌面速率长期为 0、完成瞬间又跳到极高值。
                        with self._lock:
                            session.receiving_bytes = received
                            session.updated_at = time.time()

                if received == 0 and session.expected_size != 0:
                    raise ResourceConflictError("上传分块不能为空")

                actual_digest = chunk_hasher.digest() if chunk_hasher is not None else None
                if actual_digest is not None and not secrets.compare_digest(
                    actual_digest.hex(), declared_sha256.casefold()
                ):
                    raise IntegrityMismatchError("分块完整性校验失败，请重传该分块")

                # 只有网络读取、写盘和可选分块校验全部成功后才公开新 offset。
                with self._lock:
                    if offset != session.offset:
                        raise ResourceConflictError(f"上传偏移不匹配，当前偏移为 {session.offset}")
                    if actual_digest is not None:
                        session.manifest_hasher.update(actual_digest)
                    session.offset += received
                    session.receiving_bytes = 0
                    session.updated_at = time.time()
                return session
            except BaseException:
                session.full_hasher = full_hasher_before
                # 写盘已经发生但 offset 尚未确认时，必须裁掉失败请求的尾部。
                # 即使错误发生在摘要校验阶段，也能恢复可续传的一致状态。
                _truncate_file(session.temporary, offset)
                raise
            finally:
                with self._lock:
                    session.receiving = False
                    session.receiving_bytes = 0

    def complete(
        self,
        principal: Principal,
        upload_id: str,
        declared_manifest_sha256: str | None,
    ) -> tuple[FileEntry, str, str]:
        # 最终提交同样属于写操作，必须重新检查权限。
        require(principal, Permission.WRITE)
        # 取回属于当前用户的上传会话。
        session = self._get(principal, upload_id)
        # 临时文件长度必须与声明大小完全一致，缺一个字节也不能发布。
        if session.offset != session.expected_size:
            raise ResourceConflictError(
                f"文件尚未上传完成：{session.offset}/{session.expected_size} 字节"
            )
        # 取得服务端累计的分块摘要清单哈希；快速模式下它是空输入的 SHA-256。
        manifest = session.manifest_hasher.hexdigest()
        # 严格模式下比较客户端与服务端的清单摘要，快速模式传 None 会跳过。
        if declared_manifest_sha256 is not None and not secrets.compare_digest(
            manifest, declared_manifest_sha256.casefold()
        ):
            # 清单不一致说明整次上传不可信，删除临时数据和会话。
            self.cancel(principal, upload_id)
            raise IntegrityMismatchError("文件分块清单校验失败，临时数据已清理")

        # 提交过程中出现异常时保留原异常类型和上下文。
        try:
            # Windows 不允许对只读句柄执行 fsync，因此这里显式使用读写句柄。
            # 用无缓冲读写模式重新打开已经完整写好的临时文件。
            with session.temporary.open("r+b", buffering=0) as stream:
                # 强制操作系统把该文件的脏页刷新到持久化设备；这是一次同步等待。
                os.fsync(stream.fileno())
            # 从创建会话到提交期间可能出现同名文件，所以发布前再次检查。
            if session.target.exists() and not session.overwrite:
                raise ResourceConflictError("目标文件已存在")
            # 在同一文件系统内用临时文件原子替换目标路径，外部不会看见半个文件。
            os.replace(session.temporary, session.target)
            # 读取最终文件元数据，用于构造 API 响应。
            stat = session.target.stat()
        # 捕获所有异常仅为了明确“原样抛出”；这里不做自动清理。
        except BaseException:
            # 冲突时保留临时文件和会话，用户仍可选择覆盖后重试；其他失败由取消接口清理。
            raise
        # 文件已经发布，删除内存会话和续传索引。
        self._remove_session(session)
        # 构造统一的文件条目对象，供目录列表和 HTTP 响应复用。
        entry = FileEntry(
            # 最终文件名。
            session.target.name,
            # 相对于共享根目录的公开路径。
            self.resolver.relative(session.target),
            # False 表示这是普通文件，不是目录。
            False,
            # 文件最终落盘后的字节数。
            stat.st_size,
            # 文件最后修改时间，单位为纳秒。
            stat.st_mtime_ns,
        )
        # 同时返回文件条目、完整文件 SHA-256 和分块清单 SHA-256。
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
                    "transferred_bytes": min(
                        session.expected_size,
                        session.offset + session.receiving_bytes,
                    ),
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
