"""文件管理应用服务。"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterable, Iterable
from pathlib import Path

from .errors import (
    ResourceConflictError,
    ResourceNotFoundError,
    UploadTooLargeError,
)
from .models import FileEntry, Permission, Principal
from .paths import SafePathResolver
from .security import require


class FileService:
    """封装所有文件用例和授权规则，不依赖 HTTP。"""

    def __init__(self, resolver: SafePathResolver, max_upload_bytes: int) -> None:
        self.resolver = resolver
        self.max_upload_bytes = max_upload_bytes

    def list_directory(self, principal: Principal, user_path: str = "") -> list[FileEntry]:
        require(principal, Permission.READ)
        target = self.resolver.resolve(user_path)
        if not target.exists():
            raise ResourceNotFoundError("目录不存在")
        if not target.is_dir():
            raise ResourceConflictError("目标不是目录")
        entries: list[FileEntry] = []
        try:
            children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
            for child in children:
                # 越界链接不会出现在列表中，避免暴露根目录外元数据。
                try:
                    public_path = self.resolver.relative(child)
                    stat = child.stat()
                except (OSError, ValueError):
                    continue
                entries.append(
                    FileEntry(
                        name=child.name,
                        path=public_path,
                        is_directory=child.is_dir(),
                        size=0 if child.is_dir() else stat.st_size,
                        modified_ns=stat.st_mtime_ns,
                    )
                )
        except OSError as exc:
            raise ResourceConflictError("无法读取目录") from exc
        return entries

    def open_download(self, principal: Principal, user_path: str) -> Path:
        require(principal, Permission.READ)
        target = self.resolver.resolve(user_path)
        if not target.exists() or not target.is_file():
            raise ResourceNotFoundError("文件不存在")
        return target

    async def upload(
        self,
        principal: Principal,
        user_path: str,
        chunks: AsyncIterable[bytes],
        *,
        overwrite: bool = False,
    ) -> FileEntry:
        require(principal, Permission.WRITE)
        target = self.resolver.resolve(user_path)
        if target.exists() and not overwrite:
            raise ResourceConflictError("目标文件已存在")
        if target.exists() and target.is_dir():
            raise ResourceConflictError("目标是目录")
        if not target.parent.exists() or not target.parent.is_dir():
            raise ResourceNotFoundError("父目录不存在")

        descriptor, temp_name = tempfile.mkstemp(prefix=".chfs-upload-", dir=target.parent)
        temp_path = Path(temp_name)
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as stream:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise UploadTooLargeError("上传文件超过配置上限")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        stat = target.stat()
        return FileEntry(target.name, self.resolver.relative(target), False, stat.st_size, stat.st_mtime_ns)

    def create_directory(self, principal: Principal, user_path: str) -> FileEntry:
        require(principal, Permission.WRITE)
        target = self.resolver.resolve(user_path)
        if target.exists():
            raise ResourceConflictError("目标已存在")
        try:
            target.mkdir(parents=True)
        except OSError as exc:
            raise ResourceConflictError("无法创建目录") from exc
        stat = target.stat()
        return FileEntry(target.name, self.resolver.relative(target), True, 0, stat.st_mtime_ns)

    def delete(self, principal: Principal, user_path: str, *, recursive: bool = False) -> None:
        require(principal, Permission.DELETE)
        target = self.resolver.resolve(user_path)
        if target == self.resolver.root:
            raise ResourceConflictError("不能删除共享根目录")
        if not target.exists():
            raise ResourceNotFoundError("目标不存在")
        try:
            if target.is_dir():
                if recursive:
                    shutil.rmtree(target)
                else:
                    target.rmdir()
            else:
                target.unlink()
        except OSError as exc:
            raise ResourceConflictError("删除失败；目录可能非空或文件正在使用") from exc


async def bytes_chunks(parts: Iterable[bytes]) -> AsyncIterable[bytes]:
    """测试与非 HTTP 适配器可使用的异步字节流辅助函数。"""

    for part in parts:
        yield part

