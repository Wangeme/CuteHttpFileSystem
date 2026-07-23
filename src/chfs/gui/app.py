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
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import qrcode

# Windows 默认会对未声明 DPI 感知的 Tk 窗口做位图拉伸，导致文字模糊、窗口尺寸
# 与屏幕物理像素不一致。必须在导入并初始化 tkinter 之前声明系统 DPI 感知。
if os.name == "nt":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CHFS.FileTransfer.Server")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..config import AppConfig, default_config_path, default_share_root
from ..errors import CHFSError
from ..models import Permission
from ..security import Account, hash_password
from .controller import ServerController, discover_urls

BG = "#070d13"
SIDEBAR = "#090f15"
SURFACE = "#0d151d"
SURFACE_ALT = "#14212b"
BORDER = "#30414d"
TEXT = "#d8e1e7"
MUTED = "#8da0ad"
ACCENT = "#25d7d1"
ACCENT_DARK = "#0d5d5a"
RUNNING = "#39ff63"
RUNNING_DARK = "#052d15"
DANGER = "#ff6574"
WARNING = "#9db6c2"


class CHFSApplication(tk.Tk):
    """桌面应用主窗口。"""

    def __init__(self, config_path: Path, *, auto_start: bool = True) -> None:
        super().__init__()
        self.config_path = config_path.resolve()
        self.config = self._load_or_default()
        self.accounts = list(self.config.accounts)
        self._state_events: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.controller = ServerController(self._state_events.put)
        self._active_page = "overview"

        self.title("CHFS · HTTP 文件传输服务器")
        self._window_icon = tk.PhotoImage(file=Path(__file__).with_name("chfs-icon.png"))
        self._status_waveform = tk.PhotoImage(file=Path(__file__).with_name("status-waveform.png"))
        self._animated_waveform = tk.PhotoImage(
            width=self._status_waveform.width(),
            height=self._status_waveform.height(),
        )
        self._waveform_offset = 0
        self._waveform_tick = 0
        self._render_waveform_frame()
        self.iconphoto(True, self._window_icon)
        if os.name == "nt":
            try:
                self.iconbitmap(default=str(Path(__file__).with_name("chfs.ico")))
            except tk.TclError:
                pass
        self._apply_optimal_geometry()
        self.configure(background=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_styles()
        self._create_variables()
        self._build_shell()
        self.show_page("overview")
        self.after(150, self._poll_server_state)
        self.after(90, self._animate_status_waveform)
        if auto_start:
            # 等主窗口完成绘制后再启动，确保失败信息能够正常显示。
            self.after(250, self._start_server)

    def _apply_optimal_geometry(self) -> None:
        """按当前屏幕可用尺寸选择舒适窗口大小，并在每次启动时居中。"""

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1440, max(920, screen_width - 96))
        height = min(1024, max(680, screen_height - 120))
        width = min(width, screen_width)
        height = min(height, screen_height)
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.minsize(min(1080, width), min(720, height))

    def _load_or_default(self) -> AppConfig:
        if self.config_path.exists():
            try:
                return AppConfig.load(self.config_path)
            except CHFSError as exc:
                messagebox.showwarning("配置未加载", f"{exc}\n将使用安全默认配置。")
        return AppConfig(
            share_root=default_share_root().resolve(),
            host="0.0.0.0",
            audit_log=(self.config_path.parent / "logs" / "audit.jsonl").resolve(),
        )

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
        self.full_disk_var = tk.BooleanVar(value=self.config.full_disk_access)
        self.status_var = tk.StringVar(value="已关闭")
        self.status_detail_var = tk.StringVar(value="配置就绪，启动后即可在浏览器访问")

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        default_font = ("Microsoft YaHei UI", 9)
        self.option_add("*Font", default_font)
        style.configure(
            ".",
            background=BG,
            foreground=TEXT,
            fieldbackground=SURFACE_ALT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor="#020508",
        )
        style.configure("TFrame", background=BG)
        style.configure("Sidebar.TFrame", background=SIDEBAR)
        style.configure("Surface.TFrame", background=SURFACE, relief="solid", borderwidth=1)
        style.configure("Running.TFrame", background=RUNNING_DARK, relief="solid", borderwidth=1)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("CardTitle.TLabel", background=SURFACE, foreground=MUTED, font=("Microsoft YaHei UI", 8))
        style.configure("Metric.TLabel", background=SURFACE, foreground=TEXT, font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("RunningMetric.TLabel", background=RUNNING_DARK, foreground=RUNNING, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("RunningDetail.TLabel", background=RUNNING_DARK, foreground="#9afcaf", font=("Microsoft YaHei UI", 8))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Subtitle.TLabel", foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Brand.TLabel", background=SIDEBAR, foreground=TEXT, font=("Cascadia Mono", 12, "bold"))
        style.configure("Nav.TButton", background=SIDEBAR, foreground=MUTED, padding=(18, 10), anchor="w", borderwidth=0, font=("Microsoft YaHei UI", 9))
        style.map("Nav.TButton", background=[("active", SURFACE_ALT)], foreground=[("active", TEXT)])
        style.configure("ActiveNav.TButton", background="#0c2a1a", foreground=RUNNING, padding=(18, 10), anchor="w", borderwidth=1, font=("Microsoft YaHei UI", 9, "bold"))
        style.map("ActiveNav.TButton", background=[("active", "#103622")], foreground=[("active", RUNNING)])
        style.configure("Primary.TButton", background=ACCENT, foreground="#062a27", padding=(18, 9), borderwidth=0, font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", "#5eead4"), ("disabled", BORDER)])
        style.configure("Running.TButton", background="#0e6c2b", foreground="#d8ffe1", padding=(16, 9), borderwidth=1, font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Running.TButton", background=[("active", "#128438")])
        style.configure("Secondary.TButton", background=SURFACE_ALT, foreground=TEXT, padding=(14, 9), borderwidth=1)
        style.map("Secondary.TButton", background=[("active", BORDER)])
        style.configure("Compact.TButton", background=SURFACE_ALT, foreground=TEXT, padding=(7, 6), borderwidth=1)
        style.map("Compact.TButton", background=[("active", BORDER)])
        style.configure("Danger.TButton", background="#4a2230", foreground="#fecdd3", padding=(14, 9), borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#6b293b")])
        style.configure("TEntry", padding=9, insertcolor=TEXT)
        style.configure("TCombobox", padding=8)
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=25, borderwidth=0, font=("Cascadia Mono", 8))
        style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, padding=6, borderwidth=1, font=("Microsoft YaHei UI", 8))
        style.map("Treeview", background=[("selected", ACCENT_DARK)])

    def _build_shell(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self, style="Sidebar.TFrame", width=185)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        ttk.Label(sidebar, text="CHFS 文件传输控制台", style="Brand.TLabel").pack(anchor="w", padx=18, pady=(22, 26))
        pages = [
            ("overview", "运行概览"),
            ("share", "共享与权限"),
            ("network", "网络与访问"),
            ("transfers", "传输会话"),
            ("accounts", "账户管理"),
            ("security", "安全状态"),
            ("logs", "审计日志"),
        ]
        self.nav_buttons: dict[str, ttk.Button] = {}
        for key, label in pages:
            button = ttk.Button(sidebar, text=label, style="Nav.TButton", command=lambda page=key: self.show_page(page))
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button
        ttk.Label(sidebar, text="CHFS v0.1.0\n安全 · 稳定 · 高效", style="Muted.TLabel", background=SIDEBAR, justify="left").pack(side="bottom", anchor="w", padx=18, pady=20)

        self.content = ttk.Frame(self, padding=(22, 18))
        self.content.grid(row=0, column=1, sticky="nsew")

    def show_page(self, page: str) -> None:
        self._active_page = page
        if page != "transfers":
            self.transfer_tree = None
        if page != "overview":
            self.toggle_button = None
        for key, button in self.nav_buttons.items():
            button.configure(style="ActiveNav.TButton" if key == page else "Nav.TButton")
        for child in self.content.winfo_children():
            child.destroy()
        builders = {
            "overview": self._build_overview,
            "share": self._build_share,
            "network": self._build_network,
            "transfers": self._build_transfers,
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
        header = ttk.Frame(self.content)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="运行概览", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"),
            style="Muted.TLabel",
            font=("Cascadia Mono", 9),
        ).pack(side="right")

        running = self.controller.state == "running"
        hero_style = "Running.TFrame" if running else "Surface.TFrame"
        hero = ttk.Frame(self.content, style=hero_style, padding=(20, 14))
        hero.pack(fill="x")
        hero.columnconfigure(1, weight=1)
        self.status_panel = hero
        status = ttk.Frame(hero, style=hero_style)
        status.grid(row=0, column=0, sticky="w")
        self.status_text_panel = status
        self.status_value_label = ttk.Label(
            status,
            textvariable=self.status_var,
            style="RunningMetric.TLabel" if running else "Metric.TLabel",
        )
        self.status_value_label.pack(anchor="w")
        self.status_detail_label = ttk.Label(
            status,
            textvariable=self.status_detail_var,
            style="RunningDetail.TLabel" if running else "Surface.TLabel",
        )
        self.status_detail_label.pack(anchor="w", pady=(4, 0))
        self.waveform_label = tk.Label(
            hero,
            image=self._animated_waveform,
            bg=RUNNING_DARK if running else SURFACE,
            bd=0,
        )
        self.waveform_label.grid(row=0, column=1, sticky="e", padx=18)
        if not running:
            self.waveform_label.grid_remove()
        self.toggle_button = ttk.Button(
            hero,
            text="停止服务" if running else "启动服务",
            style="Running.TButton" if running else "Primary.TButton",
            command=self._toggle_server,
        )
        self.toggle_button.grid(row=0, column=2, sticky="e")

        upper = ttk.Frame(self.content)
        upper.pack(fill="x", pady=(12, 0))
        upper.columnconfigure(0, weight=1)
        upper.columnconfigure(1, weight=1)
        self._overview_upper = upper

        addresses = ttk.Frame(upper, style="Surface.TFrame", padding=16)
        self._overview_addresses = addresses
        addresses.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(addresses, text="访问地址", style="Surface.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(addresses, text="左键打开 · 右键复制 · 悬停切换二维码", style="CardTitle.TLabel").pack(anchor="w", pady=(3, 10))
        urls = discover_urls(
            self.host_var.get(),
            self._safe_int(self.port_var.get(), 8080),
            https=bool(self.tls_cert_var.get() and self.tls_key_var.get()),
        )
        body = ttk.Frame(addresses, style="Surface.TFrame")
        body.pack(fill="x", expand=True)
        body.columnconfigure(0, weight=1)
        address_list = ttk.Frame(body, style="Surface.TFrame")
        address_list.grid(row=0, column=0, sticky="ew", padx=(0, 14))
        body.columnconfigure(1, minsize=116)
        qr_panel = ttk.Frame(body, style="Surface.TFrame", width=116)
        qr_panel.grid(row=0, column=1, sticky="ne")
        ttk.Label(qr_panel, text="扫码访问", style="CardTitle.TLabel").pack(anchor="center", pady=(0, 7))
        self.qr_label = tk.Label(qr_panel, bg="#ffffff", bd=0)
        self.qr_label.pack(anchor="center")

        for index, url in enumerate(urls):
            row = ttk.Frame(address_list, style="Surface.TFrame", padding=(10, 7))
            row.pack(fill="x")
            ttk.Label(
                row,
                text="局域网地址" if index == 0 and "127.0.0.1" not in url else "本机地址",
                style="CardTitle.TLabel",
                width=7,
            ).pack(side="left")
            url_label = tk.Label(
                row,
                text=url,
                bg=SURFACE,
                fg=ACCENT,
                cursor="hand2",
                font=("Cascadia Mono", 9, "underline"),
                anchor="w",
                justify="left",
            )
            url_label.pack(side="left", fill="x", expand=True)
            url_label.bind("<Button-1>", lambda _event, value=url: webbrowser.open(value))
            url_label.bind("<Button-3>", lambda _event, value=url: self._copy(value))
            row.bind("<Enter>", lambda _event, value=url: self._show_qr(value))
            url_label.bind("<Enter>", lambda _event, value=url: self._show_qr(value))
            if index < len(urls) - 1:
                ttk.Separator(address_list, orient="horizontal").pack(fill="x")
        if urls:
            preferred = next((item for item in urls if "192.168." in item or "10." in item or "172." in item), urls[0])
            self._show_qr(preferred)

        service = ttk.Frame(upper, style="Surface.TFrame", padding=16)
        self._overview_service = service
        service.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        service_header = ttk.Frame(service, style="Surface.TFrame")
        service_header.pack(fill="x", pady=(0, 7))
        ttk.Label(service_header, text="服务概览", style="Surface.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        ttk.Button(
            service_header,
            text="打开此电脑" if self.full_disk_var.get() else "打开目录",
            width=9,
            style="Compact.TButton",
            command=self._open_share_location,
        ).pack(side="right")
        root_name = "本机所有可用磁盘" if self.full_disk_var.get() else (Path(self.root_var.get()).name or self.root_var.get())
        service_rows = (
            ("服务名称", "CHFS 文件传输服务"),
            ("监听端口", self.port_var.get()),
            ("根目录", root_name),
            ("访客权限", self._guest_summary()),
            ("账户", f"{len(self.accounts)} 个"),
        )
        for index, (label, value) in enumerate(service_rows):
            row = ttk.Frame(service, style="Surface.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, style="CardTitle.TLabel", width=10).pack(side="left")
            ttk.Label(
                row,
                text=value,
                style="Surface.TLabel",
                font=("Cascadia Mono", 9) if label in {"监听端口", "账户"} else ("Microsoft YaHei UI", 9),
            ).pack(side="left", fill="x", expand=True)
            if index < len(service_rows) - 1:
                ttk.Separator(service, orient="horizontal").pack(fill="x")
        disk_button = ttk.Button(
            service,
            text="关闭全盘访问" if self.full_disk_var.get() else "开放全盘访问",
            style="Secondary.TButton" if self.full_disk_var.get() else "Danger.TButton",
            command=self._toggle_full_disk_access,
        )
        disk_button.pack(anchor="e", pady=(8, 0))

        upper.bind("<Configure>", self._reflow_overview)

        recent = ttk.Frame(self.content, style="Surface.TFrame", padding=(14, 10))
        recent.pack(fill="both", expand=True, pady=(12, 0))
        recent.columnconfigure(0, weight=1)
        recent.rowconfigure(1, weight=1)
        recent_header = ttk.Frame(recent, style="Surface.TFrame")
        recent_header.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        ttk.Label(recent_header, text="最近操作（最新 10 条）", style="Surface.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        ttk.Button(recent_header, text="刷新", width=6, style="Compact.TButton", command=self._load_overview_logs).pack(side="right")
        ttk.Button(recent_header, text="查看全部", width=7, style="Compact.TButton", command=lambda: self.show_page("logs")).pack(side="right", padx=(0, 6))
        columns = ("time", "actor", "action", "ip", "mac", "result")
        self.overview_log_tree = ttk.Treeview(
            recent,
            columns=columns,
            displaycolumns=columns,
            show="headings",
            selectmode="browse",
            height=10,
        )
        headings = {"time": "时间", "actor": "用户", "action": "操作 / 文件", "ip": "来源 IP", "mac": "设备标识", "result": "结果"}
        # 以 1120px 窗口为下限安排列宽；更宽时把额外空间交给“操作 / 文件”列。
        widths = {"time": 125, "actor": 70, "action": 330, "ip": 110, "mac": 125, "result": 60}
        for name in columns:
            self.overview_log_tree.heading(name, text=headings[name])
            self.overview_log_tree.column(name, width=widths[name], minwidth=24, stretch=False)
        overview_scroll = ttk.Scrollbar(recent, orient="vertical", command=self.overview_log_tree.yview)
        self.overview_log_tree.configure(yscrollcommand=overview_scroll.set)
        self.overview_log_tree.grid(row=1, column=0, sticky="nsew")
        overview_scroll.grid(row=1, column=1, sticky="ns")
        self._load_overview_logs()

    def _reflow_overview(self, event: tk.Event[tk.Misc]) -> None:
        """窄窗口改为上下排列，宽窗口保持视觉稿的双栏结构。"""

        # 常用桌面宽度应保持双栏；只有真正狭窄时才退化成上下排列。
        stacked = event.width < 700
        if getattr(self, "_overview_stacked", None) == stacked:
            return
        self._overview_stacked = stacked
        if stacked:
            self._overview_upper.columnconfigure(0, weight=1)
            self._overview_upper.columnconfigure(1, weight=0)
            self._overview_addresses.grid_configure(row=0, column=0, padx=0, pady=(0, 6))
            self._overview_service.grid_configure(row=1, column=0, padx=0, pady=(6, 0))
        else:
            self._overview_upper.columnconfigure(0, weight=1)
            self._overview_upper.columnconfigure(1, weight=1)
            self._overview_addresses.grid_configure(row=0, column=0, padx=(0, 6), pady=0)
            self._overview_service.grid_configure(row=0, column=1, padx=(6, 0), pady=0)

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
        disk_access = ttk.Frame(card, style="Surface.TFrame")
        disk_access.pack(fill="x", pady=(18, 4))
        if self.full_disk_var.get():
            tk.Label(
                disk_access,
                text="高风险模式已开启：所有访客可访问本机全部可用磁盘",
                bg=SURFACE,
                fg=DANGER,
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(side="left")
            ttk.Button(
                disk_access,
                text="关闭全盘访问",
                style="Secondary.TButton",
                command=self._toggle_full_disk_access,
            ).pack(side="right")
        else:
            ttk.Label(
                disk_access,
                text="仅在完全可信的局域网中使用全盘访客访问",
                style="CardTitle.TLabel",
            ).pack(side="left")
            ttk.Button(
                disk_access,
                text="开放全盘访客访问",
                style="Danger.TButton",
                command=self._toggle_full_disk_access,
            ).pack(side="right")
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

    def _build_transfers(self) -> None:
        self._page_header("传输会话", "实时查看正在上传、等待续传和正在下载的文件")
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="立即刷新", style="Secondary.TButton", command=self._refresh_transfers).pack(side="left")
        ttk.Label(toolbar, text="每 0.5 秒自动刷新；完成的下载保留 10 秒", style="Muted.TLabel").pack(side="left", padx=12)
        columns = ("direction", "path", "source", "progress", "speed", "status")
        holder = ttk.Frame(self.content)
        holder.pack(fill="both", expand=True)
        self.transfer_tree = ttk.Treeview(holder, columns=columns, show="headings", selectmode="none")
        headings = {
            "direction": "方向",
            "path": "文件",
            "source": "来源设备",
            "progress": "进度",
            "speed": "平均速度",
            "status": "状态",
        }
        widths = {"direction": 70, "path": 245, "source": 130, "progress": 170, "speed": 105, "status": 90}
        for name in columns:
            self.transfer_tree.heading(name, text=headings[name])
            self.transfer_tree.column(name, width=widths[name], minwidth=60, stretch=name in {"path", "progress"})
        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.transfer_tree.yview)
        self.transfer_tree.configure(yscrollcommand=scrollbar.set)
        self.transfer_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._refresh_transfers()

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
        self._page_header("审计日志", "操作日志与审计日志是同一份记录，包含来源设备和操作结果")
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Button(toolbar, text="刷新", style="Secondary.TButton", command=self._load_logs).pack(side="left")
        ttk.Button(toolbar, text="用记事本打开", style="Secondary.TButton", command=self._open_audit_log).pack(side="right")
        columns = ("time", "actor", "action", "ip", "mac", "result")
        holder = ttk.Frame(self.content)
        holder.pack(fill="both", expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.log_tree = ttk.Treeview(
            holder,
            columns=columns,
            displaycolumns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "time": "时间",
            "actor": "用户",
            "action": "操作",
            "ip": "IP 地址",
            "mac": "MAC 地址",
            "result": "结果",
        }
        self.log_headings = headings
        self.log_sort_reverse: dict[str, bool] = {}
        self._log_drag_column: str | None = None
        self._log_drag_start_x = 0
        self._log_drag_moved = False
        widths = {"time": 146, "actor": 62, "action": 238, "ip": 116, "mac": 138, "result": 58}
        min_widths = {"time": 48, "actor": 32, "action": 24, "ip": 48, "mac": 48, "result": 32}
        for name in columns:
            self.log_tree.heading(name, text=headings[name])
            self.log_tree.column(name, width=widths[name], minwidth=min_widths[name], stretch=False)
        vertical_scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.log_tree.yview)
        horizontal_scrollbar = ttk.Scrollbar(holder, orient="horizontal", command=self.log_tree.xview)
        self.log_tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        self.log_tree.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.log_tree.bind("<ButtonPress-1>", self._on_log_header_press)
        self.log_tree.bind("<B1-Motion>", self._on_log_header_drag)
        self.log_tree.bind("<ButtonRelease-1>", self._on_log_header_release)
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
            "full_disk_access": self.full_disk_var.get(),
        }
        return AppConfig.from_dict(document, base_dir=self.config_path.parent)

    def _toggle_full_disk_access(self) -> None:
        if self.controller.state != "stopped":
            messagebox.showwarning("请先停止服务", "改变全盘访问范围前必须先停止服务。")
            return
        if self.full_disk_var.get():
            if not messagebox.askyesno("关闭全盘访问", "恢复为默认 CHFShare 共享目录？"):
                return
            self.full_disk_var.set(False)
            self.root_var.set(str(default_share_root().resolve()))
        else:
            confirmed = messagebox.askyesno(
                "高风险：开放本机全部磁盘",
                "开启后，任何能连接到 CHFS 的访客无需登录即可浏览、上传和删除本机所有可访问磁盘中的文件。\n\n"
                "删除操作不可恢复，系统目录也可能被修改。仅应在完全可信的隔离局域网中开启。\n\n"
                "确定继续吗？",
                icon="warning",
            )
            if not confirmed:
                return
            self.full_disk_var.set(True)
            self.read_var.set(True)
            self.write_var.set(True)
            self.delete_var.set(True)
        current_page = self._active_page
        if self._save_config(quiet=True):
            self.show_page(current_page)

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
            self._start_server()
        else:
            self.status_var.set("正在停止")
            self.status_detail_var.set("等待当前请求结束…")
            # 停止过程可能需要等待正在处理的请求，必须放到后台线程，避免 GUI
            # 被 join 阻塞而出现文字与按钮状态交叉。
            threading.Thread(target=self.controller.stop, name="chfs-stop-worker", daemon=True).start()

    def _start_server(self) -> None:
        """保存当前配置并启动服务，可供按钮和默认自动启动共同调用。"""

        if self.controller.state != "stopped":
            return
        if not self._save_config(quiet=True):
            return
        if self.controller.start(self.config):
            self.status_var.set("正在启动")
            self.status_detail_var.set("正在绑定监听地址，请稍候…")

    def _poll_server_state(self) -> None:
        while not self._state_events.empty():
            self._state_events.get_nowait()
        state = self.controller.state
        if self.toggle_button is not None and self.toggle_button.winfo_exists():
            if state == "running":
                self.status_var.set("运行中")
                if self.full_disk_var.get():
                    self.status_detail_var.set("高风险：访客可访问本机所有可用磁盘")
                elif self.host_var.get().strip() in {"127.0.0.1", "::1", "localhost"}:
                    self.status_detail_var.set("本机文件服务在线")
                else:
                    self.status_detail_var.set("局域网文件服务在线")
                self.toggle_button.configure(text="停止服务", style="Running.TButton")
                self._apply_overview_status_style(running=True)
            elif state == "stopped":
                if self.controller.last_error:
                    self.status_var.set("启动失败")
                    self.status_detail_var.set(self.controller.last_error)
                else:
                    self.status_var.set("已关闭")
                    self.status_detail_var.set("配置就绪，启动后即可在浏览器访问")
                self.toggle_button.configure(text="启动服务", style="Primary.TButton")
                self._apply_overview_status_style(running=False)
            elif state == "starting":
                self.status_var.set("正在启动")
                self.status_detail_var.set("正在绑定监听地址，请稍候…")
                self.toggle_button.configure(text="正在启动…", style="Primary.TButton", state="disabled")
            else:
                self.status_var.set("正在停止")
                self.status_detail_var.set("等待当前请求安全结束…")
                self.toggle_button.configure(text="正在停止…", style="Danger.TButton", state="disabled")
        if self.toggle_button is not None and self.toggle_button.winfo_exists() and state not in {"starting", "stopping"}:
            self.toggle_button.configure(state="normal")
        if self._active_page == "transfers":
            self._refresh_transfers()
        self.after(200, self._poll_server_state)

    def _apply_overview_status_style(self, *, running: bool) -> None:
        """同步概览页运行条的颜色和波形，避免状态文字与视觉反馈不一致。"""

        panel = getattr(self, "status_panel", None)
        text_panel = getattr(self, "status_text_panel", None)
        value_label = getattr(self, "status_value_label", None)
        detail_label = getattr(self, "status_detail_label", None)
        waveform = getattr(self, "waveform_label", None)
        if panel is None or not panel.winfo_exists():
            return
        panel.configure(style="Running.TFrame" if running else "Surface.TFrame")
        text_panel.configure(style="Running.TFrame" if running else "Surface.TFrame")
        value_label.configure(style="RunningMetric.TLabel" if running else "Metric.TLabel")
        detail_label.configure(style="RunningDetail.TLabel" if running else "Surface.TLabel")
        waveform.configure(bg=RUNNING_DARK if running else SURFACE)
        if running:
            waveform.grid()
        else:
            waveform.grid_remove()

    def _render_waveform_frame(self) -> None:
        """从波形纹理生成循环平移帧，保持像素质感且不反复创建图片对象。"""

        source = self._status_waveform
        target = self._animated_waveform
        width = source.width()
        height = source.height()
        offset = self._waveform_offset % width
        target.blank()
        if offset == 0:
            target.tk.call(str(target), "copy", str(source), "-from", 0, 0, width, height, "-to", 0, 0)
            return
        target.tk.call(str(target), "copy", str(source), "-from", offset, 0, width, height, "-to", 0, 0)
        target.tk.call(str(target), "copy", str(source), "-from", 0, 0, offset, height, "-to", width - offset, 0)

    def _animate_status_waveform(self) -> None:
        """运行时让状态波形以不完全匀速的节奏向前跳动。"""

        if self.controller.state == "running":
            steps = (3, 4, 3, 6, 2, 5, 3, 4)
            self._waveform_offset = (
                self._waveform_offset + steps[self._waveform_tick % len(steps)]
            ) % self._status_waveform.width()
            self._waveform_tick += 1
            self._render_waveform_frame()
        self.after(90, self._animate_status_waveform)

    def _refresh_transfers(self) -> None:
        tree = getattr(self, "transfer_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        for item in tree.get_children():
            tree.delete(item)
        status_names = {
            "uploading": "上传中",
            "waiting": "等待续传",
            "downloading": "下载中",
            "completed": "已完成",
            "failed": "连接中断",
        }
        for item in self.controller.transfer_snapshots():
            transferred = int(item["transferred_bytes"])
            total = int(item["total_bytes"])
            percentage = 100 if total == 0 else min(100, transferred * 100 // total)
            progress = f"{self._format_bytes(transferred)} / {self._format_bytes(total)}  {percentage}%"
            tree.insert(
                "",
                "end",
                values=(
                    "上传" if item["direction"] == "upload" else "下载",
                    item["path"],
                    item["source"],
                    progress,
                    f"{self._format_bytes(float(item['bytes_per_second']))}/s",
                    status_names.get(str(item["status"]), str(item["status"])),
                ),
            )

    def _show_qr(self, url: str) -> None:
        """把真实 URL 编码为二维码并显示在地址列表右侧。"""

        if not hasattr(self, "qr_label") or not self.qr_label.winfo_exists():
            return
        code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
        code.add_data(url)
        code.make(fit=True)
        matrix = code.get_matrix()
        scale = max(3, 132 // len(matrix))
        size = len(matrix) * scale
        image = tk.PhotoImage(width=size, height=size)
        image.put("#ffffff", to=(0, 0, size, size))
        for row_index, row in enumerate(matrix):
            for column_index, dark in enumerate(row):
                if dark:
                    x = column_index * scale
                    y = row_index * scale
                    image.put("#07131f", to=(x, y, x + scale, y + scale))
        self._qr_photo = image
        self.qr_label.configure(image=image, width=size, height=size)

    def _choose_root(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.root_var.get(), title="选择共享目录")
        if selected:
            self.root_var.set(selected)

    def _open_share_location(self) -> None:
        """使用系统文件管理器打开当前实际共享位置。"""

        try:
            if self.full_disk_var.get():
                # 全盘模式没有唯一共享目录，因此打开“此电脑”。
                subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
                return
            path = Path(self.root_var.get()).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)
            else:
                webbrowser.open(path.as_uri())
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc))

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
        tree = getattr(self, "log_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        for item in tree.get_children():
            tree.delete(item)
        events = self._read_audit_events(limit=300)
        if not events:
            tree.insert("", "end", values=("暂无记录", "-", "启动服务并执行文件操作后会显示在这里", "-", "-", "-"))
            return
        for event in events:
            tree.insert("", "end", values=self._audit_row_values(event))

    def _sort_log_column(self, column: str) -> None:
        """按当前列排序，并在表头显示升降序方向。"""

        reverse = self.log_sort_reverse.get(column, False)
        rows = [(self.log_tree.set(item, column), item) for item in self.log_tree.get_children()]
        rows.sort(key=lambda row: row[0].casefold(), reverse=reverse)
        for index, (_value, item) in enumerate(rows):
            self.log_tree.move(item, "", index)
        for name, title in self.log_headings.items():
            self.log_tree.heading(name, text=title)
        self.log_tree.heading(column, text=f"{self.log_headings[column]} {'▼' if reverse else '▲'}")
        self.log_sort_reverse[column] = not reverse

    def _log_display_columns(self) -> list[str]:
        """返回当前可见列顺序，供表头拖放逻辑使用。"""

        displayed = tuple(self.log_tree["displaycolumns"])
        return list(self.log_tree["columns"]) if displayed == ("#all",) else list(displayed)

    def _on_log_header_press(self, event: tk.Event[tk.Misc]) -> str | None:
        """记录被按下的表头；分隔线保留原生列宽调整行为。"""

        self._log_drag_column = None
        self._log_drag_moved = False
        if self.log_tree.identify_region(event.x, event.y) != "heading":
            return None
        try:
            index = int(self.log_tree.identify_column(event.x).removeprefix("#")) - 1
            self._log_drag_column = self._log_display_columns()[index]
            self._log_drag_start_x = event.x
        except (ValueError, IndexError):
            self._log_drag_column = None
            return None
        # 阻止 Treeview 的类级表头行为抢走拖动；分隔线仍由原生逻辑负责缩放。
        return "break"

    def _on_log_header_drag(self, event: tk.Event[tk.Misc]) -> str | None:
        """把被拖动的表头插入鼠标当前所在的列位置。"""

        if self._log_drag_column is None or abs(event.x - self._log_drag_start_x) < 6:
            return None
        if self.log_tree.identify_region(event.x, event.y) != "heading":
            return None
        try:
            target_index = int(self.log_tree.identify_column(event.x).removeprefix("#")) - 1
            columns = self._log_display_columns()
            source_index = columns.index(self._log_drag_column)
            if target_index == source_index:
                return "break"
            column = columns.pop(source_index)
            columns.insert(target_index, column)
        except (ValueError, IndexError):
            return None
        self.log_tree["displaycolumns"] = tuple(columns)
        self._log_drag_moved = True
        return "break"

    def _on_log_header_release(self, event: tk.Event[tk.Misc]) -> str | None:
        """短按表头排序；拖动表头时只改变列顺序。"""

        column = self._log_drag_column
        dragged = self._log_drag_moved
        self._log_drag_column = None
        self._log_drag_moved = False
        if column is None:
            return None
        if not dragged and self.log_tree.identify_region(event.x, event.y) == "heading":
            self._sort_log_column(column)
        return "break"

    def _load_overview_logs(self) -> None:
        """在概览页使用单行表格显示最新十条操作记录。"""

        tree = getattr(self, "overview_log_tree", None)
        if tree is None or not tree.winfo_exists():
            return
        for item in tree.get_children():
            tree.delete(item)
        for event in self._read_audit_events(limit=10):
            tree.insert("", "end", values=self._audit_row_values(event))

    def _read_audit_events(self, *, limit: int) -> list[dict[str, object]]:
        """读取最近的结构化审计事件；损坏行不会影响其余日志展示。"""

        path = self.config.audit_log
        if not path or not path.exists():
            return []
        events: list[dict[str, object]] = []
        for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"timestamp": "格式错误", "action": line[:80], "success": False}
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _audit_action_text(event: dict[str, object]) -> str:
        action_names = {
            "session.login": "账户登录",
            "session.logout": "退出登录",
            "upload.create": "创建上传会话",
            "upload.cancel": "取消上传",
            "file.upload": "上传文件",
            "file.download": "下载文件",
            "file.delete": "删除文件",
            "directory.create": "新建文件夹",
            "network.reject": "拒绝网络访问",
        }
        raw_action = str(event.get("action", "-") or "-")
        action = action_names.get(raw_action, raw_action)
        details = event.get("details")
        detail_path = details.get("path") if isinstance(details, dict) else None
        return f"{action} · {detail_path}" if detail_path else action

    def _audit_row_values(self, event: dict[str, object]) -> tuple[object, ...]:
        return (
            self._format_audit_time(str(event.get("timestamp", ""))),
            event.get("actor", "-"),
            self._audit_action_text(event),
            event.get("source_ip", event.get("source", "-")),
            event.get("source_mac", "-"),
            "成功" if event.get("success") else "失败",
        )

    @staticmethod
    def _format_audit_time(value: str) -> str:
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.tzinfo is not None:
                instant = instant.astimezone()
            # 概览和表格空间有限；省略通常重复的年份，保留秒级精度。
            return instant.strftime("%m-%d %H:%M:%S")
        except (AttributeError, ValueError):
            return value or "-"

    def _open_audit_log(self) -> None:
        path = self.config.audit_log
        if path is None:
            messagebox.showinfo("未配置日志", "当前没有配置审计日志文件。")
            return
        if not path.exists():
            messagebox.showinfo("暂无日志", "日志文件会在第一次文件操作后自动创建。")
            return
        try:
            if os.name == "nt":
                # JSONL 适合作为追加式审计存储，但 Windows 默认没有文件关联。
                # 明确使用系统记事本，让普通用户无需选择应用即可直接查看。
                subprocess.Popen(["notepad.exe", str(path)])
            else:
                webbrowser.open(path.as_uri())
        except OSError as exc:
            messagebox.showerror("无法打开日志", str(exc))

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
    def _format_bytes(value: float) -> str:
        units = ("B", "KiB", "MiB", "GiB", "TiB")
        amount = float(value)
        for unit in units:
            if abs(amount) < 1024 or unit == units[-1]:
                return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{amount:.1f} TiB"

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
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--print-window-handle", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--capture-page",
        choices=("overview", "share", "network", "transfers", "accounts", "security", "logs"),
        default="overview",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--capture-state",
        choices=("stopped", "running"),
        default="stopped",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    # 自动化截图需要显式控制停止/运行状态；普通启动默认立即开启服务。
    app = CHFSApplication(args.config, auto_start=not args.print_window_handle)
    if args.capture_page != "overview":
        app.show_page(args.capture_page)
    if args.capture_state == "running":
        app.controller.start(app.config)
        app.controller.wait_until_started()
        app._poll_server_state()
    if args.print_window_handle:
        # 仅供自动化视觉验收使用。直接输出客户区的屏幕坐标与尺寸，避免控制台
        # 宿主让 Win32 MainWindowHandle/祖先窗口关系变得不可靠。
        app.geometry("1120x720+260+80")
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
