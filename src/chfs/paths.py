"""共享目录路径安全边界。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .errors import InvalidPathError


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

