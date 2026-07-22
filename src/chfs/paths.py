"""共享目录路径安全边界。"""

from __future__ import annotations

import ctypes
import os
import string
from pathlib import Path, PurePosixPath

from .errors import InvalidPathError
from .models import FileEntry


class SafePathResolver:
    """把 API 使用的 POSIX 相对路径安全映射到共享目录。

    所有磁盘用例都必须经过该对象。解析时既检查词法路径，也检查符号链接后的
    真实路径，避免目录穿越、Windows 盘符和链接逃逸。
    """

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, user_path: str | None) -> Path:
        raw = (user_path or "").replace("\\", "/")
        pure = PurePosixPath(raw)
        if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
            raise InvalidPathError("路径必须是共享目录内的规范相对路径")
        if pure.parts and ":" in pure.parts[0]:
            raise InvalidPathError("路径不能包含盘符")

        candidate = self.root.joinpath(*pure.parts).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise InvalidPathError("路径超出共享目录") from exc
        return candidate

    def relative(self, path: Path) -> str:
        """生成统一使用正斜杠的公开相对路径。"""

        try:
            return path.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError as exc:
            raise InvalidPathError("路径超出共享目录") from exc

    def is_root(self, path: Path) -> bool:
        return path.resolve(strict=False) == self.root

    def root_entries(self) -> list[FileEntry] | None:
        return None


class FullDiskPathResolver:
    """把公开路径的首段映射到当前进程可访问的各个磁盘根目录。"""

    def __init__(self, roots: dict[str, Path] | None = None) -> None:
        discovered = roots or available_disk_roots()
        self.roots = {label.upper(): root.resolve() for label, root in discovered.items()}
        if not self.roots:
            raise InvalidPathError("没有发现可访问的磁盘")

    def resolve(self, user_path: str | None) -> Path:
        parts = _safe_parts(user_path)
        if not parts:
            raise InvalidPathError("请选择一个磁盘")
        label = parts[0].upper()
        root = self.roots.get(label)
        if root is None:
            raise InvalidPathError("磁盘不存在或当前进程无权访问")
        candidate = root.joinpath(*parts[1:]).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise InvalidPathError("路径超出所选磁盘") from exc
        return candidate

    def relative(self, path: Path) -> str:
        candidate = path.resolve(strict=False)
        for label, root in self.roots.items():
            try:
                suffix = candidate.relative_to(root)
                return "/".join((label, *suffix.parts))
            except ValueError:
                continue
        raise InvalidPathError("路径不属于已开放磁盘")

    def is_root(self, path: Path) -> bool:
        candidate = path.resolve(strict=False)
        return any(candidate == root for root in self.roots.values())

    def root_entries(self) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for label, root in sorted(self.roots.items()):
            try:
                stat = root.stat()
            except OSError:
                continue
            name = f"{label}: 盘" if os.name == "nt" else "根目录"
            entries.append(FileEntry(name, label, True, 0, stat.st_mtime_ns))
        return entries


def available_disk_roots() -> dict[str, Path]:
    """枚举当前进程实际可访问的本地或映射磁盘。"""

    if os.name != "nt":
        return {"ROOT": Path("/").resolve()}
    try:
        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    except (AttributeError, OSError):
        mask = 0
    result: dict[str, Path] = {}
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        try:
            root = Path(f"{letter}:\\").resolve()
            if root.is_dir():
                result[letter] = root
        except OSError:
            continue
    return result


def _safe_parts(user_path: str | None) -> tuple[str, ...]:
    raw = (user_path or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise InvalidPathError("路径必须是规范相对路径")
    if pure.parts and ":" in pure.parts[0]:
        raise InvalidPathError("公开路径不能包含盘符符号")
    return pure.parts
