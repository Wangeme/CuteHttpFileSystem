"""CHFS 原生桌面管理器。

界面使用 Python 自带 Tk/ttk，避免额外 GUI 运行时依赖。业务操作全部委托给
AppConfig 与 ServerController，窗口代码只负责输入、反馈和状态呈现。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import webbrowser
from pathlib import Path

# Windows 默认会对未声明 DPI 感知的 Tk 窗口做位图拉伸，导致文字模糊、窗口尺寸
# 与屏幕物理像素不一致。必须在导入并初始化 tkinter 之前声明系统 DPI 感知。
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from ..config import AppConfig
from ..errors import CHFSError
from ..models import Permission
from ..security import Account, hash_password
from .controller import ServerController, discover_urls

BG = "#111722"
SIDEBAR = "#151d29"
SURFACE = "#1b2533"
SURFACE_ALT = "#222e3e"
BORDER = "#314055"
TEXT = "#ecf2f8"
MUTED = "#94a3b8"
ACCENT = "#2dd4bf"
ACCENT_DARK = "#0f766e"
DANGER = "#fb7185"
WARNING = "#fbbf24"


class CHFSApplication(tk.Tk):
    """桌面应用主窗口。"""

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path.resolve()
        self.config = self._load_or_default()
        self.accounts = list(self.config.accounts)
        self._state_events: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.controller = ServerController(self._state_events.put)
        self._active_page = "overview"

        self.title("CHFS · HTTP 文件传输服务器")
        self.geometry("1120x720")
        self.minsize(960, 640)
        self.configure(background=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_styles()
        self._create_variables()
        self._build_shell()
        self.show_page("overview")
        self.after(150, self._poll_server_state)

    def _load_or_default(self) -> AppConfig:
        if self.config_path.exists():
            try:
                return AppConfig.load(self.config_path)
            except CHFSError as exc:
                messagebox.showwarning("配置未加载", f"{exc}\n将使用安全默认配置。")
        return AppConfig(share_root=(self.config_path.parent / "shared").resolve(), audit_log=(self.config_path.parent / "logs" / "audit.jsonl").resolve())

    def _create_variables(self) -> None:
        self.root_var = tk.StringVar(value=str(self.config.share_root))
        self.host_var = tk.StringVar(value=self.config.host)
        self.port_var = tk.StringVar(value=str(self.config.port))
        self.max_mb_var = tk.StringVar(value=str(max(1, self.config.max_upload_bytes // 1024 // 1024)))
        self.ttl_hours_var = tk.StringVar(value=str(max(1, self.config.session_ttl_seconds // 3600)))
        self.tls_cert_var = tk.StringVar(value=str(self.config.tls_certificate or ""))
        self.tls_key_var = tk.StringVar(value=str(self.config.tls_private_key or ""))
        self.read_var = tk.BooleanVar(value=Permission.READ in self.config.guest_permissions)
        self.write_var = tk.BooleanVar(value=Permission.WRITE in self.config.guest_permissions)
        self.delete_var = tk.BooleanVar(value=Permission.DELETE in self.config.guest_permissions)
        self.status_var = tk.StringVar(value="已停止")
        self.status_detail_var = tk.StringVar(value="配置就绪，启动后即可在浏览器访问")

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        default_font = ("Microsoft YaHei UI", 10)
        self.option_add("*Font", default_font)
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=SURFACE_ALT, bordercolor=BORDER)
        style.configure("TFrame", background=BG)
        style.configure("Sidebar.TFrame", background=SIDEBAR)
        style.configure("Surface.TFrame", background=SURFACE, relief="flat")
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("CardTitle.TLabel", background=SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Metric.TLabel", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Subtitle.TLabel", foreground=MUTED, font=("Microsoft YaHei UI", 10))
        style.configure("Brand.TLabel", background=SIDEBAR, foreground=TEXT, font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Nav.TButton", background=SIDEBAR, foreground=MUTED, padding=(18, 12), anchor="w", borderwidth=0)
        style.map("Nav.TButton", background=[("active", SURFACE_ALT)], foreground=[("active", TEXT)])
        style.configure("ActiveNav.TButton", background=SURFACE_ALT, foreground=ACCENT, padding=(18, 12), anchor="w", borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("ActiveNav.TButton", background=[("active", SURFACE_ALT)], foreground=[("active", ACCENT)])
        style.configure("Primary.TButton", background=ACCENT, foreground="#062a27", padding=(18, 10), borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#5eead4"), ("disabled", BORDER)])
        style.configure("Secondary.TButton", background=SURFACE_ALT, foreground=TEXT, padding=(14, 9), borderwidth=1)
        style.map("Secondary.TButton", background=[("active", BORDER)])
        style.configure("Danger.TButton", background="#4a2230", foreground="#fecdd3", padding=(14, 9), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#6b293b")])
        style.configure("TEntry", padding=9, insertcolor=TEXT)
        style.configure("TCombobox", padding=8)
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=34, borderwidth=0)
        style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, padding=8, borderwidth=0)
        style.map("Treeview", background=[("selected", ACCENT_DARK)])

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self, style="Sidebar.TFrame", width=220)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        ttk.Label(sidebar, text="CHFS", style="Brand.TLabel").pack(anchor="w", padx=22, pady=(24, 2))
        ttk.Label(sidebar, text="文件传输控制台", style="Muted.TLabel", background=SIDEBAR).pack(anchor="w", padx=22, pady=(0, 28))
        pages = [
            ("overview", "运行概览"),
            ("share", "共享与权限"),
            ("network", "网络与访问"),
            ("accounts", "账户管理"),
            ("security", "安全状态"),
            ("logs", "操作日志"),
        ]
        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in pages:
            button = ttk.Button(sidebar, text=label, style="Nav.TButton", command=lambda page=key: self.show_page(page))
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button
        ttk.Label(sidebar, text="v0.1.0 · 内核已就绪", style="Muted.TLabel", background=SIDEBAR).pack(side="bottom", anchor="w", padx=22, pady=20)

        self.content = ttk.Frame(self, padding=(30, 24))
        self.content.grid(row=0, column=1, sticky="nsew")

    def show_page(self, page: str) -> None:
        self._active_page = page
        for key, button in self.nav_buttons.items():
            button.configure(style="ActiveNav.TButton" if key == page else "Nav.TButton")
        for child in self.content.winfo_children():
            child.destroy()
        builders = {
            "overview": self._build_overview,
            "share": self._build_share,
            "network": self._build_network,
            "accounts": self._build_accounts,
            "security": self._build_security,
            "logs": self._build_logs,
        }
        builders[page]()

    def _page_header(self, title: str, subtitle: str) -> None:
        ttk.Label(self.content, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.content, text=subtitle, style="Subtitle.TLabel").pack(anchor="w", pady=(4, 22))

    def _surface(self, parent: tk.Misc, **pack: object) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Surface.TFrame", padding=20)
        frame.pack(**pack)
        return frame

    def _build_overview(self) -> None:
        self._page_header("运行概览", "启动服务并把访问地址发给同一网络中的设备")
        hero = self._surface(self.content, fill="x")
        hero.columnconfigure(0, weight=1)
        status = ttk.Frame(hero, style="Surface.TFrame")
        status.grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.status_var, style="Metric.TLabel").pack(anchor="w")
        ttk.Label(status, textvariable=self.status_detail_var, style="Surface.TLabel").pack(anchor="w", pady=(5, 0))
        self.toggle_button = ttk.Button(hero, text="启动服务", style="Primary.TButton", command=self._toggle_server)
        self.toggle_button.grid(row=0, column=1, padx=(20, 0))

        metrics = ttk.Frame(self.content)
        metrics.pack(fill="x", pady=18)
        for column in range(3):
            metrics.columnconfigure(column, weight=1)
        values = [
            ("共享目录", Path(self.root_var.get()).name or self.root_var.get()),
            ("访客权限", self._guest_summary()),
            ("账户数量", str(len(self.accounts))),
        ]
        for index, (label, value) in enumerate(values):
            card = ttk.Frame(metrics, style="Surface.TFrame", padding=18)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0 if index == 2 else 6))
            ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(card, text=value, style="Surface.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(8, 0))

        addresses = self._surface(self.content, fill="both", expand=True)
        ttk.Label(addresses, text="访问地址", style="Surface.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(addresses, text="启动后，可复制下列地址到手机或其他电脑。", style="CardTitle.TLabel").pack(anchor="w", pady=(4, 15))
        for url in discover_urls(
            self.host_var.get(),
            self._safe_int(self.port_var.get(), 8080),
            https=bool(self.tls_cert_var.get() and self.tls_key_var.get()),
        ):
            row = ttk.Frame(addresses, style="Surface.TFrame")
            row.pack(fill="x", pady=5)
            ttk.Label(row, text=url, style="Surface.TLabel", font=("Cascadia Mono", 10)).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="复制", style="Secondary.TButton", command=lambda value=url: self._copy(value)).pack(side="right")
            ttk.Button(row, text="打开", style="Secondary.TButton", command=lambda value=url: webbrowser.open(value)).pack(side="right", padx=8)

    def _build_share(self) -> None:
        self._page_header("共享与权限", "设置可访问目录、上传限制和访客默认能力")
        card = self._surface(self.content, fill="x")
        self._field_label(card, "共享目录", "浏览器只能看到此目录及其子目录")
        path_row = ttk.Frame(card, style="Surface.TFrame")
        path_row.pack(fill="x", pady=(7, 18))
        ttk.Entry(path_row, textvariable=self.root_var).pack(side="left", fill="x", expand=True)
        ttk.Button(path_row, text="选择目录", style="Secondary.TButton", command=self._choose_root).pack(side="left", padx=(10, 0))
        self._field_label(card, "单文件上传上限（MB）", "上传按流式写盘，不会一次性占满内存")
        ttk.Entry(card, textvariable=self.max_mb_var, width=18).pack(anchor="w", pady=(7, 18))
        self._field_label(card, "访客权限", "未登录访问者可执行的操作；删除权限建议仅授予账户")
        checks = ttk.Frame(card, style="Surface.TFrame")
        checks.pack(anchor="w", pady=(8, 5))
        ttk.Checkbutton(checks, text="浏览与下载", variable=self.read_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="上传与新建目录", variable=self.write_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="删除", variable=self.delete_var).pack(side="left")
        self._action_bar()

    def _build_network(self) -> None:
        self._page_header("网络与访问", "控制监听地址、端口以及允许或拒绝的来源网段")
        card = self._surface(self.content, fill="both", expand=True)
        grid = ttk.Frame(card, style="Surface.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self._field_label(grid, "监听地址", "0.0.0.0 允许局域网设备访问", row=0, column=0)
        host = ttk.Combobox(grid, textvariable=self.host_var, values=("127.0.0.1", "0.0.0.0", "::"))
        host.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(7, 18))
        self._field_label(grid, "监听端口", "范围 1–65535", row=0, column=1)
        ttk.Entry(grid, textvariable=self.port_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(7, 18))
        self._field_label(grid, "允许网段（每行一个 CIDR）", "留空表示不启用允许列表", row=2, column=0)
        self._field_label(grid, "拒绝网段（每行一个 CIDR）", "拒绝规则始终优先", row=2, column=1)
        self.allow_text = tk.Text(grid, height=8, bg=SURFACE_ALT, fg=TEXT, insertbackground=TEXT, relief="flat", padx=10, pady=8)
        self.deny_text = tk.Text(grid, height=8, bg=SURFACE_ALT, fg=TEXT, insertbackground=TEXT, relief="flat", padx=10, pady=8)
        self.allow_text.grid(row=3, column=0, sticky="nsew", padx=(0, 8), pady=(7, 0))
        self.deny_text.grid(row=3, column=1, sticky="nsew", padx=(8, 0), pady=(7, 0))
        self.allow_text.insert("1.0", "\n".join(self.config.allow_networks))
        self.deny_text.insert("1.0", "\n".join(self.config.deny_networks))
        self._action_bar()

    def _build_accounts(self) -> None:
        self._page_header("账户管理", "用独立账户控制上传、删除和管理权限")
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="添加账户", style="Primary.TButton", command=self._add_account).pack(side="left")
        ttk.Button(toolbar, text="删除选中", style="Danger.TButton", command=self._delete_account).pack(side="left", padx=8)
        self.account_tree = ttk.Treeview(self.content, columns=("username", "permissions"), show="headings", selectmode="browse")
        self.account_tree.heading("username", text="用户名")
        self.account_tree.heading("permissions", text="权限")
        self.account_tree.column("username", width=220)
        self.account_tree.column("permissions", width=520)
        self.account_tree.pack(fill="both", expand=True)
        for index, account in enumerate(self.accounts):
            self.account_tree.insert("", "end", iid=str(index), values=(account.username, "、".join(sorted(item.value for item in account.permissions))))
        self._action_bar()

    def _build_security(self) -> None:
        self._page_header("安全状态", "在对外共享前检查关键保护是否开启")
        checks = [
            ("路径隔离", "已启用", "所有访问均经过真实路径边界检查"),
            ("密码存储", "PBKDF2", "独立随机盐与 310,000 次迭代"),
            ("会话有效期", f"{self.ttl_hours_var.get()} 小时", "令牌仅存内存，重启自动失效"),
            ("审计日志", "已启用" if self.config.audit_log else "未启用", str(self.config.audit_log or "未配置")),
            (
                "TLS / HTTPS",
                "已启用" if self.tls_cert_var.get() and self.tls_key_var.get() else "未启用",
                "配置证书后由服务器原生提供 HTTPS",
            ),
        ]
        for title, status, detail in checks:
            card = ttk.Frame(self.content, style="Surface.TFrame", padding=16)
            card.pack(fill="x", pady=5)
            ttk.Label(card, text=title, style="Surface.TLabel", width=18, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            color = ACCENT if status not in {"待配置", "未启用"} else WARNING
            tk.Label(card, text=status, bg=SURFACE, fg=color, font=("Microsoft YaHei UI", 10, "bold"), width=12, anchor="w").pack(side="left")
            ttk.Label(card, text=detail, style="CardTitle.TLabel").pack(side="left", fill="x", expand=True)
        tls = ttk.Frame(self.content, style="Surface.TFrame", padding=14)
        tls.pack(fill="x", pady=(12, 0))
        tls.columnconfigure(0, weight=1)
        tls.columnconfigure(1, weight=1)
        ttk.Label(tls, text="TLS 证书文件", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(tls, text="TLS 私钥文件", style="CardTitle.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
        cert_row = ttk.Frame(tls, style="Surface.TFrame")
        cert_row.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 6))
        key_row = ttk.Frame(tls, style="Surface.TFrame")
        key_row.grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=(6, 0))
        ttk.Entry(cert_row, textvariable=self.tls_cert_var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            cert_row,
            text="选择",
            style="Secondary.TButton",
            command=lambda: self._choose_tls_file(self.tls_cert_var, "选择 TLS 证书"),
        ).pack(side="left", padx=(6, 0))
        ttk.Entry(key_row, textvariable=self.tls_key_var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            key_row,
            text="选择",
            style="Secondary.TButton",
            command=lambda: self._choose_tls_file(self.tls_key_var, "选择 TLS 私钥"),
        ).pack(side="left", padx=(6, 0))
        self._action_bar()

    def _build_logs(self) -> None:
        self._page_header("操作日志", "查看最近的登录、上传、下载和删除审计事件")
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="刷新", style="Secondary.TButton", command=self._load_logs).pack(side="left")
        self.log_text = ScrolledText(self.content, bg=SURFACE, fg="#cbd5e1", insertbackground=TEXT, relief="flat", font=("Cascadia Mono", 9), padx=12, pady=12)
        self.log_text.pack(fill="both", expand=True)
        self._load_logs()

    def _field_label(self, parent: tk.Misc, title: str, help_text: str, *, row: int | None = None, column: int = 0) -> None:
        container = ttk.Frame(parent, style="Surface.TFrame")
        if row is None:
            container.pack(fill="x")
        else:
            container.grid(row=row, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0))
        ttk.Label(container, text=title, style="Surface.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        ttk.Label(container, text=help_text, style="CardTitle.TLabel").pack(anchor="w", pady=(2, 0))

    def _action_bar(self) -> None:
        bar = ttk.Frame(self.content)
        bar.pack(fill="x", pady=(18, 0))
        ttk.Button(bar, text="保存配置", style="Primary.TButton", command=self._save_config).pack(side="right")

    def _build_config(self) -> AppConfig:
        permissions = {
            permission
            for enabled, permission in (
                (self.read_var.get(), Permission.READ),
                (self.write_var.get(), Permission.WRITE),
                (self.delete_var.get(), Permission.DELETE),
            )
            if enabled
        }
        allow = self.config.allow_networks
        deny = self.config.deny_networks
        if hasattr(self, "allow_text") and self._active_page == "network":
            allow = tuple(line.strip() for line in self.allow_text.get("1.0", "end").splitlines() if line.strip())
            deny = tuple(line.strip() for line in self.deny_text.get("1.0", "end").splitlines() if line.strip())
        document = {
            "share_root": self.root_var.get().strip(),
            "host": self.host_var.get().strip(),
            "port": int(self.port_var.get()),
            "max_upload_bytes": int(self.max_mb_var.get()) * 1024 * 1024,
            "session_ttl_seconds": int(self.ttl_hours_var.get()) * 3600,
            "audit_log": str(self.config.audit_log) if self.config.audit_log else None,
            "tls_certificate": self.tls_cert_var.get().strip() or None,
            "tls_private_key": self.tls_key_var.get().strip() or None,
            "guest_permissions": sorted(item.value for item in permissions),
            "allow_networks": list(allow),
            "deny_networks": list(deny),
            "accounts": [
                {"username": item.username, "password_hash": item.password_hash, "permissions": sorted(permission.value for permission in item.permissions)}
                for item in self.accounts
            ],
        }
        return AppConfig.from_dict(document, base_dir=self.config_path.parent)

    def _save_config(self, *, quiet: bool = False) -> bool:
        if self.controller.state != "stopped":
            messagebox.showwarning("服务正在运行", "请先停止服务，再修改并保存配置。")
            return False
        try:
            self.config = self._build_config()
            self.config.save(self.config_path)
        except (ValueError, CHFSError, OSError) as exc:
            messagebox.showerror("配置无效", str(exc))
            return False
        if not quiet:
            messagebox.showinfo("保存成功", f"配置已保存到：\n{self.config_path}")
        return True

    def _toggle_server(self) -> None:
        if self.controller.state == "stopped":
            if not self._save_config(quiet=True):
                return
            if self.controller.start(self.config):
                self.status_var.set("正在启动")
                self.status_detail_var.set("正在绑定监听地址，请稍候…")
        else:
            self.status_var.set("正在停止")
            self.status_detail_var.set("等待当前请求结束…")
            self.after(10, self.controller.stop)

    def _poll_server_state(self) -> None:
        while not self._state_events.empty():
            self._state_events.get_nowait()
        state = self.controller.state
        if hasattr(self, "toggle_button"):
            if state == "running":
                self.status_var.set("服务运行中")
                self.status_detail_var.set("可从局域网设备打开下方地址")
                self.toggle_button.configure(text="停止服务", style="Danger.TButton")
            elif state == "stopped":
                self.status_var.set("已停止")
                self.status_detail_var.set("配置就绪，启动后即可在浏览器访问")
                self.toggle_button.configure(text="启动服务", style="Primary.TButton")
            elif state == "starting":
                self.status_var.set("正在启动")
                self.toggle_button.configure(text="启动中…", state="disabled")
            else:
                self.status_var.set("正在停止")
        if hasattr(self, "toggle_button") and state not in {"starting"}:
            self.toggle_button.configure(state="normal")
        self.after(200, self._poll_server_state)

    def _choose_root(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.root_var.get(), title="选择共享目录")
        if selected:
            self.root_var.set(selected)

    def _choose_tls_file(self, variable: tk.StringVar, title: str) -> None:
        selected = filedialog.askopenfilename(
            title=title,
            filetypes=(("证书与密钥", "*.pem *.crt *.cer *.key"), ("所有文件", "*.*")),
        )
        if selected:
            variable.set(selected)

    def _add_account(self) -> None:
        username = simpledialog.askstring("添加账户", "用户名（不能包含空格）：", parent=self)
        if not username:
            return
        if any(item.username == username for item in self.accounts):
            messagebox.showerror("用户名重复", "该用户名已经存在。")
            return
        password = simpledialog.askstring("添加账户", "密码（至少 8 个字符）：", parent=self, show="•")
        if not password:
            return
        try:
            account = Account(username.strip(), hash_password(password), frozenset({Permission.READ, Permission.WRITE}))
        except ValueError as exc:
            messagebox.showerror("密码无效", str(exc))
            return
        self.accounts.append(account)
        self.show_page("accounts")

    def _delete_account(self) -> None:
        selection = self.account_tree.selection()
        if not selection:
            messagebox.showinfo("未选择账户", "请先选择要删除的账户。")
            return
        index = int(selection[0])
        if messagebox.askyesno("删除账户", f"确定删除账户 {self.accounts[index].username}？"):
            self.accounts.pop(index)
            self.show_page("accounts")

    def _load_logs(self) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        path = self.config.audit_log
        if not path or not path.exists():
            self.log_text.insert("end", "暂无审计记录。启动服务并执行文件操作后会显示在这里。")
        else:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            for line in lines:
                try:
                    event = json.loads(line)
                    self.log_text.insert("end", f"{event.get('timestamp', '')}  {event.get('actor', '-'):12}  {event.get('action', '-'):<20}  {'成功' if event.get('success') else '失败'}\n")
                except json.JSONDecodeError:
                    self.log_text.insert("end", line + "\n")
        self.log_text.configure(state="disabled")

    def _copy(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status_detail_var.set("访问地址已复制到剪贴板")

    def _guest_summary(self) -> str:
        names = []
        if self.read_var.get():
            names.append("读取")
        if self.write_var.get():
            names.append("上传")
        if self.delete_var.get():
            names.append("删除")
        return " / ".join(names) if names else "禁止访问"

    @staticmethod
    def _safe_int(value: str, fallback: int) -> int:
        try:
            return int(value)
        except ValueError:
            return fallback

    def _on_close(self) -> None:
        if self.controller.state != "stopped":
            self.controller.stop()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CHFS 桌面管理器")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--print-window-handle", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--capture-page",
        choices=("overview", "share", "network", "accounts", "security", "logs"),
        default="overview",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    app = CHFSApplication(args.config)
    if args.capture_page != "overview":
        app.show_page(args.capture_page)
    if args.print_window_handle:
        # 仅供自动化视觉验收使用。直接输出客户区的屏幕坐标与尺寸，避免控制台
        # 宿主让 Win32 MainWindowHandle/祖先窗口关系变得不可靠。
        app.update_idletasks()
        app.update()
        print(
            json.dumps(
                {
                    "x": app.winfo_rootx(),
                    "y": app.winfo_rooty(),
                    "width": app.winfo_width(),
                    "height": app.winfo_height(),
                }
            ),
            flush=True,
        )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
