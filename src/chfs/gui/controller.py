"""桌面界面使用的服务生命周期控制器。"""

from __future__ import annotations

import socket
import threading
import ipaddress
import logging
from collections.abc import Callable

import uvicorn

from ..config import AppConfig
from ..http import create_app


LOGGER = logging.getLogger(__name__)


class ServerController:
    """在线程中启停单个 Uvicorn 实例。

    GUI 只通过该对象控制服务，不直接访问 Uvicorn 内部状态，便于测试并避免
    主线程被网络事件循环阻塞。
    """

    def __init__(self, on_state_change: Callable[[str], None] | None = None) -> None:
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._on_state_change = on_state_change or (lambda _state: None)
        self._application = None
        self._lifecycle_state = "stopped"
        self._last_error: str | None = None

    @property
    def state(self) -> str:
        with self._lock:
            if self._thread is None:
                return "stopped"
            if self._lifecycle_state == "stopping" and self._thread.is_alive():
                return "stopping"
            if self._server and self._server.started and self._thread.is_alive():
                return "running"
            if self._thread.is_alive():
                return "starting"
            return "stopped"

    def start(self, config: AppConfig) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._lifecycle_state = "starting"
            self._last_error = None
            if not config.full_disk_access:
                config.share_root.mkdir(parents=True, exist_ok=True)
            self._application = create_app(config)
            self._server = uvicorn.Server(
                uvicorn.Config(
                    self._application,
                    host=config.host,
                    port=config.port,
                    log_level="info",
                    access_log=True,
                    # PyInstaller 的 --windowed 模式会把 sys.stdout/sys.stderr
                    # 设为 None。Uvicorn 的默认日志配置依赖这些流，会导致服务
                    # 在线程启动前直接退出，因此由桌面程序自行管理日志。
                    log_config=None,
                    ssl_certfile=str(config.tls_certificate) if config.tls_certificate else None,
                    ssl_keyfile=str(config.tls_private_key) if config.tls_private_key else None,
                )
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(self._server,),
                name="chfs-http-server",
                daemon=True,
            )
            self._thread.start()
        self._on_state_change("starting")
        return True

    def _run(self, server: uvicorn.Server) -> None:
        try:
            server.run()
        except BaseException as exc:
            # 后台线程的异常必须保存给 GUI；窗口版 EXE 没有控制台可显示回溯。
            LOGGER.exception("HTTP 服务线程异常退出")
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._lifecycle_state = "stopped"
            self._on_state_change("stopped")

    @property
    def last_error(self) -> str | None:
        """返回最近一次启动/运行错误，供无控制台的 GUI 显示。"""

        with self._lock:
            return self._last_error

    def stop(self, timeout: float = 5.0) -> bool:
        with self._lock:
            if not self._server or not self._thread or not self._thread.is_alive():
                return False
            server = self._server
            thread = self._thread
            self._lifecycle_state = "stopping"
            server.should_exit = True
        self._on_state_change("stopping")
        thread.join(timeout=timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._lock:
                self._lifecycle_state = "stopped"
        return stopped

    def transfer_snapshots(self) -> list[dict[str, object]]:
        """返回上传和下载会话快照；GUI 读取时不接触 Uvicorn 内部状态。"""

        with self._lock:
            application = self._application
        if application is None:
            return []
        runtime = application.state.runtime
        result = runtime.uploads.snapshots() + runtime.transfers.snapshots()
        return sorted(result, key=lambda item: float(item["updated_at"]), reverse=True)

    def wait_until_started(self, timeout: float = 5.0) -> bool:
        """供测试和 GUI 状态轮询使用，不阻塞无限时间。"""

        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state == "running":
                return True
            if self._thread is not None and not self._thread.is_alive():
                return False
            time.sleep(0.02)
        return False


def discover_urls(host: str, port: int, *, https: bool = False) -> list[str]:
    """根据监听协议族生成可复制的访问地址，过滤虚拟/保留地址。"""

    if host not in {"0.0.0.0", "::"}:
        display_host = f"[{host}]" if ":" in host else host
        return [f"{'https' if https else 'http'}://{display_host}:{port}"]
    ipv4_wildcard = host == "0.0.0.0"
    addresses = {"127.0.0.1" if ipv4_wildcard else "::1"}
    link_local_fallbacks: set[str] = set()
    rfc1918_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    ula_network = ipaddress.ip_network("fc00::/7")
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None):
            address = item[4][0]
            try:
                parsed = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError:
                continue
            # 0.0.0.0 只监听 IPv4；过去把 IPv6 地址也列出来会造成“看起来可用，
            # 实际无法连接”的误导。监听 :: 时同理只展示 IPv6。
            if parsed.version != (4 if ipv4_wildcard else 6) or parsed.is_loopback:
                continue
            if parsed.is_link_local:
                if parsed.version == 4:
                    link_local_fallbacks.add(str(parsed))
                continue
            if parsed.version == 4:
                if any(parsed in network for network in rfc1918_networks) or parsed.is_global:
                    addresses.add(str(parsed))
            elif parsed in ula_network or parsed.is_global:
                addresses.add(str(parsed))
    except (OSError, UnicodeError):
        pass

    # 没有正常局域网地址时，169.254/16 仍可用于同一物理链路上的应急访问。
    if ipv4_wildcard and addresses == {"127.0.0.1"}:
        addresses.update(link_local_fallbacks)

    def address_priority(value: str) -> tuple[int, str]:
        parsed = ipaddress.ip_address(value)
        if parsed.is_loopback:
            return (3, value)
        if parsed.is_link_local:
            return (2, value)
        if parsed.version == 4 and any(parsed in network for network in rfc1918_networks):
            return (0, value)
        if parsed.version == 6 and parsed in ula_network:
            return (0, value)
        return (1, value)

    result = []
    for address in sorted(addresses, key=address_priority):
        display = f"[{address}]" if ":" in address else address
        result.append(f"{'https' if https else 'http'}://{display}:{port}")
    return result
