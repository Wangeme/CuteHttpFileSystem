"""结构化审计日志。"""

from __future__ import annotations

import json
import logging
import ctypes
import ipaddress
import socket
import struct
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

LOGGER = logging.getLogger(__name__)


def resolve_mac_address(source: str) -> str:
    """尽力解析同一二层网络内的 IPv4 MAC 地址。

    HTTP 本身不携带 MAC；Windows 的 SendARP 只对本机或同一局域网邻居有效，
    跨路由和 IPv6 请求会返回 ``-``，不能把缺失值误认为客户端异常。
    """

    try:
        address = ipaddress.ip_address(source.split("%", 1)[0])
    except ValueError:
        return "-"
    if address.is_loopback:
        return "本机"
    if address.version != 4 or not hasattr(ctypes, "windll"):
        return "-"
    try:
        destination = struct.unpack("=I", socket.inet_aton(str(address)))[0]
        buffer = (ctypes.c_ubyte * 6)()
        length = ctypes.c_ulong(len(buffer))
        result = ctypes.windll.iphlpapi.SendARP(
            ctypes.c_ulong(destination),
            ctypes.c_ulong(0),
            ctypes.byref(buffer),
            ctypes.byref(length),
        )
        if result == 0 and length.value:
            return "-".join(f"{buffer[index]:02X}" for index in range(length.value))
    except (AttributeError, OSError):
        pass
    return "-"


class AuditLogger:
    """把每个审计事件写成独立 JSON 行。

    审计属于旁路能力：写入失败会记录到进程日志，但不会让已经完成的文件操作回滚。
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path.expanduser().resolve() if path else None
        self._lock = Lock()
        self._mac_cache: dict[str, str] = {}

    def record(self, action: str, *, actor: str, source: str, success: bool, **details: Any) -> None:
        if self._path is None:
            return
        mac = self._mac_cache.get(source)
        if mac is None:
            mac = resolve_mac_address(source)
            self._mac_cache[source] = mac
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "actor": actor,
            "source": source,
            "source_ip": source,
            "source_mac": mac,
            "success": success,
            "details": details,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            with self._lock, self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        except OSError:
            LOGGER.exception("写入审计日志失败")
