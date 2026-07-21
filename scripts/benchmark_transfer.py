"""CHFS 环回大文件吞吐与完整性基准。

该脚本启动真实 Uvicorn，通过 HTTP 分块协议上传并下载指定大小的数据；它用于
发现应用层限速、内存膨胀和内容损坏，不代表具体网卡与磁盘在真实局域网的上限。
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import platform
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from chfs.config import AppConfig
from chfs.http import create_app
from chfs.uploads import DEFAULT_CHUNK_SIZE


def request_json(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    if response.status >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status}: {payload.decode(errors='replace')}")
    return response.status, json.loads(payload) if payload else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="CHFS 大文件吞吐与完整性基准")
    parser.add_argument("--size-mib", type=int, default=256)
    parser.add_argument("--output", type=Path, default=Path("artifacts/performance.json"))
    args = parser.parse_args()
    if args.size_mib < 1:
        parser.error("--size-mib 必须大于 0")

    total_size = args.size_mib * 1024 * 1024
    # 固定块避免为基准额外占用与文件同等大小的进程内存。
    pattern = bytes((index * 31 + 17) & 0xFF for index in range(DEFAULT_CHUNK_SIZE))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with tempfile.TemporaryDirectory(prefix="chfs-benchmark-") as folder:
        root = Path(folder) / "shared"
        config = AppConfig(share_root=root, host="127.0.0.1", port=port, max_upload_bytes=total_size + 1)
        server = uvicorn.Server(
            uvicorn.Config(create_app(config), host=config.host, port=config.port, log_level="error", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("基准服务未能启动")

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            create_body = json.dumps(
                {"path": "benchmark.bin", "size": total_size, "resume_key": "benchmark", "overwrite": False}
            ).encode()
            _, created = request_json(
                connection,
                "POST",
                "/api/v1/uploads",
                create_body,
                {"Content-Type": "application/json"},
            )
            upload_id = str(created["upload_id"])
            file_hasher = hashlib.sha256()
            manifest_hasher = hashlib.sha256()
            offset = 0
            upload_started = time.perf_counter()
            while offset < total_size:
                chunk = pattern[: min(DEFAULT_CHUNK_SIZE, total_size - offset)]
                digest = hashlib.sha256(chunk).digest()
                file_hasher.update(chunk)
                manifest_hasher.update(digest)
                _, state = request_json(
                    connection,
                    "PATCH",
                    f"/api/v1/uploads/{urllib.parse.quote(upload_id)}?offset={offset}",
                    chunk,
                    {"X-CHFS-Chunk-SHA256": digest.hex()},
                )
                offset = int(state["offset"])
            complete_body = json.dumps({"manifest_sha256": manifest_hasher.hexdigest()}).encode()
            _, completed = request_json(
                connection,
                "POST",
                f"/api/v1/uploads/{urllib.parse.quote(upload_id)}/complete",
                complete_body,
                {"Content-Type": "application/json"},
            )
            upload_seconds = time.perf_counter() - upload_started
            expected_hash = file_hasher.hexdigest()
            upload_hash_ok = completed["sha256"] == expected_hash

            download_hasher = hashlib.sha256()
            download_started = time.perf_counter()
            connection.request("GET", "/api/v1/content?path=benchmark.bin")
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"下载失败：HTTP {response.status}")
            received = 0
            while data := response.read(1024 * 1024):
                received += len(data)
                download_hasher.update(data)
            download_seconds = time.perf_counter() - download_started
            download_hash_ok = received == total_size and download_hasher.hexdigest() == expected_hash

            connection.request(
                "GET",
                "/api/v1/content?path=benchmark.bin",
                headers={"Range": f"bytes={total_size - 4096}-{total_size - 1}"},
            )
            range_response = connection.getresponse()
            range_payload = range_response.read()
            range_ok = range_response.status == 206 and range_payload == pattern[-4096:]

            report = {
                "timestamp": datetime.now(UTC).isoformat(),
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "size_bytes": total_size,
                "size_mib": args.size_mib,
                "chunk_bytes": DEFAULT_CHUNK_SIZE,
                "upload_seconds": round(upload_seconds, 4),
                "upload_mib_per_second": round(args.size_mib / upload_seconds, 2),
                "download_seconds": round(download_seconds, 4),
                "download_mib_per_second": round(args.size_mib / download_seconds, 2),
                "upload_sha256_verified": upload_hash_ok,
                "download_sha256_verified": download_hash_ok,
                "range_resume_verified": range_ok,
                "sha256": expected_hash,
                "result": "passed" if upload_hash_ok and download_hash_ok and range_ok else "failed",
                "note": "环回结果用于发现应用层瓶颈，不等同于真实局域网物理带宽。",
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["result"] == "passed" else 1
        finally:
            connection.close()
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())

