"""ZIP 归档流生成器。"""

from __future__ import annotations

import os
import queue
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Protocol

from .errors import InvalidPathError, ResourceConflictError


@dataclass(frozen=True, slots=True)
class ArchiveSource:
    """一个已经通过权限与路径边界校验的归档入口。"""

    path: Path
    name: str
    public_path: str


class ArchivePathBoundary(Protocol):
    """归档递归时用于复核每个磁盘条目仍位于开放边界内。"""

    def relative(self, path: Path) -> str: ...


_ARCHIVE_END = object()


class _QueueWriter:
    """把 zipfile 的同步写入转换为有界块队列，避免按文件大小占用内存。"""

    def __init__(
        self,
        chunks: queue.Queue[bytes | BaseException | object],
        cancelled: threading.Event,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self._chunks = chunks
        self._cancelled = cancelled
        self._chunk_size = chunk_size
        self._buffer = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        payload = bytes(data)
        if not payload:
            return 0
        self._position += len(payload)
        self._buffer.extend(payload)
        while len(self._buffer) >= self._chunk_size:
            self._emit(bytes(self._buffer[: self._chunk_size]))
            del self._buffer[: self._chunk_size]
        return len(payload)

    def tell(self) -> int:
        return self._position

    def seek(self, _offset: int, _whence: int = 0) -> int:
        # 明确声明为不可寻址流，zipfile 会使用数据描述符而不是回写文件头。
        raise OSError("archive stream is not seekable")

    def flush(self) -> None:
        if self._buffer:
            self._emit(bytes(self._buffer))
            self._buffer.clear()

    def _emit(self, chunk: bytes) -> None:
        while not self._cancelled.is_set():
            try:
                self._chunks.put(chunk, timeout=0.2)
                return
            except queue.Full:
                continue
        raise BrokenPipeError("archive download was cancelled")


def stream_zip_archive(
    sources: list[ArchiveSource],
    boundary: ArchivePathBoundary,
) -> Iterator[bytes]:
    """流式输出 ZIP_STORED 归档，内存只保留少量待发送数据块。

    选择不压缩是有意的：照片、视频和安装包通常已经压缩，再做 Deflate 只会占用
    CPU 并降低局域网吞吐。ZIP64 默认开启，可处理大于 4 GiB 的文件和归档。
    """

    chunks: queue.Queue[bytes | BaseException | object] = queue.Queue(maxsize=4)
    cancelled = threading.Event()

    def put_control(value: BaseException | object) -> None:
        while not cancelled.is_set():
            try:
                chunks.put(value, timeout=0.2)
                return
            except queue.Full:
                continue

    def produce() -> None:
        writer = _QueueWriter(chunks, cancelled)
        try:
            with zipfile.ZipFile(
                writer,
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for source in sources:
                    _write_source(archive, source, boundary)
        except BrokenPipeError:
            return
        except BaseException as exc:
            put_control(exc)
        finally:
            put_control(_ARCHIVE_END)

    producer = threading.Thread(target=produce, name="chfs-zip-producer", daemon=True)
    producer.start()
    try:
        while True:
            item = chunks.get()
            if item is _ARCHIVE_END:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        # 客户端中断下载时通知生产线程退出，避免它永久阻塞在已无人消费的队列上。
        cancelled.set()
        producer.join(timeout=2)


def _write_source(
    archive: zipfile.ZipFile,
    source: ArchiveSource,
    boundary: ArchivePathBoundary,
) -> None:
    if source.path.is_file():
        archive.write(source.path, PurePosixPath(source.name).as_posix())
        return
    if not source.path.is_dir():
        raise ResourceConflictError(f"无法归档特殊文件：{source.public_path}")

    def walk_error(exc: OSError) -> None:
        raise ResourceConflictError(f"无法读取文件夹：{source.public_path}") from exc

    for current_text, directory_names, file_names in os.walk(
        source.path,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        current = Path(current_text)
        relative = current.relative_to(source.path)
        archive_directory = PurePosixPath(source.name, *relative.parts)
        _write_directory_entry(archive, current, archive_directory)

        safe_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            child = current / name
            if _is_link_or_junction(child) or not _inside_boundary(boundary, child):
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories

        for name in sorted(file_names, key=str.casefold):
            child = current / name
            if _is_link_or_junction(child) or not _inside_boundary(boundary, child):
                continue
            if child.is_file():
                archive.write(child, PurePosixPath(archive_directory, name).as_posix())


def _write_directory_entry(
    archive: zipfile.ZipFile,
    directory: Path,
    archive_path: PurePosixPath,
) -> None:
    # 显式写入目录项，确保手机下载并解压后仍能保留空文件夹。
    info = zipfile.ZipInfo.from_file(directory, archive_path.as_posix().rstrip("/") + "/")
    archive.writestr(info, b"")


def _inside_boundary(boundary: ArchivePathBoundary, path: Path) -> bool:
    try:
        boundary.relative(path)
        return True
    except (InvalidPathError, OSError):
        return False


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True
