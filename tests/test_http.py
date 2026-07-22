from __future__ import annotations

import json
import http.cookiejar
import hashlib
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import uvicorn

from chfs.config import AppConfig
from chfs.http import create_app
from chfs.security import hash_password


class HttpIntegrationTests(unittest.TestCase):
    """通过真实环回 TCP 连接验证 ASGI 适配器与 Uvicorn 的组合。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        config = AppConfig.from_dict(
            {
                "share_root": "shared",
                "audit_log": "audit.jsonl",
                "guest_permissions": ["read"],
                "max_upload_bytes": 32,
                "accounts": [
                    {
                        "username": "operator",
                        "password_hash": hash_password("correct horse"),
                        "permissions": ["read", "write", "delete"],
                    }
                ],
            },
            base_dir=cls.base,
        )
        cls.application = create_app(config)
        cls.server = uvicorn.Server(
            uvicorn.Config(cls.application, host="127.0.0.1", port=cls.port, log_level="error")
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 5
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not cls.server.started:
            raise RuntimeError("测试 HTTP 服务未能启动")
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        token: str | None = None,
        content_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(), dict(exc.headers)
            finally:
                exc.close()

    def test_health_has_security_and_request_headers(self) -> None:
        status, body, headers = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        normalized = {key.casefold(): value for key, value in headers.items()}
        self.assertEqual(normalized["x-content-type-options"], "nosniff")
        self.assertIn("x-request-id", normalized)

    def test_web_interface_and_static_assets_are_served(self) -> None:
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("CHFS 文件空间", html)
        self.assertIn('type="file" multiple', html)
        self.assertIn('id="uploadSpeed"', html)
        self.assertIn('id="uploadOverallProgress"', html)
        normalized = {key.casefold(): value for key, value in headers.items()}
        self.assertIn("default-src 'self'", normalized["content-security-policy"])
        status, body, _ = self.request("GET", "/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"loadFiles", body)
        self.assertIn(b"createPixelIcon", body)
        self.assertNotIn(b'type.textContent = entry.type === "directory" ? "DIR" : "FILE"', body)
        status, body, _ = self.request("GET", "/assets/sha256.js")
        self.assertEqual(status, 200)
        self.assertIn(b"sha256Fallback", body)

    def test_session_query_returns_guest_identity(self) -> None:
        status, body, _ = self.request("GET", "/api/v1/session")
        self.assertEqual(status, 200)
        principal = json.loads(body)["principal"]
        self.assertEqual(principal["name"], "guest")
        self.assertEqual(principal["permissions"], ["read"])
        self.assertTrue(json.loads(body)["authentication_available"])

    def test_guest_can_read_but_cannot_upload(self) -> None:
        status, body, _ = self.request("GET", "/api/v1/files?path=")
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body)["entries"], list)
        status, body, _ = self.request("PUT", "/api/v1/content?path=no.txt", body=b"no")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "permission_denied")

    def test_authenticated_file_lifecycle_and_audit(self) -> None:
        login_body = json.dumps({"username": "operator", "password": "correct horse"}).encode()
        status, body, _ = self.request(
            "POST", "/api/v1/session", body=login_body, content_type="application/json"
        )
        self.assertEqual(status, 200)
        token = json.loads(body)["token"]

        encoded_path = urllib.parse.quote("资料/你好.txt")
        directory_body = json.dumps({"path": "资料"}, ensure_ascii=False).encode("utf-8")
        status, _, _ = self.request(
            "POST",
            "/api/v1/directories",
            body=directory_body,
            token=token,
            content_type="application/json; charset=utf-8",
        )
        self.assertEqual(status, 201)
        status, body, _ = self.request(
            "PUT", f"/api/v1/content?path={encoded_path}", body="你好，CHFS".encode(), token=token
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["name"], "你好.txt")

        status, body, _ = self.request("GET", "/api/v1/files?path=" + urllib.parse.quote("资料"), token=token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["entries"][0]["name"], "你好.txt")
        status, body, _ = self.request("GET", f"/api/v1/content?path={encoded_path}", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(body.decode(), "你好，CHFS")
        status, _, _ = self.request("DELETE", f"/api/v1/files?path={encoded_path}", token=token)
        self.assertEqual(status, 204)
        actions = [json.loads(line)["action"] for line in (self.base / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("session.login", actions)
        self.assertIn("file.upload", actions)
        self.assertIn("file.download", actions)
        self.assertIn("file.delete", actions)

    def test_encoded_path_traversal_is_rejected(self) -> None:
        status, body, _ = self.request("GET", "/api/v1/files?path=..%2Fsecret")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_path")

    def test_content_length_over_limit_is_rejected(self) -> None:
        login_body = json.dumps({"username": "operator", "password": "correct horse"}).encode()
        _, body, _ = self.request("POST", "/api/v1/session", body=login_body, content_type="application/json")
        token = json.loads(body)["token"]
        status, body, _ = self.request("PUT", "/api/v1/content?path=large.bin", body=b"x" * 33, token=token)
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "upload_too_large")

    def test_resumable_upload_chunk_integrity_and_range_download(self) -> None:
        login_body = json.dumps({"username": "operator", "password": "correct horse"}).encode()
        _, body, _ = self.request("POST", "/api/v1/session", body=login_body, content_type="application/json")
        token = json.loads(body)["token"]
        content = b"hello resumable transfer"
        create_body = json.dumps(
            {"path": "resumable.bin", "size": len(content), "resume_key": "http-test-resume", "overwrite": False}
        ).encode()
        status, body, _ = self.request(
            "POST", "/api/v1/uploads", body=create_body, token=token, content_type="application/json"
        )
        self.assertEqual(status, 201)
        upload_id = json.loads(body)["upload_id"]
        pieces = [content[:8], content[8:]]
        digests: list[bytes] = []
        offset = 0
        for piece in pieces:
            digest = hashlib.sha256(piece).digest()
            digests.append(digest)
            status, body, _ = self.request(
                "PATCH",
                f"/api/v1/uploads/{urllib.parse.quote(upload_id)}?offset={offset}",
                body=piece,
                token=token,
                extra_headers={"X-CHFS-Chunk-SHA256": digest.hex()},
            )
            self.assertEqual(status, 200)
            offset = json.loads(body)["offset"]
            upload_snapshots = self.application.state.runtime.uploads.snapshots()
            self.assertEqual(upload_snapshots[0]["source"], "127.0.0.1")
            self.assertEqual(upload_snapshots[0]["transferred_bytes"], offset)
        complete_body = json.dumps({"manifest_sha256": hashlib.sha256(b"".join(digests)).hexdigest()}).encode()
        status, body, _ = self.request(
            "POST",
            f"/api/v1/uploads/{urllib.parse.quote(upload_id)}/complete",
            body=complete_body,
            token=token,
            content_type="application/json",
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["sha256"], hashlib.sha256(content).hexdigest())
        status, body, headers = self.request(
            "GET",
            "/api/v1/content?path=resumable.bin",
            token=token,
            extra_headers={"Range": "bytes=6-14"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, content[6:15])
        normalized = {key.casefold(): value for key, value in headers.items()}
        self.assertEqual(normalized["accept-ranges"], "bytes")
        completed_downloads = self.application.state.runtime.transfers.snapshots()
        self.assertTrue(any(item["path"] == "resumable.bin" for item in completed_downloads))


class DownloadCookieSecurityTests(unittest.TestCase):
    """验证浏览器原生下载 Cookie 的最小权限边界。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        (cls.base / "shared").mkdir()
        (cls.base / "shared" / "private.txt").write_text("private", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        config = AppConfig.from_dict(
            {
                "share_root": "shared",
                "guest_permissions": [],
                "accounts": [{
                    "username": "reader",
                    "password_hash": hash_password("correct horse"),
                    "permissions": ["read"],
                }],
            },
            base_dir=cls.base,
        )
        cls.server = uvicorn.Server(uvicorn.Config(create_app(config), host="127.0.0.1", port=cls.port, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 5
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not cls.server.started:
            raise RuntimeError("Cookie 安全测试服务未能启动")
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def test_cookie_authorizes_get_download_but_never_put(self) -> None:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        login = urllib.request.Request(
            self.base_url + "/api/v1/session",
            data=json.dumps({"username": "reader", "password": "correct horse"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(login, timeout=3) as response:
            self.assertEqual(response.status, 200)
        with opener.open(self.base_url + "/api/v1/content?path=private.txt", timeout=3) as response:
            self.assertEqual(response.read(), b"private")

        write = urllib.request.Request(
            self.base_url + "/api/v1/content?path=forbidden.txt",
            data=b"no",
            method="PUT",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(write, timeout=3)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        self.assertFalse((self.base / "shared" / "forbidden.txt").exists())


if __name__ == "__main__":
    unittest.main()
