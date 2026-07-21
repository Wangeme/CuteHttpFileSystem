"""应用配置读取与校验。"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidConfigurationError
from .models import Permission
from .security import Account, verify_password


@dataclass(frozen=True, slots=True)
class AppConfig:
    """校验后的不可变运行配置。"""

    share_root: Path
    host: str = "127.0.0.1"
    port: int = 8080
    max_upload_bytes: int = 1024**4
    session_ttl_seconds: int = 8 * 60 * 60
    audit_log: Path | None = None
    tls_certificate: Path | None = None
    tls_private_key: Path | None = None
    guest_permissions: frozenset[Permission] = frozenset(
        {Permission.READ, Permission.WRITE, Permission.DELETE}
    )
    allow_networks: tuple[str, ...] = ()
    deny_networks: tuple[str, ...] = ()
    accounts: tuple[Account, ...] = ()

    @classmethod
    def load(cls, config_path: Path) -> "AppConfig":
        """从 JSON 文件加载，并以配置文件目录为相对路径基准。"""

        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InvalidConfigurationError(f"配置文件不存在：{config_path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidConfigurationError(f"无法读取配置文件：{exc}") from exc
        if not isinstance(document, dict):
            raise InvalidConfigurationError("配置文件根节点必须是 JSON 对象")
        return cls.from_dict(document, base_dir=config_path.resolve().parent)

    def to_dict(self, *, base_dir: Path | None = None) -> dict[str, Any]:
        """转换为可序列化配置；可选择把工作区内路径保存为相对路径。"""

        def present(path: Path | None) -> str | None:
            if path is None:
                return None
            if base_dir is not None:
                try:
                    return path.relative_to(base_dir.resolve()).as_posix()
                except ValueError:
                    pass
            return str(path)

        return {
            "share_root": present(self.share_root),
            "host": self.host,
            "port": self.port,
            "max_upload_bytes": self.max_upload_bytes,
            "session_ttl_seconds": self.session_ttl_seconds,
            "audit_log": present(self.audit_log),
            "tls_certificate": present(self.tls_certificate),
            "tls_private_key": present(self.tls_private_key),
            "guest_permissions": sorted(item.value for item in self.guest_permissions),
            "allow_networks": list(self.allow_networks),
            "deny_networks": list(self.deny_networks),
            "accounts": [
                {
                    "username": item.username,
                    "password_hash": item.password_hash,
                    "permissions": sorted(permission.value for permission in item.permissions),
                }
                for item in self.accounts
            ],
        }

    def save(self, config_path: Path) -> None:
        """以 UTF-8 JSON 保存配置，不把密码或令牌写入日志。"""

        config_path = config_path.resolve()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(base_dir=config_path.parent), ensure_ascii=False, indent=2) + "\n"
        config_path.write_text(text, encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, base_dir: Path) -> "AppConfig":
        allowed_keys = {
            "share_root",
            "host",
            "port",
            "max_upload_bytes",
            "session_ttl_seconds",
            "audit_log",
            "tls_certificate",
            "tls_private_key",
            "guest_permissions",
            "allow_networks",
            "deny_networks",
            "accounts",
        }
        unknown = sorted(set(data) - allowed_keys)
        if unknown:
            raise InvalidConfigurationError(f"存在未知配置项：{', '.join(unknown)}")
        if not isinstance(data.get("share_root"), str) or not data["share_root"].strip():
            raise InvalidConfigurationError("share_root 必须是非空字符串")

        share_root = _resolve_path(base_dir, data["share_root"])
        audit_value = data.get("audit_log")
        if audit_value is not None and not isinstance(audit_value, str):
            raise InvalidConfigurationError("audit_log 必须是字符串或 null")
        audit_log = _resolve_path(base_dir, audit_value) if audit_value else None
        certificate = _optional_path(data.get("tls_certificate"), "tls_certificate", base_dir)
        private_key = _optional_path(data.get("tls_private_key"), "tls_private_key", base_dir)
        if (certificate is None) != (private_key is None):
            raise InvalidConfigurationError("tls_certificate 与 tls_private_key 必须同时配置")
        if certificate is not None:
            if not certificate.is_file():
                raise InvalidConfigurationError(f"TLS 证书文件不存在：{certificate}")
            if private_key is None or not private_key.is_file():
                raise InvalidConfigurationError(f"TLS 私钥文件不存在：{private_key}")
        host = _string(data.get("host", "127.0.0.1"), "host")
        port = _integer(data.get("port", 8080), "port", minimum=1, maximum=65535)
        maximum = _integer(data.get("max_upload_bytes", 1024**4), "max_upload_bytes", minimum=1)
        ttl = _integer(data.get("session_ttl_seconds", 28800), "session_ttl_seconds", minimum=60)
        guest = _permissions(
            data.get("guest_permissions", ["read", "write", "delete"]),
            "guest_permissions",
        )
        allow = _networks(data.get("allow_networks", []), "allow_networks")
        deny = _networks(data.get("deny_networks", []), "deny_networks")
        accounts = _accounts(data.get("accounts", []))
        usernames = [item.username for item in accounts]
        if len(usernames) != len(set(usernames)):
            raise InvalidConfigurationError("账户用户名不能重复")
        return cls(
            share_root=share_root,
            host=host,
            port=port,
            max_upload_bytes=maximum,
            session_ttl_seconds=ttl,
            audit_log=audit_log,
            tls_certificate=certificate,
            tls_private_key=private_key,
            guest_permissions=guest,
            allow_networks=allow,
            deny_networks=deny,
            accounts=accounts,
        )


def _resolve_path(base_dir: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else base_dir / candidate).resolve()


def _optional_path(value: Any, name: str, base_dir: Path) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidConfigurationError(f"{name} 必须是字符串或 null")
    return _resolve_path(base_dir, value)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidConfigurationError(f"{name} 必须是非空字符串")
    return value.strip()


def _integer(value: Any, name: str, *, minimum: int, maximum: int | None = None) -> int:
    # bool 是 int 的子类，配置中必须显式拒绝 true/false。
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidConfigurationError(f"{name} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" 到 {maximum}" if maximum is not None else " 以上"
        raise InvalidConfigurationError(f"{name} 必须为 {minimum}{suffix}")
    return value


def _permissions(value: Any, name: str) -> frozenset[Permission]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidConfigurationError(f"{name} 必须是权限字符串数组")
    try:
        return frozenset(Permission(item) for item in value)
    except ValueError as exc:
        raise InvalidConfigurationError(f"{name} 包含未知权限") from exc


def _networks(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidConfigurationError(f"{name} 必须是 CIDR 字符串数组")
    try:
        return tuple(str(ipaddress.ip_network(item, strict=False)) for item in value)
    except ValueError as exc:
        raise InvalidConfigurationError(f"{name} 包含无效网络：{exc}") from exc


def _accounts(value: Any) -> tuple[Account, ...]:
    if not isinstance(value, list):
        raise InvalidConfigurationError("accounts 必须是对象数组")
    result: list[Account] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"username", "password_hash", "permissions"}:
            raise InvalidConfigurationError(
                f"accounts[{index}] 必须且只能包含 username、password_hash、permissions"
            )
        username = _string(item["username"], f"accounts[{index}].username")
        if len(username) > 64 or any(char.isspace() for char in username):
            raise InvalidConfigurationError(f"accounts[{index}].username 格式无效")
        password_hash = _string(item["password_hash"], f"accounts[{index}].password_hash")
        # 使用一次无意义校验确认散列至少可被解析；不能接受明文密码。
        if not password_hash.startswith("pbkdf2_sha256$") or password_hash.count("$") != 3:
            raise InvalidConfigurationError(f"accounts[{index}].password_hash 格式无效")
        verify_password("configuration-format-check", password_hash)
        permissions = _permissions(item["permissions"], f"accounts[{index}].permissions")
        result.append(Account(username, password_hash, permissions))
    return tuple(result)
