"""文件管理应用服务。"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterable, Iterable
from pathlib import Path, PurePosixPath

from .archives import ArchiveSource
from .errors import (
    ResourceConflictError,
    ResourceNotFoundError,
    UploadTooLargeError,
)
from .models import FileEntry, Permission, Principal
from .paths import FullDiskPathResolver, SafePathResolver
from .security import require


class FileService:
    """封装所有文件用例和授权规则，不依赖 HTTP。"""

    def __init__(self, resolver: SafePathResolver | FullDiskPathResolver, max_upload_bytes: int) -> None:
        self.resolver = resolver
        self.max_upload_bytes = max_upload_bytes

    def list_directory(self, principal: Principal, user_path: str = "") -> list[FileEntry]:
        require(principal, Permission.READ)
        root_entries = self.resolver.root_entries()
        if not user_path and root_entries is not None:
            return root_entries
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

    def open_archive(self, principal: Principal, user_paths: list[str]) -> list[ArchiveSource]:
        """校验一批待打包项目，并返回不暴露绝对路径的归档入口。"""

        require(principal, Permission.READ)
        if not user_paths:
            raise ResourceConflictError("没有选择要下载的文件或文件夹")
        if len(user_paths) > 100:
            raise ResourceConflictError("一次最多下载 100 个项目")

        sources: list[ArchiveSource] = []
        seen: set[str] = set()
        for user_path in user_paths:
            if not isinstance(user_path, str) or not user_path:
                raise ResourceConflictError("下载路径不能为空")
            target = self.resolver.resolve(user_path)
            if not target.exists():
                raise ResourceNotFoundError(f"目标不存在：{user_path}")
            if not target.is_file() and not target.is_dir():
                raise ResourceConflictError(f"不支持下载特殊文件：{user_path}")
            public_path = self.resolver.relative(target)
            key = os.path.normcase(str(target))
            if key in seen:
                continue
            seen.add(key)
            name = PurePosixPath(public_path).name
            if not name:
                raise ResourceConflictError("不能打包未命名的根目录")
            sources.append(ArchiveSource(target, name, public_path))
        return sources

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
        if self.resolver.is_root(target):
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

    def copy_or_move(
        self,
        principal: Principal,
        source_paths: list[str],
        destination_path: str,
        *,
        move: bool = False,
    ) -> list[FileEntry]:
        """把多个文件或目录复制/移动到同一目标目录。

        执行前先完成全部路径和冲突检查，避免明显错误造成只处理了一半的批次。
        """

        require(principal, Permission.READ)
        require(principal, Permission.WRITE)
        if move:
            require(principal, Permission.DELETE)
        if not source_paths:
            raise ResourceConflictError("没有选择要处理的文件")

        destination = self.resolver.resolve(destination_path)
        if not destination.exists() or not destination.is_dir():
            raise ResourceNotFoundError("目标目录不存在")

        planned: list[tuple[Path, Path]] = []
        target_keys: set[str] = set()
        for source_path in source_paths:
            source = self.resolver.resolve(source_path)
            if not source.exists():
                raise ResourceNotFoundError(f"源文件不存在：{source_path}")
            if self.resolver.is_root(source):
                raise ResourceConflictError("不能复制或移动根目录")
            target = destination / source.name
            key = os.path.normcase(str(target))
            if key in target_keys:
                raise ResourceConflictError(f"多个源项目具有相同名称：{source.name}")
            target_keys.add(key)
            if target.exists():
                raise ResourceConflictError(f"目标已存在：{source.name}")
            if source.is_dir():
                try:
                    destination.relative_to(source)
                except ValueError:
                    pass
                else:
                    raise ResourceConflictError("不能把文件夹复制或移动到其自身内部")
            planned.append((source, target))

        results: list[FileEntry] = []
        for source, target in planned:
            try:
                if move:
                    shutil.move(str(source), str(target))
                elif source.is_dir():
                    shutil.copytree(source, target, copy_function=shutil.copy2)
                else:
                    shutil.copy2(source, target)
            except OSError as exc:
                action = "移动" if move else "复制"
                raise ResourceConflictError(f"{action}失败：{source.name}") from exc
            stat = target.stat()
            results.append(
                FileEntry(
                    target.name,
                    self.resolver.relative(target),
                    target.is_dir(),
                    0 if target.is_dir() else stat.st_size,
                    stat.st_mtime_ns,
                )
            )
        return results


async def bytes_chunks(parts: Iterable[bytes]) -> AsyncIterable[bytes]:
    """测试与非 HTTP 适配器可使用的异步字节流辅助函数。"""

    for part in parts:
        yield part
