"""线程安全的下载传输会话登记表。"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from threading import RLock


@dataclass(slots=True)
class DownloadTransfer:
    """一个正在发送或刚刚完成的下载。"""

    transfer_id: str
    path: str
    owner: str
    source: str
    total_bytes: int
    transferred_bytes: int
    started_at: float
    updated_at: float
    status: str = "downloading"
    finished_at: float | None = None


class TransferRegistry:
    """记录下载进度，并短暂保留完成状态供桌面端观察。"""

    def __init__(self, completed_ttl_seconds: float = 10.0) -> None:
        self.completed_ttl_seconds = completed_ttl_seconds
        self._items: dict[str, DownloadTransfer] = {}
        self._lock = RLock()

    def start_download(self, path: str, owner: str, source: str, total_bytes: int) -> str:
        now = time.time()
        transfer_id = secrets.token_urlsafe(18)
        item = DownloadTransfer(
            transfer_id=transfer_id,
            path=path,
            owner=owner,
            source=source,
            total_bytes=total_bytes,
            transferred_bytes=0,
            started_at=now,
            updated_at=now,
        )
        with self._lock:
            self._items[transfer_id] = item
        return transfer_id

    def advance(self, transfer_id: str, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            item = self._items.get(transfer_id)
            if item is not None:
                item.transferred_bytes = min(item.total_bytes, item.transferred_bytes + count)
                item.updated_at = time.time()

    def finish(self, transfer_id: str, *, failed: bool = False) -> None:
        with self._lock:
            item = self._items.get(transfer_id)
            if item is None:
                return
            now = time.time()
            item.status = "failed" if failed else "completed"
            item.finished_at = now
            item.updated_at = now
            if not failed:
                item.transferred_bytes = item.total_bytes

    def snapshots(self) -> list[dict[str, object]]:
        """返回 GUI 可直接展示的不可变字典快照。"""

        now = time.time()
        with self._lock:
            expired = [
                key
                for key, item in self._items.items()
                if item.finished_at is not None and now - item.finished_at > self.completed_ttl_seconds
            ]
            for key in expired:
                self._items.pop(key, None)
            items = list(self._items.values())
        result: list[dict[str, object]] = []
        for item in items:
            elapsed = max(item.updated_at - item.started_at, 0.001)
            result.append(
                {
                    "id": item.transfer_id,
                    "direction": "download",
                    "path": item.path,
                    "owner": item.owner,
                    "source": item.source,
                    "transferred_bytes": item.transferred_bytes,
                    "total_bytes": item.total_bytes,
                    "bytes_per_second": item.transferred_bytes / elapsed,
                    "status": item.status,
                    "updated_at": item.updated_at,
                }
            )
        return sorted(result, key=lambda item: float(item["updated_at"]), reverse=True)
