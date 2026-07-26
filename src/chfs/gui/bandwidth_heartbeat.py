"""可复用的实时带宽爱心动画。

组件只消费传输快照字典，不依赖 CHFS 的控制器或 HTTP 实现。外部程序只需定期
传入 ``id``、``direction``、``status`` 和 ``transferred_bytes``，即可用相邻
快照的字节差驱动动画；以后可以直接把本文件抽成独立素材。
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Iterable, Mapping


PINK = "#ff5c9f"
PINK_LIGHT = "#ffc1da"
PINK_GLOW = "#8f234f"
GRID_MINOR = "#123420"
GRID_MAJOR = "#1a4a2b"
BACKGROUND = "#052d15"


@dataclass(frozen=True, slots=True)
class HeartbeatState:
    """一次采样后供绘图层使用的不可变状态。"""

    mode: str
    upload_bytes_per_second: float
    download_bytes_per_second: float
    utilization: float
    beats_per_minute: float

    @property
    def bytes_per_second(self) -> float:
        return self.upload_bytes_per_second + self.download_bytes_per_second


class BandwidthHeartbeatModel:
    """把累计传输字节转换成平滑的实时带宽和心跳频率。"""

    DIRECT_ACTIVE_STATUSES = {
        "upload": {"uploading"},
        "download": {"downloading"},
    }
    DIRECTIONS = ("upload", "download")
    ACTIVITY_HOLD_SECONDS = 1.35

    def __init__(self, reference_bytes_per_second: float = 125 * 1024 * 1024) -> None:
        if reference_bytes_per_second <= 0:
            raise ValueError("reference_bytes_per_second 必须大于 0")
        self.reference_bytes_per_second = float(reference_bytes_per_second)
        self._last_sample_at: float | None = None
        self._previous_bytes: dict[str, int] = {}
        self._smoothed_rates = {direction: 0.0 for direction in self.DIRECTIONS}
        self._active_until = {direction: 0.0 for direction in self.DIRECTIONS}
        self.state = HeartbeatState("idle", 0.0, 0.0, 0.0, 34.0)

    def sample(
        self,
        snapshots: Iterable[Mapping[str, object]],
        *,
        now: float | None = None,
    ) -> HeartbeatState:
        """采样传输快照；默认参考上限为千兆网络约 125 MiB/s。"""

        sampled_at = time.monotonic() if now is None else float(now)
        current_bytes: dict[str, int] = {}
        current_directions: dict[str, str] = {}
        directly_active: set[str] = set()
        for snapshot in snapshots:
            direction = str(snapshot.get("direction", ""))
            status = str(snapshot.get("status", ""))
            if direction not in self.DIRECTIONS:
                continue
            transfer_id = str(snapshot.get("id", ""))
            if not transfer_id:
                continue
            current_bytes[transfer_id] = max(0, int(snapshot.get("transferred_bytes", 0)))
            current_directions[transfer_id] = direction
            if status in self.DIRECT_ACTIVE_STATUSES[direction]:
                directly_active.add(direction)

        elapsed = 0.0 if self._last_sample_at is None else max(0.0, sampled_at - self._last_sample_at)
        deltas = {direction: 0 for direction in self.DIRECTIONS}
        if elapsed > 0:
            for transfer_id, transferred in current_bytes.items():
                if transfer_id in self._previous_bytes:
                    deltas[current_directions[transfer_id]] += max(
                        0,
                        transferred - self._previous_bytes[transfer_id],
                    )

        active_directions: set[str] = set()
        for direction in self.DIRECTIONS:
            raw_rate = deltas[direction] / elapsed if elapsed > 0 else 0.0
            if direction in directly_active or deltas[direction] > 0:
                self._active_until[direction] = sampled_at + self.ACTIVITY_HOLD_SECONDS

            # 上升较快、下降较慢。请求分块之间的短空档不会再把 80 MiB/s
            # 突然显示成 0，也不会让心形在“传输/待机”之间闪烁。
            previous_rate = self._smoothed_rates[direction]
            time_constant = 0.55 if raw_rate >= previous_rate else 1.25
            alpha = 1.0 if self._last_sample_at is None else 1.0 - math.exp(-elapsed / time_constant)
            smoothed = previous_rate + (raw_rate - previous_rate) * alpha
            if sampled_at > self._active_until[direction] and smoothed < 32 * 1024:
                smoothed = 0.0
            self._smoothed_rates[direction] = smoothed
            if sampled_at <= self._active_until[direction]:
                active_directions.add(direction)

        if active_directions == {"upload", "download"}:
            mode = "mixed"
        elif "upload" in active_directions:
            mode = "upload"
        elif "download" in active_directions:
            mode = "download"
        else:
            mode = "idle"

        upload_rate = self._smoothed_rates["upload"]
        download_rate = self._smoothed_rates["download"]
        utilization = min(1.0, (upload_rate + download_rate) / self.reference_bytes_per_second)
        # 活跃传输即使刚开始也应有可见反馈；接近千兆上限时提升到约 200 BPM。
        intensity = max(utilization, 0.08 if active_directions else 0.0)
        bpm = 34.0 + 166.0 * (intensity**0.68)
        self.state = HeartbeatState(mode, upload_rate, download_rate, utilization, bpm)
        self._previous_bytes = current_bytes
        self._last_sample_at = sampled_at
        return self.state

    def reset(self) -> HeartbeatState:
        """清除历史采样，回到低频呼吸状态。"""

        self._last_sample_at = None
        self._previous_bytes.clear()
        for direction in self.DIRECTIONS:
            self._smoothed_rates[direction] = 0.0
            self._active_until[direction] = 0.0
        self.state = HeartbeatState("idle", 0.0, 0.0, 0.0, 34.0)
        return self.state


class BandwidthHeartbeat(tk.Canvas):
    """粉色爱心带宽图；速率越高，爱心经过的频率越快。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        width: int = 512,
        height: int = 72,
        reference_bytes_per_second: float = 125 * 1024 * 1024,
        background: str = BACKGROUND,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=background,
            bd=0,
            highlightthickness=0,
        )
        self._background = background
        self._model = BandwidthHeartbeatModel(reference_bytes_per_second)
        self._running = False
        self._last_frame_at = time.monotonic()
        self._travel = 0.0
        self._draw_grid()
        self._animation_id = self.after(33, self._animate)

    @property
    def heartbeat_state(self) -> HeartbeatState:
        return self._model.state

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if not running:
            self._model.reset()

    def update_transfers(self, snapshots: Iterable[Mapping[str, object]]) -> HeartbeatState:
        """输入最新累计字节快照并返回当前动画状态。"""

        return self._model.sample(snapshots)

    def destroy(self) -> None:
        if getattr(self, "_animation_id", None) is not None:
            self.after_cancel(self._animation_id)
            self._animation_id = None
        super().destroy()

    def _draw_grid(self) -> None:
        width = int(self.cget("width"))
        height = int(self.cget("height"))
        for x in range(0, width + 1, 4):
            color = GRID_MAJOR if x % 16 == 0 else GRID_MINOR
            self.create_line(x, 0, x, height, fill=color, tags=("grid",))
        for y in range(0, height + 1, 4):
            color = GRID_MAJOR if y % 16 == 0 else GRID_MINOR
            self.create_line(0, y, width, y, fill=color, tags=("grid",))

    def _animate(self) -> None:
        now = time.monotonic()
        elapsed = min(0.1, max(0.0, now - self._last_frame_at))
        self._last_frame_at = now
        if self._running:
            state = self._model.state
            # 横向速度也随负载上升；与可变间距共同表现“带宽越满，心跳越快”。
            pixels_per_second = 92.0 + state.utilization * 115.0
            self._travel = (self._travel + pixels_per_second * elapsed) % 10000.0
            self._render(state, now)
        else:
            self.delete("dynamic")
        self._animation_id = self.after(33, self._animate)

    def _render(self, state: HeartbeatState, now: float) -> None:
        self.delete("dynamic")
        width = int(self.cget("width"))
        height = int(self.cget("height"))
        middle = height / 2 + 2
        speed = 92.0 + state.utilization * 115.0
        spacing = max(64.0, speed * 60.0 / state.beats_per_minute)
        offset = self._travel % spacing

        self.create_line(0, middle, width, middle, fill=PINK_GLOW, width=3, tags=("dynamic",))
        self.create_line(0, middle, width, middle, fill=PINK, width=1, tags=("dynamic",))

        pulse = 1.0 + 0.035 * math.sin(now * state.beats_per_minute * math.tau / 60.0)
        # 标准心形公式的实际高度约为 size 的 0.91；46 px 对应 72 px
        # 画布高度的约 58%～62%，同时留出顶部速率文字空间。
        heart_size = 46.0 * pulse
        x = width + spacing - offset
        while x > -spacing:
            self._draw_heart(x, middle - 1, heart_size)
            x -= spacing

        labels: list[str] = []
        if state.mode in {"upload", "mixed"}:
            labels.append(f"⏫ {self._format_rate(state.upload_bytes_per_second)}")
        if state.mode in {"download", "mixed"}:
            labels.append(f"⏬ {self._format_rate(state.download_bytes_per_second)}")
        if not labels:
            labels.append("♡ 待机")
        self.create_text(
            width - 8,
            7,
            text="    ".join(labels),
            anchor="ne",
            fill=PINK_LIGHT,
            font=("Microsoft YaHei UI", 8, "bold"),
            tags=("dynamic",),
        )

    def _draw_heart(
        self,
        center_x: float,
        center_y: float,
        size: float,
    ) -> None:
        points: list[float] = []
        for step in range(37):
            angle = math.tau * step / 36
            x = 16 * math.sin(angle) ** 3
            y = 13 * math.cos(angle) - 5 * math.cos(2 * angle) - 2 * math.cos(3 * angle) - math.cos(4 * angle)
            points.extend((center_x + x * size / 32, center_y - y * size / 32))
        self.create_polygon(
            points,
            fill=self._background,
            outline=PINK_GLOW,
            width=5,
            smooth=True,
            splinesteps=12,
            tags=("dynamic",),
        )
        self.create_polygon(
            points,
            fill=self._background,
            outline=PINK,
            width=2,
            smooth=True,
            splinesteps=12,
            tags=("dynamic",),
        )

    @staticmethod
    def _format_rate(value: float) -> str:
        units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
        amount = max(0.0, float(value))
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.1f} {unit}" if amount < 100 else f"{amount:.0f} {unit}"
            amount /= 1024
        return "0 B/s"
