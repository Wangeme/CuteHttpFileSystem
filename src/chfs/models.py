"""跨模块使用的不可变领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    """系统支持的最小权限集合。"""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Principal:
    """一次请求的认证主体。"""

    name: str
    permissions: frozenset[Permission]
    authenticated: bool = False

    def can(self, permission: Permission) -> bool:
        """管理员隐含拥有全部普通权限。"""

        return Permission.ADMIN in self.permissions or permission in self.permissions


@dataclass(frozen=True, slots=True)
class FileEntry:
    """返回给适配器的文件元数据，不暴露主机绝对路径。"""

    name: str
    path: str
    is_directory: bool
    size: int
    modified_ns: int

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.path,
            "type": "directory" if self.is_directory else "file",
            "size": self.size,
            "modified_ns": self.modified_ns,
        }

