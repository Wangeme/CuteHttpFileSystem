"""结构化审计日志。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

LOGGER = logging.getLogger(__name__)


class AuditLogger:
    """把每个审计事件写成独立 JSON 行。

    审计属于旁路能力：写入失败会记录到进程日志，但不会让已经完成的文件操作回滚。
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path.expanduser().resolve() if path else None
        self._lock = Lock()

    def record(self, action: str, *, actor: str, source: str, success: bool, **details: Any) -> None:
        if self._path is None:
            return
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "actor": actor,
            "source": source,
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

