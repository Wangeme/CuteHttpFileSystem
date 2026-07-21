"""认证、授权、会话和来源地址过滤。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Iterable

from .errors import AuthenticationError, PermissionDeniedError
from .models import Permission, Principal

PBKDF2_ITERATIONS = 310_000


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """生成可直接写入配置文件的密码散列字符串。"""

    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    """使用恒定时间比较校验 PBKDF2 密码。格式错误统一返回失败。"""

    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class Account:
    username: str
    password_hash: str
    permissions: frozenset[Permission]


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    principal: Principal
    expires_at: float


class SessionManager:
    """线程安全的内存会话仓库。"""

    def __init__(
        self,
        accounts: Iterable[Account],
        guest_permissions: Iterable[Permission],
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._accounts = {account.username: account for account in accounts}
        self._guest = Principal("guest", frozenset(guest_permissions), False)
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = RLock()

    @property
    def guest(self) -> Principal:
        return self._guest

    def login(self, username: str, password: str) -> Session:
        account = self._accounts.get(username)
        # 账户不存在时仍计算一次固定散列，降低基于响应时间枚举账户的可行性。
        fallback = "pbkdf2_sha256$1$MDAwMDAwMDAwMDAwMDAwMA==$QvsEdl4vX1Q2X4_Y13Hb7xyZ4I0qW5jB-jbRUgjzQu0="
        if not verify_password(password, account.password_hash if account else fallback):
            raise AuthenticationError("用户名或密码错误")
        assert account is not None
        now = self._clock()
        session = Session(
            token=secrets.token_urlsafe(32),
            principal=Principal(account.username, account.permissions, True),
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            self._purge_expired(now)
            self._sessions[session.token] = session
        return session

    def resolve(self, token: str | None) -> Principal:
        if not token:
            return self._guest
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session.expires_at <= now:
                self._sessions.pop(token, None)
                raise AuthenticationError("会话无效或已过期")
            return session.principal

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def _purge_expired(self, now: float) -> None:
        expired = [token for token, item in self._sessions.items() if item.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)


def require(principal: Principal, permission: Permission) -> None:
    """统一授权检查；适配器不得绕过此函数直接执行用例。"""

    if not principal.can(permission):
        raise PermissionDeniedError("当前身份没有执行此操作的权限")


class NetworkPolicy:
    """CIDR 来源地址策略。拒绝规则优先；允许列表为空表示允许其他地址。"""

    def __init__(self, allow: Iterable[str] = (), deny: Iterable[str] = ()) -> None:
        self._allow = tuple(ipaddress.ip_network(item, strict=False) for item in allow)
        self._deny = tuple(ipaddress.ip_network(item, strict=False) for item in deny)

    def permits(self, address: str) -> bool:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if any(ip in network for network in self._deny if network.version == ip.version):
            return False
        if not self._allow:
            return True
        return any(ip in network for network in self._allow if network.version == ip.version)

