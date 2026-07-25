"""跨设备共享文本存储。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from .errors import InvalidPathError
from .models import Permission, Principal
from .security import require

MAX_SHARED_TEXT_BYTES = 1024 * 1024


class SharedTextStore:
    """以最后写入者为准，原子持久化一段小型 UTF-8 文本。"""

    def __init__(self, path: Path) -> None:
        self._path = path.expanduser().resolve()
        self._lock = RLock()
        self._text = ""
        self._revision = 0
        self._updated_at: str | None = None
        self._load()

    def read(self, principal: Principal) -> dict[str, object]:
        require(principal, Permission.READ)
        with self._lock:
            return self._snapshot()

    def update(self, principal: Principal, text: str) -> dict[str, object]:
        require(principal, Permission.WRITE)
        if len(text.encode("utf-8")) > MAX_SHARED_TEXT_BYTES:
            raise InvalidPathError("共享文本不能超过 1 MiB")
        with self._lock:
            previous = (self._text, self._revision, self._updated_at)
            self._text = text
            self._revision += 1
            self._updated_at = datetime.now(UTC).isoformat()
            try:
                self._save()
            except BaseException:
                self._text, self._revision, self._updated_at = previous
                raise
            return self._snapshot()

    def _snapshot(self) -> dict[str, object]:
        return {
            "text": self._text,
            "revision": self._revision,
            "updated_at": self._updated_at,
            "max_bytes": MAX_SHARED_TEXT_BYTES,
        }

    def _load(self) -> None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        text = payload.get("text")
        revision = payload.get("revision")
        updated_at = payload.get("updated_at")
        if isinstance(text, str) and isinstance(revision, int) and revision >= 0:
            self._text = text
            self._revision = revision
            self._updated_at = updated_at if isinstance(updated_at, str) else None

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = json.dumps(self._snapshot(), ensure_ascii=False, separators=(",", ":"))
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(document)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self._path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
