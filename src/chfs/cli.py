"""CHFS 命令行入口。"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

import uvicorn

from .config import AppConfig
from .errors import CHFSError
from .http import create_app
from .security import hash_password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chfs", description="CHFS HTTP 文件传输服务器")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动 HTTP 服务")
    serve.add_argument("--config", type=Path, default=Path("config.json"), help="JSON 配置文件")
    check = commands.add_parser("check-config", help="检查配置但不启动服务")
    check.add_argument("--config", type=Path, default=Path("config.json"), help="JSON 配置文件")
    password = commands.add_parser("hash-password", help="交互生成密码散列")
    password.add_argument("--password", help="不推荐：直接从命令行传入密码")
    gui = commands.add_parser("gui", help="启动桌面管理器")
    gui.add_argument("--config", type=Path, default=Path("config.json"), help="JSON 配置文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "gui":
            from .gui.app import CHFSApplication

            app = CHFSApplication(args.config)
            app.mainloop()
            return 0
        if args.command == "hash-password":
            password = args.password if args.password is not None else getpass.getpass("请输入密码：")
            print(hash_password(password))
            return 0
        config = AppConfig.load(args.config)
        config.share_root.mkdir(parents=True, exist_ok=True)
        if args.command == "check-config":
            print(f"配置有效，共享目录：{config.share_root}")
            return 0
        uvicorn.run(
            create_app(config),
            host=config.host,
            port=config.port,
            log_level="info",
            ssl_certfile=str(config.tls_certificate) if config.tls_certificate else None,
            ssl_keyfile=str(config.tls_private_key) if config.tls_private_key else None,
        )
        return 0
    except (CHFSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
