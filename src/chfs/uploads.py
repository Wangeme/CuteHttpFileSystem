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
        # 客户端偏移必须等于服务端已写入长度，否则顺序或重试状态已经不一致。
        if offset != session.offset:
            raise ResourceConflictError(f"上传偏移不匹配，当前偏移为 {session.offset}")
        # SHA-256 的十六进制文本固定为 64 个字符；None 表示快速模式不校验分块。
        if declared_sha256 is not None and len(declared_sha256) != 64:
            raise IntegrityMismatchError("分块 SHA-256 格式无效")

        # 修改会话共享状态时加锁，避免监控线程读到一半更新的数据。
        with self._lock:
            # 标记当前会话正在接收请求体。
            session.receiving = True
            # 刷新最后活动时间，供过期回收逻辑判断。
            session.updated_at = time.time()
        # try/finally 保证成功或失败后都会清除 receiving 标记。
        try:
            # 单次请求最多只保留 MAX_CHUNK_SIZE，文件总大小不影响进程内存占用。
            # 为当前 HTTP 请求创建一块可增长的连续内存。
            buffer = bytearray()
            # request.stream() 会异步产出网络层陆续收到的小块 bytes。
            async for chunk in chunks:
                # 把所有网络小块复制并拼接到同一个 bytearray 中。
                buffer.extend(chunk)
                # 每次扩展后检查上限，防止客户端用超大请求耗尽内存。
                if len(buffer) > MAX_CHUNK_SIZE:
                    raise UploadTooLargeError("单个上传分块超过 32 MiB")
            # 非空文件不接受空 PATCH，避免 offset 没有推进却返回成功。
            if not buffer and session.expected_size != 0:
                raise ResourceConflictError("上传分块不能为空")
            # 当前偏移加本块长度不能超过创建会话时声明的文件总大小。
            if session.offset + len(buffer) > session.expected_size:
                raise UploadTooLargeError("收到的数据超过声明的文件大小")

            # 默认没有分块摘要；快速模式会保持 None。
            actual_digest = None
            # 只有客户端提供摘要时才额外计算当前分块的 SHA-256。
            if declared_sha256 is not None:
                # 对已经完整缓存的分块做一次同步哈希计算，结果是 32 字节摘要。
                actual_digest = hashlib.sha256(buffer).digest()
                # 常量时间比较可避免普通字符串比较带来的时序侧信道。
                if not secrets.compare_digest(actual_digest.hex(), declared_sha256.casefold()):
                    raise IntegrityMismatchError("分块完整性校验失败，请重传该分块")

            # append 由偏移检查保证顺序；写入成功后才推进会话状态。
            # 写文件和更新会话必须作为一个受锁保护的逻辑整体执行。
            with self._lock:
                # 获取锁后再次检查偏移，防止两个并发 PATCH 都通过第一次检查。
                if offset != session.offset:
                    raise ResourceConflictError(f"上传偏移不匹配，当前偏移为 {session.offset}")
                # 以无缓冲追加模式打开临时文件；每个请求都会进行一次打开和关闭。
                with session.temporary.open("ab", buffering=0) as stream:
                    # 同步把整个 bytearray 写入操作系统文件接口；这里会阻塞当前事件循环线程。
                    stream.write(buffer)
                # 用同一份内存数据增量计算完整文件的 SHA-256，同样是同步 CPU 工作。
                session.full_hasher.update(buffer)
                # 严格校验模式下，把每个分块的二进制摘要加入“摘要清单”的哈希。
                if actual_digest is not None:
                    session.manifest_hasher.update(actual_digest)
                # 只有写入和哈希均成功后，才推进服务端确认偏移。
                session.offset += len(buffer)
                # 记录这次成功写入后的时间。
                session.updated_at = time.time()
            # 返回更新后的会话，由 HTTP 层把新 offset 发回浏览器。
            return session
        # 无论读取、校验或写盘在哪一步失败，都执行 finally。
        finally:
            # 清除共享状态仍然需要持锁。
            with self._lock:
                # 表示当前 HTTP 分块请求已经结束。
                session.receiving = False

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
